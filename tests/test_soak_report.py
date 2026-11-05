"""soak_report: pure summary counters for a quest-tether soak-run log (#3): python -m unittest tests.test_soak_report -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import soak_report  # noqa: E402

SAMPLE = "\n".join([
    "2026-11-04 16:23:13,074 [INFO] Tethering active!",
    "2026-11-04 16:23:23,114 [INFO] gnirehtet process died!",
    "2026-11-04 16:23:23,114 [INFO] Reconnect attempt 1/10",
    "2026-11-04 16:23:28,159 [INFO] Reconnected!",
    "16:30:00 [INFO] client start refused: Error type 3",
    "garbage line with no time",
    "16:40:05 [INFO] Disconnected",
])

EMPTY = {"connects": 0, "drops": 0, "reconnects": 0, "refusals": 0,
         "disconnects": 0, "first": None, "last": None, "lines": 0}


class SoakSummary(unittest.TestCase):
    def test_empty_text(self):
        self.assertEqual(soak_report.summarize(""), EMPTY)

    def test_none_text(self):
        self.assertEqual(soak_report.summarize(None), EMPTY)

    def test_sample(self):
        self.assertEqual(
            soak_report.summarize(SAMPLE),
            {"connects": 1, "drops": 1, "reconnects": 1, "refusals": 1,
             "disconnects": 1, "first": "16:23:13", "last": "16:40:05", "lines": 6})

    def test_disconnected_prefix_is_not_a_disconnect(self):
        self.assertEqual(
            soak_report.summarize("16:41:00 [INFO] Disconnected from Wi-Fi"),
            {"connects": 0, "drops": 0, "reconnects": 0, "refusals": 0,
             "disconnects": 0, "first": "16:41:00", "last": "16:41:00", "lines": 1})

    def test_two_tethering_active(self):
        text = "16:00:00 [INFO] Tethering active!\n16:05:00 [INFO] Tethering active!\n"
        self.assertEqual(soak_report.summarize(text)["connects"], 2)


class SoakSummaryFormat(unittest.TestCase):
    def test_sample(self):
        self.assertEqual(
            soak_report.format_summary(soak_report.summarize(SAMPLE)),
            "Log span: 16:23:13 to 16:40:05\n"
            "Connects: 1\n"
            "Drops: 1\n"
            "Reconnects: 1\n"
            "Refusals: 1\n"
            "Disconnects: 1\n"
            "Unrecovered drops: 0")

    def test_empty_text(self):
        text = soak_report.format_summary(soak_report.summarize(""))
        self.assertTrue(text.startswith("Log span: none\n"))
        self.assertTrue(text.endswith("Unrecovered drops: 0"))

    def test_two_drops(self):
        text = "10:00:00 [INFO] gnirehtet process died!\n10:05:00 [INFO] gnirehtet process died!"
        lines = soak_report.format_summary(soak_report.summarize(text)).splitlines()
        self.assertEqual(lines[-1], "Unrecovered drops: 2")
        self.assertEqual(lines[0], "Log span: 10:00:00 to 10:05:00")

    def test_reconnects_exceed_drops_is_not_negative(self):
        s = {"connects": 0, "drops": 0, "reconnects": 3, "refusals": 0,
             "disconnects": 0, "first": None, "last": None, "lines": 0}
        self.assertEqual(
            soak_report.format_summary(s).splitlines()[-1],
            "Unrecovered drops: 0")


if __name__ == "__main__":
    unittest.main()
