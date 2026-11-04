"""Crash wiring: scrub, zip, upload, limit, queue and retry (temp folders only)."""

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import crash_report  # noqa: E402

UTC = timezone.utc


class Clock:
    def __init__(self, value=None):
        self.value = value or datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeUploader:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def __call__(self, name, data):
        self.calls.append((name, data))
        return self.result


def crash(message="boom"):
    try:
        raise ValueError(message)
    except ValueError:
        return sys.exc_info()


class TempCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = folder.name
        self.clock = Clock()
        self.log_path = os.path.join(self.dir, "app.log")

    def reporter(self, uploader, *, enabled=None, log_path=None, rand_hex=None):
        return crash_report.Reporter(
            self.dir,
            enabled=enabled,
            base="https://drop.example.test",
            token="fake-token",
            uploader=uploader,
            clock=self.clock,
            log_path=log_path or self.log_path,
            hostname="example-host",
            home="/home/someone",
            rand_hex=rand_hex or (lambda: "0a1b2c3d"),
        )


class ReportTests(TempCase):
    def test_one_zip_named_and_filled_from_the_crash(self):
        uploader = FakeUploader()
        reporter = self.reporter(uploader)
        reporter.report(*crash("bad value"))
        reporter.wait()
        self.assertEqual(len(uploader.calls), 1)
        name, data = uploader.calls[0]
        self.assertEqual(
            name,
            f"quest-tether-{crash_report.platform_name()}-20261101T000000Z-0a1b2c3d.zip",
        )
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(sorted(archive.namelist()), ["log.txt", "report.json"])
            report = json.loads(archive.read("report.json"))
        self.assertEqual(report["app"], "quest-tether")
        self.assertEqual(report["exception_type"], "ValueError")
        self.assertEqual(report["message"], "bad value")
        self.assertIn("ValueError: bad value", report["traceback"])
        self.assertEqual(report["timestamp"], "2026-11-01T00:00:00Z")
        self.assertTrue(report["install_id"])
        self.assertIn("version", report)
        self.assertIn("commit", report)
        self.assertIn("os", report)
        self.assertIn("kernel", report)
        self.assertIn("python", report)

    def test_the_same_exception_is_one_report(self):
        uploader = FakeUploader()
        reporter = self.reporter(uploader)
        thrown = crash()
        reporter.report(*thrown)
        reporter.report(*thrown)
        reporter.wait()
        self.assertEqual(len(uploader.calls), 1)

    def test_planted_secrets_are_gone_from_report_and_log(self):
        with open(self.log_path, "w", encoding="utf-8") as stream:
            stream.write("password=hunter2 wlan aa:bb:cc:dd:ee:ff on example-host\n")
            stream.write("opened /home/someone/app/main.py\n")
        uploader = FakeUploader()
        reporter = self.reporter(uploader)
        reporter.report(
            *crash(
                "password=hunter2 on example-host aa:bb:cc:dd:ee:ff at /home/someone/app"
            )
        )
        reporter.wait()
        _, data = uploader.calls[0]
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            text = (
                archive.read("report.json").decode() + archive.read("log.txt").decode()
            )
        for secret in ("hunter2", "example-host", "aa:bb", "/home/someone", "someone"):
            self.assertNotIn(secret, text)

    def test_the_setting_off_sends_nothing(self):
        uploader = FakeUploader()
        reporter = self.reporter(uploader, enabled=False)
        reporter.report(*crash())
        reporter.wait()
        self.assertEqual(uploader.calls, [])

    def test_no_drop_config_is_a_quiet_no_op(self):
        uploader = FakeUploader()
        with mock.patch.object(crash_report, "drop_config", return_value=None):
            reporter = crash_report.Reporter(
                self.dir,
                uploader=uploader,
                clock=self.clock,
                log_path=self.log_path,
            )
        with self.assertLogs("crash_report", level="INFO") as logs:
            reporter.report(*crash())
        reporter.wait()
        self.assertEqual(uploader.calls, [])
        self.assertEqual(len(logs.records), 1)

    def test_at_most_five_uploads_a_day(self):
        uploader = FakeUploader()
        reporter = self.reporter(uploader)
        for i in range(6):
            self.clock.value = datetime(2026, 11, 1, 0, 0, i, tzinfo=UTC)
            reporter.report(*crash(f"crash {i}"))
        reporter.wait()
        self.assertEqual(len(uploader.calls), 5)

    def test_offline_report_queues_and_a_new_start_retries_it(self):
        offline = FakeUploader(result=False)
        reporter = self.reporter(offline)
        reporter.report(*crash())
        reporter.wait()
        self.assertEqual(len(offline.calls), 1)
        self.assertEqual(len(reporter.queue.pending()), 1)
        queued = reporter.queue.pending()[0]

        online = FakeUploader(result=True)
        restarted = self.reporter(online)
        self.assertEqual(restarted.retry_pending(), 1)
        self.assertEqual([name for name, _ in online.calls], [queued])
        self.assertEqual(restarted.queue.pending(), [])

    def test_the_zip_is_queued_before_the_upload_starts(self):
        seen = []
        reporter = None

        def uploader(name, data):
            seen.append(reporter.queue.pending())
            return True

        reporter = self.reporter(uploader)
        reporter.report(*crash())
        reporter.wait()
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(reporter.queue.pending(), [])

    def test_the_queue_keeps_the_newest_ten(self):
        names = iter(f"{i:08x}" for i in range(12))
        uploader = FakeUploader(result=False)
        reporter = self.reporter(uploader, rand_hex=lambda: next(names))
        for i in range(12):
            self.clock.value = datetime(2026, 11, 1, 0, 0, i, tzinfo=UTC)
            reporter.report(*crash(f"crash {i}"))
        reporter.wait()
        expected = [
            crash_report.report_filename(
                crash_report.platform_name(),
                datetime(2026, 11, 1, 0, 0, i, tzinfo=UTC),
                f"{i:08x}",
            )
            for i in range(2, 12)
        ]
        self.assertEqual(reporter.queue.pending(), expected)

    def test_the_off_switch_survives_a_new_reporter(self):
        first = self.reporter(FakeUploader())
        first.set_enabled(False)
        uploader = FakeUploader()
        second = self.reporter(uploader)
        self.assertFalse(second.enabled)
        second.report(*crash())
        second.wait()
        self.assertEqual(uploader.calls, [])


class UploadTests(unittest.TestCase):
    def test_put_to_the_shared_drop_path(self):
        seen = {}

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["type"] = request.get_header("Content-type")
            seen["timeout"] = timeout
            return Response()

        ok = crash_report.upload(
            "https://drop.example.test/",
            "tok/en",
            "a b.zip",
            b"zip",
            timeout=3.0,
            opener=opener,
        )
        self.assertTrue(ok)
        self.assertEqual(
            seen["url"],
            "https://drop.example.test/public.php/dav/files/tok%2Fen/a%20b.zip",
        )
        self.assertEqual(seen["method"], "PUT")
        self.assertEqual(seen["type"], "application/zip")
        self.assertEqual(seen["timeout"], 3.0)


if __name__ == "__main__":
    unittest.main()
