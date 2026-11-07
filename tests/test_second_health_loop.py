"""One health loop per tether (#108), with fakes: no headset needed.

With auto-reconnect off, a relay death stops the health loop. A new session never
starts a second live loop next to one that is still running.
"""
import os
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherManager, TetherState  # noqa: E402

REAL_SLEEP = time.sleep


def make_manager():
    adb = Mock()
    adb.adb_path = "adb"
    manager = TetherManager(adb, gnirehtet_path="fake.jar")
    manager.POST_CONNECT_COOLDOWN = 0
    manager._wifi_mode = "leave"
    manager._update_stats = Mock(return_value=True)
    manager._check_tunnel = Mock()
    manager._attempt_reconnect = Mock()
    return manager


class RelayDeathWithAutoReconnectOff(unittest.TestCase):
    def setUp(self):
        self.manager = make_manager()
        self.manager._auto_reconnect = False
        self.manager.is_relay_running = lambda: False
        self.logs = []
        self.states = []
        self.manager.set_callbacks(
            on_state_change=lambda state, stats: self.states.append(state),
            on_log=self.logs.append,
        )
        self.sleeps = 0

        def fake_sleep(seconds):
            self.sleeps += 1
            if self.sleeps > 50:
                self.manager._running = False
                self.fail("health loop kept running after the relay died")
        patcher = patch("tether_manager.time.sleep", fake_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_loop_stops_after_one_death_report(self):
        self.manager._running = True
        self.manager._session_started = True

        self.manager._health_loop("quest-serial")

        self.assertFalse(self.manager._running)
        self.assertEqual(self.logs.count("gnirehtet process died!"), 1)
        self.assertEqual(self.manager.stats.state, TetherState.ERROR)
        self.assertEqual(self.manager.stats.last_error, "Process died")
        self.manager._attempt_reconnect.assert_not_called()

    def test_the_stop_lands_before_the_error_state(self):
        running_at_error = []
        self.manager.set_callbacks(
            on_state_change=lambda state, stats: running_at_error.append(
                (state, self.manager._running)
            )
        )
        self.manager._running = True

        self.manager._relay_died("quest-serial")

        self.assertEqual(running_at_error, [(TetherState.ERROR, False)])


class StartHealthMonitor(unittest.TestCase):
    def setUp(self):
        self.manager = make_manager()
        self.manager.HEALTH_JOIN_TIMEOUT = 0.05
        self.started = []
        self.release = threading.Event()
        self.manager._health_loop = self._fake_loop
        self.addCleanup(self.release.set)

    def _fake_loop(self, serial):
        self.started.append(serial)
        self.release.wait(5)

    def _old_thread(self, target):
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        self.manager._health_thread = thread
        return thread

    def test_starts_a_loop_when_none_is_running(self):
        self.manager._start_health_monitor("quest-serial")
        REAL_SLEEP(0.05)

        self.assertEqual(self.started, ["quest-serial"])
        self.assertTrue(self.manager._health_thread.is_alive())

    def test_waits_for_an_old_loop_that_is_exiting_then_starts_one(self):
        old = self._old_thread(lambda: REAL_SLEEP(0.01))

        self.manager._start_health_monitor("quest-serial")
        REAL_SLEEP(0.05)

        self.assertFalse(old.is_alive())
        self.assertIsNot(self.manager._health_thread, old)
        self.assertEqual(self.started, ["quest-serial"])

    def test_keeps_an_old_loop_that_is_still_alive_and_starts_no_second(self):
        old_release = threading.Event()
        self.addCleanup(old_release.set)
        old = self._old_thread(lambda: old_release.wait(5))
        logs = []
        self.manager.set_callbacks(on_log=logs.append)

        self.manager._start_health_monitor("new-serial")

        self.assertIs(self.manager._health_thread, old)
        self.assertEqual(self.started, [], "no second loop was started")
        self.assertEqual(self.manager._health_serial, "new-serial")
        self.assertTrue(any("not starting a second" in line for line in logs))

    def test_a_dead_old_handle_is_replaced(self):
        old = self._old_thread(lambda: None)
        old.join()

        self.manager._start_health_monitor("quest-serial")
        REAL_SLEEP(0.05)

        self.assertIsNot(self.manager._health_thread, old)
        self.assertEqual(self.started, ["quest-serial"])


class KeptLoopFollowsTheNewSerial(unittest.TestCase):
    def test_the_loop_polls_the_serial_of_the_current_session(self):
        manager = make_manager()
        manager.is_relay_running = lambda: True
        manager._health_serial = "new-serial"
        cycles = []

        def fake_sleep(seconds):
            cycles.append(seconds)
            if len(cycles) >= 2:
                manager._running = False
        with patch("tether_manager.time.sleep", fake_sleep):
            manager._running = True
            manager._health_loop("old-serial")

        manager._update_stats.assert_called_with("new-serial")


class DisconnectKeepsAStuckLoop(unittest.TestCase):
    def test_a_loop_that_outlives_the_join_keeps_its_handle(self):
        manager = make_manager()
        stuck = Mock()
        stuck.is_alive.return_value = True
        manager._health_thread = stuck
        manager._kill_process = Mock()

        manager.disconnect(headset_present=False)

        stuck.join.assert_called_once_with(timeout=5)
        self.assertIs(manager._health_thread, stuck)

    def test_a_loop_that_exits_in_the_join_is_cleared(self):
        manager = make_manager()
        done = Mock()
        done.is_alive.return_value = False
        manager._health_thread = done
        manager._kill_process = Mock()

        manager.disconnect(headset_present=False)

        self.assertIsNone(manager._health_thread)


class DeathThenReconnectRunsOneLoop(unittest.TestCase):
    """The issue's flow on real threads: relay death with auto-reconnect off, then a
    new session's health monitor. Only one loop is alive at the end."""

    def test_one_live_loop_after_death_and_a_new_start(self):
        manager = make_manager()
        manager._auto_reconnect = False
        relay_alive = [False]
        manager.is_relay_running = lambda: relay_alive[0]

        with patch("tether_manager.time.sleep", lambda s: REAL_SLEEP(0.001)):
            manager._running = True
            manager._session_started = True
            manager._start_health_monitor("quest-serial")
            first = manager._health_thread
            first.join(2)
            self.assertFalse(first.is_alive(), "the loop stops after the death")
            self.assertEqual(manager.stats.state, TetherState.ERROR)

            # The next Connect: a live relay and the session running again.
            relay_alive[0] = True
            manager._running = True
            manager._start_health_monitor("quest-serial")
            second = manager._health_thread
            REAL_SLEEP(0.02)
            alive = [t for t in threading.enumerate()
                     if getattr(t, "_target", None) == manager._health_loop]
            manager._running = False
            second.join(2)

        self.assertIsNot(first, second)
        self.assertEqual(len(alive), 1)


if __name__ == "__main__":
    unittest.main()
