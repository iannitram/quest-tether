"""cli_format: pure text formatters for the CLI (#74): python -m unittest tests.test_cli_format -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import cli_format  # noqa: E402


class FormatRate(unittest.TestCase):
    def test_none(self):
        self.assertEqual(cli_format.format_rate(None), "-")

    def test_zero(self):
        self.assertEqual(cli_format.format_rate(0), "0 B/s")

    def test_bytes(self):
        self.assertEqual(cli_format.format_rate(999), "999 B/s")

    def test_kilobytes(self):
        self.assertEqual(cli_format.format_rate(1000), "1.0 kB/s")
        self.assertEqual(cli_format.format_rate(15360), "15.4 kB/s")

    def test_megabytes(self):
        self.assertEqual(cli_format.format_rate(2_500_000), "2.5 MB/s")


class FormatUptime(unittest.TestCase):
    def test_none(self):
        self.assertEqual(cli_format.format_uptime(None), "-")

    def test_negative(self):
        self.assertEqual(cli_format.format_uptime(-5), "-")

    def test_seconds(self):
        self.assertEqual(cli_format.format_uptime(0), "0s")
        self.assertEqual(cli_format.format_uptime(59), "59s")

    def test_minutes(self):
        self.assertEqual(cli_format.format_uptime(61), "1m 01s")

    def test_hours(self):
        self.assertEqual(cli_format.format_uptime(3600), "1h 00m")
        self.assertEqual(cli_format.format_uptime(3725.9), "1h 02m")


class FormatStatus(unittest.TestCase):
    def test_full(self):
        status = {"state": "connected", "rx_bps": 2_500_000, "tx_bps": 1000,
                  "uptime_s": 61, "battery_level": 80, "reconnects": 2,
                  "temp_c": 37.46, "zone": "green", "error": None}
        self.assertEqual(
            cli_format.format_status(status),
            "State:      connected\n"
            "Traffic:    down 2.5 MB/s, up 1.0 kB/s\n"
            "Uptime:     1m 01s\n"
            "Battery:    80%, 37.5 C (green)\n"
            "Reconnects: 2")

    def test_empty(self):
        self.assertEqual(
            cli_format.format_status({}),
            "State:      unknown\n"
            "Traffic:    down -, up -\n"
            "Uptime:     -\n"
            "Battery:    -\n"
            "Reconnects: 0")

    def test_error_without_zone(self):
        status = {"state": "error", "battery_level": 50, "temp_c": 40,
                  "error": "no headset found"}
        self.assertEqual(
            cli_format.format_status(status),
            "State:      error\n"
            "Traffic:    down -, up -\n"
            "Uptime:     -\n"
            "Battery:    50%, 40.0 C (unknown)\n"
            "Reconnects: 0\n"
            "Error:      no headset found")


class ExitCode(unittest.TestCase):
    def test_none(self):
        self.assertEqual(cli_format.exit_code(None), 1)

    def test_not_a_dict(self):
        self.assertEqual(cli_format.exit_code("x"), 1)

    def test_ok(self):
        self.assertEqual(cli_format.exit_code({"ok": True}), 0)
        self.assertEqual(cli_format.exit_code({"state": "connected"}), 0)

    def test_failure(self):
        self.assertEqual(
            cli_format.exit_code({"ok": False, "error": "no headset found"}), 1)

    def test_bad_usage(self):
        self.assertEqual(
            cli_format.exit_code({"ok": False, "error": "bad usage"}), 2)


if __name__ == "__main__":
    unittest.main()
