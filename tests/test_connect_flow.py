"""Flow tests for connect and tunnel health without external dependencies."""
import unittest
from unittest.mock import Mock, patch

import tether_manager
from tether_manager import TetherManager, TetherState


class ConnectFlowTests(unittest.TestCase):
    def make_manager(self, vpn_results):
        adb = Mock()
        adb.adb_path = "adb"
        adb.is_vpn_up.side_effect = vpn_results
        adb.get_device_details.return_value = {}
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        process = Mock()
        process.pid = 123
        process.poll.return_value = None
        manager._process = process
        manager._spawn_relay = Mock()
        manager._kill_stale_process = Mock()
        manager.restore_wifi_if_flagged = Mock()
        manager._apply_wifi_policy = Mock()
        manager._start_health_monitor = Mock()
        return manager

    @patch("tether_manager.time.sleep", return_value=None)
    def test_connect_does_not_report_connected_when_tunnel_never_appears(self, _sleep):
        manager = self.make_manager([False] * 23)
        states = []
        manager.set_callbacks(on_state_change=lambda state, stats: states.append(state))

        self.assertTrue(manager.connect("quest-serial"))

        self.assertNotIn(TetherState.CONNECTED, states,
                         "connect reported CONNECTED without tun0")
        self.assertNotEqual(manager.stats.state, TetherState.CONNECTED,
                            "stats.state is CONNECTED without tun0")

    @patch("tether_manager.time.sleep", return_value=None)
    def test_connect_reports_connected_when_tunnel_appears(self, _sleep):
        manager = self.make_manager([False, True])
        manager.set_callbacks(on_state_change=lambda state, stats: None)

        self.assertTrue(manager.connect("quest-serial"))

        self.assertEqual(manager.stats.state, TetherState.CONNECTED)

    @patch("tether_manager.time.sleep", return_value=None)
    def test_health_loop_promotes_only_after_tunnel_appears(self, _sleep):
        adb = Mock()
        adb.get_tun0_stats.side_effect = [None, None, {"rx_bytes": 1, "tx_bytes": 2}]
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager.POST_CONNECT_COOLDOWN = 0
        manager._wifi_mode = "leave"
        manager._process = Mock()
        manager._process.poll.return_value = None
        states = []
        manager.set_callbacks(on_state_change=lambda state, stats: states.append(state))
        manager._set_state(TetherState.STARTING)

        original_update_stats = manager._update_stats
        polls = 0

        def update_until_tunnel(serial):
            nonlocal polls
            result = original_update_stats(serial)
            polls += 1
            if polls == 3:
                manager._running = False
            return result

        manager._update_stats = update_until_tunnel
        manager._running = True
        manager._health_loop("quest-serial")

        self.assertEqual(polls, 3)
        self.assertEqual(states, [TetherState.STARTING, TetherState.CONNECTED],
                         "health loop should stay STARTING while tun0 is missing, then promote")


if __name__ == "__main__":
    unittest.main()
