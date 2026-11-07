"""
Tether Manager
Runs the gnirehtet relay and tunnels the headset through it. Split mode (default,
#66) starts `gnirehtet relay -p <port>` alone, waits until the port listens, then starts
the Quest-side client ourselves over adb. The old `gnirehtet run` path stays behind
QT_RELAY_MODE=run for A/B tests. We add device detection, GUI, graceful disconnect,
auto-reconnect, and throughput stats.
The split flow and the 25 ms / 5 s port wait are borrowed from gnirehtet-gui
(Apache-2.0, credited in NOTICE).
"""

import socket
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
import qt_platform
import heal_policy
from heal import Heal, Healer

logger = logging.getLogger(__name__)


class TetherState(Enum):
    IDLE = "idle"
    STARTING = "starting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    ERROR = "error"


# States in which a connect request must not start another relay. Both the
# service handler and the USB monitor call TetherManager.connect(), so the
# guard lives here to cover both paths. A reconnect in progress owns the relay
# (#86): a replug during it must not start a second one or count as a reconnect.
ACTIVE_CONNECT_STATES = (
    TetherState.STARTING,
    TetherState.CONNECTED,
    TetherState.RECONNECTING,
)


def _state_for_tunnel(tun0_present: bool) -> TetherState:
    """Return the user-visible state supported by the current tunnel check."""
    return TetherState.CONNECTED if tun0_present else TetherState.STARTING


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
    battery_temp_c: Optional[float] = None


class TetherManager:
    """Runs the gnirehtet relay (split mode by default, `gnirehtet run` for A/B) and its Quest client."""

    RELAY_PORT = 31416
    HEALTH_CHECK_INTERVAL = 5.0
    RECONNECT_DELAY = 3.0
    MAX_RECONNECT_ATTEMPTS = 10
    POST_CONNECT_COOLDOWN = 10.0
    BATTERY_POLL_INTERVAL = 120.0
    # Disconnect (#64): how long tun0 may take to go after the STOP intent, the
    # poll step, and the shorter wait after a force-stop. Defaults; device-tune later.
    VPN_CLOSE_DEADLINE = 6.0
    VPN_CLOSE_POLL = 0.5
    VPN_FORCE_STOP_WAIT = 2.0
    # Quit path (force_kill): best-effort STOP, each adb call capped at this.
    FORCE_KILL_STOP_TIMEOUT = 2.0
    # Split relay (#66): relay modes, and how long/how often to wait for the port.
    RELAY_MODES = ("split", "run")
    RELAY_LISTEN_TIMEOUT = 5.0
    RELAY_LISTEN_POLL = 0.025

    # Self-healing (#65): with the relay alive and tun0 gone, the headset must be
    # awake and silent for heal_policy.GRACE before a restart. Restarts back off
    # 60 s doubling to 300 s. This many restarts with no tunnel in between end
    # auto-recovery with ERROR (owner to confirm the number).
    HEAL_FINAL_RESTARTS = 10
    # Wi-Fi sync: re-check the PC's network this often; give up on one PC
    # network after this many failed attempts (until the PC's network changes).
    WIFI_SYNC_INTERVAL = 30.0
    WIFI_SYNC_MAX_ATTEMPTS = 3

    def __init__(
        self, adb: ADBManager, gnirehtet_path: Optional[str] = None, platform=None
    ):
        self.adb = adb
        self.platform = platform or qt_platform.current()
        self._data_dir = self.platform.data_dir()
        self._wifi_flag = os.path.join(self._data_dir, "wifi-off.flag")
        self._java_path = self.platform.tool_path("java", []) or "java"
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
        # flap_max=None: the liveness backoff (#65) replaces the flap breaker.
        self._healer = Healer(
            flap_max=None,
            max_reconnects=self.MAX_RECONNECT_ATTEMPTS,
        )
        self._liveness = heal_policy.Liveness()
        self._liveness_lock = threading.Lock()
        self._tun_up_since: Optional[float] = None
        # Headset Wi-Fi policy while tethered: 'off' | 'sync' | 'leave'
        self._wifi_mode = os.environ.get("QT_WIFI_MODE", "off")
        self._wifi_off_failed = (
            False  # tunnel check failed once this session; don't retry-loop
        )
        self._last_wifi_check = 0.0
        self._wifi_attempts: Dict[str, int] = {}  # PC SSID -> failed attempts
        self._relay_log = None
        # 'split' (relay alone + our client start) or 'run' (gnirehtet run, old path)
        self._relay_mode = os.environ.get("QT_RELAY_MODE", "split")
        if self._relay_mode not in self.RELAY_MODES:
            self._relay_mode = "split"

        # Callbacks
        self._on_state_change: Optional[Callable] = None
        self._on_stats_update: Optional[Callable] = None
        self._on_log: Optional[Callable] = None

    def _find_gnirehtet(self) -> str:
        """Locate the relay this platform runs (Windows JAR, Linux native binary).
        Check next to exe (frozen builds) first, then next to script."""
        search_dirs = []
        if getattr(sys, "frozen", False):
            search_dirs.append(os.path.join(os.path.dirname(sys.executable), "tools"))
        search_dirs.append(os.path.join(os.path.dirname(__file__), "tools"))
        for tools_dir in search_dirs:
            for name, needs_java in self.platform.relay_candidates():
                path = os.path.join(tools_dir, name)
                if not os.path.isfile(path):
                    continue
                if not needs_java and not os.access(path, os.X_OK):
                    continue
                return path
        return ""

    def _relay_needs_java(self) -> bool:
        """Whether the relay file this manager was given is the Java one."""
        name = os.path.basename(self.gnirehtet_path)
        for candidate, needs_java in self.platform.relay_candidates():
            if name == candidate:
                return needs_java
        return True  # an explicit unknown path keeps the old Java assumption

    def is_relay_available(self) -> bool:
        """Check the relay file and, for the Java relay, the runtime."""
        if not self.gnirehtet_path or not os.path.isfile(self.gnirehtet_path):
            return False
        if not self._relay_needs_java():
            return os.access(self.gnirehtet_path, os.X_OK)
        try:
            result = subprocess.run(
                [self._java_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=self.platform.no_window_flags(),
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

    @property
    def relay_mode(self) -> str:
        return self._relay_mode

    @relay_mode.setter
    def relay_mode(self, value: str):
        if value in self.RELAY_MODES:
            self._relay_mode = value

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

    def _client_refusal_text(self) -> str:
        error = getattr(self.adb, "last_client_error", None)
        if isinstance(error, str) and error:
            return f"client start refused: {error}"
        code = getattr(self.adb, "last_client_exit_code", None)
        return f"client start failed (exit code {code})"

    def _log_client_start_refused(self):
        self._log(self._client_refusal_text())

    def _set_state(self, state: TetherState, error: str = ""):
        if state is TetherState.CONNECTED:
            # Any route back to Connected starts a new outage, so the next
            # drop gets the full reconnect budget.
            self._reconnect_attempts = 0
        self.stats.state = state
        if error:
            self.stats.last_error = error
        if self._on_state_change:
            self._on_state_change(state, self.stats)

    # --- Process Management ---

    def _kill_stale_process(self):
        """Kill any orphaned gnirehtet process holding port 31416."""
        try:
            for pid in self.platform.stale_relay_pids(self.RELAY_PORT):
                if self._process and self._process.pid == pid:
                    continue
                self._log(
                    f"Found stale process on port {self.RELAY_PORT} (PID {pid}) — killing..."
                )
                self.platform.kill(pid)
                self._log("Stale process killed")
        except Exception as e:
            self._log(f"Warning: could not check for stale process: {e}")

    def _spawn_relay(self, serial: str) -> bool:
        """Start `gnirehtet run` with output appended to relay.log (never DEVNULL —
        a silent relay failure is what hid the no-tunnel bug)."""
        tools_dir = os.path.dirname(self.gnirehtet_path)
        if self.relay_mode == "split":
            # relay alone on the port; no serial, the client is started by adb (#66)
            cmd = self.platform.relay_server_command(tools_dir, self.RELAY_PORT)
        else:
            cmd = self.platform.relay_command(tools_dir, serial)

        # gnirehtet run calls adb internally; ADB may not be on PATH.
        env = os.environ.copy()
        env["ADB"] = self.adb.adb_path
        apk_path = os.path.join(os.path.dirname(self.gnirehtet_path), "gnirehtet.apk")
        if os.path.isfile(apk_path):
            env["GNIREHTET_APK"] = apk_path

        try:
            os.makedirs(self._data_dir, exist_ok=True)
            if self._relay_log:
                try:
                    self._relay_log.close()
                except Exception:
                    pass
            self._relay_log = open(os.path.join(self._data_dir, "relay.log"), "ab")
            self._relay_log.write(
                f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} relay start {serial} ===\n".encode()
            )
            self._relay_log.flush()
            out = self._relay_log
        except OSError:
            out = subprocess.DEVNULL

        self._process = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=self.platform.no_window_flags(),
            env=env,
        )
        self.stats.relay_pid = self._process.pid
        return True

    def _relay_log_last_line(self) -> str:
        """Last non-empty line the current relay wrote to relay.log; "" if none.

        Each start writes a "=== ... relay start" marker first, so a marker reached
        first (going backwards) means this relay printed nothing.
        """
        try:
            with open(os.path.join(self._data_dir, "relay.log"), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 4096))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        for line in reversed(tail.splitlines()):
            text = line.strip()
            if not text:
                continue
            if text.startswith("==="):
                return ""
            return text
        return ""

    @staticmethod
    def _port_open(port: int) -> bool:
        """True if something accepts connections on 127.0.0.1:port (a 0.2 s probe)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def _wait_until_listening(self, port: int) -> bool:
        """Poll until the relay listens on port. False if it dies first or the deadline passes."""
        deadline = time.monotonic() + self.RELAY_LISTEN_TIMEOUT
        while True:
            if self._process is None or self._process.poll() is not None:
                return False
            if self._port_open(port):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.RELAY_LISTEN_POLL)

    def _port_blocker(self, port: int) -> Optional[int]:
        """PID of a program on port that is not gnirehtet, else None. Read-only."""
        try:
            holders = self.platform.port_holders(port)
        except Exception as e:
            self._log(f"Warning: could not check port {port}: {e}")
            return None
        for pid, is_gnirehtet in holders:
            if not is_gnirehtet:
                return pid
        return None

    def _start_split_relay(self, serial: str) -> Optional[str]:
        """Split mode (#66): start the relay alone, wait for its port, then start the
        Quest client ourselves. Returns None when the relay listens and the client
        start was accepted; else the reason (the caller logs it). Kills only the relay
        it started, and nothing else."""
        port = self.RELAY_PORT
        blocker = self._port_blocker(port)
        if blocker is not None:
            return (
                f"port {port} is held by PID {blocker}, which is not gnirehtet"
                " — not starting the relay"
            )

        try:
            self._spawn_relay(serial)
        except FileNotFoundError:
            return "Java or gnirehtet JAR not found"
        except Exception as e:
            return str(e)
        self._log(f"relay started (PID {self._process.pid}), waiting for port {port}")

        if not self._wait_until_listening(port):
            if self._process.poll() is not None:
                last = self._relay_log_last_line()
                reason = "relay exited at startup: " + (
                    last or "no output in relay.log"
                )
            else:
                reason = (
                    f"relay did not listen on port {port} within "
                    f"{int(self.RELAY_LISTEN_TIMEOUT)} s"
                )
                self._kill_process()
            return reason

        if not self.adb.start_gnirehtet_client(serial, port):
            self._kill_process()
            return self._client_refusal_text()
        return None

    def _start_run_relay(self, serial: str) -> bool:
        """Run mode: `gnirehtet run` starts relay, tunnel and client in one process."""
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
        return True

    def _kill_process(self):
        """Kill the gnirehtet run process. Escalates through terminate → kill → platform kill."""
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
                self.platform.kill(pid)
        self._process = None
        self.stats.relay_pid = None

    def force_kill(self):
        """Immediate process tree kill. Used on app quit — no logging, no ADB cleanup.

        Waits for the process to fully exit so file handles are released
        before PyInstaller tries to clean up its temp directory.

        One exception (#64, as gnirehtet-gui does): a best-effort STOP intent
        goes to the headset first, with each adb call capped, so the VPN does
        not outlive the relay. Total wait stays around 4 s at most.
        """
        proc = self._process
        if not proc:
            return
        serial = self.stats.device_serial
        if serial:
            try:
                self.adb.stop_gnirehtet_client(
                    serial, timeout=self.FORCE_KILL_STOP_TIMEOUT
                )
            except Exception:
                pass
        # Another thread may clear self._process during the STOP; use our copy.
        pid = proc.pid
        try:
            self.platform.kill_tree(pid)
            proc.wait(timeout=5)
        except Exception:
            pass
        self._process = None
        # Brief pause to let Windows release file handles
        time.sleep(0.5)

    def is_relay_running(self) -> bool:
        """Check if the gnirehtet process is alive."""
        return self._process is not None and self._process.poll() is None

    def note_usb_event(self, message: str):
        """Log a USB event the service saw (#86). Changes no state."""
        self._log(message)

    # --- Connect / Disconnect ---

    def connect(self, serial: str) -> bool:
        """Start gnirehtet run — handles relay, tunnel, and client in one process."""
        if self.stats.state in ACTIVE_CONNECT_STATES:
            self._log(f"Already {self.stats.state.value} — not starting another relay")
            return True

        self._running = True
        self._reconnect_attempts = 0
        self.stats.device_serial = serial

        self._set_state(TetherState.STARTING)

        # A previous session/crash may have left the headset's Wi-Fi off
        self.restore_wifi_if_flagged(serial)

        # Kill any orphaned process from a previous crash
        self._kill_stale_process()

        self._healer.reset()
        self._set_liveness(heal_policy.Liveness())
        self._tun_up_since = None

        if self.relay_mode == "split":
            self._log("Starting gnirehtet relay (split mode)...")
            error = self._start_split_relay(serial)
            if error:
                self._log(error)
                self._set_state(TetherState.ERROR, error)
                return False
        else:
            self._log("Starting gnirehtet run...")
            if not self._start_run_relay(serial):
                return False

        # Wait for tun0 to appear: the relay runs and the client start was accepted
        self._log("Waiting for VPN tunnel...")
        tun0_present = False
        for i in range(15):
            time.sleep(1.5)
            if self._process.poll() is not None:
                self._log("gnirehtet process died while waiting for tunnel")
                self._set_state(TetherState.ERROR, "Process died during setup")
                return False
            if self.adb.is_vpn_up(serial):
                tun0_present = True
                break
            self._log(f"Waiting for tun0... ({i + 1}/15)")
        else:
            # tun0 never appeared but the relay is alive: `gnirehtet run` only tries
            # the Quest-side client start once, so re-issue it ourselves.
            self._log("tun0 not detected — re-issuing client start")
            if self.adb.start_gnirehtet_client(serial, self.RELAY_PORT):
                for i in range(8):
                    time.sleep(1.5)
                    if self.adb.is_vpn_up(serial):
                        tun0_present = True
                        self._log("tun0 up after client re-start")
                        break
                else:
                    self._log("tun0 still missing — health monitor will keep trying")
            else:
                self._log_client_start_refused()

        # Cache device details — only full ADB detail fetch of the session
        details = self.adb.get_device_details(serial)
        self.stats.device_model = details.get("model", "Quest")
        self.stats.vpn_ip = details.get("vpn_ip", "")
        self.stats.battery_level = details.get("battery_level", "")
        self.stats.battery_status = details.get("battery_status", "")
        self._last_battery_time = 0.0  # read the temperature on the first health cycle

        self._set_state(_state_for_tunnel(tun0_present))
        self._start_time = time.time()
        self._log("Tethering active!")

        self._apply_wifi_policy(serial)

        self._start_health_monitor(serial)
        return True

    def disconnect(self, headset_present: bool = True):
        """Graceful shutdown, VPN first (#64; the order is from quest-vd-wired).

        1. Stop the VPN on the headset and wait (bounded) for tun0 to go.
        2. Put Wi-Fi back and verify it.
        3. Always, even if 1 or 2 failed: remove the reverse tunnels, then kill
           the relay. The relay dies last.

        With headset_present=False (the cable is out, #86) steps 1 to 3 that
        talk to the headset are skipped, so no adb call waits on a missing
        device. Only the health thread and the relay are stopped.
        """
        self._running = False
        self._set_state(TetherState.STOPPING)

        # Stop health monitor
        if self._health_thread:
            self._health_thread.join(timeout=5)
            self._health_thread = None

        serial = self.stats.device_serial

        try:
            if not headset_present:
                self._log("Headset absent — skipping headset-side cleanup")
            elif serial:
                self._stop_vpn_on_headset(serial)
        except Exception as e:
            self._log(f"Headset-side stop failed: {e}")
        finally:
            if headset_present and serial:
                try:
                    self.adb.remove_all_reverse_tunnels(serial)
                except Exception as e:
                    self._log(f"Could not remove reverse tunnels: {e}")
            self._kill_process()

        self.stats = TetherStats()
        self._start_time = None
        self._set_state(TetherState.IDLE)
        self._log("Disconnected")

    def _stop_vpn_on_headset(self, serial: str):
        """Steps 1 and 2 of disconnect. Assumes the caller kills the relay after."""
        if self.adb.find_serial() != serial:
            self._log(
                "Headset not reachable over adb — the VPN may still be on. "
                "Stopping the relay anyway."
            )
            return

        self._log("Telling Quest to drop VPN...")
        if not self.adb.stop_gnirehtet_client(serial):  # wakes the headset first
            self._log("Headset refused the STOP intent — waiting to see if the VPN closes")

        if self._wait_vpn_closed(serial):
            self._log("VPN interface down")
        else:
            state = self.adb.vpn_agent_state(serial)
            self._log(
                f"VPN still up after {self.VPN_CLOSE_DEADLINE:g} s "
                f"(headset VPN agent: {state or 'not listed'}) — force-stopping..."
            )
            self.adb.force_stop_gnirehtet(serial)
            if self._wait_vpn_closed(serial, deadline=self.VPN_FORCE_STOP_WAIT):
                self._log("VPN interface down")
            else:
                self._log("Warning: VPN interface still present")

        # Toggle Wi-Fi to restore DNS
        self._log("Toggling Wi-Fi to restore DNS...")
        self.adb.toggle_wifi(serial, wait_seconds=5.0)
        self._clear_wifi_flag()

        # Verify Wi-Fi connectivity
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

    def _wait_vpn_closed(self, serial: str, deadline: Optional[float] = None) -> bool:
        """Poll tun0 until it is gone or the deadline passes (seconds). Never sleeps
        past the deadline, so a fixed sleep is not needed (#64)."""
        limit = time.monotonic() + (
            self.VPN_CLOSE_DEADLINE if deadline is None else deadline
        )
        while True:
            if self.adb.is_vpn_down(serial):
                return True
            if time.monotonic() >= limit:
                return False
            time.sleep(min(self.VPN_CLOSE_POLL, max(0.0, limit - time.monotonic())))

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

                # Rung 0: relay process died -> back off, then full reconnect
                if not self.is_relay_running():
                    self._relay_died(serial)
                else:
                    # Relay alive: is the tunnel actually there?
                    self._check_tunnel(serial, tun_ok, time.time())
                if not self._running:
                    return   # auto-recovery gave up: no Wi-Fi sync after the stop

                # Keep the headset Wi-Fi policy applied (headset may re-enable Wi-Fi itself)
                if (
                    self._wifi_mode != "leave"
                    and time.time() - self._last_wifi_check >= self.WIFI_SYNC_INTERVAL
                ):
                    self._apply_wifi_policy(serial)

            except Exception as e:
                logger.exception("Health check error")

            time.sleep(self.HEALTH_CHECK_INTERVAL)

    # --- Self-healing ---

    def _liveness_now(self) -> heal_policy.Liveness:
        with self._liveness_lock:
            return self._liveness

    def _set_liveness(self, live: heal_policy.Liveness):
        with self._liveness_lock:
            self._liveness = live

    def _check_tunnel(self, serial: str, tun_ok: bool, now: float):
        """Relay alive: update the state and run the tunnel-missing branch (#65)."""
        tunnel_state = _state_for_tunnel(tun_ok)
        if self.stats.state != tunnel_state:
            self._set_state(tunnel_state)
        if tun_ok:
            self._tunnel_seen(now)
        elif self._auto_reconnect:
            self._tunnel_missing(serial, now)

    def _tunnel_seen(self, now: float):
        """tun0 is up. The silence and awake clocks restart at once, so a later miss
        has to earn its own grace. The restart count and backoff stay until tun0 has
        stayed up for GRACE, so a relay that dies again right after a restart
        still backs off (#65)."""
        self._healer.observe(now, True, True)
        self._set_liveness(self._liveness_now().ineligible())
        if self._tun_up_since is None:
            self._tun_up_since = now
        if now - self._tun_up_since >= heal_policy.GRACE:
            self._set_liveness(self._liveness_now().connected())

    def _tunnel_missing(self, serial: str, now: float):
        """tun0 is gone while the relay is up. Heal only while the headset is awake
        and has been silent for the grace period, with the restart backoff (#65).
        A sleeping headset causes no heal and no breaker trip."""
        self._tun_up_since = None
        live = self._liveness_now().silent(now)
        if live.probe_due(now):
            awake = self.adb.is_headset_awake(serial)
            if awake is None:
                # No reading: not proof of awake. Drop the stale awake clock so no
                # awake restart rests on it; a bounded run of these is handled below.
                live = live.unreadable(now)
            else:
                live = live.observed(awake, now)
        self._set_liveness(live)

        unreadable = False
        if not live.restart_due(now):
            if not live.unreadable_restart_due(now):
                return
            # Listed in adb in any state (offline, unauthorized) or `adb devices`
            # itself failing (wedged adb server): heal it. Read and not in the list
            # (unplugged): keep waiting, #86 owns that.
            if self.adb.device_listed(serial) is False:
                return
            unreadable = True
        if self._restart_cap_reached():
            self._give_up_healing(self._keeps_dropping_text())
            return
        action = self._healer.escalate(now)
        if action is Heal.GIVE_UP:
            self._give_up_healing("Max reconnect attempts reached")
            return
        if unreadable and action is Heal.RESTART_CLIENT:
            action = Heal.RESTART_ADB_THEN_CLIENT   # a wedged adb needs its server restarted first
        try:
            self._heal(serial, action)
        finally:
            # Even when the heal raises: the backoff and the cap must still advance.
            self._note_restart(time.time())

    def _relay_died(self, serial: str):
        """The relay process exited: reconnect, waiting out the restart backoff."""
        self._log("gnirehtet process died!")
        self._tun_up_since = None
        if not (self._auto_reconnect and self._running):
            self._set_state(TetherState.ERROR, "Process died")
            return
        if self._restart_cap_reached():
            self._give_up_healing(self._keeps_dropping_text())
            return
        self._set_state(TetherState.RECONNECTING)
        self._wait_backoff()
        if self._running:
            try:
                self._attempt_reconnect(serial)
            finally:
                # Even when the reconnect raises: the backoff and the cap must still advance.
                self._note_restart(time.time())

    def _restart_cap_reached(self) -> bool:
        return self._liveness_now().restarts >= self.HEAL_FINAL_RESTARTS

    def _note_restart(self, now: float):
        with self._liveness_lock:
            self._liveness = self._liveness.restarted(now)

    def _wait_backoff(self):
        """Sleep until the restart backoff allows the next relay restart. Steps of
        at most 1 s, so a disconnect is not held up by a long sleep (#65)."""
        while self._running:
            due = self._liveness_now().next_restart_at
            if due is None:
                return
            remaining = due - time.time()
            if remaining <= 0:
                return
            time.sleep(min(1.0, remaining))

    def _keeps_dropping_text(self) -> str:
        return (
            "Tether keeps dropping — check the USB cable/port, that only one Quest Tether "
            "is running, and " + os.path.join(self._data_dir, "relay.log")
        )

    def _give_up_healing(self, error: str):
        """The one place auto-recovery stops for good."""
        restarts = self._liveness_now().restarts
        self._log(
            f"Auto-recovery stopped after {restarts} restarts with no tunnel — "
            "not retrying"
        )
        self._set_state(TetherState.ERROR, error)
        self._running = False

    def _heal(self, serial: str, action: Heal) -> bool:
        """Relay is alive but tun0 keeps vanishing. Escalate gently:
        1. re-issue the client start   2. restart adb server + client
        3. full relay reconnect.  Returns True if the health loop should exit."""
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

        start_refused = False
        if action is Heal.RESTART_CLIENT:
            self._log("tun0 missing while relay is up — re-issuing client start")
            if not self.adb.start_gnirehtet_client(serial, self.RELAY_PORT):
                self._log_client_start_refused()
                start_refused = True
        elif action is Heal.RESTART_ADB_THEN_CLIENT:
            self._log("Still no tun0 — restarting adb server, then client")
            self.adb.restart_adb_server()
            if not self.adb.start_gnirehtet_client(serial, self.RELAY_PORT):
                self._log_client_start_refused()
                start_refused = True
        else:
            self._log("Client restarts didn't stick — full relay reconnect")
            self._attempt_reconnect(serial)
            return not self._running

        if start_refused:
            return False

        for _ in range(6):
            time.sleep(1.5)
            if self.adb.is_vpn_up(serial):
                self._log("Tunnel restored")
                self._reconnect_attempts = 0
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
                        status_map = {
                            "1": "Unknown",
                            "2": "Charging",
                            "3": "Discharging",
                            "4": "Not Charging",
                            "5": "Full",
                        }
                        self.stats.battery_status = status_map.get(
                            status_code, status_code
                        )
                    elif "temperature:" in line:
                        temp_raw = line.split(":")[-1].strip()
                        try:
                            self.stats.battery_temp_c = float(temp_raw) / 10.0
                        except ValueError:
                            self.stats.battery_temp_c = None

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
            os.remove(self._wifi_flag)
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
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._wifi_flag, "w") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        except OSError:
            pass
        self.adb.run_adb("-s", serial, "shell", "svc", "wifi", "disable")
        time.sleep(3.0)
        if self.adb.check_wifi_connectivity(
            serial
        ):  # despite the name: tests the default route
            self._log("Headset Wi-Fi off — all traffic on the USB tunnel")
        else:
            self._log(
                "Tunnel didn't carry traffic with Wi-Fi off — turning Wi-Fi back on"
            )
            self.adb.run_adb("-s", serial, "shell", "svc", "wifi", "enable")
            self._clear_wifi_flag()
            self._wifi_off_failed = True

    def restore_wifi_if_flagged(self, serial: str):
        """If a previous session left the headset's Wi-Fi off, turn it back on."""
        if os.path.exists(self._wifi_flag):
            self._log("Restoring headset Wi-Fi left off by a previous session")
            self.adb.run_adb("-s", serial, "shell", "svc", "wifi", "enable")
            self._clear_wifi_flag()

    # --- Wi-Fi Sync ---

    def _sync_wifi(self, serial: str):
        """Put the headset on the PC's Wi-Fi. Bounded per PC network so a bad
        password or out-of-range AP can't turn into a retry loop."""
        self._last_wifi_check = time.time()
        pc = net_sync.get_pc_ssid(self.platform)
        if pc and self._wifi_attempts.get(pc, 0) >= self.WIFI_SYNC_MAX_ATTEMPTS:
            return
        result = net_sync.sync_quest_wifi(
            self.adb, serial, self._log, platform=self.platform
        )
        if pc:
            if result in ("failed", "manual"):
                self._wifi_attempts[pc] = self._wifi_attempts.get(pc, 0) + 1
                if self._wifi_attempts[pc] >= self.WIFI_SYNC_MAX_ATTEMPTS:
                    self._log(
                        f'Wi-Fi sync: giving up on "{pc}" until the PC\'s network changes'
                    )
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

        if self._reconnect_attempts == 0:
            # Count outages, not attempts: every retry inside one drop is still
            # the same reconnect from the user's point of view (#79).
            self.stats.reconnect_count += 1
        self._reconnect_attempts += 1
        self._set_state(TetherState.RECONNECTING)
        self._log(
            f"Reconnect attempt {self._reconnect_attempts}/{self.MAX_RECONNECT_ATTEMPTS}"
        )

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

        # Restart the relay
        self._kill_stale_process()
        if self.relay_mode == "split":
            error = self._start_split_relay(actual_serial)
            if error:
                self._log(f"Failed to restart: {error}")
                return
            self._log(f"relay restarted (PID {self._process.pid})")
        else:
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
                self._healer.reset_after_reconnect()
                self._prev_stat_time = 0
                self._log("Reconnected!")
                return
            if i == 4:
                self._log("tun0 slow — re-issuing client start")
                if not self.adb.start_gnirehtet_client(actual_serial, self.RELAY_PORT):
                    self._log_client_start_refused()
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


def temperature_zone(temp_c: Optional[float]) -> str:
    """Zone for a battery temperature in C: green below 38, yellow 38-42, red above 42, unknown for None."""
    if temp_c is None:
        return "unknown"
    if temp_c < 38.0:
        return "green"
    if temp_c <= 42.0:
        return "yellow"
    return "red"
