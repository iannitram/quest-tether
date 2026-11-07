"""Flow tests for connect and tunnel health without external dependencies."""
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
        # The fake spawn hands over the relay, as the real one does: connect()
        # kills a relay that is already running before it spawns (#107).
        manager._spawn_relay = Mock(
            side_effect=lambda serial: setattr(manager, "_process", process))
        manager.relay_mode = "run"  # run-mode spawn; split mode is in test_split_relay.py
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
    def test_connect_while_connected_starts_no_second_relay(self, _sleep):
        manager = self.make_manager([])
        manager._set_state(TetherState.CONNECTED)

        self.assertTrue(manager.connect("quest-serial"))

        manager._spawn_relay.assert_not_called()
        manager._kill_stale_process.assert_not_called()

    @patch("tether_manager.time.sleep", return_value=None)
    def test_connect_while_starting_starts_no_second_relay(self, _sleep):
        manager = self.make_manager([])
        manager._set_state(TetherState.STARTING)

        self.assertTrue(manager.connect("quest-serial"))

        manager._spawn_relay.assert_not_called()

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

    def test_health_loop_demotes_when_tunnel_disappears(self):
        manager = self.make_manager([])
        manager._spawn_relay("quest-serial")  # a live relay, as connect() leaves it
        manager.adb.get_tun0_stats.return_value = None
        manager.adb.run_adb.return_value.returncode = 1
        manager.POST_CONNECT_COOLDOWN = 0
        manager._wifi_mode = "leave"
        manager._auto_reconnect = False
        manager._heal = Mock()
        states = []
        manager.set_callbacks(on_state_change=lambda state, stats: states.append(state))
        manager._set_state(TetherState.CONNECTED)

        manager._running = True
        self.addCleanup(setattr, manager, "_running", False)
        with patch("tether_manager.time.sleep",
                   side_effect=lambda _: setattr(manager, "_running", False)):
            manager._health_loop("quest-serial")

        manager.adb.get_tun0_stats.assert_called_once_with("quest-serial")
        manager._heal.assert_not_called()
        self.assertEqual(manager.stats.state, TetherState.STARTING,
                         "health loop must demote CONNECTED when tun0 disappears")
        self.assertEqual(states, [TetherState.CONNECTED, TetherState.STARTING])


class FakeClock:
    """Stands in for time.monotonic and time.sleep: sleep advances the clock."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeAdb:
    """Records the order of headset-side calls into one shared list."""

    adb_path = "adb"

    def __init__(self, clock, calls, reachable=True, vpn_down=True):
        self.clock = clock
        self.calls = calls
        self.reachable = reachable
        self.vpn_down = vpn_down
        self.stop_raises = None
        self.stop_result = True
        self.on_stop = None
        self.toggle_raises = None
        self.stop_timeouts = []
        self.force_stop_at = None

    def find_serial(self):
        self.calls.append("find_serial")
        return "quest" if self.reachable else None

    def stop_gnirehtet_client(self, serial, timeout=10.0):
        self.calls.append("stop")
        self.stop_timeouts.append(timeout)
        if self.on_stop:
            self.on_stop()
        if self.stop_raises:
            raise self.stop_raises
        return self.stop_result

    def is_vpn_down(self, serial):
        self.calls.append("is_vpn_down")
        return self.vpn_down

    def vpn_agent_state(self, serial):
        self.calls.append("agent_state")
        return "CONNECTED"

    def force_stop_gnirehtet(self, serial):
        self.calls.append("force_stop")
        self.force_stop_at = self.clock.now
        return True

    def toggle_wifi(self, serial, wait_seconds=4.0):
        self.calls.append("wifi_toggle")
        if self.toggle_raises:
            raise self.toggle_raises
        return True

    def check_wifi_connectivity(self, serial):
        self.calls.append("wifi_check")
        return True

    def remove_all_reverse_tunnels(self, serial):
        self.calls.append("reverse_remove")
        return True


class FakeProcess:
    def __init__(self, calls):
        self.pid = 123
        self.calls = calls

    def poll(self):
        return None

    def terminate(self):
        self.calls.append("terminate")

    def wait(self, timeout=None):
        self.calls.append("wait")
        return 0

    def kill(self):
        self.calls.append("kill")


class DisconnectTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.logs = []
        self.clock = FakeClock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher_m = patch("tether_manager.time.monotonic", self.clock.monotonic)
        patcher_s = patch("tether_manager.time.sleep", self.clock.sleep)
        patcher_m.start()
        patcher_s.start()
        self.addCleanup(patcher_m.stop)
        self.addCleanup(patcher_s.stop)

    def make_manager(self, **adb_kwargs):
        adb = FakeAdb(self.clock, self.calls, **adb_kwargs)
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager.platform = Mock()  # never kill a real pid from a test
        manager._wifi_flag = os.path.join(self.tmp.name, "wifi-off.flag")
        manager._process = FakeProcess(self.calls)
        manager.stats.device_serial = "quest"
        manager.set_callbacks(on_log=self.logs.append)
        manager._set_state(TetherState.CONNECTED)
        return manager

    def test_stop_intent_is_sent_before_the_relay_is_killed(self):
        manager = self.make_manager()

        manager.disconnect()

        self.assertEqual(self.calls, [
            "find_serial", "stop", "is_vpn_down",
            "wifi_toggle", "wifi_check", "reverse_remove",
            "terminate", "wait",
        ])
        self.assertEqual(manager.stats.state, TetherState.IDLE)
        self.assertEqual(self.clock.sleeps, [], "a closed VPN must not wait")

    def test_reverse_tunnels_removed_and_relay_killed_when_stop_raises(self):
        manager = self.make_manager()
        manager.adb.stop_raises = RuntimeError("adb went away")

        manager.disconnect()  # must not raise

        self.assertIn("reverse_remove", self.calls)
        self.assertIn("terminate", self.calls)
        self.assertIsNone(manager._process)
        self.assertTrue(any("adb went away" in line for line in self.logs))

    def test_relay_killed_even_when_wifi_restore_raises(self):
        manager = self.make_manager()
        manager.adb.toggle_raises = OSError("no shell")

        manager.disconnect()

        self.assertIn("reverse_remove", self.calls)
        self.assertLess(self.calls.index("reverse_remove"), self.calls.index("terminate"))
        self.assertIsNone(manager._process)

    def test_poll_has_a_deadline_then_force_stops(self):
        manager = self.make_manager(vpn_down=False)

        manager.disconnect()

        self.assertEqual(manager.adb.force_stop_at, manager.VPN_CLOSE_DEADLINE,
                         "force-stop should come at the 6 s deadline")
        self.assertEqual(set(self.clock.sleeps), {manager.VPN_CLOSE_POLL},
                         "the wait must poll, not sleep a fixed time")
        self.assertIn("force_stop", self.calls)
        self.assertIn("Warning: VPN interface still present", self.logs)
        self.assertIn("terminate", self.calls)

    def test_poll_returns_as_soon_as_tun0_is_gone(self):
        manager = self.make_manager()

        def close_after_two_polls(serial):
            self.calls.append("is_vpn_down")
            return len([c for c in self.calls if c == "is_vpn_down"]) >= 3

        manager.adb.is_vpn_down = close_after_two_polls

        manager.disconnect()

        self.assertNotIn("force_stop", self.calls)
        self.assertEqual(self.clock.sleeps, [manager.VPN_CLOSE_POLL] * 2)

    def test_unreachable_headset_skips_headset_steps_and_warns(self):
        manager = self.make_manager(reachable=False)

        manager.disconnect()

        self.assertNotIn("stop", self.calls)
        self.assertNotIn("wifi_toggle", self.calls)
        self.assertIn("terminate", self.calls)
        self.assertTrue(any("VPN may still be on" in line for line in self.logs))

    def test_force_kill_sends_a_bounded_stop_before_the_tree_kill(self):
        manager = self.make_manager()
        manager.platform.kill_tree.side_effect = lambda pid: self.calls.append("kill_tree")

        with patch("tether_manager.time.sleep"):
            manager.force_kill()

        self.assertEqual(manager.adb.stop_timeouts, [manager.FORCE_KILL_STOP_TIMEOUT])
        self.assertLess(self.calls.index("stop"), self.calls.index("kill_tree"))
        self.assertIsNone(manager._process)

    def test_force_kill_still_kills_when_the_stop_raises(self):
        manager = self.make_manager()
        manager.adb.stop_raises = RuntimeError("adb went away")
        manager.platform.kill_tree.side_effect = lambda pid: self.calls.append("kill_tree")

        with patch("tether_manager.time.sleep"):
            manager.force_kill()

        self.assertIn("kill_tree", self.calls)

    def test_force_kill_survives_the_process_being_cleared_during_the_stop(self):
        manager = self.make_manager()
        manager.platform.kill_tree.side_effect = lambda pid: self.calls.append(f"kill_tree:{pid}")
        manager.adb.on_stop = lambda: setattr(manager, "_process", None)

        with patch("tether_manager.time.sleep"):
            manager.force_kill()  # must not raise

        self.assertIn("kill_tree:123", self.calls)

    def test_a_refused_stop_intent_is_logged_and_disconnect_goes_on(self):
        manager = self.make_manager()
        manager.adb.stop_result = False

        manager.disconnect()

        self.assertTrue(any("refused the STOP" in line for line in self.logs))
        self.assertIn("terminate", self.calls)

    def test_the_last_poll_sleep_is_clamped_to_the_deadline(self):
        manager = self.make_manager(vpn_down=False)

        self.assertFalse(manager._wait_vpn_closed("quest", deadline=0.75))

        self.assertEqual(self.clock.sleeps, [0.5, 0.25])
        self.assertAlmostEqual(self.clock.now, 0.75)

    def test_force_kill_without_a_serial_sends_no_stop(self):
        manager = self.make_manager()
        manager.stats.device_serial = None

        with patch("tether_manager.time.sleep"):
            manager.force_kill()

        self.assertNotIn("stop", self.calls)


if __name__ == "__main__":
    unittest.main()
