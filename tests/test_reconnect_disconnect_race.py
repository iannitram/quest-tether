"""A disconnect() during an in-flight reconnect leaves no relay running (#103).

The health thread can be between its stop check and its relay spawn when
disconnect() runs. These tests drive each window with fakes: no headset, no
real relay, no real sleeps. python3 -m unittest discover -s tests
"""

import os
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from heal import Heal  # noqa: E402
from tether_manager import (  # noqa: E402
    STOPPED_BY_DISCONNECT,
    TetherManager,
    TetherState,
)

REAL_SLEEP = time.sleep  # captured before any test patches time.sleep


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        REAL_SLEEP(0.005)
    return True


class FakeProc:
    """A relay process: alive until terminated or killed."""

    def __init__(self, pid):
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.returncode = None

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode


class FakeAdb:
    """Records every headset-side call, so a test can see what happened after a stop."""

    adb_path = "adb"

    def __init__(self):
        self.calls = []
        self.vpn_up = False
        self.on_vpn_check = None  # runs inside is_vpn_up
        self.on_adb_restart = None  # runs inside restart_adb_server

    def find_quest(self):
        self.calls.append("find_quest")
        return {"serial": "quest-serial", "status": "device"}

    def is_vpn_up(self, serial):
        self.calls.append("is_vpn_up")
        if self.on_vpn_check:
            self.on_vpn_check()
        return self.vpn_up

    def restart_adb_server(self):
        self.calls.append("restart_adb_server")
        if self.on_adb_restart:
            self.on_adb_restart()

    def start_gnirehtet_client(self, serial, port):
        self.calls.append("start_gnirehtet_client")
        return True

    def remove_all_reverse_tunnels(self, serial):
        self.calls.append("remove_all_reverse_tunnels")

    def stop_gnirehtet_client(self, serial, timeout=None):
        self.calls.append("stop_gnirehtet_client")
        return True

    def find_serial(self):
        return "quest-serial"

    def is_vpn_down(self, serial):
        return True


class Harness:
    """A TetherManager with its relay spawn replaced by a recording fake."""

    def __init__(self, mode="split"):
        self.adb = FakeAdb()
        self.spawned = []
        self.before_return = None  # runs inside a spawn, after self._process is set
        self.manager = TetherManager(self.adb, gnirehtet_path="fake.jar")
        self.manager.relay_mode = mode
        self.logs = []
        self.manager.set_callbacks(on_log=self.logs.append)
        self.manager._kill_stale_process = Mock()
        self.manager._port_blocker = Mock(return_value=None)
        self.manager._stop_vpn_on_headset = Mock()
        self.manager.restore_wifi_if_flagged = Mock()
        self.manager._spawn_relay = self._spawn

    def _spawn(self, serial):
        proc = FakeProc(100 + len(self.spawned))
        self.spawned.append(proc)
        self.manager._process = proc
        self.manager.stats.relay_pid = proc.pid
        if self.before_return is not None:
            self.before_return(proc)
        return True

    def start_session(self):
        """What connect() leaves behind: a running session with a Connected state."""
        self.manager._running = True
        self.manager._session_started = True
        self.manager.stats.device_serial = "quest-serial"
        self.manager._set_state(TetherState.CONNECTED)


class DisconnectDuringReconnect(unittest.TestCase):
    def assert_no_relay_left(self, h):
        for proc in h.spawned:
            self.assertTrue(proc.terminated, f"relay PID {proc.pid} was left running")
        self.assertIsNone(h.manager._process)

    def test_disconnect_in_the_reconnect_delay_spawns_no_relay(self):
        for mode in ("split", "run"):
            with self.subTest(mode=mode):
                h = Harness(mode)
                h.start_session()
                old = FakeProc(1)
                h.manager._process = old  # the relay that just died

                def sleep(seconds):
                    if seconds == TetherManager.RECONNECT_DELAY:
                        h.manager.disconnect(headset_present=False)

                with patch("tether_manager.time.sleep", side_effect=sleep):
                    h.manager._attempt_reconnect("quest-serial")

                self.assertTrue(old.terminated)
                self.assertEqual(h.spawned, [], "no relay after a stop")
                self.assertEqual(h.adb.calls, [], "no headset call after a stop")
                self.assertIsNone(h.manager._process)
                self.assertEqual(h.manager.stats.state, TetherState.IDLE)

    @patch("tether_manager.time.sleep", return_value=None)
    def test_disconnect_from_another_thread_while_the_relay_spawns(self, _sleep):
        h = Harness("split")
        h.start_session()
        spawn_started = threading.Event()
        release = threading.Event()

        def hold_spawn(proc):
            spawn_started.set()
            release.wait(5)

        h.before_return = hold_spawn
        m = h.manager
        with patch.object(TetherManager, "_port_open", return_value=True):
            reconnect = threading.Thread(
                target=m._attempt_reconnect, args=("quest-serial",)
            )
            reconnect.start()
            self.assertTrue(spawn_started.wait(5), "the spawn never started")
            # disconnect() joins nothing here (no health thread), so its kill waits on
            # the spawn that holds _proc_lock. Release the spawn once disconnect has
            # started, so the stop lands during the spawn.
            stopper = threading.Thread(
                target=m.disconnect, kwargs={"headset_present": False}
            )
            stopper.start()
            self.assertTrue(wait_until(lambda: not m._running))
            release.set()
            reconnect.join(5)
            stopper.join(5)

        self.assertFalse(reconnect.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertEqual(len(h.spawned), 1)
        self.assert_no_relay_left(h)
        self.assertEqual(m.stats.state, TetherState.IDLE)
        self.assertNotIn("start_gnirehtet_client", h.adb.calls)
        self.assertIn("Disconnect during reconnect — relay stopped", h.logs)

    def test_a_stopped_session_spawns_nothing(self):
        h = Harness("split")
        h.start_session()
        h.manager._running = False

        self.assertIsNone(h.manager._spawn_relay_if_live("quest-serial"))
        self.assertEqual(h.spawned, [])

    @patch("tether_manager.time.sleep", return_value=None)
    def test_disconnect_in_the_tun0_wait_keeps_the_relay_down_and_no_client_start(
        self, _sleep
    ):
        # Run mode: split mode starts its client before the tun0 wait, which is not
        # the re-issued start this test is about.
        h = Harness("run")
        h.start_session()
        m = h.manager

        def disconnect_once():
            h.adb.on_vpn_check = None
            m.disconnect(headset_present=False)

        h.adb.on_vpn_check = disconnect_once
        with patch.object(TetherManager, "_port_open", return_value=True):
            m._attempt_reconnect("quest-serial")

        self.assertEqual(h.adb.calls.count("is_vpn_up"), 1, "the wait ends at once")
        self.assertNotIn("start_gnirehtet_client", h.adb.calls)
        self.assert_no_relay_left(h)
        self.assertEqual(len(h.spawned), 1)
        self.assertEqual(m.stats.state, TetherState.IDLE)

    @patch("tether_manager.time.sleep", return_value=None)
    def test_a_stop_while_waiting_for_the_headset_ends_the_wait(self, _sleep):
        h = Harness("split")
        h.start_session()
        m = h.manager
        h.adb.find_quest = Mock(return_value=None)  # the cable is out
        polls = []

        def sleep(seconds):
            if seconds == 2:  # the headset poll in _wait_for_device_and_reconnect
                polls.append(seconds)
                if len(polls) == 2:
                    m.disconnect(headset_present=False)

        with patch("tether_manager.time.sleep", side_effect=sleep):
            m._attempt_reconnect("quest-serial")

        self.assertEqual(len(polls), 2, "the 60 s wait ends at the stop")
        self.assertEqual(h.spawned, [])
        self.assertEqual(m.stats.state, TetherState.IDLE)
        self.assertNotEqual(m.stats.state, TetherState.ERROR)


class SplitRelayStop(unittest.TestCase):
    def test_a_stop_during_the_listen_wait_kills_the_relay_and_starts_no_client(self):
        h = Harness("split")
        h.start_session()
        m = h.manager

        def listen_then_stop(port):
            m._running = False  # the session is stopped; the relay still listens
            return True

        with patch.object(TetherManager, "_wait_until_listening", side_effect=listen_then_stop):
            result = m._start_split_relay("quest-serial")

        self.assertEqual(result, STOPPED_BY_DISCONNECT)
        self.assertNotIn("start_gnirehtet_client", h.adb.calls)
        self.assertEqual(len(h.spawned), 1)
        self.assertTrue(h.spawned[0].terminated)
        self.assertIsNone(m._process)

    def test_connect_stopped_during_the_start_sets_no_error_state(self):
        h = Harness("split")
        m = h.manager

        def listen_then_stop(port):
            m._running = False
            return True

        with patch.object(TetherManager, "_port_open", return_value=True), patch.object(
            TetherManager, "_wait_until_listening", side_effect=listen_then_stop
        ):
            self.assertFalse(m.connect("quest-serial"))

        self.assertNotEqual(m.stats.state, TetherState.ERROR)
        self.assertNotIn("start_gnirehtet_client", h.adb.calls)
        self.assertTrue(all(p.terminated for p in h.spawned))


class HealAfterStop(unittest.TestCase):
    def test_heal_after_a_stop_makes_no_adb_call_and_returns_true(self):
        h = Harness("split")
        h.start_session()
        h.manager._running = False  # disconnect() has run

        self.assertTrue(h.manager._heal("quest-serial", Heal.RESTART_CLIENT))

        self.assertEqual(h.adb.calls, [])
        self.assertEqual(h.manager.stats.state, TetherState.CONNECTED)

    @patch("tether_manager.time.sleep", return_value=None)
    def test_a_stop_during_the_adb_restart_starts_no_client(self, _sleep):
        h = Harness("split")
        h.start_session()
        m = h.manager
        h.adb.on_adb_restart = lambda: setattr(m, "_running", False)

        self.assertTrue(m._heal("quest-serial", Heal.RESTART_ADB_THEN_CLIENT))

        self.assertEqual(h.adb.calls, ["find_quest", "restart_adb_server"])

    @patch("tether_manager.time.sleep", return_value=None)
    def test_a_stop_during_the_tun0_check_never_sets_connected(self, _sleep):
        h = Harness("split")
        h.start_session()
        m = h.manager
        m._set_state(TetherState.STARTING)
        h.adb.vpn_up = True  # tun0 is up, so only the stop keeps it from Connected
        h.adb.on_vpn_check = lambda: setattr(m, "_running", False)

        self.assertTrue(m._heal("quest-serial", Heal.RESTART_CLIENT))

        self.assertIn("start_gnirehtet_client", h.adb.calls)
        self.assertNotEqual(m.stats.state, TetherState.CONNECTED)


class OneReadProcessManager(TetherManager):
    """A manager whose relay handle is there on the first read and gone on every read
    after: disconnect() cleared it between two reads in another thread (#103)."""

    _real_process = None
    _reads = 0

    @property
    def _process(self):
        if self._reads == 0:
            self._reads += 1
            return self._real_process
        return None

    @_process.setter
    def _process(self, value):
        self._real_process = value
        self._reads = 0


class RelayHandleReadOnce(unittest.TestCase):
    """The health thread reads self._process once per check, so a disconnect() that
    clears it mid-check cannot raise AttributeError there (#103)."""

    def make(self):
        m = OneReadProcessManager(FakeAdb(), gnirehtet_path="fake.jar")
        m._process = FakeProc(7)
        return m

    def test_wait_until_listening_survives_a_cleared_relay_handle(self):
        m = self.make()
        with patch.object(TetherManager, "_port_open", return_value=True):
            self.assertTrue(m._wait_until_listening(31416))

    def test_is_relay_running_survives_a_cleared_relay_handle(self):
        m = self.make()
        self.assertTrue(m.is_relay_running())


class StopLandsInEachWait(unittest.TestCase):
    """Places where a stop lands and the next step must not run."""

    @patch("tether_manager.time.sleep", return_value=None)
    def test_a_stop_during_find_quest_spawns_nothing(self, _sleep):
        h = Harness("split")
        h.start_session()
        m = h.manager
        m._process = FakeProc(1)

        def find_quest_then_stop():
            h.adb.calls.append("find_quest")
            m.disconnect(headset_present=False)
            return {"serial": "quest-serial", "status": "device"}

        h.adb.find_quest = find_quest_then_stop
        m._attempt_reconnect("quest-serial")

        self.assertEqual(h.spawned, [])
        self.assertNotIn("start_gnirehtet_client", h.adb.calls)
        self.assertEqual(m.stats.state, TetherState.IDLE)

    def test_a_stop_while_the_headset_comes_back_spawns_nothing(self):
        h = Harness("split")
        h.start_session()
        m = h.manager
        quests = iter([None, {"serial": "quest-serial", "status": "device"}])
        h.adb.find_quest = Mock(side_effect=lambda: next(quests))

        def sleep(seconds):
            if seconds == 1:  # the pause after the headset shows up again
                m.disconnect(headset_present=False)

        with patch("tether_manager.time.sleep", side_effect=sleep):
            m._wait_for_device_and_reconnect("quest-serial")

        self.assertEqual(h.spawned, [])
        self.assertEqual(h.adb.find_quest.call_count, 2, "no second reconnect started")
        self.assertEqual(m.stats.state, TetherState.IDLE)

    @patch("tether_manager.time.sleep", return_value=None)
    def test_a_stop_while_the_headset_is_hidden_starts_no_adb_restart(self, _sleep):
        h = Harness("split")
        h.start_session()
        m = h.manager

        def find_quest_then_stop():
            h.adb.calls.append("find_quest")
            m._running = False
            return None

        h.adb.find_quest = find_quest_then_stop

        self.assertTrue(m._heal("quest-serial", Heal.RESTART_CLIENT))
        self.assertEqual(h.adb.calls, ["find_quest"])

    @patch("tether_manager.time.sleep", return_value=None)
    def test_a_stop_during_the_slow_tun0_wait_starts_no_client(self, _sleep):
        h = Harness("run")
        h.start_session()
        m = h.manager
        checks = []

        def vpn_check():
            checks.append(1)
            if len(checks) == 4:  # the check just before the re-issued client start
                m.disconnect(headset_present=False)

        h.adb.on_vpn_check = vpn_check
        with patch.object(TetherManager, "_port_open", return_value=True):
            m._attempt_reconnect("quest-serial")

        self.assertNotIn("start_gnirehtet_client", h.adb.calls)
        self.assertEqual(len(checks), 4, "the wait ends at the stop")
        self.assertEqual(m.stats.state, TetherState.IDLE)


if __name__ == "__main__":
    unittest.main()
