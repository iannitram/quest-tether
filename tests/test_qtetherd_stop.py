"""qtetherd SIGTERM stop: the normal disconnect, capped, then serve() returns (no headset, no signals sent): python -m unittest discover -s tests"""

import os
import signal
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qtetherd  # noqa: E402
from test_systemd_unit import unit  # noqa: E402


class FakeTether:
    """Records disconnect calls; can block until released to test the cap."""

    def __init__(self, block=None, error=None):
        self.calls = []
        self.block = block
        self.error = error
        self.done = threading.Event()

    def disconnect(self, headset_present=True):
        self.calls.append(headset_present)
        if self.block is not None:
            self.block.wait(5)
        if self.error is not None:
            raise self.error
        self.done.set()


class StopTether(unittest.TestCase):
    def test_runs_the_normal_disconnect_with_the_headset_when_it_is_on_usb(self):
        tether = FakeTether()
        self.assertTrue(qtetherd.stop_tether(tether, lambda: "SERIAL"))
        self.assertEqual(tether.calls, [True])

    def test_skips_headset_steps_when_no_headset_is_on_usb(self):
        tether = FakeTether()
        self.assertTrue(qtetherd.stop_tether(tether, lambda: None))
        self.assertEqual(tether.calls, [False])

    def test_holds_the_shared_lock_while_disconnecting(self):
        seen = []
        tether = FakeTether()
        original = tether.disconnect

        def disconnect(headset_present=True):
            seen.append(qtetherd._TETHER_LOCK.locked())
            original(headset_present=headset_present)

        tether.disconnect = disconnect
        qtetherd.stop_tether(tether, lambda: "SERIAL")
        self.assertEqual(seen, [True])

    def test_gives_up_after_the_cap_and_does_not_hang(self):
        release = threading.Event()
        tether = FakeTether(block=release)
        try:
            with self.assertLogs("qtetherd", level="WARNING"):
                self.assertFalse(qtetherd.stop_tether(tether, lambda: "SERIAL", cap=0.2))
        finally:
            release.set()
        # The disconnect was still started; only the wait was capped.
        self.assertEqual(tether.calls, [True])
        self.assertTrue(tether.done.wait(5))

    def test_a_failing_disconnect_is_logged_and_still_counts_as_finished(self):
        tether = FakeTether(error=RuntimeError("adb gone"))
        with self.assertLogs("qtetherd", level="ERROR") as logs:
            self.assertTrue(qtetherd.stop_tether(tether, lambda: "SERIAL"))
        self.assertIn("disconnect on stop failed", logs.output[0])

    def test_the_default_cap_is_short_and_inside_the_unit_timeout(self):
        self.assertEqual(qtetherd.STOP_CAP_SECONDS, 20.0)


class StopWhileAnotherRequestHoldsTheLock(unittest.TestCase):
    """A connect holds the shared lock for tens of seconds; SIGTERM must still disconnect (#110)."""

    def _hold_lock(self):
        lock = threading.Lock()
        held = threading.Event()
        release = threading.Event()

        def hold():
            with lock:
                held.set()
                release.wait(10)

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        self.assertTrue(held.wait(5))
        self.addCleanup(thread.join, 5)
        self.addCleanup(release.set)
        return lock, release

    def test_a_held_lock_does_not_stop_the_disconnect_from_running(self):
        lock, _release = self._hold_lock()
        tether = FakeTether()
        with patch.object(qtetherd, "STOP_LOCK_WAIT_SECONDS", 0.1):
            result = qtetherd.stop_tether(tether, lambda: "SERIAL", lock=lock, cap=5.0)
        self.assertTrue(result)
        self.assertEqual(tether.calls, [True])

    def test_a_held_lock_is_left_to_its_owner(self):
        lock, _release = self._hold_lock()
        tether = FakeTether()
        with patch.object(qtetherd, "STOP_LOCK_WAIT_SECONDS", 0.1):
            qtetherd.stop_tether(tether, lambda: "SERIAL", lock=lock, cap=5.0)
        self.assertTrue(lock.locked(), "stop released a lock it did not hold")

    def test_a_lock_released_within_the_wait_is_taken_before_disconnecting(self):
        lock, release = self._hold_lock()
        seen = []
        tether = FakeTether()
        original = tether.disconnect

        def disconnect(headset_present=True):
            seen.append(lock.locked())
            original(headset_present=headset_present)

        tether.disconnect = disconnect
        threading.Timer(0.1, release.set).start()
        with patch.object(qtetherd, "STOP_LOCK_WAIT_SECONDS", 3.0):
            self.assertTrue(qtetherd.stop_tether(tether, lambda: "SERIAL", lock=lock, cap=5.0))
        self.assertEqual(tether.calls, [True])
        self.assertEqual(seen, [True])

    def test_the_lock_wait_is_shorter_than_the_stop_cap(self):
        self.assertLess(qtetherd.STOP_LOCK_WAIT_SECONDS, qtetherd.STOP_CAP_SECONDS)


class StopTimeoutsAgree(unittest.TestCase):
    """The caps nest: disconnect cap < unit TimeoutStopSec < the `service stop` client wait (#110)."""

    def test_the_stop_cap_is_below_the_unit_timeout(self):
        self.assertLess(qtetherd.STOP_CAP_SECONDS, float(unit()["Service"]["TimeoutStopSec"]))

    def test_service_stop_waits_longer_than_the_unit_stop_timeout(self):
        from qt_platform import linux

        done = subprocess.CompletedProcess([], 0, "inactive\n", "")
        with patch.object(linux.subprocess, "run", return_value=done) as run:
            linux.LinuxPlatform().service("stop")
        stop_call = run.call_args_list[0]
        self.assertEqual(stop_call.args[0][-2:], ["stop", linux.SERVICE_UNIT])
        self.assertGreater(
            stop_call.kwargs["timeout"], float(unit()["Service"]["TimeoutStopSec"])
        )


class SigtermHandler(unittest.TestCase):
    def _install(self, tether, server, find_serial=lambda: "SERIAL", cap=5.0):
        with patch("qtetherd.signal.signal") as install:
            qtetherd._install_stop_handler(tether, find_serial, server, cap=cap)
        install.assert_called_once()
        self.assertEqual(install.call_args.args[0], signal.SIGTERM)
        return install.call_args.args[1]

    def test_sigterm_runs_the_disconnect_then_shuts_the_server_down(self):
        tether = FakeTether()
        server = Mock()
        shut = threading.Event()
        server.shutdown.side_effect = shut.set
        handler = self._install(tether, server)

        handler(signal.SIGTERM, None)

        self.assertEqual(tether.calls, [True])
        self.assertTrue(shut.wait(5), "server.shutdown() was not called")

    def test_sigterm_with_no_headset_still_stops_the_relay(self):
        tether = FakeTether()
        server = Mock()
        shut = threading.Event()
        server.shutdown.side_effect = shut.set
        handler = self._install(tether, server, find_serial=lambda: None)

        handler(signal.SIGTERM, None)

        self.assertEqual(tether.calls, [False])
        self.assertTrue(shut.wait(5))

    def test_a_second_sigterm_does_not_start_a_second_disconnect(self):
        tether = FakeTether()
        server = Mock()
        shut = threading.Event()
        server.shutdown.side_effect = shut.set
        handler = self._install(tether, server)

        handler(signal.SIGTERM, None)
        handler(signal.SIGTERM, None)

        self.assertEqual(tether.calls, [True])
        self.assertTrue(shut.wait(5))

    def test_shutdown_happens_even_when_the_disconnect_runs_past_the_cap(self):
        release = threading.Event()
        tether = FakeTether(block=release)
        server = Mock()
        shut = threading.Event()
        server.shutdown.side_effect = shut.set
        handler = self._install(tether, server, cap=0.2)
        try:
            handler(signal.SIGTERM, None)
            self.assertTrue(shut.wait(5), "a stuck disconnect held the service")
        finally:
            release.set()


class ServeWiring(unittest.TestCase):
    def _run_serve(self, **kwargs):
        adb = Mock()
        adb.find_serial = Mock(return_value="SERIAL")
        tether = Mock()
        tether.auto_reconnect = False
        server = Mock()
        with (
            patch("qtetherd.ADBManager", return_value=adb),
            patch("qtetherd.TetherManager", return_value=tether),
            patch("qtetherd.make_server", return_value=server),
            patch("qtetherd.signal.signal") as install,
        ):
            qtetherd.serve(**kwargs)
        return install, server

    def test_serve_leaves_sigterm_alone_by_default(self):
        install, server = self._run_serve()
        install.assert_not_called()
        server.serve_forever.assert_called_once_with()

    def test_serve_with_stop_on_sigterm_installs_the_handler_before_serving(self):
        install, server = self._run_serve(stop_on_sigterm=True)
        install.assert_called_once()
        self.assertEqual(install.call_args.args[0], signal.SIGTERM)
        server.serve_forever.assert_called_once_with()

    def test_main_serves_with_the_sigterm_stop(self):
        with (
            patch("qtetherd.crash_report.install") as crash_install,
            patch("qtetherd.setup_logging", return_value="x.log") as setup,
            patch("qtetherd.serve") as serve,
        ):
            qtetherd.main()
        setup.assert_called_once_with()
        crash_install.assert_called_once_with(log_path="x.log")
        serve.assert_called_once_with(stop_on_sigterm=True)


if __name__ == "__main__":
    unittest.main()
