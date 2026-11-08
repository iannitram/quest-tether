"""Relay restarts that cannot succeed end in Error, and every failure says why (#128).

Three things are checked, with fakes only (no headset, no real relay, no real sleeps):

- a port held by another program, or a relay that is missing or not executable,
  ends a reconnect in Error at once, with the headset put back, instead of looping
  through the restart backoff;
- any other failed restart sets its reason on the stats and stays Reconnecting;
- the restart backoff logs once, with the time the wait ends;
- connect()'s client-started error exits (a refused client start, a relay that died
  during setup) restore the headset through _end_in_error.

python3 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from heal_policy import Liveness  # noqa: E402
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


class Clock:
    """Fake time: sleep() advances time.time() and returns at once."""

    def __init__(self):
        self.now = 1_000_000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Harness:
    """A TetherManager with a live session, a fake relay and a fake adb."""

    def __init__(self, case, mode="split"):
        self.clock = Clock()
        self.adb = Mock()
        self.adb.adb_path = "adb"
        self.adb.find_quest.return_value = {"serial": SERIAL, "status": "device"}
        self.adb.find_serial.return_value = SERIAL
        self.adb.is_vpn_up.return_value = False
        self.adb.is_vpn_down.return_value = True
        self.adb.start_gnirehtet_client.return_value = True
        self.adb.stop_gnirehtet_client.return_value = True
        self.adb.check_wifi_connectivity.return_value = True
        self.adb.get_device_details.return_value = {}
        self.adb.last_client_error = "boom"
        self.spawned = []
        self.spawn_error = None      # an exception _spawn_relay raises instead of spawning
        self.logs = []
        self.states = []             # (state, last_error) at each state change
        m = self.m = TetherManager(self.adb, gnirehtet_path="fake.jar")
        m.relay_mode = mode
        m.set_callbacks(on_state_change=self._on_state, on_log=self.logs.append)
        m._spawn_relay = self._spawn
        m._kill_stale_process = Mock()
        m._port_blocker = Mock(return_value=None)
        m._wait_until_listening = Mock(return_value=True)
        m.restore_wifi_if_flagged = Mock()
        m._apply_wifi_policy = Mock()
        m._start_health_monitor = Mock()
        m._wifi_flag = os.path.join(tempfile.mkdtemp(prefix="qt128-"), "wifi-off.flag")
        case.addCleanup(shutil.rmtree, os.path.dirname(m._wifi_flag), True)
        for name, fake in (("sleep", self.clock.sleep), ("time", self.clock.time)):
            patcher = patch(f"tether_manager.time.{name}", side_effect=fake)
            patcher.start()
            case.addCleanup(patcher.stop)

    def _spawn(self, serial):
        if self.spawn_error is not None:
            raise self.spawn_error
        proc = FakeProc(100 + len(self.spawned))
        self.spawned.append(proc)
        self.m._process = proc
        self.m.stats.relay_pid = proc.pid
        return True

    def _on_state(self, state, stats):
        self.states.append((state, stats.last_error))

    def live_session(self):
        """What connect() leaves behind: a live relay, a running session, CONNECTED."""
        self._spawn(SERIAL)
        self.m._running = True
        self.m._session_started = True
        self.m.stats.device_serial = SERIAL
        self.m._set_state(TetherState.CONNECTED)
        self.states.clear()
        self.adb.reset_mock()
        return self.spawned[-1]

    def last_state(self):
        return self.states[-1][0] if self.states else None


class RestartThatCannotSucceedEndsInError(unittest.TestCase):
    def test_port_held_by_another_program_ends_in_error_and_restores_the_headset(self):
        h = Harness(self)
        h.live_session()
        h.m._process = None
        h.m._port_blocker = Mock(return_value=4242)
        spawned_before = len(h.spawned)

        h.m._attempt_reconnect(SERIAL)

        self.assertEqual(h.last_state(), TetherState.ERROR)
        self.assertIn("held by PID 4242", h.m.stats.last_error)
        self.assertFalse(h.m._running)
        self.assertEqual(len(h.spawned), spawned_before, "a relay was spawned on a held port")
        self.assertTrue(h.adb.stop_gnirehtet_client.called, "the headset VPN was not stopped")

    def test_missing_relay_binary_in_split_mode_ends_in_error(self):
        h = Harness(self, mode="split")
        h.live_session()
        h.m._process = None
        h.spawn_error = FileNotFoundError("gnirehtet")

        h.m._attempt_reconnect(SERIAL)

        self.assertEqual(h.last_state(), TetherState.ERROR)
        self.assertEqual(h.m.stats.last_error, "Java or gnirehtet JAR not found")
        self.assertFalse(h.m._running)
        self.assertTrue(h.adb.stop_gnirehtet_client.called)

    def test_missing_relay_binary_in_run_mode_ends_in_error(self):
        h = Harness(self, mode="run")
        h.live_session()
        h.m._process = None
        h.spawn_error = FileNotFoundError("gnirehtet")

        h.m._attempt_reconnect(SERIAL)

        self.assertEqual(h.last_state(), TetherState.ERROR)
        self.assertFalse(h.m._running)
        self.assertTrue(h.m.stats.last_error, "Error with no reason")

    def test_relay_not_executable_ends_in_error(self):
        h = Harness(self, mode="split")
        h.live_session()
        h.m._process = None
        h.spawn_error = PermissionError(13, "Permission denied", "gnirehtet")

        h.m._attempt_reconnect(SERIAL)

        self.assertEqual(h.last_state(), TetherState.ERROR)
        self.assertIn("Permission denied", h.m.stats.last_error)
        self.assertFalse(h.m._running)

    def test_port_held_during_a_heal_ends_in_error_and_stops_the_loop(self):
        h = Harness(self)
        h.live_session()
        h.m._process = None
        h.m._port_blocker = Mock(return_value=4242)

        h.m._heal(SERIAL, Mock())  # the heal path goes through _attempt_reconnect

        self.assertEqual(h.last_state(), TetherState.ERROR)
        self.assertFalse(h.m._running)


class DisconnectDuringAFailedRestartOwnsTheState(unittest.TestCase):
    def _disconnect_lands(self, h):
        h.m._running = False  # what disconnect() does first

    def test_fatal_failure_after_a_disconnect_sets_no_error_and_leaves_the_headset(self):
        h = Harness(self)
        h.live_session()
        h.m._process = None
        h.m._port_blocker = Mock(
            side_effect=lambda port: self._disconnect_lands(h) or 4242
        )

        h.m._attempt_reconnect(SERIAL)

        self.assertNotIn(TetherState.ERROR, [s for s, _ in h.states])
        self.assertFalse(h.adb.stop_gnirehtet_client.called)

    def test_retryable_failure_after_a_disconnect_sets_no_state(self):
        h = Harness(self)
        h.live_session()
        h.m._process = None

        def listen(port):
            self._disconnect_lands(h)
            return False

        h.m._wait_until_listening = Mock(side_effect=listen)
        h.m._attempt_reconnect(SERIAL)

        self.assertEqual(h.m.stats.last_error, "")

    def test_refused_client_start_after_a_disconnect_sets_no_state(self):
        h = Harness(self)
        h.live_session()
        h.m._process = None

        calls = []

        def start(serial, port):
            calls.append(1)
            if len(calls) == 1:
                return True
            self._disconnect_lands(h)  # the re-issued start is refused as disconnect lands
            return False

        h.adb.start_gnirehtet_client.side_effect = start
        h.m._attempt_reconnect(SERIAL)

        self.assertEqual(h.m.stats.last_error, "")


class OtherRestartFailuresSayWhy(unittest.TestCase):
    def test_listen_timeout_sets_the_reason_and_stays_reconnecting(self):
        h = Harness(self, mode="split")
        h.live_session()
        h.m._process = None
        h.m._wait_until_listening = Mock(return_value=False)

        h.m._attempt_reconnect(SERIAL)

        self.assertEqual(h.last_state(), TetherState.RECONNECTING)
        self.assertIn("did not listen on port", h.m.stats.last_error)
        self.assertTrue(h.m._running, "a retryable failure must not end the session")

    def test_refused_client_start_on_reconnect_sets_the_reason(self):
        h = Harness(self, mode="split")
        h.live_session()
        h.m._process = None
        h.adb.is_vpn_up.return_value = False
        h.adb.start_gnirehtet_client.side_effect = [True, False]

        h.m._attempt_reconnect(SERIAL)

        self.assertEqual(h.last_state(), TetherState.RECONNECTING)
        self.assertEqual(h.m.stats.last_error, "client start refused: boom")


class BackoffLogsOnce(unittest.TestCase):
    def test_backoff_wait_logs_one_line_with_the_time_it_ends(self):
        h = Harness(self)
        h.live_session()
        due = h.clock.now + 60
        h.m._liveness = Liveness(next_restart_at=due)

        h.m._wait_backoff()

        waits = [line for line in h.logs if "waiting until" in line.lower()]
        self.assertEqual(len(waits), 1, h.logs)
        expected = time.strftime("%H:%M:%S", time.localtime(due))
        self.assertIn(expected, waits[0])
        self.assertAlmostEqual(sum(h.clock.sleeps), 60.0)

    def test_no_backoff_logs_nothing(self):
        h = Harness(self)
        h.live_session()
        h.m._liveness = Liveness(next_restart_at=None)

        h.m._wait_backoff()

        self.assertEqual([l for l in h.logs if "waiting until" in l.lower()], [])


class ConnectErrorExitsRestoreTheHeadset(unittest.TestCase):
    def _connect(self, h):
        h.m._wifi_flag = os.path.join(tempfile.mkdtemp(prefix="qt128c-"), "f")
        h.m._kill_stale_process = Mock()
        return h.m.connect(SERIAL)

    def test_relay_died_during_setup_restores_the_headset(self):
        h = Harness(self, mode="split")

        def kill_relay(_seconds):
            h.m._process = None  # the relay is gone while connect waits for tun0

        h.clock.sleep = lambda seconds: (h.clock.sleeps.append(seconds), kill_relay(seconds))
        with patch("tether_manager.time.sleep", side_effect=h.clock.sleep):
            self.assertFalse(self._connect(h))

        self.assertEqual(h.last_state(), TetherState.ERROR)
        self.assertEqual(h.m.stats.last_error, "Process died during setup")
        self.assertTrue(h.adb.stop_gnirehtet_client.called, "the headset VPN was not stopped")

    def test_refused_client_start_in_connect_restores_the_headset(self):
        h = Harness(self, mode="split")
        h.adb.start_gnirehtet_client.return_value = False

        self.assertFalse(self._connect(h))

        self.assertEqual(h.last_state(), TetherState.ERROR)
        self.assertEqual(h.m.stats.last_error, "client start refused: boom")
        self.assertTrue(h.adb.stop_gnirehtet_client.called, "the headset VPN was not stopped")

    def test_port_held_in_connect_does_not_touch_the_headset(self):
        h = Harness(self, mode="split")
        h.m._port_blocker = Mock(return_value=4242)

        self.assertFalse(self._connect(h))

        self.assertEqual(h.last_state(), TetherState.ERROR)
        self.assertIn("held by PID 4242", h.m.stats.last_error)
        self.assertFalse(h.adb.stop_gnirehtet_client.called,
                         "a relay that never started must not drop the headset VPN")
        self.assertFalse(h.adb.toggle_wifi.called)


if __name__ == "__main__":
    unittest.main()
