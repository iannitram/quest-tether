"""Client-start refusals skip the wait and say why (#63): python -m unittest discover -s tests"""

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from heal import Heal  # noqa: E402
from tether_manager import TetherManager  # noqa: E402


def make_manager():
    adb = Mock()
    adb.find_quest.return_value = {"serial": "quest-serial", "status": "device"}
    adb.is_vpn_up.return_value = False
    adb.get_device_details.return_value = {}
    adb.start_gnirehtet_client.return_value = False
    adb.last_client_error = "Error type 3"
    manager = TetherManager(adb, gnirehtet_path="fake.jar")
    manager._kill_stale_process = Mock()
    manager._spawn_relay = Mock()
    manager.restore_wifi_if_flagged = Mock()
    manager._apply_wifi_policy = Mock()
    manager._start_health_monitor = Mock()
    process = Mock()
    process.pid = 123
    process.poll.return_value = None
    manager._process = process
    logs = []
    manager.set_callbacks(on_log=logs.append)
    return manager, logs


class ClientStartRefused(unittest.TestCase):
    @patch("tether_manager.time.sleep", return_value=None)
    def test_connect_logs_the_refusal_and_skips_the_extra_wait(self, _sleep):
        manager, logs = make_manager()
        polls_at_refusal = []

        def refuse(serial, port):
            polls_at_refusal.append(manager.adb.is_vpn_up.call_count)
            return False

        manager.adb.start_gnirehtet_client.side_effect = refuse

        self.assertTrue(manager.connect("quest-serial"))

        self.assertIn("client start refused: Error type 3", logs)
        self.assertEqual(len(polls_at_refusal), 1)
        self.assertEqual(
            manager.adb.is_vpn_up.call_count,
            polls_at_refusal[0],
            "no tun0 poll may run after the client start is refused",
        )

    @patch("tether_manager.time.sleep", return_value=None)
    def test_heal_logs_the_refusal_and_skips_the_wait(self, _sleep):
        manager, logs = make_manager()

        self.assertFalse(manager._heal("quest-serial", Heal.RESTART_CLIENT))

        self.assertIn("client start refused: Error type 3", logs)
        manager.adb.is_vpn_up.assert_not_called()

    @patch("tether_manager.time.sleep", return_value=None)
    def test_reconnect_logs_the_refusal_and_skips_the_rest_of_the_wait(self, _sleep):
        manager, logs = make_manager()
        manager._running = True
        manager._kill_process = Mock()
        manager._kill_stale_process = Mock()
        manager._spawn_relay = Mock()
        manager._process = Mock(pid=123)
        polls_at_refusal = []

        def refuse(serial, port):
            polls_at_refusal.append(manager.adb.is_vpn_up.call_count)
            return False

        manager.adb.start_gnirehtet_client.side_effect = refuse

        manager._attempt_reconnect("quest-serial")

        self.assertIn("client start refused: Error type 3", logs)
        self.assertEqual(len(polls_at_refusal), 1)
        self.assertEqual(
            manager.adb.is_vpn_up.call_count,
            polls_at_refusal[0],
            "no tun0 poll may run after the client start is refused",
        )

    def test_failure_without_an_error_line_reports_the_exit_code(self):
        manager, logs = make_manager()
        manager.adb.last_client_error = None
        manager.adb.last_client_exit_code = 7

        manager._log_client_start_refused()

        self.assertIn("client start failed (exit code 7)", logs)
        self.assertNotIn("client start refused", logs)


if __name__ == "__main__":
    unittest.main()
