"""connect() stops cleanly, and a health loop kept from an old session idles (#115).

Fakes only: no headset, no relay process, no real sleeps in connect().
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherManager, TetherState  # noqa: E402

REAL_SLEEP = time.sleep


def make_manager(vpn_up=False):
    adb = Mock()
    adb.adb_path = "adb"
    adb.is_vpn_up.return_value = vpn_up
    adb.get_device_details.return_value = {}
    adb.start_gnirehtet_client.return_value = True
    manager = TetherManager(adb, gnirehtet_path="fake.jar")
    manager.relay_mode = "run"
    manager._wifi_mode = "off"
    manager._kill_stale_process = Mock()
    manager.restore_wifi_if_flagged = Mock()
    manager._apply_wifi_policy = Mock()
    manager._start_health_monitor = Mock()
    return manager


def fake_process(poll=None):
    process = Mock(pid=123)
    process.poll.return_value = poll
    return process


class ConnectStopChecks(unittest.TestCase):
    def setUp(self):
        self.manager = make_manager()
        self.process = fake_process()
        self.states = []
        self.manager.set_callbacks(
            on_state_change=lambda state, stats: self.states.append(state))

    def relay_starts(self, process=None, on_start=None):
        """Stand in for _start_run_relay: the relay is up and handed over."""
        process = process or self.process

        def start(serial):
            self.manager._process = process
            if on_start:
                on_start()
            return True

        self.manager._start_run_relay = Mock(side_effect=start)

    def connect(self, hook=None):
        with patch("tether_manager.time.sleep", side_effect=hook or (lambda _s: None)):
            return self.manager.connect("quest-serial")

    def stop(self):
        self.manager._running = False

    def assert_no_connected_no_error(self):
        self.assertNotIn(TetherState.CONNECTED, self.states)
        self.assertNotIn(TetherState.ERROR, self.states)

    def test_stop_after_relay_start_kills_the_relay_and_sets_no_state(self):
        self.relay_starts(on_start=self.stop)

        self.assertFalse(self.connect())

        self.process.terminate.assert_called()
        self.assertIsNone(self.manager._process)
        self.assert_no_connected_no_error()
        self.assertEqual(self.states, [TetherState.STARTING])
        self.manager.adb.is_vpn_up.assert_not_called()
        self.manager._apply_wifi_policy.assert_not_called()
        self.manager._start_health_monitor.assert_not_called()

    def test_relay_killed_during_tunnel_wait_by_a_stop_does_not_raise(self):
        self.relay_starts()

        def stop_and_kill(_seconds):
            # What disconnect() does: stop, then kill the relay (clears the handle).
            self.stop()
            self.manager._process = None

        self.assertFalse(self.connect(hook=lambda _s: stop_and_kill(_s)))

        self.assert_no_connected_no_error()
        self.manager._start_health_monitor.assert_not_called()

    def test_relay_gone_during_tunnel_wait_without_a_stop_is_an_error(self):
        self.relay_starts()

        def kill_without_stop(_seconds):
            self.manager._process = None

        self.assertFalse(self.connect(hook=kill_without_stop))

        self.assertEqual(self.states[-1], TetherState.ERROR)
        self.assertEqual(self.manager.stats.last_error, "Process died during setup")

    def test_genuine_relay_death_during_tunnel_wait_is_an_error(self):
        self.relay_starts(process=fake_process(poll=1))

        self.assertFalse(self.connect())

        self.assertEqual(self.states[-1], TetherState.ERROR)
        self.assertEqual(self.manager.stats.last_error, "Process died during setup")
        self.assertNotIn(TetherState.CONNECTED, self.states)

    def test_stop_during_tunnel_check_returns_before_device_details(self):
        self.relay_starts()

        def vpn_up_then_stop(serial):
            self.stop()
            return True

        self.manager.adb.is_vpn_up.side_effect = vpn_up_then_stop

        self.assertFalse(self.connect())

        self.manager.adb.get_device_details.assert_not_called()
        self.assert_no_connected_no_error()

    def test_stop_during_device_details_sets_no_connected_and_no_wifi_change(self):
        self.relay_starts()
        self.manager.adb.is_vpn_up.return_value = True

        def details_then_stop(serial):
            self.stop()
            return {}

        self.manager.adb.get_device_details.side_effect = details_then_stop

        self.assertFalse(self.connect())

        self.assertNotIn(TetherState.CONNECTED, self.states)
        self.manager._apply_wifi_policy.assert_not_called()
        self.manager._start_health_monitor.assert_not_called()
        self.assertNotEqual(self.manager.stats.state, TetherState.CONNECTED)

    def test_stop_during_wifi_policy_skips_the_health_monitor(self):
        self.relay_starts()
        self.manager.adb.is_vpn_up.return_value = True
        self.manager._apply_wifi_policy = Mock(
            side_effect=lambda serial: self.stop())

        self.assertFalse(self.connect())

        self.manager._apply_wifi_policy.assert_called_once_with("quest-serial")
        self.manager._start_health_monitor.assert_not_called()

    def test_stop_during_reissue_loop_returns_quietly(self):
        self.relay_starts()
        calls = []

        def hook(_seconds):
            calls.append(1)
            if len(calls) == 18:  # 15 tun0 polls, then 3 polls of the re-issue loop
                self.stop()

        self.assertFalse(self.connect(hook=hook))

        self.manager.adb.start_gnirehtet_client.assert_called_once()
        self.manager.adb.get_device_details.assert_not_called()
        self.assert_no_connected_no_error()

    def test_superseded_connect_returns_false_without_a_kill(self):
        self.relay_starts()
        self.manager.adb.is_vpn_up.return_value = True
        self.manager._kill_process = Mock()

        def details_then_supersede(serial):
            # A newer connect() started while this one was still blocked.
            self.manager._session_gen += 1
            return {}

        self.manager.adb.get_device_details.side_effect = details_then_supersede

        self.assertFalse(self.connect())

        self.manager._kill_process.assert_not_called()
        self.assertTrue(self.manager._running)
        self.assert_no_connected_no_error()
        self.assertNotIn(TetherState.CONNECTED, self.states)
        self.manager._start_health_monitor.assert_not_called()

    def test_connect_hands_the_loop_over_before_starting_it(self):
        seen = []
        self.manager._start_health_monitor = Mock(
            side_effect=lambda serial: seen.append(
                (self.manager._health_active_gen, self.manager._session_gen)))
        self.relay_starts()
        self.manager.adb.is_vpn_up.return_value = True

        self.assertTrue(self.connect())
        self.manager._set_state(TetherState.IDLE)
        self.manager._running = False
        self.assertTrue(self.connect())

        self.assertEqual(seen, [(1, 1), (2, 2)])

    def test_real_disconnect_during_tunnel_wait_leaves_idle(self):
        self.relay_starts()

        def disconnect_now(_seconds):
            # The real thing, as qtetherd does it: stop, clear the state, kill the relay.
            self.manager.disconnect(headset_present=False)

        self.assertFalse(self.connect(hook=disconnect_now))

        self.assertEqual(self.manager.stats.state, TetherState.IDLE)
        self.assertEqual(self.states[-1], TetherState.IDLE)
        self.assertNotIn(TetherState.CONNECTED, self.states)
        self.assertNotIn(TetherState.ERROR, self.states)
        self.assertIsNone(self.manager._process)
        self.manager._apply_wifi_policy.assert_not_called()
        self.manager._start_health_monitor.assert_not_called()

    def test_stop_during_the_connected_state_callback_skips_the_wifi_policy(self):
        self.relay_starts()
        self.manager.adb.is_vpn_up.return_value = True

        def stop_when_connected(state, stats):
            self.states.append(state)
            if state is TetherState.CONNECTED:
                self.stop()

        self.manager.set_callbacks(on_state_change=stop_when_connected)

        self.assertFalse(self.connect())

        self.manager._apply_wifi_policy.assert_not_called()
        self.manager._start_health_monitor.assert_not_called()

    def test_stop_during_client_restart_does_not_wait_again(self):
        self.relay_starts()
        sleeps = []
        self.manager.adb.start_gnirehtet_client.side_effect = (
            lambda serial, port: self.stop() or True)

        self.assertFalse(self.connect(hook=sleeps.append))

        self.assertEqual(len(sleeps), 15)  # the 15 tun0 polls, none in the re-issue loop
        self.manager.adb.get_device_details.assert_not_called()

    def test_stop_during_wifi_policy_puts_wifi_back(self):
        # The policy's adb calls can land after disconnect() restored Wi-Fi, so connect()
        # puts Wi-Fi back itself when a stop came during the policy (#115).
        self.relay_starts()
        self.manager.adb.is_vpn_up.return_value = True
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        flag = os.path.join(tmp.name, "wifi-off.flag")
        self.manager._data_dir = tmp.name
        self.manager._wifi_flag = flag
        self.manager.restore_wifi_if_flagged = (
            TetherManager.restore_wifi_if_flagged.__get__(self.manager))

        def policy_writes_flag_then_stop(serial):
            with open(flag, "w") as f:
                f.write("now")
            self.stop()

        self.manager._apply_wifi_policy = Mock(side_effect=policy_writes_flag_then_stop)

        self.assertFalse(self.connect())

        self.manager.adb.run_adb.assert_any_call(
            "-s", "quest-serial", "shell", "svc", "wifi", "enable")
        self.assertFalse(os.path.exists(flag))
        self.manager._start_health_monitor.assert_not_called()


class GenerationBeforeRunning(unittest.TestCase):
    def test_session_generation_is_bumped_before_running_turns_true(self):
        # A loop kept from the last session wakes as soon as _running is True. If the
        # generation still has the old value then, it counts as current and can heal
        # beside the new connect (#115).
        seen = []

        class Recording(TetherManager):
            @property
            def _running(self):
                return self.__dict__.get("_running_value", False)

            @_running.setter
            def _running(self, value):
                self.__dict__["_running_value"] = value
                if value:
                    seen.append((self._session_gen, self._health_active_gen))

        adb = Mock()
        adb.adb_path = "adb"
        adb.is_vpn_up.return_value = True
        adb.get_device_details.return_value = {}
        manager = Recording(adb, gnirehtet_path="fake.jar")
        manager.relay_mode = "run"
        manager._wifi_mode = "off"
        manager._kill_stale_process = Mock()
        manager.restore_wifi_if_flagged = Mock()
        manager._apply_wifi_policy = Mock()
        manager._start_health_monitor = Mock()
        process = fake_process()

        def start(serial):
            manager._process = process
            return True

        manager._start_run_relay = Mock(side_effect=start)
        with patch("tether_manager.time.sleep"):
            self.assertTrue(manager.connect("quest-serial"))

        # (new generation, loop not yet handed over)
        self.assertEqual(seen, [(1, 0)])


class KeptHealthLoop(unittest.TestCase):
    """A loop left from an old session must idle until connect() hands it over."""

    def setUp(self):
        self.manager = make_manager()
        self.manager.HEALTH_CHECK_INTERVAL = 0.01
        self.manager.POST_CONNECT_COOLDOWN = 0
        self.manager._wifi_mode = "leave"
        self.manager._running = True
        self.manager._session_started = True
        self.manager._session_gen = 1       # a new connect() has started
        self.manager._health_active_gen = 0  # ...but has not handed the loop over
        self.manager._update_stats = Mock(return_value=True)
        self.manager._relay_died = Mock()
        self.manager._check_tunnel = Mock()
        self.manager.is_relay_running = lambda: False  # a dead relay: would heal
        self.thread = None

    def start_loop(self):
        self.thread = threading.Thread(
            target=self.manager._health_loop, args=("quest-serial",), daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.manager._running = False
        if self.thread is not None:
            self.thread.join(2)

    def wait_for(self, condition, seconds=2.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            REAL_SLEEP(0.01)
        return False

    def test_kept_loop_idles_until_the_hand_over(self):
        self.start_loop()
        REAL_SLEEP(0.1)

        self.manager._update_stats.assert_not_called()
        self.manager._relay_died.assert_not_called()
        self.manager._check_tunnel.assert_not_called()

        # connect() hands the loop over for the new session
        self.manager._health_active_gen = self.manager._session_gen

        self.assertTrue(self.wait_for(lambda: self.manager._relay_died.called))

    def test_stuck_adb_call_wakes_into_a_newer_session_and_does_nothing(self):
        entered = threading.Event()
        release = threading.Event()

        def stuck_stats(serial):
            entered.set()
            release.wait(5)
            return True

        self.manager._update_stats = Mock(side_effect=stuck_stats)
        self.manager._health_active_gen = 1
        self.manager._session_gen = 1
        self.start_loop()
        self.assertTrue(entered.wait(2))

        self.manager._session_gen = 2  # a new connect() starts during the adb call
        release.set()
        REAL_SLEEP(0.1)

        self.manager._relay_died.assert_not_called()
        self.manager._check_tunnel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
