"""Compound secret labels must still be redacted (review of #41)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crash_scrub import scrub  # noqa: E402


class CompoundLabels(unittest.TestCase):
    def test_label_inside_a_longer_name_is_redacted(self):
        self.assertEqual(scrub("wifi_password=hunter2", "", "", ()), "wifi_password=<redacted>")
        self.assertEqual(scrub("access_token: abc.def", "", "", ()), "access_token=<redacted>")
        self.assertEqual(scrub("mypassword=abc", "", "", ()), "mypassword=<redacted>")


if __name__ == "__main__":
    unittest.main()
