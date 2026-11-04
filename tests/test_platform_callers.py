"""Caller-level tests: TetherManager goes through the platform for process control."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tether_manager import TetherManager
from qt_platform.linux import LinuxPlatform
from qt_platform.windows import WindowsPlatform


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

    def relay_candidates(self):
        return [("fake.jar", True)]

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


class RelayLookupTests(unittest.TestCase):
    def make_manager(self, platform, root):
        with (
            patch.object(platform, "data_dir", return_value=root),
            patch.object(platform, "tool_path", return_value=None),
            patch("tether_manager.__file__", os.path.join(root, "tether_manager.py")),
        ):
            return TetherManager(Mock(), platform=platform)

    def test_linux_finds_the_native_relay_and_needs_no_java(self):
        with tempfile.TemporaryDirectory() as root:
            tools = os.path.join(root, "tools")
            os.makedirs(tools)
            relay = os.path.join(tools, "gnirehtet")
            open(relay, "w").close()
            os.chmod(relay, 0o755)

            with patch("tether_manager.subprocess.run") as run:
                manager = self.make_manager(LinuxPlatform(), root)
                self.assertEqual(manager.gnirehtet_path, relay)
                self.assertTrue(manager.is_relay_available())
                run.assert_not_called()

    @unittest.skipIf(sys.platform == "win32", "X_OK has no meaning on Windows")
    def test_linux_ignores_a_non_executable_relay(self):
        with tempfile.TemporaryDirectory() as root:
            tools = os.path.join(root, "tools")
            os.makedirs(tools)
            relay = os.path.join(tools, "gnirehtet")
            open(relay, "w").close()
            os.chmod(relay, 0o644)

            manager = self.make_manager(LinuxPlatform(), root)

            self.assertEqual(manager.gnirehtet_path, "")
            self.assertFalse(manager.is_relay_available())

    def _windows_manager_with_jar(self, root):
        tools = os.path.join(root, "tools")
        os.makedirs(tools)
        jar = os.path.join(tools, "gnirehtet.jar")
        open(jar, "wb").close()
        return self.make_manager(WindowsPlatform(), root), jar

    def test_windows_jar_with_java_is_available(self):
        with tempfile.TemporaryDirectory() as root:
            manager, jar = self._windows_manager_with_jar(root)

            with patch(
                "tether_manager.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ):
                self.assertEqual(manager.gnirehtet_path, jar)
                self.assertTrue(manager.is_relay_available())

    def test_windows_jar_without_java_is_not_available(self):
        with tempfile.TemporaryDirectory() as root:
            manager, jar = self._windows_manager_with_jar(root)

            with patch("tether_manager.subprocess.run", side_effect=FileNotFoundError):
                self.assertEqual(manager.gnirehtet_path, jar)
                self.assertFalse(manager.is_relay_available())

    def test_windows_does_not_accept_an_extensionless_relay(self):
        with tempfile.TemporaryDirectory() as root:
            tools = os.path.join(root, "tools")
            os.makedirs(tools)
            open(os.path.join(tools, "gnirehtet"), "w").close()

            manager = self.make_manager(WindowsPlatform(), root)

            self.assertEqual(manager.gnirehtet_path, "")
            self.assertFalse(manager.is_relay_available())


if __name__ == "__main__":
    unittest.main()
