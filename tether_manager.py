"""
Tether Manager
Wraps `gnirehtet run` — the stock command that handles relay, tunnel,
and client management as a single process. We add device detection,
GUI, graceful disconnect, auto-reconnect, and throughput stats.
"""

import subprocess
import sys
import threading
import time
import os
import logging
from enum import Enum
from typing import Optional, Callable, Dict
from dataclasses import dataclass
from adb_manager import ADBManager
import net_sync

# Suppress console windows for all subprocess calls on Windows
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

logger = logging.getLogger(__name__)


class TetherState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class TetherStats:
    """Live connection statistics."""
    state: TetherState = TetherState.IDLE
    uptime_seconds: float = 0.0
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_rate_bps: float = 0.0
    tx_rate_bps: float = 0.0
    reconnect_count: int = 0
    last_error: str = ""
    relay_pid: Optional[int] = None
    device_serial: str = ""
    device_model: str = ""
    vpn_ip: str = ""
    battery_level: str = ""
    battery_status: str = ""


class TetherManager:
    """Wraps gnirehtet run as a single managed process."""

    RELAY_PORT = 31416
    HEALTH_CHECK_INTERVAL = 5.0
    RECONNECT_DELAY = 3.0
    MAX_RECONNECT_ATTEMPTS = 10
    POST_CONNECT_COOLDOWN = 10.0
    BATTERY_POLL_INTERVAL = 120.0

    # Self-healing: consecutive missed tun0 reads (with the relay alive) before
    # each rung of the ladder, and the rate limit between heal actions.
    TUN_MISSES_TO_HEAL = 3
    HEAL_MIN_INTERVAL = 30.0
    HEALS_BEFORE_FULL_RECONNECT = 3
    # Flap breaker: this many heals/reconnects inside the window = stop looping.
    FLAP_LIMIT = 6
    FLAP_WINDOW = 600.0
    # Wi-Fi sync: re-check the PC's network this often; give up on one PC
    # network after this many failed attempts (until the PC's network changes).
    WIFI_SYNC_INTERVAL = 30.0
    WIFI_SYNC_MAX_ATTEMPTS = 3

    LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "QuestTether")

    def __init__(self, adb: ADBManager, gnirehtet_path: Optional[str] = None):
        self.adb = adb
        self._java_path = self._find_java()
        self.gnirehtet_path = gnirehtet_path or self._find_gnirehtet()

        self._process: Optional[subprocess.Popen] = None
        self._health_thread: Optional[threading.Thread] = None
        self._running = False
        self._auto_reconnect = True
        self._reconnect_attempts = 0

        self.stats = TetherStats()
        self._start_time: Optional[float] = None
        self._prev_rx: int = 0
        self._prev_tx: int = 0
        self._prev_stat_time: float = 0
        self._last_battery_time: float = 0.0

        # Health / self-heal state
        self._tun_misses = 0
        self._heals_in_row = 0
        self._last_heal_time = 0.0
        self._recovery_events: list = []   # timestamps of heals + reconnects
        # Headset Wi-Fi policy while tethered: 'off' | 'sync' | 'leave'
        self._wifi_mode = os.environ.get('QT_WIFI_MODE', 'off')
        self._wifi_off_failed = False   # tunnel check failed once this session; don't retry-loop
        self._last_wifi_check = 0.0
        self._wifi_attempts: Dict[str, int] = {}   # PC SSID -> failed attempts
        self._relay_log = None

        # Callbacks
        self._on_state_change: Optional[Callable] = None
        self._on_stats_update: Optional[Callable] = None
        self._on_log: Optional[Callable] = None

    def _find_java(self) -> str:
        """Locate Java runtime."""
        try:
            result = subprocess.run(
                ["where", "java"] if os.name == "nt" else ["which", "java"],
                capture_output=True, text=True, timeout=5,
                creationflags=_NO_WINDOW,
            )
            if result.returncode == 0:
                return result.stdout.strip().split("\n")[0].strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return "java"

    def _find_gnirehtet(self) -> str:
        """Locate gnirehtet Java JAR. Check next to exe (frozen builds) first,
        then next to script."""
        search_dirs = []
        if getattr(sys, 'frozen', False):
            search_dirs.append(os.path.join(os.path.dirname(sys.executable), "tools"))
        search_dirs.append(os.path.join(os.path.dirname(__file__), "tools"))
        for tools_dir in search_dirs:
            jar_path = os.path.join(tools_dir, "gnirehtet.jar")
            if os.path.isfile(jar_path):
                return jar_path
        return ""

    def is_relay_available(self) -> bool:
        """Check if gnirehtet JAR and Java runtime are available."""
        if not self.gnirehtet_path or not os.path.isfile(self.gnirehtet_path):
            return False
        try:
            result = subprocess.run(
                [self._java_path, "-version"],
                capture_output=True, text=True, timeout=5,
                creationflags=_NO_WINDOW,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @property
    def auto_reconnect(self) -> bool:
        return self._auto_reconnect

    @auto_reconnect.setter
    def auto_reconnect(self, value: bool):
        self._auto_reconnect = value

    WIFI_MODES = ("off", "sync", "leave")
    WIFI_FLAG = os.path.join(LOG_DIR, "wifi-off.flag")

    @property
    def wifi_mode(self) -> str:
        return self._wifi_mode

    @wifi_mode.setter
    def wifi_mode(self, value: str):
        if value in self.WIFI_MODES:
            self._wifi_mode = value
            self._wifi_off_failed = False

    def set_callbacks(self, on_state_change=None, on_stats_update=None, on_log=None):
        self._on_state_change = on_state_change
        self._on_stats_update = on_stats_update
        self._on_log = on_log

    def _log(self, msg: str):
        logger.info(msg)
        if self._on_log:
            self._on_log(msg)

    def _set_state(self, state: TetherState, error: str = ""):
        self.stats.state = state
        if error:
            self.stats.last_error = error
        if self._on_state_change:
            self._on_state_change(state, self.stats)

    # --- Process Management ---

    def _kill_stale_process(self):
        """Kill any orphaned gnirehtet process holding port 31416."""
        if os.name != "nt":
            return
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
                creationflags=_NO_WINDOW,
            )
            for line in result.stdout.split("\n"):
                if "31416" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if self._process and self._process.pid == pid:
                        continue
                    if not self._is_gnirehtet_pid(pid):
                        self._log(f"Port 31416 is held by PID {pid}, which is not gnirehtet — leaving it alone")
                        continue
                    self._log(f"Found stale process on port 31416 (PID {pid}) — killing...")
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=5,
                                   creationflags=_NO_WINDOW)
                    time.sleep(0.5)
                    self._log("Stale process killed")
        except Exception as e:
            self._log(f"Warning: could not check for stale process: {e}")

    @staticmethod
    def _is_gnirehtet_pid(pid: int) -> bool:
        """True if the PID's command line mentions gnirehtet. Fails open (True)
        if the lookup itself fails, matching the old kill-anything behaviour."""
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return True
            return "gnirehtet" in r.stdout.lower()
        except Exception:
            return True

    def _spawn_relay(self, serial: str) -> bool:
        """Start `gnirehtet run` with output appended to relay.log (never DEVNULL —
        a silent relay failure is what hid the no-tunnel bug)."""
        cmd = [self._java_path, "-jar", self.gnirehtet_path, "run", serial]

        # gnirehtet run calls adb internally; ADB may not be on PATH.
        env = os.environ.copy()
        env["ADB"] = self.adb.adb_path
        apk_path = os.path.join(os.path.dirname(self.gnirehtet_path), "gnirehtet.apk")
        if os.path.isfile(apk_path):
            env["GNIREHTET_APK"] = apk_path

        try:
            os.makedirs(self.LOG_DIR, exist_ok=True)
            if self._relay_log:
                try:
                    self._relay_log.close()
                except Exception:
                    pass
            self._relay_log = open(os.path.join(self.LOG_DIR, "relay.log"), "ab")
            self._relay_log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} relay start {serial} ===\n".encode())
            self._relay_log.flush()
            out = self._relay_log
        except OSError:
            out = subprocess.DEVNULL

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            cmd, stdout=out, stderr=subprocess.STDOUT,
            creationflags=creationflags, env=env,
        )
        self.stats.relay_pid = self._process.pid
        return True

    def _kill_process(self):
        """Kill the gnirehtet run process. Escalates through terminate → kill → taskkill."""
        if not self._process:
            return
        pid = self._process.pid
        self._log(f"Stopping gnirehtet (PID {pid})...")
        try:
            self._process.terminate()
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._log("Didn't stop gracefully — killing...")
            self._process.kill()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=5,
                                   creationflags=_NO_WINDOW)
        self._process = None
        self.stats.relay_pid = None

    def force_kill(self):
        """Immediate process tree kill. Used on app quit — no logging, no ADB cleanup.

        Waits for the process to fully exit so file handles are released
        before PyInstaller tries to clean up its temp directory.
        """
        if not self._process:
            return
        pid = self._process.pid
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=5,
                               creationflags=_NO_WINDOW)
            else:
                self._process.kill()
            self._process.wait(timeout=5)
        except Exception:
            pass
        self._process = None
        # Brief pause to let Windows release file handles
        time.sleep(0.5)

    def is_relay_running(self) -> bool:
        """Check if the gnirehtet process is alive."""
        return self._process is not None and self._process.poll() is None

    # --- Connect / Disconnect ---

    def connect(self, serial: str) -> bool:
        """Start gnirehtet run — handles relay, tunnel, and client in one process."""
        self._running = True
        self._reconnect_attempts = 0
        self.stats.device_serial = serial

        self._set_state(TetherState.STARTING)

        # A previous session/crash may have left the headset's Wi-Fi off
        self.restore_wifi_if_flagged(serial)

        # Kill any orphaned process from a previous crash
        self._kill_stale_process()

        self._tun_misses = 0
        self._heals_in_row = 0
        self._recovery_events.clear()
        self._log("Starting gnirehtet run...")

        try:
            self._spawn_relay(serial)
            self._log(f"gnirehtet started (PID {self._process.pid})")
        except FileNotFoundError:
            self._log(f"Java or JAR not found: {self.gnirehtet_path}")
            self._set_state(TetherState.ERROR, "Java or gnirehtet JAR not found")
            return False
        except Exception as e:
            self._log(f"Failed to start gnirehtet: {e}")
            self._set_state(TetherState.ERROR, str(e))
            return False

        # Give it a moment, then check it didn't die immediately
        time.sleep(1.5)
        if self._process.poll() is not None:
            self._log("gnirehtet exited immediately — check Java and JAR path")
            self._set_state(TetherState.ERROR, "Process exited immediately")
            return False

        # Wait for tun0 to appear — gnirehtet run handles relay + tunnel + client
        self._log("Waiting for VPN tunnel...")
        for i in range(15):
            time.sleep(1.5)
            if self._process.poll() is not None:
                self._log("gnirehtet process died while waiting for tunnel")
                self._set_state(TetherState.ERROR, "Process died during setup")
                return False
            if self.adb.is_vpn_up(serial):
                break
            self._log(f"Waiting for tun0... ({i + 1}/15)")
        else:
            # tun0 never appeared but the relay is alive: `gnirehtet run` only tries
            # the Quest-side client start once, so re-issue it ourselves.
            self._log("tun0 not detected — re-issuing client start")
            self.adb.start_gnirehtet_client(serial, self.RELAY_PORT)
            for i in range(8):
                time.sleep(1.5)
                if self.adb.is_vpn_up(serial):
                    self._log("tun0 up after client re-start")
                    break
            else:
                self._log("tun0 still missing — health monitor will keep trying")

        # Cache device details — only full ADB detail fetch of the session
        details = self.adb.get_device_details(serial)
        self.stats.device_model = details.get("model", "Quest")
        self.stats.vpn_ip = details.get("vpn_ip", "")
        self.stats.battery_level = details.get("battery_level", "")
        self.stats.battery_status = details.get("battery_status", "")
        self._last_battery_time = time.time()

        self._set_state(TetherState.CONNECTED)
        self._start_time = time.time()
        self._log("Tethering active!")

        self._apply_wifi_policy(serial)

        self._start_health_monitor(serial)
        return True

    def disconnect(self):
        """Graceful shutdown — stop VPN on Quest, then kill gnirehtet process."""
        self._running = False
        self._set_state(TetherState.STOPPING)

        # Stop health monitor
        if self._health_thread:
            self._health_thread.join(timeout=5)
            self._health_thread = None

        serial = self.stats.device_serial

        # Step 1: Kill the gnirehtet run process (stops relay)
        self._kill_process()

        if serial:
            # Step 2: Tell Quest to drop VPN (relay is gone, but VPN may linger)
            self._log("Telling Quest to drop VPN...")
            self.adb.stop_gnirehtet_client(serial)
            time.sleep(1.5)

            # Step 3: Force-stop if VPN still up
            if not self.adb.is_vpn_down(serial):
                self._log("VPN still up — force-stopping...")
                self.adb.force_stop_gnirehtet(serial)
                time.sleep(1.0)

            if self.adb.is_vpn_down(serial):
                self._log("VPN interface down")
            else:
                self._log("Warning: VPN interface still present")

            # Step 4: Toggle Wi-Fi to restore DNS
            self._log("Toggling Wi-Fi to restore DNS...")
            self.adb.toggle_wifi(serial, wait_seconds=5.0)
            self._clear_wifi_flag()

            # Step 5: Verify Wi-Fi connectivity
            wifi_ok = False
            for attempt in range(6):
                if self.adb.check_wifi_connectivity(serial):
                    wifi_ok = True
                    self._log("Quest Wi-Fi restored (routing + DNS)!")
                    break
                self._log(f"Waiting for Wi-Fi... ({attempt + 1}/6)")
                time.sleep(1.5)

            if not wifi_ok:
                self._log("Could not confirm Wi-Fi restored — may need manual reconnect")

            # Step 6: Clean up any lingering ADB tunnels
            self.adb.remove_all_reverse_tunnels(serial)

        self.stats = TetherStats()
        self._start_time = None
        self._set_state(TetherState.IDLE)
        self._log("Disconnected")

    # --- Health Monitoring ---

    def _start_health_monitor(self, serial: str):
        self._health_thread = threading.Thread(
            target=self._health_loop, args=(serial,), daemon=True
        )
        self._health_thread.start()

    def _health_loop(self, serial: str):
        """Monitor gnirehtet process health and gather stats.

        The gnirehtet run process handles everything internally.
        We just watch for it to die and collect throughput stats
        from its Windows process I/O counters (zero ADB calls).
        Battery is the only ADB contact, every ~2 min.
        """
        # Post-connect cooldown
        self._log(f"Health monitor starting in {int(self.POST_CONNECT_COOLDOWN)}s...")
        cooldown = 0.0
        while self._running and cooldown < self.POST_CONNECT_COOLDOWN:
            time.sleep(1.0)
            cooldown += 1.0
        if not self._running:
            return
        self._log("Health monitor active")

        while self._running:
            try:
                tun_ok = self._update_stats(serial)

                # Rung 0: relay process died -> full reconnect
                if not self.is_relay_running():
                    self._log("gnirehtet process died!")
                    if self._auto_reconnect and self._running:
                        if self._flapping("relay died"):
                            return
                        self._attempt_reconnect(serial)
                    else:
                        self._set_state(TetherState.ERROR, "Process died")
                    continue

                # Relay alive: is the tunnel actually there?
                if tun_ok:
                    self._tun_misses = 0
                    self._heals_in_row = 0
                elif self._auto_reconnect:
                    self._tun_misses += 1
                    if self._tun_misses >= self.TUN_MISSES_TO_HEAL:
                        if self._heal(serial):
                            return  # flapping breaker tripped or full reconnect took over

                # Keep the headset Wi-Fi policy applied (headset may re-enable Wi-Fi itself)
                if self._wifi_mode != 'leave' and time.time() - self._last_wifi_check >= self.WIFI_SYNC_INTERVAL:
                    self._apply_wifi_policy(serial)

            except Exception as e:
                logger.exception("Health check error")

            time.sleep(self.HEALTH_CHECK_INTERVAL)

    # --- Self-healing ---

    def _note_recovery(self) -> int:
        """Record a heal/reconnect event; return how many fell inside the flap window."""
        now = time.time()
        self._recovery_events = [t for t in self._recovery_events if now - t < self.FLAP_WINDOW]
        self._recovery_events.append(now)
        return len(self._recovery_events)

    def _flapping(self, reason: str) -> bool:
        """Trip the breaker if we've been recovering too often. Returns True if tripped."""
        if self._note_recovery() > self.FLAP_LIMIT:
            self._log(f"Connection is flapping ({self.FLAP_LIMIT}+ recoveries in "
                      f"{int(self.FLAP_WINDOW // 60)} min, last: {reason}) — stopping auto-recovery")
            self._set_state(
                TetherState.ERROR,
                "Tether keeps dropping — check the USB cable/port, that only one Quest Tether "
                "is running, and %LOCALAPPDATA%\\QuestTether\\relay.log",
            )
            self._running = False
            return True
        return False

    def _heal(self, serial: str) -> bool:
        """Relay is alive but tun0 keeps vanishing. Escalate gently:
        1. re-issue the client start   2. restart adb server + client
        3. full relay reconnect.  Returns True if the health loop should exit."""
        now = time.time()
        if now - self._last_heal_time < self.HEAL_MIN_INTERVAL:
            return False
        self._last_heal_time = now
        self._tun_misses = 0

        if self._flapping("tun0 missing"):
            return True

        self._heals_in_row += 1
        n = self._heals_in_row
        self._set_state(TetherState.RECONNECTING)

        # Is the headset even visible on USB?
        quest = self.adb.find_quest()
        if not quest or quest["status"] != "device":
            self._log("Headset not visible to adb — restarting adb server")
            self.adb.restart_adb_server()
            quest = self.adb.find_quest()
            if not quest or quest["status"] != "device":
                self._log("Headset still not on USB — full reconnect")
                self._attempt_reconnect(serial)
                return not self._running

        if n == 1:
            self._log("tun0 missing while relay is up — re-issuing client start")
            self.adb.start_gnirehtet_client(serial, self.RELAY_PORT)
        elif n == 2:
            self._log("Still no tun0 — restarting adb server, then client")
            self.adb.restart_adb_server()
            self.adb.start_gnirehtet_client(serial, self.RELAY_PORT)
        else:
            self._log("Client restarts didn't stick — full relay reconnect")
            self._heals_in_row = 0
            self._attempt_reconnect(serial)
            return not self._running

        for _ in range(6):
            time.sleep(1.5)
            if self.adb.is_vpn_up(serial):
                self._log("Tunnel restored")
                self._set_state(TetherState.CONNECTED)
                self._prev_stat_time = 0  # don't compute a rate across the gap
                return False
        return False

    def _update_stats(self, serial: str) -> bool:
        """Update throughput from tun0 interface stats and battery from ADB.
        Returns True if tun0 was readable (tunnel present) this cycle.

        Reads /proc/net/dev on the Quest (1 ADB call per health check cycle).
        This gives us real tun0 rx/tx bytes with correct download/upload split.
        At 10s intervals and 16+ MB/s throughput, one ADB call is imperceptible.
        """
        now = time.time()

        if self._start_time:
            self.stats.uptime_seconds = now - self._start_time

        # tun0 traffic counters — 1 ADB call
        tun0 = self.adb.get_tun0_stats(serial)
        if not tun0:
            # Tunnel missing: zero the rates rather than freezing stale numbers
            self.stats.rx_rate_bps = 0.0
            self.stats.tx_rate_bps = 0.0
        if tun0:
            rx = tun0["rx_bytes"]
            tx = tun0["tx_bytes"]

            if self._prev_stat_time > 0:
                dt = now - self._prev_stat_time
                if dt > 0:
                    self.stats.rx_rate_bps = (rx - self._prev_rx) / dt
                    self.stats.tx_rate_bps = (tx - self._prev_tx) / dt

            self.stats.rx_bytes = rx
            self.stats.tx_bytes = tx
            self._prev_rx = rx
            self._prev_tx = tx
            self._prev_stat_time = now

        # Battery — every ~2 min
        if now - self._last_battery_time >= self.BATTERY_POLL_INTERVAL:
            self._last_battery_time = now
            result = self.adb.run_adb("-s", serial, "shell", "dumpsys", "battery")
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "level:" in line:
                        self.stats.battery_level = line.split(":")[-1].strip() + "%"
                    elif "status:" in line:
                        status_code = line.split(":")[-1].strip()
                        status_map = {"1": "Unknown", "2": "Charging", "3": "Discharging",
                                      "4": "Not Charging", "5": "Full"}
                        self.stats.battery_status = status_map.get(status_code, status_code)

        if self._on_stats_update:
            self._on_stats_update(self.stats)
        return tun0 is not None

    # --- Headset Wi-Fi policy ---

    def _apply_wifi_policy(self, serial: str):
        self._last_wifi_check = time.time()
        if self._wifi_mode == "off":
            self._wifi_off(serial)
        elif self._wifi_mode == "sync":
            self._sync_wifi(serial)

    def _clear_wifi_flag(self):
        try:
            os.remove(self.WIFI_FLAG)
        except OSError:
            pass

    def _wifi_off(self, serial: str):
        """Turn the headset's Wi-Fi off so every byte rides the USB tunnel.
        Only cuts Wi-Fi once the tunnel is proven to carry traffic, and records
        a flag so a crash/unplug can be undone the next time we see the headset."""
        if self._wifi_off_failed:
            return
        status = self.adb.run_adb("-s", serial, "shell", "cmd", "wifi", "status")
        if status.returncode == 0 and "wifi is disabled" in status.stdout.lower():
            return  # already off
        if not self.adb.is_vpn_up(serial):
            return  # never cut Wi-Fi without a tunnel underneath
        try:
            os.makedirs(self.LOG_DIR, exist_ok=True)
            with open(self.WIFI_FLAG, "w") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        except OSError:
            pass
        self.adb.run_adb("-s", serial, "shell", "svc", "wifi", "disable")
        time.sleep(3.0)
        if self.adb.check_wifi_connectivity(serial):  # despite the name: tests the default route
            self._log("Headset Wi-Fi off — all traffic on the USB tunnel")
        else:
            self._log("Tunnel didn't carry traffic with Wi-Fi off — turning Wi-Fi back on")
            self.adb.run_adb("-s", serial, "shell", "svc", "wifi", "enable")
            self._clear_wifi_flag()
            self._wifi_off_failed = True

    def restore_wifi_if_flagged(self, serial: str):
        """If a previous session left the headset's Wi-Fi off, turn it back on."""
        if os.path.exists(self.WIFI_FLAG):
            self._log("Restoring headset Wi-Fi left off by a previous session")
            self.adb.run_adb("-s", serial, "shell", "svc", "wifi", "enable")
            self._clear_wifi_flag()

    # --- Wi-Fi Sync ---

    def _sync_wifi(self, serial: str):
        """Put the headset on the PC's Wi-Fi. Bounded per PC network so a bad
        password or out-of-range AP can't turn into a retry loop."""
        self._last_wifi_check = time.time()
        pc = net_sync.get_pc_ssid()
        if pc and self._wifi_attempts.get(pc, 0) >= self.WIFI_SYNC_MAX_ATTEMPTS:
            return
        result = net_sync.sync_quest_wifi(self.adb, serial, self._log)
        if pc:
            if result in ("failed", "manual"):
                self._wifi_attempts[pc] = self._wifi_attempts.get(pc, 0) + 1
                if self._wifi_attempts[pc] >= self.WIFI_SYNC_MAX_ATTEMPTS:
                    self._log(f"Wi-Fi sync: giving up on \"{pc}\" until the PC's network changes")
            else:
                self._wifi_attempts.pop(pc, None)

    def sync_wifi_now(self):
        """Manual trigger (UI button): reset attempt limits and sync immediately."""
        serial = self.stats.device_serial
        if serial:
            self._wifi_attempts.clear()
            self._sync_wifi(serial)

    # --- Auto-Reconnect ---

    def _attempt_reconnect(self, serial: str):
        """Kill and restart the gnirehtet run process."""
        if self._reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
            self._log(f"Max reconnect attempts ({self.MAX_RECONNECT_ATTEMPTS}) reached")
            self._set_state(TetherState.ERROR, "Max reconnect attempts reached")
            self._running = False
            return

        self._reconnect_attempts += 1
        self.stats.reconnect_count += 1
        self._set_state(TetherState.RECONNECTING)
        self._log(f"Reconnect attempt {self._reconnect_attempts}/{self.MAX_RECONNECT_ATTEMPTS}")

        # Kill the old process
        self._kill_process()
        time.sleep(self.RECONNECT_DELAY)

        # Check device is still on USB
        quest = self.adb.find_quest()
        if not quest or quest["status"] != "device":
            self._log("Device not found on USB — waiting...")
            self._wait_for_device_and_reconnect(serial)
            return

        actual_serial = quest["serial"]
        self.stats.device_serial = actual_serial

        # Restart gnirehtet run
        self._kill_stale_process()
        try:
            self._spawn_relay(actual_serial)
            self._log(f"gnirehtet restarted (PID {self._process.pid})")
        except Exception as e:
            self._log(f"Failed to restart: {e}")
            return

        # Wait for tun0; re-issue the client start once if the first try was missed
        time.sleep(2.0)
        for i in range(10):
            if self.adb.is_vpn_up(actual_serial):
                self._set_state(TetherState.CONNECTED)
                self._reconnect_attempts = 0
                self._tun_misses = 0
                self._prev_stat_time = 0
                self._log("Reconnected!")
                return
            if i == 4:
                self._log("tun0 slow — re-issuing client start")
                self.adb.start_gnirehtet_client(actual_serial, self.RELAY_PORT)
            time.sleep(1.5)

        self._log("Reconnect: tun0 not detected — will retry")

    def _wait_for_device_and_reconnect(self, serial: str):
        """Wait for Quest to reappear on USB, then reconnect."""
        self._set_state(TetherState.RECONNECTING)
        self._log("Waiting for device to reappear on USB...")

        wait_time = 0
        max_wait = 60

        while self._running and wait_time < max_wait:
            time.sleep(2)
            wait_time += 2

            quest = self.adb.find_quest()
            if quest and quest["status"] == "device":
                self._log(f"Device reconnected ({quest['serial']})")
                time.sleep(1)
                self._attempt_reconnect(quest["serial"])
                return

        if self._running:
            self._log("Device did not reconnect within timeout")
            self._set_state(TetherState.ERROR, "Device did not reconnect")
            self._running = False
