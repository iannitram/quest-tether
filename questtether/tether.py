"""
Tether Manager
Wraps ``gnirehtet run`` — the stock command that handles relay, tunnel, and
client management as a single process. We add device detection, graceful
disconnect, auto-reconnect, and throughput stats.

Prefers the native gnirehtet binary (Rust build — bundled as tools/gnirehtet on
Linux, tools/gnirehtet.exe on Windows), and falls back to the Java jar
(``java -jar gnirehtet.jar``) if only that is present. Both accept the identical
``run SERIAL`` command and honor the ADB / GNIREHTET_APK environment variables.
"""

import os
import shutil
import subprocess
import threading
import time
import logging
from enum import Enum
from typing import Optional, Callable
from dataclasses import dataclass

from . import platform_support as ps
from .adb import ADBManager

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

    def __init__(self, adb: ADBManager, gnirehtet_path: Optional[str] = None):
        self.adb = adb
        # ("native", path) or ("jar", path); native is preferred.
        self._relay_kind, self._relay_path = self._find_gnirehtet(gnirehtet_path)
        self._java_path = self._find_java() if self._relay_kind == "jar" else None
        self._apk_path = self._find_apk()

        self._process: Optional[subprocess.Popen] = None
        self._health_thread: Optional[threading.Thread] = None
        self._running = False
        self._auto_reconnect = True
        self._reconnect_attempts = 0

        # Where gnirehtet's own stdout/stderr go. DEVNULL by default; the CLI can
        # set this to None (inherit) for --verbose relay logs.
        self.relay_output = subprocess.DEVNULL

        self.stats = TetherStats()
        self._start_time: Optional[float] = None
        self._prev_rx: int = 0
        self._prev_tx: int = 0
        self._prev_stat_time: float = 0
        self._last_battery_time: float = 0.0

        # Callbacks
        self._on_state_change: Optional[Callable] = None
        self._on_stats_update: Optional[Callable] = None
        self._on_log: Optional[Callable] = None

    # --- Tool discovery ---

    def _find_gnirehtet(self, override):
        """Return (kind, path): prefer a native binary, else the Java jar."""
        if override and os.path.isfile(override):
            kind = "jar" if override.endswith(".jar") else "native"
            return kind, override
        native_names = ["gnirehtet.exe"] if ps.IS_WINDOWS else ["gnirehtet"]
        for td in ps.tools_dirs():
            for name in native_names:
                p = os.path.join(td, name)
                if os.path.isfile(p):
                    return "native", p
            jar = os.path.join(td, "gnirehtet.jar")
            if os.path.isfile(jar):
                return "jar", jar
        # Last resort: bare name on PATH (native).
        return "native", ps.find_binary(native_names)

    def _find_apk(self) -> str:
        for td in ps.tools_dirs():
            p = os.path.join(td, "gnirehtet.apk")
            if os.path.isfile(p):
                return p
        return ""

    def _find_java(self) -> str:
        """Locate Java runtime (only needed for the .jar fallback)."""
        which = "where" if ps.IS_WINDOWS else "which"
        result = ps.run([which, "java"], timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0].strip()
        return "java"

    def is_relay_available(self) -> bool:
        """Check the gnirehtet relay engine is usable."""
        if not self._relay_path or not os.path.isfile(self._relay_path):
            # Might be a bare name resolved on PATH.
            if not (self._relay_path and shutil.which(os.path.basename(self._relay_path))):
                return False
        if self._relay_kind == "jar":
            result = ps.run([self._java_path, "-version"], timeout=5)
            return result.returncode == 0
        return True

    @property
    def relay_engine(self) -> str:
        """Human-readable description of the relay engine in use."""
        if self._relay_kind == "jar":
            return f"Java jar ({self._relay_path})"
        return f"native ({self._relay_path})"

    # --- Command building ---

    def _build_run_cmd(self, serial: str):
        if self._relay_kind == "jar":
            return [self._java_path, "-jar", self._relay_path, "run", serial]
        return [self._relay_path, "run", serial]

    def _relay_env(self):
        """gnirehtet calls adb internally; point it at our bundled adb + apk."""
        env = os.environ.copy()
        env["ADB"] = self.adb.adb_path
        if self._apk_path:
            env["GNIREHTET_APK"] = self._apk_path
        return env

    def _spawn(self, serial: str) -> bool:
        """Launch a gnirehtet run process for `serial`. Returns True on spawn."""
        self._kill_stale_process()
        cmd = self._build_run_cmd(serial)
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=self.relay_output,
                stderr=self.relay_output,
                env=self._relay_env(),
                **ps.NEW_GROUP_KW,
            )
            self.stats.relay_pid = self._process.pid
            self._log(f"gnirehtet started (PID {self._process.pid})")
            return True
        except FileNotFoundError:
            self._log(f"gnirehtet or Java not found: {self._relay_path}")
            self._set_state(TetherState.ERROR, "gnirehtet engine not found")
            return False
        except Exception as e:
            self._log(f"Failed to start gnirehtet: {e}")
            self._set_state(TetherState.ERROR, str(e))
            return False

    # --- Callbacks / logging ---

    @property
    def auto_reconnect(self) -> bool:
        return self._auto_reconnect

    @auto_reconnect.setter
    def auto_reconnect(self, value: bool):
        self._auto_reconnect = value

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
        """Kill any orphaned gnirehtet process holding the relay port."""
        try:
            for pid in ps.pids_on_port(self.RELAY_PORT):
                if self._process and self._process.pid == pid:
                    continue
                self._log(f"Found stale process on port {self.RELAY_PORT} (PID {pid}) — killing...")
                if ps.IS_WINDOWS:
                    ps.run(["taskkill", "/F", "/PID", str(pid)], timeout=5)
                else:
                    try:
                        os.kill(pid, 9)
                    except ProcessLookupError:
                        pass
                time.sleep(0.5)
        except Exception as e:
            self._log(f"Warning: could not check for stale process: {e}")

    def _kill_process(self):
        """Gracefully stop the gnirehtet run process (SIGTERM → SIGKILL)."""
        if not self._process:
            return
        self._log(f"Stopping gnirehtet (PID {self._process.pid})...")
        ps.terminate_tree(self._process, timeout=3.0, log=self._log)
        self._process = None
        self.stats.relay_pid = None

    def force_kill(self):
        """Immediate process-tree kill. Used on app quit — no ADB cleanup."""
        if not self._process:
            return
        ps.terminate_tree(self._process, timeout=1.0)
        self._process = None
        time.sleep(0.3)

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
        self._log(f"Starting gnirehtet run ({self.relay_engine})...")

        if not self._spawn(serial):
            return False

        # Give it a moment, then check it didn't die immediately
        time.sleep(1.5)
        if self._process.poll() is not None:
            self._log("gnirehtet exited immediately — check adb + relay engine")
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
            self._log("tun0 not detected yet — gnirehtet may still be setting up")

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

        # Step 1: Kill the gnirehtet run process (stops relay). On POSIX this
        # SIGTERM also asks gnirehtet to drop the VPN client itself.
        self._kill_process()

        if serial:
            # Step 2: Tell Quest to drop VPN (belt and suspenders)
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

        The gnirehtet run process handles everything internally. We watch for it
        to die and collect throughput from the Quest's tun0 counters.
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
                self._update_stats(serial)

                if not self.is_relay_running():
                    self._log("gnirehtet process died!")
                    if self._auto_reconnect and self._running:
                        self._attempt_reconnect(serial)
                    else:
                        self._set_state(TetherState.ERROR, "Process died")
                    continue

            except Exception:
                logger.exception("Health check error")

            time.sleep(self.HEALTH_CHECK_INTERVAL)

    def _update_stats(self, serial: str):
        """Update throughput from tun0 interface stats and battery from ADB."""
        now = time.time()

        if self._start_time:
            self.stats.uptime_seconds = now - self._start_time

        # tun0 traffic counters — 1 ADB call
        tun0 = self.adb.get_tun0_stats(serial)
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
        if not self._spawn(actual_serial):
            return

        # Wait for tun0
        time.sleep(2.0)
        for i in range(10):
            # Honor a disconnect requested mid-reconnect (user hit Disconnect).
            if not self._running:
                return
            if self.adb.is_vpn_up(actual_serial):
                self._set_state(TetherState.CONNECTED)
                self._reconnect_attempts = 0
                self._log("Reconnected!")
                return
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
