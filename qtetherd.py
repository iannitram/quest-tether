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
  {"cmd": "settings-set", "settings": {...}} -> {"ok": true, "settings": {...}}
Clients: main.py's window on Linux and the Decky plugin's backend.
"""

import collections
import json
import logging
import os
import socket
import socketserver
import sys
from typing import Any, Callable, Dict, List

import crash_report
import net_sync
from adb_manager import ADBManager
from tether_manager import TetherManager, temperature_zone

HOST = "127.0.0.1"
PORT = 31417

log = logging.getLogger(__name__)

# The window's log and the service's own log share this folder with relay.log.
LOG_FILES = {
    "app": ("quest-tether.log", "qtetherd.log", "app.log"),
    "window": ("quest-tether.log",),
    "service": ("qtetherd.log",),
    "relay": ("relay.log",),
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
    raw_n = req.get("n", req.get("lines", req.get("count", 50)))
    try:
        n = int(raw_n)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad line count"}
    if n < 0:
        return {"ok": False, "error": "bad line count"}

    raw_source = (
        req.get("source")
        or req.get("file")
        or req.get("which")
        or req.get("log")
        or "app"
    )
    source = str(raw_source).lower()
    if source.endswith(".log"):
        source = source[:-4]
    aliases = {"quest-tether": "window", "qtetherd": "service", "app-log": "app"}
    source = aliases.get(source, source)
    if source not in LOG_FILES:
        return {"ok": False, "error": "unknown log: " + str(raw_source)}

    folder = _data_dir(tether)
    for name in LOG_FILES[source]:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return {"ok": True, "source": source, "lines": _tail(path, n)}
    return {"ok": True, "source": source, "lines": []}


def _settings(tether) -> Dict[str, Any]:
    return {
        "auto_reconnect": bool(getattr(tether, "auto_reconnect", True)),
        "wifi_mode": getattr(tether, "wifi_mode", "off"),
        "crash_reports": crash_report.is_enabled(),
    }


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
        if key == "auto_reconnect":
            clean[key] = bool(value)
        elif key == "wifi_mode":
            if value not in ("off", "sync", "leave"):
                return {"ok": False, "error": "bad wifi_mode: " + str(value)}
            clean[key] = value
        elif key == "crash_reports":
            clean[key] = bool(value)
        else:
            return {"ok": False, "error": "unknown setting: " + str(key)}

    for key, value in clean.items():
        if key == "auto_reconnect":
            tether.auto_reconnect = value
        elif key == "wifi_mode":
            tether.wifi_mode = value
        elif key == "crash_reports":
            crash_report.set_enabled(value)
    return {"ok": True, "settings": _settings(tether)}


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


def handle_request(tether, find_serial, req) -> dict:
    """Handle one request using the supplied tether and headset lookup."""
    if not isinstance(req, dict) or not isinstance(req.get("cmd"), str):
        return {"ok": False, "error": "bad request"}

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
            "temp_c": s.battery_temp_c,
            "zone": temperature_zone(s.battery_temp_c),
            "error": s.last_error,
        }
    if cmd == "connect":
        serial = find_serial()
        if serial is None:
            return {"ok": False, "error": "no headset found"}
        if tether.connect(serial):
            return {"ok": True}
        return {"ok": False, "error": tether.stats.last_error or "connect failed"}
    if cmd == "disconnect":
        tether.disconnect()
        return {"ok": True}
    if cmd == "wifi-sync":
        return _wifi_sync(tether, find_serial)
    if cmd == "logs":
        return _logs(tether, req)
    if cmd == "settings-get":
        return {"ok": True, "settings": _settings(tether)}
    if cmd == "settings-set":
        return _settings_set(tether, req)
    return {"ok": False, "error": "unknown command: " + cmd}


def make_server(handler: Callable[[Any], dict], host: str = HOST, port: int = PORT) -> socketserver.ThreadingTCPServer:
    """Bind a loopback JSON-lines server; the caller runs serve_forever()."""
    if host != HOST:
        raise ValueError("qtetherd binds to 127.0.0.1 only")

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            line = self.rfile.readline(65536)
            try:
                parsed = json.loads(line)
            except ValueError:
                answer = {"ok": False, "error": "bad request"}
            else:
                try:
                    answer = handler(parsed)
                except Exception:
                    answer = {"ok": False, "error": "internal error"}
            self.wfile.write((json.dumps(answer) + "\n").encode("utf-8"))

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = False
        daemon_threads = True

    return Server((host, port), Handler)


def serve(host: str = HOST, port: int = PORT) -> None:
    """Run the service until stopped."""
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
    crash_report.install(log_path=log_path)

    adb = ADBManager()
    tether = TetherManager(adb)
    try:
        server = make_server(
            lambda req: handle_request(tether, adb.find_serial, req), host, port
        )
    except OSError:
        print(f"qtetherd: port {port} is already in use", file=sys.stderr)
        raise SystemExit(1)
    server.serve_forever()


def request(cmd: dict, host: str = HOST, port: int = PORT, timeout: float = 5.0) -> dict:
    """Send one request to a running service and return its answer (client side)."""
    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
        line = conn.makefile("rb").readline()
    if not line:
        raise ConnectionError("no answer")
    return json.loads(line)


if __name__ == "__main__":
    serve()
