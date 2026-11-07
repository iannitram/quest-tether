"""No relay is left running after auto-recovery gives up (#107).

Before the fix, _give_up_healing and the max-attempts exit of _attempt_reconnect
set ERROR with the relay still alive, and the next connect() spawned a second
relay over the handle to the first. These tests use fakes: no headset, no real
relay, no real sleeps. python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherManager, TetherState  # noqa: E402


class FakeProc:
    """A relay process: alive until terminated or killed."""

    def __init__(self, pid):
        self.pid = pid
        self.terminated = False
        self.returncode = None

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode


class Harness:
    """A TetherManager whose relay spawn records fake processes."""

    def __init__(self, mode="run"):
        self.adb = Mock()
        self.adb.adb_path = "adb"
        self.adb.is_vpn_up.return_value = True
        self.adb.start_gnirehtet_client.return_value = True
        self.adb.get_device_details.return_value = {}
        self.adb.find_quest.return_value = {"serial": "quest-serial", "status": "device"}
        self.spawned = []
        self.logs = []
        self.states = []
        m = self.manager = TetherManager(self.adb, gnirehtet_path="fake.jar")
        m.relay_mode = mode
        m.set_callbacks(on_state_change=self._on_state, on_log=self.logs.append)
        m._spawn_relay = self._spawn
        m._kill_stale_process = Mock()
        m._port_blocker = Mock(return_value=None)
        m._wait_until_listening = Mock(return_value=True)
        m.restore_wifi_if_flagged = Mock()
        m._apply_wifi_policy = Mock()
        m._start_health_monitor = Mock()

    def _spawn(self, serial):
        proc = FakeProc(100 + len(self.spawned))
        self.spawned.append(proc)
        self.manager._process = proc
        self.manager.stats.relay_pid = proc.pid
        return True

    def _on_state(self, state, stats):
        # What a UI sees at each state: is a relay still up?
        self.states.append((state, self.manager.is_relay_running()))

    def live_session(self):
        """What connect() leaves behind: a live relay and a running session."""
        self._spawn("quest-serial")
        self.manager._running = True
        self.manager._session_started = True
        self.manager.stats.device_serial = "quest-serial"
        self.manager._set_state(TetherState.CONNECTED)
        return self.spawned[-1]

    def running_relays(self):
        return [p for p in self.spawned if p.poll() is None]


class GiveUpKillsTheRelay(unittest.TestCase):
    def test_give_up_healing_kills_a_live_relay(self):
        h = Harness()
        relay = h.live_session()

        h.manager._give_up_healing("Tether keeps dropping")

        self.assertTrue(relay.terminated, "the relay outlived the give-up")
        self.assertIsNone(h.manager._process)
        self.assertIsNone(h.manager.stats.relay_pid)
        self.assertEqual(h.manager.stats.state, TetherState.ERROR)
        self.assertEqual(h.manager.stats.last_error, "Tether keeps dropping")
        self.assertFalse(h.manager._running)

    def test_error_after_give_up_is_set_with_no_relay_running(self):
        h = Harness()
        h.live_session()

        h.manager._give_up_healing("Tether keeps dropping")

        self.assertEqual(h.states[-1], (TetherState.ERROR, False))

    def test_give_up_with_the_relay_already_dead_still_sets_error(self):
        h = Harness()
        relay = h.live_session()
        relay.returncode = 1  # the relay died on its own

        h.manager._give_up_healing("Tether keeps dropping")

        self.assertIsNone(h.manager._process)
        self.assertEqual(h.manager.stats.state, TetherState.ERROR)

    def test_give_up_with_no_relay_sets_error(self):
        h = Harness()
        h.manager._running = True
        h.manager._session_started = True

        h.manager._give_up_healing("Tether keeps dropping")

        self.assertEqual(h.manager.stats.state, TetherState.ERROR)
        self.assertEqual(h.spawned, [])


class MaxAttemptsKillsTheRelay(unittest.TestCase):
    @patch("tether_manager.time.sleep", return_value=None)
    def test_max_attempts_exit_kills_a_live_relay_and_spawns_none(self, _sleep):
        h = Harness()
        relay = h.live_session()
        h.manager._reconnect_attempts = h.manager.MAX_RECONNECT_ATTEMPTS

        h.manager._attempt_reconnect("quest-serial")

        self.assertTrue(relay.terminated, "the relay outlived the max-attempts exit")
        self.assertEqual(len(h.spawned), 1, "the max-attempts exit spawned a relay")
        self.assertIsNone(h.manager._process)
        self.assertEqual(h.manager.stats.state, TetherState.ERROR)
        self.assertEqual(h.manager.stats.last_error, "Max reconnect attempts reached")
        self.assertEqual(h.states[-1], (TetherState.ERROR, False))
        self.assertFalse(h.manager._running)
        h.adb.find_quest.assert_not_called()


class ConnectKillsTheLastRelay(unittest.TestCase):
    def connect_over_a_live_relay(self, mode):
        h = Harness(mode)
        old = h.live_session()
        # The old state: ERROR with the relay still up, as before the give-up fix.
        h.manager._running = False
        h.manager._set_state(TetherState.ERROR, "Max reconnect attempts reached")

        with patch("tether_manager.time.sleep", return_value=None):
            self.assertTrue(h.manager.connect("quest-serial"))
        return h, old

    def test_connect_in_run_mode_kills_the_live_relay_before_spawning(self):
        h, old = self.connect_over_a_live_relay("run")

        self.assertTrue(old.terminated, "connect() left the old relay running")
        self.assertEqual(h.running_relays(), [h.spawned[-1]])
        self.assertIs(h.manager._process, h.spawned[-1])
        self.assertEqual(h.manager.stats.relay_pid, h.spawned[-1].pid)
        self.assertEqual(h.manager.stats.state, TetherState.CONNECTED)
        self.assertIn(
            "The last session's relay is still running — stopping it first", h.logs
        )

    def test_connect_in_split_mode_kills_the_live_relay_before_spawning(self):
        h, old = self.connect_over_a_live_relay("split")

        self.assertTrue(old.terminated, "connect() left the old relay running")
        self.assertEqual(h.running_relays(), [h.spawned[-1]])
        self.assertEqual(h.manager.stats.state, TetherState.CONNECTED)

    def test_connect_kills_the_old_relay_before_the_stale_port_scan(self):
        h = Harness()
        old = h.live_session()
        h.manager._running = False
        h.manager._set_state(TetherState.ERROR, "Process died")
        seen = []
        h.manager._kill_stale_process = Mock(
            side_effect=lambda: seen.append(old.terminated))

        with patch("tether_manager.time.sleep", return_value=None):
            h.manager.connect("quest-serial")

        # The stale scan skips our own PID, so our own kill must come first.
        self.assertEqual(seen, [True])

    def test_connect_with_a_dead_relay_handle_logs_no_kill(self):
        h = Harness()
        old = h.live_session()
        old.returncode = 1
        h.manager._running = False
        h.manager._set_state(TetherState.ERROR, "Process died")

        with patch("tether_manager.time.sleep", return_value=None):
            self.assertTrue(h.manager.connect("quest-serial"))

        self.assertFalse(old.terminated)
        self.assertNotIn(
            "The last session's relay is still running — stopping it first", h.logs
        )
        self.assertEqual(h.running_relays(), [h.spawned[-1]])


class GiveUpThenConnect(unittest.TestCase):
    """The issue's case: give up with the relay alive, then Connect."""

    @patch("tether_manager.time.sleep", return_value=None)
    def test_give_up_then_connect_leaves_one_relay(self, _sleep):
        h = Harness()
        old = h.live_session()

        h.manager._give_up_healing("Tether keeps dropping")
        self.assertTrue(h.manager.connect("quest-serial"))

        self.assertTrue(old.terminated)
        self.assertEqual(h.running_relays(), [h.spawned[-1]])
        self.assertEqual(len(h.spawned), 2)
        self.assertEqual(h.manager.stats.state, TetherState.CONNECTED)

    @patch("tether_manager.time.sleep", return_value=None)
    def test_max_attempts_then_connect_leaves_one_relay(self, _sleep):
        h = Harness("split")
        old = h.live_session()
        h.manager._reconnect_attempts = h.manager.MAX_RECONNECT_ATTEMPTS

        h.manager._attempt_reconnect("quest-serial")
        self.assertTrue(h.manager.connect("quest-serial"))

        self.assertTrue(old.terminated)
        self.assertEqual(h.running_relays(), [h.spawned[-1]])
        self.assertEqual(h.manager.stats.state, TetherState.CONNECTED)


if __name__ == "__main__":
    unittest.main()
