"""qtetherd: the Linux tether service (docs/code-map.md, "Who calls whom").

It owns the one TetherManager on Linux and answers one JSON object per line on
127.0.0.1:31417 (never another interface):
  {"cmd": "status"}     -> {"state": "connected", "temp_c": 37.5, "zone": "green", ...}
  {"cmd": "connect"}    -> {"ok": true} or {"ok": false, "error": "..."}
  {"cmd": "disconnect"} -> {"ok": true}
  {"cmd": "wifi-sync"}  -> {"ok": true, "result": "same"|"switched"|...}
  {"cmd": "logs", "source": "app"|"relay", "n": 50}
                        -> {"ok": true, "source": "app", "lines": ["...", ...]}
  {"cmd": "settings-get"} -> {"ok": true, "settings": {...}}
  {"cmd": "settings-set", "settings": {...}} -> {"ok": true, "settings": {...}, "persisted": bool}
                        (plus "warning" when the change applied but could not be saved)
Settings persist in settings.json in the config folder (#117). serve() loads it
at start; QT_WIFI_MODE still wins for wifi_mode when it is a valid value.
Clients: main.py's window on Linux, the Decky plugin's backend and cli.py.
"""

import collections
import json
import logging
import os
import socket
import signal
import socketserver
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

import crash_report
import net_sync
import qt_platform
from adb_manager import ADBManager
from qt_platform import settings_file
from tether_manager import ACTIVE_CONNECT_STATES, TetherManager, temperature_zone

HOST = "127.0.0.1"
PORT = 31417
HANDLER_TIMEOUT = 10
MAX_LOG_LINES = 1000
# Longest a SIGTERM stop may run the normal disconnect (#110). The unit's
# TimeoutStopSec is a little longer, so systemd never kills us mid-stop.
STOP_CAP_SECONDS = 20.0
# How long the stop waits for the shared lock before disconnecting without it
# (#110). A running connect holds the lock for up to about 30 s, and the stop
# must not sit behind it.
STOP_LOCK_WAIT_SECONDS = 2.0

log = logging.getLogger(__name__)

# One lock around every TetherManager call: the monitor callbacks and request
# threads share the same manager.
_TETHER_LOCK = threading.Lock()
# Serializes settings-set (apply and save) so two requests cannot write the file
# out of order. Settings never take _TETHER_LOCK.
_SETTINGS_LOCK = threading.Lock()

# The window's log and the service's own log share this folder with relay.log.
# Each source maps to exactly one file.
LOG_FILES = {
    "app": "quest-tether.log",
    "window": "quest-tether.log",
    "service": "qtetherd.log",
    "relay": "relay.log",
}


def _data_dir(tether) -> str:
    """The state folder the tether uses, else the app's platform folder."""
    folder = getattr(tether, "_data_dir", None)
    return folder if isinstance(folder, str) and folder else crash_report.data_dir()


def _tail(path: str, n: int) -> List[str]:
    """The last n lines of a text file, without their newlines ([] if unreadable)."""
    if n <= 0:
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            return [line.rstrip("\r\n") for line in collections.deque(stream, maxlen=n)]
    except OSError:
        return []


def _logs(tether, req) -> dict:
    raw_n = req.get("n", 50)
    try:
        n = int(raw_n)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad line count"}
    if n < 0:
        return {"ok": False, "error": "bad line count"}
    n = min(n, MAX_LOG_LINES)

    raw_source = req.get("source", "app")
    source = str(raw_source).lower()
    if source.endswith(".log"):
        source = source[:-4]
    aliases = {"quest-tether": "window", "qtetherd": "service", "app-log": "app"}
    source = aliases.get(source, source)
    if source not in LOG_FILES:
        return {"ok": False, "error": "unknown log: " + str(raw_source)}

    path = os.path.join(_data_dir(tether), LOG_FILES[source])
    lines = _tail(path, n) if os.path.isfile(path) else []
    return {"ok": True, "source": source, "lines": lines}


def _settings(tether) -> Dict[str, Any]:
    return {
        "auto_reconnect": bool(getattr(tether, "auto_reconnect", True)),
        "wifi_mode": getattr(tether, "wifi_mode", "off"),
        "crash_reports": crash_report.is_enabled(),
    }


def _settings_path() -> Optional[str]:
    """Where settings.json lives: the platform config folder (None if it cannot be made)."""
    try:
        return settings_file.settings_path(qt_platform.current().config_dir())
    except (OSError, RuntimeError):
        log.warning("config folder unavailable; service settings will not persist")
        return None


def _persist(changed: Dict[str, Any], path: Optional[str] = None) -> bool:
    """Save the changed keys merged over what settings.json already holds; True if written.

    Only the keys the request changed are written, so a value an env var forces
    in memory is never persisted. An unreadable file is replaced by just the
    changed keys. The caller holds _SETTINGS_LOCK.
    """
    target = path if path is not None else _settings_path()
    if not target:
        return False
    merged = settings_file.load_settings(target)
    merged.update({k: v for k, v in changed.items() if k in settings_file.PERSISTED_KEYS})
    return settings_file.save_settings(target, merged)


def apply_saved_settings(tether, path: Optional[str] = None, env=None) -> dict:
    """Load settings.json into the tether at service start; return what was applied (#117).

    Order, highest first: a valid QT_WIFI_MODE env var wins for wifi_mode; then the
    file; then the TetherManager defaults (auto_reconnect on, wifi_mode off). A
    missing or bad file leaves the defaults in place.
    """
    env = os.environ if env is None else env
    target = path if path is not None else _settings_path()
    saved = settings_file.load_settings(target) if target else {}
    applied: Dict[str, Any] = {}
    if "auto_reconnect" in saved:
        tether.auto_reconnect = saved["auto_reconnect"]
        applied["auto_reconnect"] = saved["auto_reconnect"]
    if "wifi_mode" in saved:
        if env.get("QT_WIFI_MODE") in settings_file.WIFI_MODES:
            log.info("wifi_mode: QT_WIFI_MODE is set, so the saved value is not used")
        else:
            tether.wifi_mode = saved["wifi_mode"]
            applied["wifi_mode"] = saved["wifi_mode"]
    if getattr(tether, "wifi_mode", "off") not in settings_file.WIFI_MODES:
        tether.wifi_mode = "off"
    if applied:
        log.info("settings loaded from %s: %s", target, applied)
    return applied


def _settings_set(tether, req) -> dict:
    updates = req.get("settings")
    if not isinstance(updates, dict):
        if "key" in req:
            updates = {req["key"]: req.get("value")}
        else:
            updates = {k: v for k, v in req.items() if k != "cmd"}
    if not isinstance(updates, dict) or not updates:
        return {"ok": False, "error": "no settings given"}

    aliases = {
        "auto-reconnect": "auto_reconnect",
        "wifi-mode": "wifi_mode",
        "crash-reports": "crash_reports",
    }
    clean: Dict[str, Any] = {}
    for key, value in updates.items():
        key = aliases.get(key, key)
        if key not in ("auto_reconnect", "wifi_mode", "crash_reports"):
            return {"ok": False, "error": "unknown setting: " + str(key)}
        if not settings_file.valid_value(key, value):
            if key == "wifi_mode":
                return {"ok": False, "error": "bad wifi_mode: " + str(value)}
            return {"ok": False, "error": "bad " + key}
        clean[key] = value

    with _SETTINGS_LOCK:
        for key, value in clean.items():
            if key == "auto_reconnect":
                tether.auto_reconnect = value
            elif key == "wifi_mode":
                tether.wifi_mode = value
            elif key == "crash_reports":
                crash_report.set_enabled(value)
        # crash_report.set_enabled saves its own file, so a crash-only change
        # has nothing for settings.json to write.
        if not any(key in settings_file.PERSISTED_KEYS for key in clean):
            return {"ok": True, "settings": _settings(tether), "persisted": True}
        persisted = _persist(clean)
        answer = {"ok": True, "settings": _settings(tether), "persisted": persisted}
    if not persisted:
        answer["warning"] = "applied, but not saved: the setting resets on restart"
    return answer


def _wifi_sync(tether, find_serial) -> dict:
    serial = find_serial()
    if serial is None:
        return {"ok": False, "error": "no headset found"}
    adb = getattr(tether, "adb", None)
    if adb is None:
        return {"ok": False, "error": "no adb"}
    result = net_sync.sync_quest_wifi(adb, serial, log.info)
    answer = {"ok": result in ("same", "switched"), "result": result}
    if not answer["ok"]:
        answer["error"] = "Wi-Fi sync: " + result
    return answer


def handle_request(tether, find_serial, req, lock=None) -> dict:
    """Handle one request using the supplied tether and headset lookup.

    Only commands that drive the tether take the shared lock. Status, logs and
    settings stay lock-free so a long connect cannot freeze the window's polls.
    """
    if not isinstance(req, dict) or not isinstance(req.get("cmd"), str):
        return {"ok": False, "error": "bad request"}

    lock = lock or _TETHER_LOCK
    cmd = req["cmd"]
    if cmd == "status":
        s = tether.stats
        return {
            "state": s.state.value,
            "rx_bps": s.rx_rate_bps,
            "tx_bps": s.tx_rate_bps,
            "uptime_s": s.uptime_seconds,
            "battery_level": s.battery_level,
            "reconnects": s.reconnect_count,
            "relay_clients": s.relay_clients,
            "temp_c": s.battery_temp_c,
            "zone": temperature_zone(s.battery_temp_c),
            "device_serial": s.device_serial,
            "device_model": s.device_model,
            "vpn_ip": s.vpn_ip,
            "battery_status": s.battery_status,
            "error": s.last_error,
        }
    if cmd == "connect":
        with lock:
            state = tether.stats.state
            if state in ACTIVE_CONNECT_STATES:
                return {"ok": True, "already": state.value}
            serial = find_serial()
            if serial is None:
                return {"ok": False, "error": "no headset found"}
            if tether.connect(serial):
                return {"ok": True}
            return {"ok": False, "error": tether.stats.last_error or "connect failed"}
    if cmd == "disconnect":
        with lock:
            # No headset on USB: skip the headset-side steps, which would only
            # wait on a missing device (#86).
            tether.disconnect(headset_present=find_serial() is not None)
        return {"ok": True}
    if cmd == "wifi-sync":
        with lock:
            return _wifi_sync(tether, find_serial)
    if cmd == "logs":
        return _logs(tether, req)
    if cmd == "settings-get":
        return {"ok": True, "settings": _settings(tether)}
    if cmd == "settings-set":
        return _settings_set(tether, req)
    return {"ok": False, "error": "unknown command: " + cmd}


def _monitor_callbacks(tether, stopped: Optional[threading.Event] = None):
    """Connect/disconnect callbacks for USB monitoring when auto-reconnect is on.

    The health loop is the one recovery path (#86). An unplug never runs the
    headset-side disconnect, and a replug while the tether is active or
    reconnecting never starts a second relay. So a drop counts once, in
    _attempt_reconnect, and the health loop picks the headset up again.
    """

    def on_connected(device_info):
        if not isinstance(device_info, dict):
            return
        with _TETHER_LOCK:
            if stopped is not None and stopped.is_set():
                return  # the service is stopping: no connect after the disconnect (#125)
            serial = device_info.get("serial")
            if not tether.auto_reconnect or not serial:
                return
            if device_info.get("status", "device") != "device":
                # The monitor fires again once the prompt is accepted (#109).
                if device_info.get("_unauthorized"):
                    tether.note_usb_event(
                        "Headset unauthorized — accept the USB debugging prompt"
                    )
                return
            state = tether.stats.state
            if state in ACTIVE_CONNECT_STATES:
                tether.note_usb_event(
                    f"Headset back on USB while {state.value} — no new relay"
                )
                tether.note_replug(serial)
                return
            tether.connect(serial)

    def on_disconnected():
        with _TETHER_LOCK:
            if stopped is not None and stopped.is_set():
                return
            if not tether.auto_reconnect:
                return
            state = tether.stats.state
            if state in ACTIVE_CONNECT_STATES:
                tether.note_usb_event(
                    f"USB lost while {state.value} — health monitor will reconnect"
                )
                return
            if tether.is_relay_running():
                # Nothing to recover: stop the leftover relay, headset untouched.
                tether.disconnect(headset_present=False)

    return on_connected, on_disconnected


def make_server(
    handler: Callable[[Any], dict], host: str = HOST, port: int = PORT
) -> socketserver.ThreadingTCPServer:
    """Bind a loopback JSON-lines server; the caller runs serve_forever()."""
    if host != HOST:
        raise ValueError("qtetherd binds to 127.0.0.1 only")

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.connection.settimeout(HANDLER_TIMEOUT)
            line = self.rfile.readline(65536)
            try:
                parsed = json.loads(line)
            except ValueError:
                answer = {"ok": False, "error": "bad request"}
            else:
                if not isinstance(parsed, dict):
                    answer = {"ok": False, "error": "bad request"}
                else:
                    try:
                        answer = handler(parsed)
                    except Exception:
                        answer = {"ok": False, "error": "internal error"}
            self.wfile.write((json.dumps(answer) + "\n").encode("utf-8"))

    class Server(socketserver.ThreadingTCPServer):
        # True so a restart can bind while the last requests sit in TIME_WAIT
        # (#125); Linux still refuses a second live listener.
        allow_reuse_address = True
        daemon_threads = True

    return Server((host, port), Handler)


def setup_logging() -> str:
    """Send logs to the console and qtetherd.log in the data folder; return the log path.

    Called only when the service runs as a program, never from serve(), so tests
    that call serve() leave the real log and the root logger alone (#85).
    """
    log_path = os.path.join(crash_report.data_dir(), "qtetherd.log")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(), handler],
    )
    return log_path


def stop_tether(
    tether, find_serial: Callable[[], Any], lock=None, cap: float = STOP_CAP_SECONDS
) -> bool:
    """Run the normal disconnect (stop intent, Wi-Fi restore) within cap seconds (#110).

    The disconnect runs on a daemon thread, so a stuck adb call cannot hold the
    service past the cap. It waits at most STOP_LOCK_WAIT_SECONDS for the same
    lock the request handlers take. If a connect still holds it, the disconnect
    runs without the lock: the process is exiting, and disconnect() clears the
    running flag so the connect stops at its next check. The lock is only
    released when the stop took it. Returns True when the disconnect finished in
    time.
    """
    lock = lock or _TETHER_LOCK
    done = threading.Event()

    def work() -> None:
        got_lock = False
        try:
            got_lock = lock.acquire(timeout=STOP_LOCK_WAIT_SECONDS)
            if not got_lock:
                log.warning("stop: another request holds the lock; disconnecting anyway")
            tether.disconnect(headset_present=find_serial() is not None)
        except Exception:
            log.exception("disconnect on stop failed")
        finally:
            if got_lock:
                lock.release()
            done.set()

    threading.Thread(target=work, name="qtetherd-stop", daemon=True).start()
    if done.wait(cap):
        return True
    log.warning("stop: disconnect did not finish within %g s; exiting anyway", cap)
    return False


def _install_stop_handler(
    tether,
    find_serial: Callable[[], Any],
    server,
    cap: float = STOP_CAP_SECONDS,
    adb=None,
    stopped: Optional[threading.Event] = None,
) -> None:
    """Make SIGTERM run stop_tether, then end serve_forever() (#110).

    A second SIGTERM during the stop does nothing, so one disconnect runs.
    """
    stopping = threading.Event()

    def on_sigterm(signum, frame) -> None:
        if stopping.is_set():
            return
        stopping.set()
        log.info("SIGTERM: stopping the tether before exit")
        # Stop the USB monitor first (#125): the flag makes a plug event that
        # is already running skip its connect, and stop_monitoring() ends the
        # loop, so nothing connects after the disconnect.
        if stopped is not None:
            stopped.set()
        if adb is not None:
            try:
                adb.stop_monitoring()
            except Exception:
                log.exception("stopping the USB monitor failed")
        stop_tether(tether, find_serial, cap=cap)
        # shutdown() waits for serve_forever(), which is the thread running
        # this handler, so it must run on another thread.
        threading.Thread(target=server.shutdown, name="qtetherd-shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, on_sigterm)


def serve(host: str = HOST, port: int = PORT, stop_on_sigterm: bool = False) -> None:
    """Run the service until stopped.

    The saved settings load after the bind and before the USB monitor starts
    (#117). The monitor always starts; the callbacks check auto_reconnect and
    do nothing while it is off, so turning it on later works. Order:
    QT_WIFI_MODE (valid) for wifi_mode, then settings.json, then the defaults.

    stop_on_sigterm installs the SIGTERM stop (#110). Only the program's main()
    asks for it, so tests that call serve() keep the process's signals.
    """
    adb = ADBManager()
    tether = TetherManager(adb)
    # Bind before touching the headset: a second copy must exit without
    # starting its own monitor and auto-connecting to the device.
    try:
        server = make_server(
            lambda req: handle_request(tether, adb.find_serial, req), host, port
        )
    except OSError:
        print(f"qtetherd: port {port} is already in use", file=sys.stderr)
        raise SystemExit(1)
    apply_saved_settings(tether)
    stopped = threading.Event()
    on_connected, on_disconnected = _monitor_callbacks(tether, stopped)
    adb.start_monitoring(on_connected=on_connected, on_disconnected=on_disconnected)
    if stop_on_sigterm:
        _install_stop_handler(tether, adb.find_serial, server, adb=adb, stopped=stopped)
    server.serve_forever()


def request(
    cmd: dict, host: str = HOST, port: int = PORT, timeout: float = 5.0
) -> dict:
    """Send one request to a running service and return its answer (client side)."""
    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        line = conn.makefile("rb").readline()
    if not line:
        raise ConnectionError("no answer")
    return json.loads(line)


def main() -> None:
    """The program: logs and crash hook, then serve with the SIGTERM stop (#110)."""
    crash_report.install(log_path=setup_logging())
    serve(stop_on_sigterm=True)


if __name__ == "__main__":
    main()
