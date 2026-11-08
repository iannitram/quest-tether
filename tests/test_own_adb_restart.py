"""The core's own adb-server restart (#127) must not look like a replug or an unplug.

A fake adb whose device list goes empty while kill-server runs, and the real
_monitor_step and qtetherd callbacks. No adb, no headset: python3 -m unittest discover -s tests
"""

import os
import sys
import subprocess
import time
import unittest
from unittest import mock
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import adb_manager  # noqa: E402
import qtetherd  # noqa: E402
from tether_manager import TetherState  # noqa: E402

SERIAL = "1WMHH111111111"
LISTED = (
    "List of devices attached\n"
    f"{SERIAL}\tdevice product:hollywood model:Quest_3 device:hollywood transport_id:1\n"
)


class FakeAdbServer:
    """Answers the adb calls the monitor and restart make. The device list is empty
    while the server is down (kill-server to start-server), as on a real restart."""

    def __init__(self):
        self.listed = True
        self.on_kill = None  # runs inside kill-server, after the list has emptied

    def run_adb(self, *args, timeout=10):
        if args == ("kill-server",):
            self.listed = False
            if self.on_kill:
                self.on_kill()
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ("start-server",):
            self.listed = True
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("devices", "-l"):
            return subprocess.CompletedProcess(args, 0, LISTED if self.listed else "List of devices attached\n", "")
        return subprocess.CompletedProcess(args, 1, "", "unexpected")


def make_adb(server):
    platform = Mock()
    platform.no_window_flags.return_value = 0
    adb = adb_manager.ADBManager(adb_path="adb", platform=platform)
    adb.run_adb = server.run_adb
    return adb


class OwnRestartIgnoresMonitor(unittest.TestCase):

    def setUp(self):
        self.sleep = mock.patch("adb_manager.time.sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.server = FakeAdbServer()
        self.adb = make_adb(self.server)
        self.events = []
        self.adb._on_device_connected = lambda info: self.events.append(("connected", info["serial"]))
        self.adb._on_device_disconnected = lambda: self.events.append(("disconnected", None))
        self.adb._monitor_step()   # the headset is on USB before the restart
        self.assertEqual(self.events, [("connected", SERIAL)])
        self.events.clear()

    def test_poll_during_own_restart_fires_nothing(self):
        self.server.on_kill = self.adb._monitor_step   # sees the empty list mid-restart

        self.adb.restart_adb_server()

        self.assertEqual(self.events, [])

    def test_headset_back_after_own_restart_fires_nothing(self):
        self.server.on_kill = self.adb._monitor_step

        self.adb.restart_adb_server()
        self.adb._monitor_step()   # listed again, same serial, inside the quiet window

        self.assertEqual(self.events, [])

    def test_callbacks_do_not_note_a_replug_after_own_restart(self):
        tether = Mock()
        tether.auto_reconnect = True
        tether.stats.state = TetherState.CONNECTED
        self.adb._on_device_connected, self.adb._on_device_disconnected = qtetherd._monitor_callbacks(tether)
        self.server.on_kill = self.adb._monitor_step

        self.adb.restart_adb_server()
        self.adb._monitor_step()

        tether.note_replug.assert_not_called()
        tether.note_usb_event.assert_not_called()
        tether.connect.assert_not_called()

    def test_quiet_window_is_about_five_seconds(self):
        self.adb.restart_adb_server()
        self.server.listed = False

        with mock.patch("adb_manager.time.monotonic", return_value=time.monotonic() + 4.0):
            self.adb._monitor_step()
        self.assertEqual(self.events, [])

        with mock.patch("adb_manager.time.monotonic", return_value=time.monotonic() + 6.0):
            self.adb._monitor_step()
        self.assertEqual(self.events, [("disconnected", None)])

    def test_real_unplug_and_replug_after_the_window_still_fire(self):
        self.adb.restart_adb_server()
        self.server.listed = False

        with mock.patch("adb_manager.time.monotonic", return_value=time.monotonic() + 6.0):
            self.adb._monitor_step()
            self.server.listed = True
            self.adb._monitor_step()

        self.assertEqual(self.events, [("disconnected", None), ("connected", SERIAL)])


if __name__ == "__main__":
    unittest.main()
