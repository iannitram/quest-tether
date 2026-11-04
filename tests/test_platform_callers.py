"""Caller-level tests: TetherManager goes through the platform for process control."""

import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tether_manager import TetherManager


class FakePlatform:
    name = "fake"

    def __init__(self, data_dir):
        self._data_dir = data_dir
        self.stale = []
        self.killed = []
        self.killed_trees = []
        self.ssid = None
        self.wifi = None

    def data_dir(self):
        return self._data_dir

    def tool_path(self, name, base_dirs):
        return None

    def no_window_flags(self):
        return 0

    def stale_relay_pids(self, port):
        return list(self.stale)

    def kill(self, pid):
        self.killed.append(pid)

    def kill_tree(self, pid):
        self.killed_trees.append(pid)

    def pc_ssid(self):
        return self.ssid

    def pc_wifi(self):
        return self.wifi


class PlatformCallerTests(unittest.TestCase):
    def make_manager(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        platform = FakePlatform(tmp.name)
        adb = Mock()
        adb.adb_path = "adb"
        manager = TetherManager(adb, gnirehtet_path="fake.jar", platform=platform)
        return manager, platform

    def test_kill_stale_process_skips_own_pid_and_kills_the_rest(self):
        manager, platform = self.make_manager()
        platform.stale = [111, 222]
        manager._process = Mock(pid=111)

        manager._kill_stale_process()

        self.assertEqual(platform.killed, [222])

    def test_kill_process_escalation_calls_platform_kill(self):
        manager, platform = self.make_manager()
        process = Mock(pid=123)
        process.wait.side_effect = [
            subprocess.TimeoutExpired("gnirehtet", 3),
            subprocess.TimeoutExpired("gnirehtet", 2),
        ]
        manager._process = process

        manager._kill_process()

        self.assertEqual(platform.killed, [123])
        self.assertIsNone(manager._process)

    def test_force_kill_calls_platform_kill_tree(self):
        manager, platform = self.make_manager()
        manager._process = Mock(pid=321)

        with patch("tether_manager.time.sleep"):
            manager.force_kill()

        self.assertEqual(platform.killed_trees, [321])
        self.assertIsNone(manager._process)


if __name__ == "__main__":
    unittest.main()
