"""Platform selection (no headset needed): python -m unittest discover tests"""
import sys
import unittest

sys.path.insert(0, ".")
import qt_platform  # noqa: E402


SUPPORTED = sys.platform == "win32" or sys.platform.startswith("linux")


class Current(unittest.TestCase):
    @unittest.skipUnless(SUPPORTED, "Windows and Linux only")
    def test_picks_this_os(self):
        p = qt_platform.current()
        self.assertEqual(p.name, "windows" if sys.platform == "win32" else "linux")

    @unittest.skipIf(SUPPORTED, "only on an unsupported OS")
    def test_refuses_other_os(self):
        with self.assertRaises(RuntimeError):
            qt_platform.current()


if __name__ == "__main__":
    unittest.main()
