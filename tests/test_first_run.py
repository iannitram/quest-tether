"""First-run tool check names what is missing (temp folders only): python -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, ".")
from first_run import missing_tools  # noqa: E402

WIN = ["adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "gnirehtet.jar", "gnirehtet.apk"]
LIN = ["adb", "gnirehtet", "gnirehtet.apk"]


def nothing(name):
    return None


def everything(name):
    return "/usr/bin/" + name


class Missing(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = t.name

    def put(self, *names):
        for n in names:
            open(os.path.join(self.dir, n), "w").close()

    def test_windows_complete_is_empty(self):
        self.put(*WIN)
        self.assertEqual(missing_tools("windows", self.dir, everything), [])

    def test_windows_empty_folder_names_every_piece_in_order(self):
        self.assertEqual(missing_tools("windows", self.dir, nothing),
                         ["%s is missing from %s" % (n, self.dir) for n in WIN]
                         + ["Java was not found on PATH (the Windows relay needs it)"])

    def test_windows_one_missing_piece(self):
        self.put(*[n for n in WIN if n != "gnirehtet.apk"])
        self.assertEqual(missing_tools("windows", self.dir, everything), ["gnirehtet.apk is missing from " + self.dir])

    def test_windows_java_missing_only(self):
        self.put(*WIN)
        self.assertEqual(missing_tools("windows", self.dir, lambda n: None if n == "java" else "/x/" + n),
                         ["Java was not found on PATH (the Windows relay needs it)"])

    def test_adb_on_path_counts_as_present(self):
        self.put(*[n for n in WIN if n != "adb.exe"])
        self.assertEqual(missing_tools("windows", self.dir, everything), [])

    def test_linux_complete_needs_no_java(self):
        self.put(*LIN)
        self.assertEqual(missing_tools("linux", self.dir, nothing), [])

    def test_linux_empty_folder(self):
        self.assertEqual(missing_tools("linux", self.dir, nothing), ["%s is missing from %s" % (n, self.dir) for n in LIN])

    def test_linux_adb_on_path_counts_as_present(self):
        self.put("gnirehtet", "gnirehtet.apk")
        self.assertEqual(missing_tools("linux", self.dir, lambda n: "/usr/bin/adb" if n == "adb" else None), [])

    def test_unknown_platform_is_an_error(self):
        with self.assertRaises(ValueError):
            missing_tools("plan9", self.dir, nothing)


if __name__ == "__main__":
    unittest.main()
