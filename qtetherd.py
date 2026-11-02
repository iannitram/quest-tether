"""qtetherd: the Linux tether service (stub; docs/code-map.md, "Who calls whom").

It owns the one TetherManager on Linux and answers one JSON object per line on
127.0.0.1:31417 (never another interface):
  {"cmd": "status"}     -> {"state": "connected", "temp_c": 37.5, "zone": "green", ...}
  {"cmd": "connect"}    -> {"ok": true} or {"ok": false, "error": "..."}
  {"cmd": "disconnect"} -> {"ok": true}
Clients: main.py's window on Linux and the Decky plugin's backend.
"""

from tether_manager import temperature_zone

HOST = "127.0.0.1"
PORT = 31417


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
    return {"ok": False, "error": "unknown command: " + cmd}


def serve(host: str = HOST, port: int = PORT) -> None:
    """Run the service until stopped."""
    raise NotImplementedError


def request(cmd: dict, host: str = HOST, port: int = PORT, timeout: float = 5.0) -> dict:
    """Send one request to a running service and return its answer (client side)."""
    raise NotImplementedError


if __name__ == "__main__":
    serve()
