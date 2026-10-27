"""qtetherd: the Linux tether service (stub; docs/code-map.md, "Who calls whom").

It owns the one TetherManager on Linux and answers one JSON object per line on
127.0.0.1:31417 (never another interface):
  {"cmd": "status"}     -> {"state": "connected", "temp_c": 37.5, "zone": "green", ...}
  {"cmd": "connect"}    -> {"ok": true} or {"ok": false, "error": "..."}
  {"cmd": "disconnect"} -> {"ok": true}
Clients: main.py's window on Linux and the Decky plugin's backend.
"""

HOST = "127.0.0.1"
PORT = 31417


def serve(host: str = HOST, port: int = PORT) -> None:
    """Run the service until stopped."""
    raise NotImplementedError


def request(cmd: dict, host: str = HOST, port: int = PORT, timeout: float = 5.0) -> dict:
    """Send one request to a running service and return its answer (client side)."""
    raise NotImplementedError


if __name__ == "__main__":
    serve()
