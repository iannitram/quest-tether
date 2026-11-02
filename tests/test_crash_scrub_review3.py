"""Escaped quotes in quoted values and line structure (review of #41)."""
import sys
import unittest

sys.path.insert(0, ".")
from crash_scrub import scrub  # noqa: E402


def s(text):
    return scrub(text, "/home/someone", "example-host", ())


class Review3(unittest.TestCase):
    def test_escaped_quote_inside_quoted_value(self):
        out = s('{"password": "hun\\"ter two", "x": 1}')
        self.assertNotIn("ter", out)
        self.assertNotIn("two", out)
        self.assertIn('"x": 1', out)
        self.assertNotIn("ter", s("{'token': 'hun\\'ter two'}"))

    def test_label_at_line_end_keeps_next_line(self):
        text = "password:\nnext line"
        self.assertEqual(s(text), text)
        self.assertEqual(s("password: x\nnext"), "password=<redacted>\nnext")

    def test_dash_s_at_line_end_keeps_next_line(self):
        text = "ls -s\nfoo"
        self.assertEqual(s(text), text)

    def test_tab_separator_still_redacted(self):
        self.assertEqual(s("token\t=\tabc"), "token=<redacted>")

    def test_idempotent_on_new_cases(self):
        for text in ('{"password": "hun\\"ter two"}', "password:\nnext", "psk:\tx y"):
            once = s(text)
            self.assertEqual(s(once), once)


if __name__ == "__main__":
    unittest.main()
