"""USB monitor events from a fake adb device list (no adb, no headset): python3 -m unittest discover -s tests"""

import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import adb_manager  # noqa: E402
import qtetherd  # noqa: E402
from tether_manager import TetherManager, TetherState  # noqa: E402

SERIAL = "1WMHH111111111"


def quest(status, serial=SERIAL):
    return {"serial": serial, "status": status, "model": "Quest_3"}


class MonitorStep(unittest.TestCase):
    """Drive _monitor_step with a scripted find_quest, one poll per entry."""

    def run_polls(self, *polls):
        platform = Mock()
        platform.no_window_flags.return_value = 0
        adb = adb_manager.ADBManager(adb_path="adb", platform=platform)
        adb.find_quest = Mock(side_effect=[dict(p) if p else None for p in polls])
        events = []
        adb._on_device_connected = lambda info: events.append(("connected", info))
        adb._on_device_disconnected = lambda: events.append(("disconnected", None))
        for _ in polls:
            adb._monitor_step()
        return events

    def kinds(self, events):
        return [(kind, info and info["status"]) for kind, info in events]

    def test_accepting_the_prompt_fires_connected_once(self):
        events = self.run_polls(
            quest("unauthorized"), quest("unauthorized"), quest("device"), quest("device")
        )
        self.assertEqual(
            self.kinds(events),
            [("connected", "unauthorized"), ("connected", "device")],
        )
        self.assertTrue(events[0][1]["_unauthorized"])
        self.assertNotIn("_unauthorized", events[1][1])
        self.assertEqual(events[1][1]["serial"], SERIAL)

    def test_authorized_headset_fires_once(self):
        events = self.run_polls(quest("device"), quest("device"), quest("device"))
        self.assertEqual(self.kinds(events), [("connected", "device")])

    def test_known_serial_going_unauthorized_fires_nothing(self):
        events = self.run_polls(quest("device"), quest("unauthorized"))
        self.assertEqual(self.kinds(events), [("connected", "device")])

    def test_unplug_then_replug_fires_again(self):
        events = self.run_polls(quest("device"), None, quest("device"))
        self.assertEqual(
            self.kinds(events),
            [("connected", "device"), ("disconnected", None), ("connected", "device")],
        )

    def test_unplug_while_unauthorized_fires_disconnected(self):
        events = self.run_polls(quest("unauthorized"), None, quest("unauthorized"))
        self.assertEqual(
            self.kinds(events),
            [
                ("connected", "unauthorized"),
                ("disconnected", None),
                ("connected", "unauthorized"),
            ],
        )

    def test_new_serial_fires(self):
        events = self.run_polls(quest("device"), quest("device", serial="OTHER"))
        self.assertEqual([info["serial"] for _, info in events], [SERIAL, "OTHER"])

    def test_loop_survives_a_failing_poll(self):
        platform = Mock()
        platform.no_window_flags.return_value = 0
        adb = adb_manager.ADBManager(adb_path="adb", platform=platform)

        def boom():
            adb._monitoring = False
            raise RuntimeError("adb gone")

        adb._monitor_step = Mock(side_effect=boom)
        adb._monitoring = True
        adb._monitor_loop(0)
        adb._monitor_step.assert_called_once_with()


class ServicePairing(unittest.TestCase):
    """The qtetherd callbacks fed by the real monitor step (#109)."""

    def test_service_connects_after_the_prompt_is_accepted(self):
        tether = Mock()
        tether.auto_reconnect = True
        tether.stats.state = TetherState.IDLE
        on_connected, on_disconnected = qtetherd._monitor_callbacks(tether)
        platform = Mock()
        platform.no_window_flags.return_value = 0
        adb = adb_manager.ADBManager(adb_path="adb", platform=platform)
        adb._on_device_connected = on_connected
        adb._on_device_disconnected = on_disconnected
        adb.find_quest = Mock(
            side_effect=[quest("unauthorized"), quest("unauthorized"), quest("device"), quest("device")]
        )

        adb._monitor_step()
        adb._monitor_step()
        tether.connect.assert_not_called()
        tether.note_usb_event.assert_called_once_with(
            "Headset unauthorized — accept the USB debugging prompt"
        )

        adb._monitor_step()
        adb._monitor_step()
        tether.connect.assert_called_once_with(SERIAL)

    def test_unauthorized_event_logs_and_starts_nothing(self):
        adb = Mock()
        tether = TetherManager(adb, gnirehtet_path="fake.jar")
        tether._set_state(TetherState.IDLE)
        tether._spawn_relay = Mock()
        logs = []
        tether.set_callbacks(on_log=logs.append)
        on_connected, _ = qtetherd._monitor_callbacks(tether)

        on_connected(dict(quest("unauthorized"), _unauthorized=True))

        tether._spawn_relay.assert_not_called()
        self.assertEqual(adb.mock_calls, [])
        self.assertTrue(any("accept the USB debugging prompt" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
