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


class Serve(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.patches = [
            patch("qtetherd.crash_report.data_dir", return_value=self.dir),
            patch("qtetherd.crash_report.install"),
            patch("qtetherd.logging.basicConfig"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

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
        with (
            patch("qtetherd.ADBManager", return_value=Mock()),
            patch("qtetherd.TetherManager", return_value=Mock()),
            patch("qtetherd.make_server", side_effect=OSError("in use")),
            redirect_stderr(err),
        ):
            with self.assertRaises(SystemExit) as raised:
                qtetherd.serve()

        self.assertEqual(raised.exception.code, 1)
        message = err.getvalue().strip().splitlines()[-1]
        self.assertEqual(message, "qtetherd: port 31417 is already in use")

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
        callbacks["on_connected"]({"serial": "HEADSET", "status": "device"})
        tether.connect.assert_called_once_with("HEADSET")
        callbacks["on_disconnected"]()
        tether.disconnect.assert_called_once_with()

    def test_auto_reconnect_off_does_not_start_usb_monitoring(self):
        adb, _ = self._serve(False)

        adb.start_monitoring.assert_not_called()


if __name__ == "__main__":
    unittest.main()
