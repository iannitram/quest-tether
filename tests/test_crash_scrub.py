"""Crash report scrubber: planted secrets must not survive (no network): python -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crash_scrub import scrub  # noqa: E402

HOME = "/home/someone"
HOST = "example-host"


def s(text, extra=()):
    return scrub(text, HOME, HOST, tuple(extra))


class Scrub(unittest.TestCase):
    def test_home_path_becomes_tilde(self):
        self.assertEqual(s('File "/home/someone/app/main.py", line 3'), 'File "~/app/main.py", line 3')

    def test_windows_home_in_either_slash_style(self):
        self.assertEqual(scrub(r"C:\Users\someone\app\x.py", r"C:\Users\someone", HOST, ()), r"~\app\x.py")
        self.assertEqual(scrub("C:/Users/someone/app/x.py", r"C:\Users\someone", HOST, ()), "~/app/x.py")

    def test_mac_addresses(self):
        self.assertEqual(s("wlan aa:bb:cc:dd:ee:ff up; BSSID 00-1A-2B-3C-4D-5E"), "wlan <mac> up; BSSID <mac>")

    def test_hostname_any_case(self):
        self.assertEqual(s("on Example-Host and example-host"), "on <host> and <host>")

    def test_password_psk_token_values(self):
        self.assertEqual(s("password=hunter2 next"), "password=<redacted> next")
        self.assertEqual(s("psk: example-pass"), "psk=<redacted>")
        self.assertEqual(s("Token : abc.def-123"), "Token=<redacted>")
        self.assertEqual(s("api_key=K1 secret=S2"), "api_key=<redacted> secret=<redacted>")

    def test_serial_and_adb_dash_s(self):
        self.assertEqual(s("serial: 1WMHH0000"), "serial=<redacted>")
        self.assertEqual(s("adb -s 1WMHH0000 shell ls"), "adb -s <serial> shell ls")

    def test_extra_exact_strings(self):
        self.assertEqual(s("joined MyNet twice: MyNet", extra=("MyNet",)), "joined <redacted> twice: <redacted>")

    def test_the_planted_set_is_all_gone(self):
        text = ("user /home/someone ran on example-host mac aa:bb:cc:dd:ee:ff "
                "password=hunter2 -s 1WMHH0000")
        out = s(text)
        for secret in ("someone", "example-host", "aa:bb", "hunter2", "1WMHH0000"):
            self.assertNotIn(secret, out)

    def test_plain_text_is_unchanged(self):
        text = "Traceback (most recent call last):\n  ValueError: bad value 42"
        self.assertEqual(s(text), text)

    def test_empty_home_or_host_does_nothing_odd(self):
        self.assertEqual(scrub("a/b c", "", "", ()), "a/b c")
        self.assertEqual(scrub("a/b c", "/", "", ()), "a/b c")

    def test_scrubbing_twice_changes_nothing_more(self):
        once = s("password=hunter2 /home/someone aa:bb:cc:dd:ee:ff")
        self.assertEqual(s(once), once)


if __name__ == "__main__":
    unittest.main()
