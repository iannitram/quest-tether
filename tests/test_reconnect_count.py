"""One USB drop counts as one reconnect, however many attempts it takes (#79)."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qtetherd  # noqa: E402
from heal import Heal  # noqa: E402
from tether_manager import TetherManager, TetherState  # noqa: E402


class FakeAdb:
    """The headset leaves USB once and comes back on the next attempt."""

    def __init__(self):
        self.find_quest_calls = 0

    def find_quest(self):
        self.find_quest_calls += 1
        if self.find_quest_calls == 1:
            return None
        return {"serial": "quest-serial", "status": "device"}

    def is_vpn_up(self, serial):
        return True


class ReconnectCount(unittest.TestCase):
    @patch("tether_manager.time.sleep", return_value=None)
    def test_one_drop_counts_once_though_two_attempts_are_logged(self, _sleep):
        adb = FakeAdb()
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager._running = True
        manager._kill_process = Mock()
        manager._kill_stale_process = Mock()
        manager._spawn_relay = Mock()
        manager.relay_mode = "run"
        manager._process = Mock(pid=123)
        logs = []
        manager.set_callbacks(on_log=logs.append)

        manager._attempt_reconnect("quest-serial")

        self.assertEqual(manager.stats.reconnect_count, 1)
        self.assertIn("Reconnect attempt 1/10", logs)
        self.assertIn("Reconnect attempt 2/10", logs)
        self.assertEqual(
            manager._reconnect_attempts, 0, "a successful reconnect starts a new outage"
        )

    @patch("tether_manager.time.sleep", return_value=None)
    def test_a_successful_heal_clears_the_reconnect_attempts(self, _sleep):
        adb = Mock()
        adb.find_quest.return_value = {"serial": "quest-serial", "status": "device"}
        adb.start_gnirehtet_client.return_value = True
        adb.is_vpn_up.return_value = True
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager._reconnect_attempts = 4

        manager._heal("quest-serial", Heal.RESTART_CLIENT)

        self.assertEqual(
            manager._reconnect_attempts, 0, "tunnel restored starts a new outage"
        )

    def test_any_return_to_connected_clears_the_reconnect_attempts(self):
        adb = Mock()
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager._reconnect_attempts = 4

        manager._set_state(TetherState.CONNECTED)

        self.assertEqual(
            manager._reconnect_attempts, 0, "Connected means the outage is over"
        )

    @patch("tether_manager.time.sleep", return_value=None)
    def test_unplug_replug_through_service_callbacks(self, _sleep):
        """The cable comes out and goes back in during the reconnect (#86).

        One drop is one reconnect, one relay is started, and no headset-side
        call is made. The replug does not start a second connect().
        """
        adb = Mock()
        adb.is_vpn_up.return_value = True
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager.relay_mode = "run"
        manager._running = True
        manager._set_state(TetherState.CONNECTED)
        manager.stats.device_serial = "S"
        manager._kill_process = Mock()
        manager._kill_stale_process = Mock()
        manager._spawn_relay = Mock()
        manager._process = Mock(pid=1)
        logs = []
        manager.set_callbacks(on_log=logs.append)
        on_connected, on_disconnected = qtetherd._monitor_callbacks(manager)

        on_disconnected()  # unplug: the health loop owns the recovery
        on_disconnected.drain()

        polls = []

        def find_quest():
            polls.append(1)
            if len(polls) == 1:
                return None  # the cable is out
            if len(polls) == 2:
                # the cable goes back in while the wait loop polls
                on_connected({"serial": "S", "status": "device"})
                on_connected.drain()
            return {"serial": "S", "status": "device"}

        adb.find_quest.side_effect = find_quest

        manager._attempt_reconnect("S")  # the health loop's reconnect

        self.assertEqual(manager.stats.reconnect_count, 1)
        manager._spawn_relay.assert_called_once()
        self.assertEqual(manager.stats.state, TetherState.CONNECTED)
        headset_calls = {c[0] for c in adb.mock_calls} - {"find_quest", "is_vpn_up"}
        self.assertEqual(headset_calls, set(), "no headset-side call on unplug or replug")
        self.assertIn("Headset back on USB while reconnecting — no new relay", logs)

    def test_disconnect_headset_absent_skips_headset_steps(self):
        adb = Mock()
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager._set_state(TetherState.CONNECTED)
        manager.stats.device_serial = "S"
        manager._kill_process = Mock()
        logs = []
        manager.set_callbacks(on_log=logs.append)

        manager.disconnect(headset_present=False)

        self.assertEqual(adb.mock_calls, [])
        manager._kill_process.assert_called_once_with()
        self.assertEqual(manager.stats.state, TetherState.IDLE)
        self.assertIn("Headset absent — skipping headset-side cleanup", logs)

    @patch("tether_manager.time.sleep", return_value=None)
    def test_disconnect_with_headset_still_removes_reverse_tunnels(self, _sleep):
        adb = Mock()
        adb.find_serial.return_value = "S"
        adb.is_vpn_down.return_value = True
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager._set_state(TetherState.CONNECTED)
        manager.stats.device_serial = "S"
        manager._kill_process = Mock()

        manager.disconnect()

        adb.remove_all_reverse_tunnels.assert_called_once_with("S")
        manager._kill_process.assert_called_once_with()

    def test_connect_while_reconnecting_returns_true_without_relay(self):
        manager = TetherManager(Mock(), gnirehtet_path="fake.jar")
        manager._set_state(TetherState.RECONNECTING)
        manager._spawn_relay = Mock()
        manager._start_split_relay = Mock()
        manager._kill_stale_process = Mock()

        self.assertTrue(manager.connect("S"))

        manager._spawn_relay.assert_not_called()
        manager._start_split_relay.assert_not_called()
        manager._kill_stale_process.assert_not_called()
        self.assertEqual(manager.stats.state, TetherState.RECONNECTING)


if __name__ == "__main__":
    unittest.main()
