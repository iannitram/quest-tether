"""Crash report name, fields and zip (no network, no files): python -m unittest discover -s tests"""
import io
import json
import os
import sys
import unittest
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crash_report import build_report, last_lines, make_zip, report_filename  # noqa: E402

NOW = datetime(2026, 10, 31, 12, 34, 56)
ARGS = dict(app_version="0.9.0", commit="abc1234", os_name="Linux 6.1", kernel="6.1.0",
            python_version="3.12.1", exc_type="ValueError", message="bad value",
            traceback_text="Traceback...\nValueError: bad value", now=NOW, install_id="id-1")


class Filename(unittest.TestCase):
    def test_name_format(self):
        self.assertEqual(report_filename("linux", NOW, "0a1b2c3d"), "quest-tether-linux-20261031T123456Z-0a1b2c3d.zip")
        self.assertEqual(report_filename("windows", NOW, "ffffffff"), "quest-tether-windows-20261031T123456Z-ffffffff.zip")

    def test_random_part_must_be_8_lowercase_hex(self):
        for bad in ("", "0a1b2c3", "0a1b2c3d4", "0A1B2C3D", "zzzzzzzz"):
            with self.assertRaises(ValueError):
                report_filename("linux", NOW, bad)


class Report(unittest.TestCase):
    def test_fields(self):
        self.assertEqual(build_report(**ARGS), {
            "app": "quest-tether", "version": "0.9.0", "commit": "abc1234", "os": "Linux 6.1",
            "kernel": "6.1.0", "python": "3.12.1", "exception_type": "ValueError", "message": "bad value",
            "traceback": "Traceback...\nValueError: bad value", "timestamp": "2026-10-31T12:34:56Z",
            "install_id": "id-1"})


class LastLines(unittest.TestCase):
    def test_keeps_the_last_n(self):
        text = "\n".join(str(i) for i in range(1, 601))
        out = last_lines(text, 500).splitlines()
        self.assertEqual((len(out), out[0], out[-1]), (500, "101", "600"))

    def test_default_is_500(self):
        text = "\n".join(str(i) for i in range(1, 601))
        self.assertEqual(len(last_lines(text).splitlines()), 500)

    def test_short_text_is_unchanged(self):
        self.assertEqual(last_lines("a\nb", 500), "a\nb")
        self.assertEqual(last_lines("", 500), "")


class Zip(unittest.TestCase):
    def open(self, report, log):
        return zipfile.ZipFile(io.BytesIO(make_zip(report, log)))

    def test_holds_exactly_report_json_and_log_txt(self):
        z = self.open(build_report(**ARGS), "line 1\nline 2")
        self.assertEqual(sorted(z.namelist()), ["log.txt", "report.json"])

    def test_report_json_round_trips(self):
        report = build_report(**ARGS)
        z = self.open(report, "x")
        self.assertEqual(json.loads(z.read("report.json")), report)

    def test_log_is_cut_to_the_last_500_lines(self):
        log = "\n".join(str(i) for i in range(1, 701))
        lines = self.open(build_report(**ARGS), log).read("log.txt").decode().splitlines()
        self.assertEqual((len(lines), lines[0], lines[-1]), (500, "201", "700"))


if __name__ == "__main__":
    unittest.main()
