"""qtetherd request handling against a fake tether (no headset; loopback only): python -m unittest discover -s tests"""

import json
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qtetherd  # noqa: E402
from tether_manager import TetherState, TetherStats  # noqa: E402


class FakeTether:
    def __init__(self, stats, connect_ok=True):
        self.stats = stats
        self.connect_ok = connect_ok
        self.calls = []
        self.adb = object()
        self.auto_reconnect = True
        self.wifi_mode = "off"

    def connect(self, serial):
        self.calls.append(("connect", serial))
        return self.connect_ok

    def disconnect(self, headset_present=True):
        self.calls.append(("disconnect", headset_present))


class LockProbeTether:
    """Records whether the shared tether lock is held at each manager call."""

    def __init__(self, stats):
        self._stats = stats
        self.adb = object()
        self.auto_reconnect = True
        self.wifi_mode = "off"
        self.lock_checks = []

    @property
    def stats(self):
        self.lock_checks.append(qtetherd._TETHER_LOCK.locked())
        return self._stats

    def connect(self, serial):
        self.lock_checks.append(qtetherd._TETHER_LOCK.locked())
        return True

    def disconnect(self, headset_present=True):
        self.lock_checks.append(qtetherd._TETHER_LOCK.locked())


def stats(**kw):
    base = dict(
        state=TetherState.CONNECTED,
        uptime_seconds=125.0,
        rx_rate_bps=2000.0,
        tx_rate_bps=500.0,
        reconnect_count=2,
        battery_level="81",
        battery_status="discharging",
        battery_temp_c=37.5,
        device_serial="1WMHH0000",
        device_model="Quest 3",
        vpn_ip="10.0.0.2",
        last_error="",
    )
    base.update(kw)
    return TetherStats(**base)


SERIAL = lambda: "1WMHH0000"  # noqa: E731
NONE = lambda: None  # noqa: E731


class Status(unittest.TestCase):
    def test_status_fields(self):
        t = FakeTether(stats())
        self.assertEqual(
            qtetherd.handle_request(t, SERIAL, {"cmd": "status"}),
            {
                "version": 1,
                "adb_path": None,
                "state": "connected",
                "rx_bps": 2000.0,
                "tx_bps": 500.0,
                "uptime_s": 125.0,
                "battery_level": "81",
                "reconnects": 2,
                "relay_clients": None,
                "temp_c": 37.5,
                "zone": "green",
                "device_serial": "1WMHH0000",
                "device_model": "Quest 3",
                "vpn_ip": "10.0.0.2",
                "battery_status": "discharging",
                "error": "",
            },
        )
        self.assertEqual(t.calls, [])

    def test_zone_follows_temperature(self):
        for temp, zone in ((40.0, "yellow"), (43.5, "red"), (None, "unknown")):
            r = qtetherd.handle_request(
                FakeTether(stats(battery_temp_c=temp)), SERIAL, {"cmd": "status"}
            )
            self.assertEqual((r["temp_c"], r["zone"]), (temp, zone))

    def test_state_value_and_error_text(self):
        r = qtetherd.handle_request(
            FakeTether(stats(state=TetherState.ERROR, last_error="boom")),
            SERIAL,
            {"cmd": "status"},
        )
        self.assertEqual((r["state"], r["error"]), ("error", "boom"))


class Commands(unittest.TestCase):
    def test_connect_uses_the_found_serial(self):
        t = FakeTether(stats(state=TetherState.IDLE))
        self.assertEqual(
            qtetherd.handle_request(t, SERIAL, {"cmd": "connect"}), {"ok": True}
        )
        self.assertEqual(t.calls, [("connect", "1WMHH0000")])

    def test_connect_without_headset_does_not_call_the_tether(self):
        t = FakeTether(stats(state=TetherState.IDLE))
        self.assertEqual(
            qtetherd.handle_request(t, NONE, {"cmd": "connect"}),
            {"ok": False, "error": "no headset found"},
        )
        self.assertEqual(t.calls, [])

    def test_connect_failure_reports_the_tether_error(self):
        t = FakeTether(
            stats(state=TetherState.IDLE, last_error="no relay"), connect_ok=False
        )
        self.assertEqual(
            qtetherd.handle_request(t, SERIAL, {"cmd": "connect"}),
            {"ok": False, "error": "no relay"},
        )

    def test_connect_failure_without_error_text(self):
        t = FakeTether(stats(state=TetherState.IDLE), connect_ok=False)
        self.assertEqual(
            qtetherd.handle_request(t, SERIAL, {"cmd": "connect"}),
            {"ok": False, "error": "connect failed"},
        )

    def test_disconnect(self):
        t = FakeTether(stats())
        self.assertEqual(
            qtetherd.handle_request(t, SERIAL, {"cmd": "disconnect"}), {"ok": True}
        )
        self.assertEqual(t.calls, [("disconnect", True)])

    def test_disconnect_with_no_headset_on_usb_skips_headset_steps(self):
        t = FakeTether(stats())
        self.assertEqual(
            qtetherd.handle_request(t, lambda: None, {"cmd": "disconnect"}),
            {"ok": True},
        )
        self.assertEqual(t.calls, [("disconnect", False)])

    def test_connect_while_connected_returns_already_and_starts_nothing(self):
        t = FakeTether(stats(state=TetherState.CONNECTED))
        self.assertEqual(
            qtetherd.handle_request(t, SERIAL, {"cmd": "connect"}),
            {"ok": True, "already": "connected"},
        )
        self.assertEqual(t.calls, [])

    def test_connect_while_starting_returns_already_and_starts_nothing(self):
        t = FakeTether(stats(state=TetherState.STARTING))
        self.assertEqual(
            qtetherd.handle_request(t, SERIAL, {"cmd": "connect"}),
            {"ok": True, "already": "starting"},
        )
        self.assertEqual(t.calls, [])


class Locking(unittest.TestCase):
    def test_only_connect_and_disconnect_take_the_shared_lock(self):
        for req, expected in (
            ({"cmd": "status"}, False),
            ({"cmd": "connect"}, True),
            ({"cmd": "disconnect"}, True),
        ):
            t = LockProbeTether(stats(state=TetherState.IDLE))
            qtetherd.handle_request(t, SERIAL, req)
            self.assertTrue(t.lock_checks, req)
            self.assertEqual(all(t.lock_checks), expected, req)

    def test_status_returns_while_a_slow_connect_holds_the_lock(self):
        started = threading.Event()
        release = threading.Event()

        class SlowConnect:
            def __init__(self):
                self.stats = stats(state=TetherState.IDLE)
                self.adb = object()
                self.auto_reconnect = True
                self.wifi_mode = "off"

            def connect(self, serial):
                started.set()
                release.wait(5)
                return True

        tether = SlowConnect()
        connecting = threading.Thread(
            target=qtetherd.handle_request,
            args=(tether, SERIAL, {"cmd": "connect"}),
        )
        connecting.start()
        self.assertTrue(started.wait(2), "connect never started")
        try:
            answer = {}
            reader = threading.Thread(
                target=lambda: answer.update(
                    qtetherd.handle_request(tether, SERIAL, {"cmd": "status"})
                )
            )
            reader.start()
            reader.join(2)
            self.assertFalse(reader.is_alive(), "status waited for the connect lock")
            self.assertEqual(answer["state"], "idle")
        finally:
            release.set()
            connecting.join(5)

    def test_wifi_sync_takes_the_shared_lock(self):
        seen = []

        def sync(*args, **kwargs):
            seen.append(qtetherd._TETHER_LOCK.locked())
            return "same"

        with patch("qtetherd.net_sync.sync_quest_wifi", side_effect=sync):
            answer = qtetherd.handle_request(
                FakeTether(stats()), SERIAL, {"cmd": "wifi-sync"}
            )
        self.assertEqual(answer, {"ok": True, "result": "same"})
        self.assertEqual(seen, [True])


class WifiSync(unittest.TestCase):
    def test_without_a_headset_net_sync_is_not_called(self):
        with patch("qtetherd.net_sync.sync_quest_wifi") as sync:
            self.assertEqual(
                qtetherd.handle_request(
                    FakeTether(stats()), NONE, {"cmd": "wifi-sync"}
                ),
                {"ok": False, "error": "no headset found"},
            )
            sync.assert_not_called()

    def test_same_network_is_a_success(self):
        t = FakeTether(stats())
        with patch("qtetherd.net_sync.sync_quest_wifi", return_value="same") as sync:
            self.assertEqual(
                qtetherd.handle_request(t, SERIAL, {"cmd": "wifi-sync"}),
                {"ok": True, "result": "same"},
            )
            sync.assert_called_once()
            self.assertIs(sync.call_args.args[0], t.adb)
            self.assertEqual(sync.call_args.args[1], "1WMHH0000")

    def test_failed_sync_is_reported(self):
        with patch("qtetherd.net_sync.sync_quest_wifi", return_value="manual"):
            self.assertEqual(
                qtetherd.handle_request(
                    FakeTether(stats()), SERIAL, {"cmd": "wifi-sync"}
                ),
                {"ok": False, "result": "manual", "error": "Wi-Fi sync: manual"},
            )


class WifiSecret(unittest.TestCase):
    def test_key_goes_to_adb_but_never_into_a_reply_or_log(self):
        secret = "S3cretPassphrase!"
        logs = []
        ssids = iter(["OtherNetwork"] + ["HomeNetwork"] * 10)

        class Adb:
            def __init__(self):
                self.calls = []

            def run_adb(self, *args, **kwargs):
                self.calls.append(args)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

        t = FakeTether(stats())
        t.adb = Adb()
        with (
            patch("qtetherd.net_sync.get_pc_ssid", return_value="HomeNetwork"),
            patch(
                "qtetherd.net_sync.get_pc_wifi",
                return_value=("HomeNetwork", secret, "wpa2"),
            ),
            patch(
                "qtetherd.net_sync.get_quest_ssid",
                side_effect=lambda *a: next(ssids),
            ),
            patch("qtetherd.net_sync.time.sleep"),
            patch("qtetherd.log.info", side_effect=logs.append),
        ):
            answer = qtetherd.handle_request(t, SERIAL, {"cmd": "wifi-sync"})

        self.assertEqual(answer, {"ok": True, "result": "switched"})
        self.assertTrue(any(secret in str(call) for call in t.adb.calls))
        self.assertNotIn(secret, json.dumps(answer))
        self.assertFalse(any(secret in line for line in logs))
        self.assertIn("HomeNetwork", "\n".join(logs))


class Logs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tether = FakeTether(stats())
        self.tether._data_dir = self.tmp.name
        with open(
            os.path.join(self.tmp.name, "quest-tether.log"), "w", encoding="utf-8"
        ) as f:
            f.write("one\ntwo\nthree\n")
        with open(os.path.join(self.tmp.name, "relay.log"), "w", encoding="utf-8") as f:
            f.write("relay one\nrelay two\n")

    def test_app_log_tail_and_default_count(self):
        answer = qtetherd.handle_request(self.tether, SERIAL, {"cmd": "logs", "n": 2})
        self.assertEqual(
            answer, {"ok": True, "source": "app", "lines": ["two", "three"]}
        )

    def test_relay_log_by_source(self):
        answer = qtetherd.handle_request(
            self.tether, SERIAL, {"cmd": "logs", "source": "relay.log", "n": 1}
        )
        self.assertEqual(
            answer, {"ok": True, "source": "relay", "lines": ["relay two"]}
        )

    def test_only_source_and_n_are_read(self):
        answer = qtetherd.handle_request(
            self.tether,
            SERIAL,
            {
                "cmd": "logs",
                "source": "relay",
                "lines": 1,
                "count": 1,
                "file": "quest-tether.log",
                "which": "app",
                "log": "app",
            },
        )
        self.assertEqual(
            answer,
            {
                "ok": True,
                "source": "relay",
                "lines": ["relay one", "relay two"],
            },
        )

    def test_n_is_capped_at_one_thousand(self):
        with open(
            os.path.join(self.tmp.name, "quest-tether.log"), "w", encoding="utf-8"
        ) as f:
            f.writelines(f"line {i}\n" for i in range(1005))
        answer = qtetherd.handle_request(
            self.tether, SERIAL, {"cmd": "logs", "source": "app", "n": 5000}
        )
        self.assertEqual(len(answer["lines"]), 1000)
        self.assertEqual(answer["lines"][0], "line 5")
        self.assertEqual(answer["lines"][-1], "line 1004")

    def test_missing_log_is_an_empty_tail(self):
        answer = qtetherd.handle_request(
            self.tether, SERIAL, {"cmd": "logs", "source": "service"}
        )
        self.assertEqual(answer, {"ok": True, "source": "service", "lines": []})

    def test_app_source_reads_only_its_one_file(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        tether = FakeTether(stats())
        tether._data_dir = other.name
        with open(os.path.join(other.name, "qtetherd.log"), "w", encoding="utf-8") as f:
            f.write("service only\n")
        answer = qtetherd.handle_request(tether, SERIAL, {"cmd": "logs"})
        self.assertEqual(answer, {"ok": True, "source": "app", "lines": []})

    def test_bad_count_and_unknown_source(self):
        self.assertEqual(
            qtetherd.handle_request(self.tether, SERIAL, {"cmd": "logs", "n": "many"}),
            {"ok": False, "error": "bad line count"},
        )
        self.assertEqual(
            qtetherd.handle_request(
                self.tether, SERIAL, {"cmd": "logs", "source": "boot"}
            ),
            {"ok": False, "error": "unknown log: boot"},
        )


class Settings(unittest.TestCase):
    def setUp(self):
        # settings-set saves settings.json: keep it in a temp folder, never the real config dir.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings_file = os.path.join(tmp.name, "settings.json")
        patcher = patch("qtetherd._settings_path", return_value=self.settings_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_has_the_window_settings(self):
        with patch("qtetherd.crash_report.is_enabled", return_value=True):
            answer = qtetherd.handle_request(
                FakeTether(stats()), SERIAL, {"cmd": "settings-get"}
            )
        self.assertEqual(
            answer,
            {
                "ok": True,
                "settings": {
                    "auto_reconnect": True,
                    "wifi_mode": "off",
                    "crash_reports": True,
                },
            },
        )

    def test_set_each_setting(self):
        t = FakeTether(stats())
        with (
            patch("qtetherd.crash_report.is_enabled", return_value=True),
            patch("qtetherd.crash_report.set_enabled") as set_enabled,
        ):
            answer = qtetherd.handle_request(
                t,
                SERIAL,
                {
                    "cmd": "settings-set",
                    "settings": {
                        "auto_reconnect": False,
                        "wifi_mode": "sync",
                        "crash_reports": False,
                    },
                },
            )
            set_enabled.assert_called_once_with(False)
        self.assertFalse(t.auto_reconnect)
        self.assertEqual(t.wifi_mode, "sync")
        self.assertEqual(
            answer,
            {
                "ok": True,
                "settings": {
                    "auto_reconnect": False,
                    "wifi_mode": "sync",
                    "crash_reports": True,
                },
                "persisted": True,
            },
        )

    def test_key_value_form_and_aliases(self):
        t = FakeTether(stats())
        with patch("qtetherd.crash_report.is_enabled", return_value=False):
            answer = qtetherd.handle_request(
                t, SERIAL, {"cmd": "settings-set", "key": "wifi-mode", "value": "leave"}
            )
        self.assertEqual(t.wifi_mode, "leave")
        self.assertTrue(answer["ok"])

    def test_bad_settings_are_refused_before_any_change(self):
        t = FakeTether(stats())
        self.assertEqual(
            qtetherd.handle_request(
                t, SERIAL, {"cmd": "settings-set", "settings": {"nope": 1}}
            ),
            {"ok": False, "error": "unknown setting: nope"},
        )
        self.assertEqual(
            qtetherd.handle_request(
                t, SERIAL, {"cmd": "settings-set", "settings": {"wifi_mode": "maybe"}}
            ),
            {"ok": False, "error": "bad wifi_mode: maybe"},
        )
        self.assertTrue(t.auto_reconnect)
        self.assertEqual(t.wifi_mode, "off")

    def test_boolean_settings_must_be_real_booleans(self):
        for key in ("auto_reconnect", "crash_reports"):
            for value in (1, 0, "true", None):
                t = FakeTether(stats())
                with patch("qtetherd.crash_report.set_enabled") as set_enabled:
                    answer = qtetherd.handle_request(
                        t, SERIAL, {"cmd": "settings-set", "settings": {key: value}}
                    )
                self.assertEqual(
                    answer, {"ok": False, "error": "bad " + key}, (key, value)
                )
                self.assertTrue(t.auto_reconnect)
                set_enabled.assert_not_called()

    def test_boolean_alias_must_be_a_real_boolean(self):
        t = FakeTether(stats())
        self.assertEqual(
            qtetherd.handle_request(
                t,
                SERIAL,
                {"cmd": "settings-set", "key": "auto-reconnect", "value": "on"},
            ),
            {"ok": False, "error": "bad auto_reconnect"},
        )
        self.assertTrue(t.auto_reconnect)


class OverLoopback(unittest.TestCase):
    def setUp(self):
        self.tether = FakeTether(stats())
        self.server = qtetherd.make_server(
            lambda req: qtetherd.handle_request(self.tether, SERIAL, req),
            "127.0.0.1",
            0,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def test_status_and_settings_over_an_ephemeral_port(self):
        self.assertEqual(
            qtetherd.request({"cmd": "status"}, "127.0.0.1", self.port)["state"],
            "connected",
        )
        with patch("qtetherd.crash_report.is_enabled", return_value=True):
            answer = qtetherd.request({"cmd": "settings-get"}, "127.0.0.1", self.port)
        self.assertEqual(answer["settings"]["wifi_mode"], "off")


class BadRequests(unittest.TestCase):
    def test_unknown_command(self):
        self.assertEqual(
            qtetherd.handle_request(FakeTether(stats()), SERIAL, {"cmd": "reboot"}),
            {"ok": False, "error": "unknown command: reboot"},
        )

    def test_not_a_command_object(self):
        for bad in ({}, {"cmd": 5}, [], "status", None):
            self.assertEqual(
                qtetherd.handle_request(FakeTether(stats()), SERIAL, bad),
                {"ok": False, "error": "bad request"},
            )


if __name__ == "__main__":
    unittest.main()
