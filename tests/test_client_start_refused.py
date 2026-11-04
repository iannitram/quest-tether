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

        self.assertTrue(manager.connect("quest-serial"))

        self.assertIn("client start refused: Error type 3", logs)
        # 15 polls for tun0; the 8-poll retry must not run after a refusal.
        self.assertEqual(manager.adb.is_vpn_up.call_count, 15)

    @patch("tether_manager.time.sleep", return_value=None)
    def test_heal_logs_the_refusal_and_skips_the_wait(self, _sleep):
        manager, logs = make_manager()

        self.assertFalse(manager._heal("quest-serial", Heal.RESTART_CLIENT))

        self.assertIn("client start refused: Error type 3", logs)
        manager.adb.is_vpn_up.assert_not_called()


if __name__ == "__main__":
    unittest.main()
