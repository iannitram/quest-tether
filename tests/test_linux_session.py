"""LinuxPlatform single instance and start-at-login (temp folders only): python -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qt_platform import linux  # noqa: E402
from qt_platform.linux import LinuxPlatform  # noqa: E402


class Autostart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.path = self.tmp.name + "/autostart/quest-tether.desktop"

    def test_off_by_default(self):
        self.assertFalse(LinuxPlatform().autostart())
        self.assertFalse(os.path.exists(self.path))

    def test_turning_on_writes_the_desktop_file(self):
        self.assertTrue(LinuxPlatform().set_autostart(True))
        self.assertTrue(LinuxPlatform().autostart())
        text = open(self.path).read()
        self.assertTrue(text.startswith("[Desktop Entry]\nType=Application\nName=Quest Tether\nExec="))
        exec_line = [x for x in text.splitlines() if x.startswith("Exec=")][0]
        self.assertTrue(exec_line.endswith(" --minimized"))
        self.assertIn(sys.executable, exec_line)

    def test_turning_off_removes_it_and_is_repeatable(self):
        LinuxPlatform().set_autostart(True)
        self.assertFalse(LinuxPlatform().set_autostart(False))
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(LinuxPlatform().set_autostart(False))


@unittest.skipUnless(sys.platform.startswith("linux"), "flock needs Linux")
class SingleInstance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.release)

    @staticmethod
    def release():
        h = getattr(linux, "_lock_handle", None)
        if h is not None:
            h.close()
            linux._lock_handle = None

    def test_first_copy_wins_and_the_lock_file_is_in_the_data_dir(self):
        self.assertTrue(LinuxPlatform().single_instance("QuestTetherSingleInstance"))
        self.assertTrue(os.path.exists(self.tmp.name + "/quest-tether/QuestTetherSingleInstance.lock"))

    def test_second_copy_is_refused_and_keeps_the_first_lock(self):
        self.assertTrue(LinuxPlatform().single_instance("Same"))
        first = linux._lock_handle
        self.assertFalse(LinuxPlatform().single_instance("Same"))
        self.assertIs(linux._lock_handle, first)

    def test_different_names_do_not_clash(self):
        self.assertTrue(LinuxPlatform().single_instance("One"))
        self.assertTrue(LinuxPlatform().single_instance("Two"))

    def test_after_release_a_new_copy_may_start(self):
        self.assertTrue(LinuxPlatform().single_instance("Again"))
        self.release()
        self.assertTrue(LinuxPlatform().single_instance("Again"))


if __name__ == "__main__":
    unittest.main()
