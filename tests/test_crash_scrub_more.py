"""JSON-style keys, plural labels and case-insensitive Windows homes (review of #41)."""
import sys
import unittest

sys.path.insert(0, ".")
from crash_scrub import scrub  # noqa: E402


def s(text, home=""):
    return scrub(text, home, "", ())


class MoreLeaks(unittest.TestCase):
    def test_json_quoted_keys(self):
        self.assertNotIn("hunter2", s('{"password": "hunter2", "x": 1}'))
        self.assertNotIn("two", s('{"password": "one two"}'))
        self.assertNotIn("hunter2", s("{'token':'hunter2'}"))
        self.assertIn('"x": 1', s('{"password": "hunter2", "x": 1}'))

    def test_plural_label(self):
        self.assertEqual(s("passwords: x"), "passwords=<redacted>")

    def test_windows_home_case_insensitive(self):
        home = r"C:\Users\someone"
        self.assertEqual(s(r"C:\USERS\SOMEONE\app", home), r"~\app")
        self.assertEqual(s("c:/users/Someone/app", home), "~/app")

    def test_idempotent(self):
        once = s('{"password": "hunter2"} passwords: x', r"C:\Users\someone")
        self.assertEqual(s(once, r"C:\Users\someone"), once)


if __name__ == "__main__":
    unittest.main()
