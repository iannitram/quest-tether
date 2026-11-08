"""Error and stop paths: no false Error after Disconnect, and the headset is restored (#116).

Three things are checked, with fakes only (no headset, no real relay, no real sleeps):

- a stopped session (disconnect() ran) keeps its state: _relay_died, _check_tunnel and
  _give_up_healing return early and never set ERROR over STOPPING or IDLE;
- the error exits that leave the headset VPN up (_relay_died with auto-reconnect off,
  _give_up_healing, the max-attempts exit) send the STOP intent and restore Wi-Fi
  first, then set ERROR with the same message as before;
- the helper that does that restore is best effort: it skips a missing headset, caps
  its waits, clears the Wi-Fi flag and never raises.

python3 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherManager, TetherState  # noqa: E402

SERIAL = "quest-serial"


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


class FakeAdb:
    """Records every adb call by name, in order. Each answer is configurable."""

    def __init__(self):
        self.adb_path = "adb"
        self.calls = []
        self.listed = SERIAL          # what find_serial() reports (None: unplugged)
        self.vpn_down = True          # is_vpn_down() answer
        self.wifi_ok = True           # check_wifi_connectivity() answer
        self.raise_on_stop = None     # an exception for stop_gnirehtet_client
        self.observer = None          # called with each call name, before it returns

    def _rec(self, name, *args):
        self.calls.append((name, args))
        if self.observer:
            self.observer(name)

    def names(self):
        return [name for name, _ in self.calls]

    def find_serial(self):
        self._rec("find_serial")
        return self.listed

    def find_quest(self, *args, **kwargs):
        self._rec("find_quest")
        return None

    def stop_gnirehtet_client(self, serial, timeout=10.0):
        self._rec("stop_gnirehtet_client", serial)
        if self.raise_on_stop is not None:
            raise self.raise_on_stop
        return True

    def is_vpn_down(self, serial):
        self._rec("is_vpn_down", serial)
        return self.vpn_down

    def vpn_agent_state(self, serial):
        self._rec("vpn_agent_state", serial)
        return None

    def force_stop_gnirehtet(self, serial):
        self._rec("force_stop_gnirehtet", serial)
        return True

    def toggle_wifi(self, serial, wait_seconds=4.0):
        self._rec("toggle_wifi", serial)
        return True

    def check_wifi_connectivity(self, serial):
        self._rec("check_wifi_connectivity", serial)
        return self.wifi_ok


class Harness:
    """A TetherManager with a live session, a fake relay and a fake adb."""

    def __init__(self, case):
        self.adb = FakeAdb()
        self.spawned = []
        self.logs = []
        self.states = []       # (state, relay alive) at each state change
        self.events = []       # adb calls and state changes, in the order they happen
        self.adb.observer = lambda name: self.events.append(("adb", name))
        self.m = TetherManager(self.adb, gnirehtet_path="fake.jar")
        self.tmp = tempfile.mkdtemp(prefix="qt116-")
        case.addCleanup(shutil.rmtree, self.tmp, True)
        self.m._wifi_flag = os.path.join(self.tmp, "wifi-off.flag")
        self.m._spawn_relay = self._spawn
        self.m._kill_stale_process = lambda: None
        self.m.set_callbacks(on_state_change=self._on_state, on_log=self.logs.append)
        sleep = patch("tether_manager.time.sleep", return_value=None)
        sleep.start()
        case.addCleanup(sleep.stop)

    def _spawn(self, serial):
        proc = FakeProc(100 + len(self.spawned))
        self.spawned.append(proc)
        self.m._process = proc
        self.m.stats.relay_pid = proc.pid
        return True

    def _on_state(self, state, stats):
        self.states.append((state, self.m.is_relay_running()))
        self.events.append(("state", state))

    def live_session(self):
        """What connect() leaves behind: a live relay, a running session, CONNECTED."""
        self._spawn(SERIAL)
        self.m._running = True
        self.m._session_started = True
        self.m.stats.device_serial = SERIAL
        self.m._set_state(TetherState.CONNECTED)
        self.events.clear()
        self.adb.calls.clear()
        self.states.clear()
        return self.spawned[-1]

    def stopped_session(self, state=TetherState.IDLE):
        """What disconnect() leaves behind: the session started, then stopped."""
        self.live_session()
        self.m._running = False
        self.m._set_state(state)
        self.events.clear()
        self.adb.calls.clear()
        self.states.clear()

    def error_index(self):
        for i, event in enumerate(self.events):
            if event == ("state", TetherState.ERROR):
                return i
        return None

    def index_of_adb(self, name):
        for i, event in enumerate(self.events):
            if event == ("adb", name):
                return i
        return None


class StoppedSessionKeepsItsState(unittest.TestCase):
    def test_relay_death_after_a_stop_leaves_idle_alone(self):
        h = Harness(self)
        h.stopped_session(TetherState.IDLE)
        restarts = h.m._liveness_now().restarts

        h.m._relay_died(SERIAL)

        self.assertEqual(h.m.stats.state, TetherState.IDLE)
        self.assertEqual(h.states, [], "a state was set after the stop")
        self.assertEqual(h.adb.calls, [], "adb was called after the stop")
        self.assertEqual(h.m._liveness_now().restarts, restarts)
        self.assertNotIn("Process died", h.m.stats.last_error)

    def test_relay_death_after_a_stop_leaves_stopping_alone(self):
        h = Harness(self)
        h.stopped_session(TetherState.STOPPING)

        h.m._relay_died(SERIAL)

        self.assertEqual(h.m.stats.state, TetherState.STOPPING)
        self.assertEqual(h.states, [])

    def test_relay_death_after_a_stop_with_auto_reconnect_on_does_nothing(self):
        h = Harness(self)
        h.stopped_session(TetherState.IDLE)
        h.m._auto_reconnect = True
        h.m._attempt_reconnect = lambda serial: self.fail("reconnected after a stop")

        h.m._relay_died(SERIAL)

        self.assertEqual(h.states, [])

    def test_check_tunnel_after_a_stop_leaves_stopping_alone_with_tun0_up(self):
        h = Harness(self)
        h.stopped_session(TetherState.STOPPING)

        h.m._check_tunnel(SERIAL, True, time.time())

        self.assertEqual(h.m.stats.state, TetherState.STOPPING)
        self.assertEqual(h.states, [])
        self.assertEqual(h.adb.calls, [])

    def test_check_tunnel_after_a_stop_leaves_stopping_alone_with_tun0_down(self):
        h = Harness(self)
        h.stopped_session(TetherState.STOPPING)

        h.m._check_tunnel(SERIAL, False, time.time())

        self.assertEqual(h.m.stats.state, TetherState.STOPPING)
        self.assertEqual(h.states, [])
        self.assertEqual(h.adb.calls, [], "the missing-tunnel branch ran after a stop")

    def test_give_up_after_a_stop_is_a_no_op(self):
        h = Harness(self)
        relay = h.live_session()
        h.m._running = False          # disconnect() has started
        h.m._set_state(TetherState.IDLE)
        h.states.clear()
        h.events.clear()
        h.adb.calls.clear()

        h.m._give_up_healing("Tether keeps dropping", SERIAL)

        self.assertEqual(h.m.stats.state, TetherState.IDLE)
        self.assertEqual(h.states, [], "ERROR was set over IDLE")
        self.assertFalse(relay.terminated, "give-up killed the relay after a stop")
        self.assertEqual(h.adb.calls, [])


class ErrorPathRestoresTheHeadset(unittest.TestCase):
    def test_relay_death_with_auto_reconnect_off_sends_stop_and_restores_wifi_first(self):
        h = Harness(self)
        relay = h.live_session()
        h.m._auto_reconnect = False
        relay.returncode = 1          # the relay died on its own

        h.m._relay_died(SERIAL)

        self.assertEqual(h.m.stats.state, TetherState.ERROR)
        self.assertEqual(h.m.stats.last_error, "Process died")
        self.assertFalse(h.m._running)
        self.assertIn("stop_gnirehtet_client", h.adb.names())
        self.assertIn("toggle_wifi", h.adb.names())
        self.assertLess(h.index_of_adb("toggle_wifi"), h.error_index())
        self.assertLess(h.index_of_adb("stop_gnirehtet_client"), h.error_index())

    def test_relay_death_restore_runs_while_the_state_is_not_error(self):
        h = Harness(self)
        h.live_session()
        h.m._auto_reconnect = False
        h.m._process.returncode = 1
        state_at_each_adb_call = []
        h.adb.observer = lambda name: state_at_each_adb_call.append(
            (name, h.m.stats.state)
        )

        h.m._relay_died(SERIAL)

        self.assertTrue(state_at_each_adb_call, "the restore made no adb call")
        for name, state in state_at_each_adb_call:
            self.assertNotEqual(state, TetherState.ERROR, f"{name} ran after ERROR")
        self.assertEqual(h.m.stats.state, TetherState.ERROR)

    def test_give_up_kills_the_relay_sends_stop_restores_wifi_then_errors(self):
        h = Harness(self)
        relay = h.live_session()

        h.m._give_up_healing("Tether keeps dropping", SERIAL)

        self.assertTrue(relay.terminated, "the relay outlived the give-up")
        self.assertIsNone(h.m._process)
        self.assertEqual(h.m.stats.state, TetherState.ERROR)
        self.assertEqual(h.m.stats.last_error, "Tether keeps dropping")
        self.assertFalse(h.m._running)
        self.assertIn("stop_gnirehtet_client", h.adb.names())
        self.assertIn("toggle_wifi", h.adb.names())
        self.assertLess(h.index_of_adb("toggle_wifi"), h.error_index())
        self.assertEqual(h.states[-1], (TetherState.ERROR, False))

    def test_give_up_uses_the_stats_serial_when_none_is_passed(self):
        h = Harness(self)
        h.live_session()

        h.m._give_up_healing("Tether keeps dropping")

        self.assertIn(("stop_gnirehtet_client", (SERIAL,)), h.adb.calls)
        self.assertIn("toggle_wifi", h.adb.names())

    def test_max_attempts_exit_kills_sends_stop_and_restores_wifi_then_errors(self):
        h = Harness(self)
        relay = h.live_session()
        h.m._reconnect_attempts = h.m.MAX_RECONNECT_ATTEMPTS

        h.m._attempt_reconnect(SERIAL)

        self.assertTrue(relay.terminated, "the relay outlived the max-attempts exit")
        self.assertEqual(len(h.spawned), 1, "the max-attempts exit spawned a relay")
        self.assertEqual(h.m.stats.state, TetherState.ERROR)
        self.assertEqual(h.m.stats.last_error, "Max reconnect attempts reached")
        self.assertFalse(h.m._running)
        self.assertIn("Max reconnect attempts (10) reached", h.logs)
        self.assertIn("stop_gnirehtet_client", h.adb.names())
        self.assertIn("toggle_wifi", h.adb.names())
        self.assertLess(h.index_of_adb("toggle_wifi"), h.error_index())
        self.assertEqual(h.states[-1], (TetherState.ERROR, False))


class RestoreHelper(unittest.TestCase):
    def test_missing_headset_only_asks_for_the_serial(self):
        h = Harness(self)
        h.adb.listed = None

        h.m._restore_headset_best_effort(SERIAL)

        self.assertEqual(h.adb.names(), ["find_serial"])
        self.assertIn("Headset not reachable — VPN may still be on", h.logs)
        self.assertEqual(h.m.stats.state, TetherState.IDLE, "the helper set a state")

    def test_another_serial_on_the_bus_is_treated_as_missing(self):
        h = Harness(self)
        h.adb.listed = "other-serial"

        h.m._restore_headset_best_effort(SERIAL)

        self.assertEqual(h.adb.names(), ["find_serial"])

    def test_no_serial_is_a_no_op(self):
        h = Harness(self)

        h.m._restore_headset_best_effort(None)
        h.m._restore_headset_best_effort("")

        self.assertEqual(h.adb.calls, [])
        self.assertEqual(h.spawned, [])

    def test_vpn_still_up_gets_one_force_stop(self):
        h = Harness(self)
        h.adb.vpn_down = False

        # The clock moves 1 s per read, so the 2 s wait ends after a few polls.
        clock = iter(range(0, 1000))
        with patch("tether_manager.time.monotonic", side_effect=lambda: float(next(clock))):
            h.m._restore_headset_best_effort(SERIAL)

        self.assertEqual(h.adb.names().count("force_stop_gnirehtet"), 1)
        self.assertLess(
            h.adb.names().index("force_stop_gnirehtet"),
            h.adb.names().index("toggle_wifi"),
        )
        self.assertIn("toggle_wifi", h.adb.names())

    def test_vpn_down_skips_the_force_stop(self):
        h = Harness(self)
        h.adb.vpn_down = True

        h.m._restore_headset_best_effort(SERIAL)

        self.assertNotIn("force_stop_gnirehtet", h.adb.names())
        self.assertIn("toggle_wifi", h.adb.names())

    def test_a_raising_stop_is_swallowed_and_the_caller_still_errors(self):
        h = Harness(self)
        relay = h.live_session()
        h.adb.raise_on_stop = RuntimeError("adb wedged")

        h.m._give_up_healing("Tether keeps dropping", SERIAL)   # must not raise

        self.assertTrue(relay.terminated)
        self.assertEqual(h.m.stats.state, TetherState.ERROR)
        self.assertEqual(h.m.stats.last_error, "Tether keeps dropping")
        self.assertIn("Best-effort headset restore failed: adb wedged", h.logs)

    def test_the_helper_never_raises_even_when_wifi_fails(self):
        h = Harness(self)

        def boom(serial, wait_seconds=4.0):
            raise OSError("adb gone")
        h.adb.toggle_wifi = boom

        h.m._restore_headset_best_effort(SERIAL)   # must not raise

        self.assertTrue(any("Best-effort headset restore failed" in line for line in h.logs))

    def test_the_wifi_flag_is_cleared_after_the_toggle(self):
        h = Harness(self)
        with open(h.m._wifi_flag, "w") as f:
            f.write("2026-11-07 07:00:00")
        seen = []
        h.adb.observer = lambda name: seen.append(
            (name, os.path.exists(h.m._wifi_flag))
        ) if name == "toggle_wifi" else None

        h.m._restore_headset_best_effort(SERIAL)

        self.assertEqual(seen, [("toggle_wifi", True)], "the flag is cleared before the toggle")
        self.assertFalse(os.path.exists(h.m._wifi_flag))

    def test_the_wifi_wait_and_the_vpn_wait_are_capped(self):
        h = Harness(self)
        waits = {}
        h.adb.toggle_wifi = lambda serial, wait_seconds=4.0: waits.update(
            wifi=wait_seconds
        ) or True
        h.adb.vpn_down = True

        h.m._restore_headset_best_effort(SERIAL)

        self.assertEqual(waits["wifi"], TetherManager.ERROR_RESTORE_WIFI_WAIT)
        self.assertLessEqual(TetherManager.ERROR_RESTORE_VPN_WAIT, 2.0)
        self.assertLessEqual(TetherManager.ERROR_RESTORE_WIFI_WAIT, 2.0)

    def test_the_helper_checks_wifi_once_and_does_not_retry(self):
        h = Harness(self)
        h.adb.wifi_ok = False

        h.m._restore_headset_best_effort(SERIAL)

        self.assertEqual(h.adb.names().count("check_wifi_connectivity"), 1)
        self.assertIn("Could not confirm Wi-Fi restored — may need manual reconnect", h.logs)


if __name__ == "__main__":
    unittest.main()


class DisconnectDuringTheRestore(unittest.TestCase):
    """A Disconnect (or a new Connect) that lands while the error-path restore runs.

    The error exits clear _running themselves, so _stop_requested() cannot tell a later
    disconnect() apart. The restore takes seconds, and disconnect() joins the health
    thread for only 5 s, so the two overlap. disconnect() then owns the state and the
    headset: the error exit must not set ERROR over IDLE or a new session, and must not
    run a second Wi-Fi toggle against disconnect()'s (#116)."""

    def _disconnect_in_restore(self, h, then=None):
        """Run a real disconnect() the first time the restore sends the STOP intent."""
        fired = []

        def observer(name):
            if name == "stop_gnirehtet_client" and not fired:
                fired.append(True)
                h.m.disconnect()
                if then:
                    then()

        h.adb.observer = observer
        return fired

    def _assert_disconnect_owns_it(self, h, fired):
        self.assertTrue(fired, "the restore never reached the STOP intent")
        self.assertEqual(h.m.stats.state, TetherState.IDLE)
        self.assertNotIn(TetherState.ERROR, [s for s, _ in h.states],
                         "ERROR was set over a concurrent disconnect()")
        self.assertEqual(h.adb.names().count("toggle_wifi"), 1,
                         "the restore toggled Wi-Fi again after disconnect() did")

    def test_relay_death_with_auto_reconnect_off(self):
        h = Harness(self)
        relay = h.live_session()
        h.m._auto_reconnect = False
        relay.returncode = 1
        fired = self._disconnect_in_restore(h)

        h.m._relay_died(SERIAL)

        self._assert_disconnect_owns_it(h, fired)

    def test_give_up_healing(self):
        h = Harness(self)
        h.live_session()
        fired = self._disconnect_in_restore(h)

        h.m._give_up_healing("Tether keeps dropping", SERIAL)

        self._assert_disconnect_owns_it(h, fired)

    def test_max_attempts_exit(self):
        h = Harness(self)
        h.live_session()
        h.m._reconnect_attempts = h.m.MAX_RECONNECT_ATTEMPTS
        fired = self._disconnect_in_restore(h)

        h.m._attempt_reconnect(SERIAL)

        self._assert_disconnect_owns_it(h, fired)

    def test_a_new_connect_during_the_restore_is_not_overwritten_with_error(self):
        h = Harness(self)
        h.live_session()

        def new_connect():
            # What connect() leaves behind a moment later: a running session.
            h.m._running = True
            h.m._session_started = True
            h.m._set_state(TetherState.STARTING)

        fired = self._disconnect_in_restore(h, then=new_connect)

        h.m._give_up_healing("Tether keeps dropping", SERIAL)

        self.assertTrue(fired)
        self.assertEqual(h.m.stats.state, TetherState.STARTING)
        self.assertNotIn(TetherState.ERROR, [s for s, _ in h.states])
