"""One USB drop counts as one reconnect, however many attempts it takes (#79)."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherManager  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
