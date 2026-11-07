"""qtetherd.serve wiring, with the core and server mocked (no headset, no sockets): python -m unittest discover -s tests"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qtetherd  # noqa: E402
from tether_manager import TetherManager, TetherState  # noqa: E402


class Serve(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.patches = [
            patch("qtetherd.crash_report.data_dir", return_value=self.dir),
            # No real settings.json is read or written (#117).
            patch("qtetherd._settings_path", return_value=os.path.join(self.dir, "settings.json")),
            patch("qtetherd.crash_report.install"),
            patch("qtetherd.logging.basicConfig"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_serve_leaves_logging_alone(self):
        """serve() must not open a log file or configure the root logger (#85)."""
        with (
            patch("qtetherd.ADBManager"),
            patch("qtetherd.TetherManager"),
            patch("qtetherd.make_server"),
            patch("qtetherd.logging.FileHandler") as file_handler,
        ):
            qtetherd.serve()
        file_handler.assert_not_called()
        qtetherd.logging.basicConfig.assert_not_called()
        qtetherd.crash_report.install.assert_not_called()

    def test_builds_one_core_and_serves_requests(self):
        adb = Mock()
        adb.find_serial = Mock(return_value="SERIAL")
        tether = Mock()
        server = Mock()

        with (
            patch("qtetherd.ADBManager", return_value=adb) as make_adb,
            patch("qtetherd.TetherManager", return_value=tether) as make_tether,
            patch("qtetherd.make_server", return_value=server) as make_server,
            patch("qtetherd.handle_request", return_value={"ok": True}) as handle,
        ):
            qtetherd.serve()
            handler = make_server.call_args.args[0]
            self.assertEqual(handler({"cmd": "status"}), {"ok": True})

        make_adb.assert_called_once_with()
        make_tether.assert_called_once_with(adb)
        server.serve_forever.assert_called_once_with()
        handle.assert_called_once_with(tether, adb.find_serial, {"cmd": "status"})

    def test_port_taken_prints_one_line_and_exits_one(self):
        err = io.StringIO()
        adb = Mock()
        tether = Mock()
        tether.auto_reconnect = True
        with (
            patch("qtetherd.ADBManager", return_value=adb),
            patch("qtetherd.TetherManager", return_value=tether),
            patch("qtetherd.make_server", side_effect=OSError("in use")),
            redirect_stderr(err),
        ):
            with self.assertRaises(SystemExit) as raised:
                qtetherd.serve()

        self.assertEqual(raised.exception.code, 1)
        message = err.getvalue().strip().splitlines()[-1]
        self.assertEqual(message, "qtetherd: port 31417 is already in use")
        adb.start_monitoring.assert_not_called()

    def _serve(self, auto_reconnect):
        adb = Mock()
        adb.find_serial = Mock(return_value="SERIAL")
        tether = Mock()
        tether.auto_reconnect = auto_reconnect
        server = Mock()
        with (
            patch("qtetherd.ADBManager", return_value=adb),
            patch("qtetherd.TetherManager", return_value=tether),
            patch("qtetherd.make_server", return_value=server),
        ):
            qtetherd.serve()
        return adb, tether

    def test_auto_reconnect_starts_usb_monitoring_with_callbacks(self):
        adb, tether = self._serve(True)

        adb.start_monitoring.assert_called_once()
        callbacks = adb.start_monitoring.call_args.kwargs
        tether.stats.state = TetherState.IDLE
        callbacks["on_connected"]({"serial": "HEADSET", "status": "device"})
        tether.connect.assert_called_once_with("HEADSET")
        tether.stats.state = TetherState.CONNECTED
        callbacks["on_disconnected"]()
        tether.disconnect.assert_not_called()

    def test_auto_reconnect_off_does_not_start_usb_monitoring(self):
        adb, _ = self._serve(False)

        adb.start_monitoring.assert_not_called()

    def test_monitor_callback_starts_no_second_relay_while_connected(self):
        tether = TetherManager(Mock(), gnirehtet_path="fake.jar")
        tether._set_state(TetherState.CONNECTED)
        tether._spawn_relay = Mock()
        tether._kill_stale_process = Mock()
        on_connected, _ = qtetherd._monitor_callbacks(tether)

        on_connected({"serial": "HEADSET", "status": "device"})

        tether._spawn_relay.assert_not_called()
        tether._kill_stale_process.assert_not_called()

    def _real_tether(self, state):
        """A real core with a mock adb: any headset call shows up in adb.mock_calls."""
        adb = Mock()
        tether = TetherManager(adb, gnirehtet_path="fake.jar")
        tether._set_state(state)
        tether.stats.device_serial = "HEADSET"
        tether.stats.reconnect_count = 1
        tether._kill_process = Mock()
        tether._spawn_relay = Mock()
        tether._kill_stale_process = Mock()
        logs = []
        tether.set_callbacks(on_log=logs.append)
        return adb, tether, logs

    def test_monitor_callback_starts_no_second_relay_while_reconnecting(self):
        adb, tether, logs = self._real_tether(TetherState.RECONNECTING)
        on_connected, _ = qtetherd._monitor_callbacks(tether)

        on_connected({"serial": "HEADSET", "status": "device"})

        tether._spawn_relay.assert_not_called()
        tether._kill_stale_process.assert_not_called()
        self.assertEqual(adb.mock_calls, [])
        self.assertEqual(tether.stats.state, TetherState.RECONNECTING)
        self.assertEqual(tether.stats.reconnect_count, 1, "a replug is not a reconnect")
        self.assertIn("Headset back on USB while reconnecting — no new relay", logs)

    def test_unplug_while_connected_leaves_recovery_to_the_health_loop(self):
        adb, tether, logs = self._real_tether(TetherState.CONNECTED)
        _, on_disconnected = qtetherd._monitor_callbacks(tether)

        on_disconnected()

        self.assertEqual(adb.mock_calls, [], "no headset-side call on unplug")
        tether._kill_process.assert_not_called()
        self.assertEqual(tether.stats.state, TetherState.CONNECTED)
        self.assertEqual(tether.stats.reconnect_count, 1, "stats survive the unplug")
        self.assertIn("USB lost while connected — health monitor will reconnect", logs)

    def test_unplug_stops_an_orphan_relay_without_touching_the_headset(self):
        adb, tether, logs = self._real_tether(TetherState.ERROR)
        tether._process = Mock(pid=7)
        tether._process.poll.return_value = None
        _, on_disconnected = qtetherd._monitor_callbacks(tether)

        on_disconnected()

        self.assertEqual(adb.mock_calls, [])
        tether._kill_process.assert_called_once_with()
        self.assertEqual(tether.stats.state, TetherState.IDLE)
        self.assertIn("Headset absent — skipping headset-side cleanup", logs)

    def test_unplug_while_idle_does_nothing(self):
        adb, tether, _ = self._real_tether(TetherState.IDLE)
        tether._process = None
        _, on_disconnected = qtetherd._monitor_callbacks(tether)

        on_disconnected()

        self.assertEqual(adb.mock_calls, [])
        tether._kill_process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
