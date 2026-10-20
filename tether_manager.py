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
                    self._log(f"Found stale process on port 31416 (PID {pid}) — killing...")
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=5,
                                   creationflags=_NO_WINDOW)
                    time.sleep(0.5)
                    self._log("Stale process killed")
        except Exception as e:
            self._log(f"Warning: could not check for stale process: {e}")

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

        # Kill any orphaned process from a previous crash
        self._kill_stale_process()

        # Build command: java -jar gnirehtet.jar run SERIAL
        cmd = [self._java_path, "-jar", self.gnirehtet_path, "run", serial]
        self._log(f"Starting gnirehtet run...")

        # gnirehtet run calls adb internally. ADB may not be on PATH
        # (Ian's setup has it in tools/ only). Set the ADB and
        # GNIREHTET_APK env vars so gnirehtet can find them.
        env = os.environ.copy()
        env["ADB"] = self.adb.adb_path
        apk_path = os.path.join(os.path.dirname(self.gnirehtet_path), "gnirehtet.apk")
        if os.path.isfile(apk_path):
            env["GNIREHTET_APK"] = apk_path

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                env=env,
            )
            self.stats.relay_pid = self._process.pid
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
            # Didn't break — tun0 never appeared, but process is still running
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
                self._update_stats(serial)

                # Is the process still alive?
                if not self.is_relay_running():
                    self._log("gnirehtet process died!")
                    if self._auto_reconnect and self._running:
                        self._attempt_reconnect(serial)
                    else:
                        self._set_state(TetherState.ERROR, "Process died")
                    continue

            except Exception as e:
                logger.exception("Health check error")

            time.sleep(self.HEALTH_CHECK_INTERVAL)

    def _update_stats(self, serial: str):
        """Update throughput from tun0 interface stats and battery from ADB.

        Reads /proc/net/dev on the Quest (1 ADB call per health check cycle).
        This gives us real tun0 rx/tx bytes with correct download/upload split.
        At 10s intervals and 16+ MB/s throughput, one ADB call is imperceptible.
        """
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
        self._kill_stale_process()
        cmd = [self._java_path, "-jar", self.gnirehtet_path, "run", actual_serial]

        env = os.environ.copy()
        env["ADB"] = self.adb.adb_path
        apk_path = os.path.join(os.path.dirname(self.gnirehtet_path), "gnirehtet.apk")
        if os.path.isfile(apk_path):
            env["GNIREHTET_APK"] = apk_path

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                env=env,
            )
            self.stats.relay_pid = self._process.pid
            self._log(f"gnirehtet restarted (PID {self._process.pid})")
        except Exception as e:
            self._log(f"Failed to restart: {e}")
            return

        # Wait for tun0
        time.sleep(2.0)
        for i in range(10):
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
