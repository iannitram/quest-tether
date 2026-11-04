"""Caller-level Wi-Fi sync tests with a fake platform and a fake ADB.

The platform's key is only read when the headset must be switched, and an
unreadable key falls back to the manual-join hint instead of no-pc-wifi.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import net_sync


class FakePlatform:
    def __init__(self, ssid, wifi=None):
        self.ssid = ssid
        self.wifi = wifi
        self.ssid_calls = 0
        self.wifi_calls = 0

    def pc_ssid(self):
        self.ssid_calls += 1
        return self.ssid

    def pc_wifi(self):
        self.wifi_calls += 1
        return self.wifi


class FakeADB:
    def __init__(self, quest_ssids):
        self.quest_ssids = list(quest_ssids)
        self.commands = []

    def run_adb(self, *args, **kwargs):
        self.commands.append(args)
        if args[-2:] == ("wifi", "status"):
            ssid = self.quest_ssids.pop(0) if self.quest_ssids else None
            if ssid is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0, stdout=f'Wifi is connected to "{ssid}"\n', stderr=""
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class SyncQuestWifi(unittest.TestCase):
    def test_same_does_not_read_the_key(self):
        platform = FakePlatform("ExampleNet", ("ExampleNet", "secret", "wpa2"))
        adb = FakeADB(["ExampleNet"])
        logs = []

        result = net_sync.sync_quest_wifi(
            adb, "S1", logs.append, settle_seconds=0, platform=platform
        )

        self.assertEqual(result, "same")
        self.assertEqual(platform.wifi_calls, 0)
        self.assertFalse(any("connect-network" in str(args) for args in adb.commands))

    def test_unreadable_key_returns_manual_with_join_hint(self):
        platform = FakePlatform("ExampleNet", None)
        adb = FakeADB(["OtherNet"])
        logs = []

        result = net_sync.sync_quest_wifi(
            adb, "S1", logs.append, settle_seconds=0, platform=platform
        )

        self.assertEqual(result, "manual")
        self.assertTrue(
            any('Join "ExampleNet" on the headset manually' in m for m in logs)
        )
        self.assertFalse(any("connect-network" in str(args) for args in adb.commands))

    def test_switched_passes_security_keyword_to_connect_network(self):
        platform = FakePlatform("ExampleNet", ("ExampleNet", "secret", "wpa3"))
        adb = FakeADB(["OtherNet", "ExampleNet"])
        logs = []

        with (
            patch("net_sync.time.sleep"),
            patch("net_sync.time.time", return_value=0.0),
        ):
            result = net_sync.sync_quest_wifi(
                adb, "S1", logs.append, settle_seconds=25, platform=platform
            )

        self.assertEqual(result, "switched")
        connect = next(
            args[-1] for args in adb.commands if "connect-network" in str(args)
        )
        self.assertIn(" wpa3 ", connect)
        self.assertIn("'secret'", connect)
        self.assertTrue(any('headset now on "ExampleNet"' in m for m in logs))

    def test_connect_uses_the_ssid_from_the_key_lookup(self):
        platform = FakePlatform("OldNet", ("NewNet", "secret", "wpa2"))
        adb = FakeADB(["OtherNet", "NewNet"])
        logs = []

        with (
            patch("net_sync.time.sleep"),
            patch("net_sync.time.time", return_value=0.0),
        ):
            result = net_sync.sync_quest_wifi(
                adb, "S1", logs.append, settle_seconds=25, platform=platform
            )

        self.assertEqual(result, "switched")
        connect = next(
            args[-1] for args in adb.commands if "connect-network" in str(args)
        )
        self.assertIn("'NewNet'", connect)
        self.assertNotIn("'OldNet'", connect)


if __name__ == "__main__":
    unittest.main()
