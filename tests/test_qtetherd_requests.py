"""qtetherd request handling against a fake tether (no headset, no sockets): python -m unittest discover -s tests"""
import sys
import unittest

sys.path.insert(0, ".")
import qtetherd  # noqa: E402
from tether_manager import TetherState, TetherStats  # noqa: E402


class FakeTether:
    def __init__(self, stats, connect_ok=True):
        self.stats = stats
        self.connect_ok = connect_ok
        self.calls = []

    def connect(self, serial):
        self.calls.append(("connect", serial))
        return self.connect_ok

    def disconnect(self):
        self.calls.append(("disconnect",))


def stats(**kw):
    base = dict(state=TetherState.CONNECTED, uptime_seconds=125.0, rx_rate_bps=2000.0, tx_rate_bps=500.0,
                reconnect_count=2, battery_level="81", battery_temp_c=37.5, last_error="")
    base.update(kw)
    return TetherStats(**base)


SERIAL = lambda: "1WMHH0000"  # noqa: E731
NONE = lambda: None  # noqa: E731


class Status(unittest.TestCase):
    def test_status_fields(self):
        t = FakeTether(stats())
        self.assertEqual(qtetherd.handle_request(t, SERIAL, {"cmd": "status"}), {
            "state": "connected", "rx_bps": 2000.0, "tx_bps": 500.0, "uptime_s": 125.0,
            "battery_level": "81", "reconnects": 2, "temp_c": 37.5, "zone": "green", "error": ""})
        self.assertEqual(t.calls, [])

    def test_zone_follows_temperature(self):
        for temp, zone in ((40.0, "yellow"), (43.5, "red"), (None, "unknown")):
            r = qtetherd.handle_request(FakeTether(stats(battery_temp_c=temp)), SERIAL, {"cmd": "status"})
            self.assertEqual((r["temp_c"], r["zone"]), (temp, zone))

    def test_state_value_and_error_text(self):
        r = qtetherd.handle_request(FakeTether(stats(state=TetherState.ERROR, last_error="boom")),
                                    SERIAL, {"cmd": "status"})
        self.assertEqual((r["state"], r["error"]), ("error", "boom"))


class Commands(unittest.TestCase):
    def test_connect_uses_the_found_serial(self):
        t = FakeTether(stats())
        self.assertEqual(qtetherd.handle_request(t, SERIAL, {"cmd": "connect"}), {"ok": True})
        self.assertEqual(t.calls, [("connect", "1WMHH0000")])

    def test_connect_without_headset_does_not_call_the_tether(self):
        t = FakeTether(stats())
        self.assertEqual(qtetherd.handle_request(t, NONE, {"cmd": "connect"}),
                         {"ok": False, "error": "no headset found"})
        self.assertEqual(t.calls, [])

    def test_connect_failure_reports_the_tether_error(self):
        t = FakeTether(stats(last_error="no relay"), connect_ok=False)
        self.assertEqual(qtetherd.handle_request(t, SERIAL, {"cmd": "connect"}), {"ok": False, "error": "no relay"})

    def test_connect_failure_without_error_text(self):
        t = FakeTether(stats(), connect_ok=False)
        self.assertEqual(qtetherd.handle_request(t, SERIAL, {"cmd": "connect"}),
                         {"ok": False, "error": "connect failed"})

    def test_disconnect(self):
        t = FakeTether(stats())
        self.assertEqual(qtetherd.handle_request(t, SERIAL, {"cmd": "disconnect"}), {"ok": True})
        self.assertEqual(t.calls, [("disconnect",)])


class BadRequests(unittest.TestCase):
    def test_unknown_command(self):
        self.assertEqual(qtetherd.handle_request(FakeTether(stats()), SERIAL, {"cmd": "reboot"}),
                         {"ok": False, "error": "unknown command: reboot"})

    def test_not_a_command_object(self):
        for bad in ({}, {"cmd": 5}, [], "status", None):
            self.assertEqual(qtetherd.handle_request(FakeTether(stats()), SERIAL, bad),
                             {"ok": False, "error": "bad request"})


if __name__ == "__main__":
    unittest.main()
