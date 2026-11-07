"""soak_report sampler, throughput and report helpers (#3), with fakes only: python -m unittest tests.test_soak_sampler -v"""
import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import soak_report  # noqa: E402

MEMINFO = "MemTotal:       16000000 kB\nMemFree:  1000 kB\nMemAvailable:    4000000 kB\n"


class FakeClock:
    """Time moves only through sleep(), so the sampler never really waits."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Throughput(unittest.TestCase):
    def test_empty_session_is_zero(self):
        meter = soak_report.Throughput()
        self.assertEqual((meter.peak, meter.average, meter.count), (0, 0, 0))

    def test_peak_and_integer_average(self):
        meter = soak_report.Throughput()
        for value in (100, 300, 200):
            meter.add(value)
        self.assertEqual(meter.peak, 300)
        self.assertEqual(meter.average, 200)

    def test_average_is_floored(self):
        meter = soak_report.Throughput()
        for value in (1, 2):
            meter.add(value)
        self.assertEqual(meter.average, 1)

    def test_none_nan_bool_and_text_are_skipped_not_zero(self):
        meter = soak_report.Throughput()
        for value in (None, math.nan, math.inf, True, "500", 400):
            meter.add(value)
        self.assertEqual(meter.count, 1)
        self.assertEqual(meter.average, 400)

    def test_negative_counts_as_zero(self):
        meter = soak_report.Throughput()
        meter.add(-50)
        meter.add(100)
        self.assertEqual(meter.count, 2)
        self.assertEqual(meter.average, 50)
        self.assertEqual(meter.peak, 100)

    def test_float_readings_are_truncated(self):
        meter = soak_report.Throughput()
        meter.add(1234.9)
        self.assertEqual(meter.peak, 1234)


class MemUsed(unittest.TestCase):
    def test_total_minus_available(self):
        self.assertEqual(soak_report.mem_used_kb(MEMINFO), 12000000)

    def test_missing_available_is_none(self):
        self.assertIsNone(soak_report.mem_used_kb("MemTotal: 100 kB\n"))

    def test_missing_total_is_none(self):
        self.assertIsNone(soak_report.mem_used_kb("MemAvailable: 100 kB\n"))

    def test_empty_and_none_are_none(self):
        self.assertIsNone(soak_report.mem_used_kb(""))
        self.assertIsNone(soak_report.mem_used_kb(None))

    def test_available_above_total_is_not_negative(self):
        self.assertEqual(soak_report.mem_used_kb("MemTotal: 10 kB\nMemAvailable: 20 kB\n"), 0)


class ParsePs(unittest.TestCase):
    def test_rows_keep_full_args(self):
        rows = soak_report.parse_ps("  100     1  2048 /usr/bin/python3 qtetherd.py --x\n  101   100   512 gnirehtet run SER\n")
        self.assertEqual(rows[0], {"pid": 100, "ppid": 1, "rss_kb": 2048, "args": "/usr/bin/python3 qtetherd.py --x"})
        self.assertEqual(rows[1]["args"], "gnirehtet run SER")

    def test_malformed_rows_are_skipped(self):
        text = "abc 1 2 foo\n 5 6\n\n 7 8 9 bar\n"
        self.assertEqual([r["pid"] for r in soak_report.parse_ps(text)], [7])

    def test_empty_and_none(self):
        self.assertEqual(soak_report.parse_ps(""), [])
        self.assertEqual(soak_report.parse_ps(None), [])


class ProcessRole(unittest.TestCase):
    def test_service_and_cli_are_app(self):
        self.assertEqual(soak_report.process_role("/usr/bin/python3 /opt/quest-tether/qtetherd.py"), "app")
        self.assertEqual(soak_report.process_role("/usr/bin/quest-tether status --json"), "app")
        self.assertEqual(soak_report.process_role("python3 qtetherd.py"), "app")

    def test_relay_native_and_java(self):
        self.assertEqual(soak_report.process_role("/t/gnirehtet run SERIAL"), "relay")
        self.assertEqual(soak_report.process_role("java -jar /t/Gnirehtet.jar run X"), "relay")

    def test_windows_style_path(self):
        self.assertEqual(soak_report.process_role("C:\\tools\\gnirehtet.exe run X"), "relay")

    def test_shell_that_only_mentions_the_name_is_not_counted(self):
        self.assertIsNone(soak_report.process_role("bash -c cd /home/deck/dev/quest-tether-gh && make"))
        self.assertIsNone(soak_report.process_role("python3 /home/deck/dev/quest-tether-wt/x/soak_report.py sample"))
        self.assertIsNone(soak_report.process_role("ps -eo pid=,ppid=,rss=,args="))

    def test_interpreter_without_script_is_none(self):
        self.assertIsNone(soak_report.process_role("python3 -c print(1)"))
        self.assertIsNone(soak_report.process_role("python3"))

    def test_empty_is_none(self):
        self.assertIsNone(soak_report.process_role(""))
        self.assertIsNone(soak_report.process_role(None))


class Orphans(unittest.TestCase):
    def row(self, pid, ppid, args, rss=10):
        return {"pid": pid, "ppid": ppid, "rss_kb": rss, "args": args}

    def test_relay_under_running_app_is_not_orphan(self):
        rows = [self.row(10, 1, "python3 qtetherd.py"), self.row(11, 10, "gnirehtet run X")]
        self.assertEqual(soak_report.count_orphans(rows), 0)

    def test_relay_reparented_after_app_exit_is_orphan(self):
        rows = [self.row(11, 900, "gnirehtet run X"), self.row(12, 1, "java -jar gnirehtet.jar")]
        self.assertEqual(soak_report.count_orphans(rows), 2)

    def test_relay_with_no_app_running_counts(self):
        self.assertEqual(soak_report.count_orphans([self.row(11, 1, "gnirehtet run X")]), 1)

    def test_no_processes(self):
        self.assertEqual(soak_report.count_orphans([]), 0)


class BuildRecord(unittest.TestCase):
    def rows(self):
        return [
            {"pid": 10, "ppid": 1, "rss_kb": 3000, "args": "python3 qtetherd.py"},
            {"pid": 11, "ppid": 10, "rss_kb": 700, "args": "gnirehtet run X"},
            {"pid": 99, "ppid": 1, "rss_kb": 5, "args": "bash -c quest-tether"},
        ]

    def test_reachable_record(self):
        status = {"state": "connected", "rx_bps": 1500.0, "tx_bps": 20.0, "reconnects": 2, "temp_c": 30}
        record = soak_report.build_record(12.5, status, self.rows(), MEMINFO)
        self.assertEqual(record, {
            "t": 12.5, "state": "connected", "rx_bps": 1500.0, "tx_bps": 20.0,
            "reconnects": 2, "mem_used_kb": 12000000, "app_rss_kb": 3000,
            "relay_rss_kb": 700, "orphans": 0,
        })

    def test_unanswered_service_is_unreachable(self):
        record = soak_report.build_record(1, None, [], None)
        self.assertEqual(record["state"], "unreachable")
        self.assertIsNone(record["rx_bps"])
        self.assertIsNone(record["mem_used_kb"])
        self.assertEqual((record["app_rss_kb"], record["relay_rss_kb"], record["orphans"]), (0, 0, 0))

    def test_record_is_json_ready(self):
        record = soak_report.build_record(1, {"state": "idle", "rx_bps": 0.0}, self.rows(), MEMINFO)
        json.dumps(record)


class SummarizeSamples(unittest.TestCase):
    def records(self):
        return [
            {"t": 100.0, "state": "connected", "rx_bps": 100, "tx_bps": 10, "reconnects": 1,
             "mem_used_kb": 1000, "app_rss_kb": 500, "relay_rss_kb": 50, "orphans": 0},
            {"t": 105.0, "state": "unreachable", "rx_bps": None, "tx_bps": None, "reconnects": None,
             "mem_used_kb": None, "app_rss_kb": 0, "relay_rss_kb": 0, "orphans": 0},
            {"t": 110.0, "state": "connected", "rx_bps": 300, "tx_bps": 30, "reconnects": 3,
             "mem_used_kb": 1400, "app_rss_kb": 600, "relay_rss_kb": 40, "orphans": 2},
        ]

    def test_empty(self):
        s = soak_report.summarize_samples([])
        self.assertEqual(s["samples"], 0)
        self.assertEqual((s["rx_peak"], s["rx_average"], s["tx_peak"], s["tx_average"]), (0, 0, 0, 0))
        self.assertIsNone(s["mem_delta_kb"])
        self.assertIsNone(s["orphans_max"])
        self.assertEqual(s["duration_s"], 0.0)

    def test_throughput_memory_rss_and_orphans(self):
        s = soak_report.summarize_samples(self.records())
        self.assertEqual(s["samples"], 3)
        self.assertEqual(s["connected_samples"], 2)
        self.assertEqual(s["unreachable_samples"], 1)
        self.assertEqual((s["rx_peak"], s["rx_average"]), (300, 200))
        self.assertEqual((s["tx_peak"], s["tx_average"]), (30, 20))
        self.assertEqual(s["duration_s"], 10.0)
        self.assertEqual(s["reconnects"], 2)
        self.assertEqual((s["mem_start_kb"], s["mem_end_kb"], s["mem_delta_kb"]), (1000, 1400, 400))
        self.assertEqual((s["app_rss_start_kb"], s["app_rss_end_kb"], s["app_rss_delta_kb"]), (500, 600, 100))
        self.assertEqual((s["relay_rss_start_kb"], s["relay_rss_end_kb"], s["relay_rss_delta_kb"]), (50, 40, -10))
        self.assertEqual((s["orphans_max"], s["orphans_last"]), (2, 2))

    def test_missing_ends_give_no_change(self):
        records = [{"t": 1, "mem_used_kb": None}, {"t": 2, "mem_used_kb": 5}]
        s = soak_report.summarize_samples(records)
        self.assertEqual(s["mem_start_kb"], 5)
        self.assertEqual(s["mem_end_kb"], 5)
        self.assertEqual(s["mem_delta_kb"], 0)
        only_none = soak_report.summarize_samples([{"t": 1, "mem_used_kb": None}])
        self.assertIsNone(only_none["mem_delta_kb"])


class FormatReport(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(soak_report.format_report(soak_report.summarize_samples([])), "Samples: 0\nSpan: none")

    def test_full_block(self):
        records = [
            {"t": 100.0, "state": "connected", "rx_bps": 100, "tx_bps": 10, "reconnects": 1,
             "mem_used_kb": 1000, "app_rss_kb": 500, "relay_rss_kb": 50, "orphans": 0},
            {"t": 110.0, "state": "connected", "rx_bps": 300, "tx_bps": 30, "reconnects": 1,
             "mem_used_kb": 1400, "app_rss_kb": 600, "relay_rss_kb": 40, "orphans": 1},
        ]
        text = soak_report.format_report(soak_report.summarize_samples(records))
        span = "Span: {} to {} (10.0 s)".format(soak_report._clock_text(100.0), soak_report._clock_text(110.0))
        self.assertEqual(text, "\n".join([
            "Samples: 2 (connected 2, unreachable 0)",
            span,
            "Down throughput: peak 300 B/s, average 200 B/s",
            "Up throughput: peak 30 B/s, average 20 B/s",
            "Reconnects during run: 0",
            "Memory used (system): start 1000 kB, end 1400 kB, change +400 kB",
            "App memory (RSS): start 500 kB, end 600 kB, change +100 kB",
            "Relay memory (RSS): start 50 kB, end 40 kB, change -10 kB",
            "Orphan relay processes: max 1, last 1",
        ]))

    def test_unknown_memory_reads_na(self):
        text = soak_report.format_report(soak_report.summarize_samples([{"t": 1, "state": "unreachable"}]))
        self.assertIn("Memory used (system): n/a", text)
        self.assertIn("Reconnects during run: n/a", text)
        self.assertIn("Orphan relay processes: max n/a, last n/a", text)


class ReadRecords(unittest.TestCase):
    def test_skips_blank_bad_and_non_dict_lines(self):
        text = '{"t": 1}\n\nnot json\n[1, 2]\n{"t": 2}\n'
        self.assertEqual([r["t"] for r in soak_report.read_records(text)], [1, 2])

    def test_empty(self):
        self.assertEqual(soak_report.read_records(""), [])
        self.assertEqual(soak_report.read_records(None), [])


class Sampler(unittest.TestCase):
    def run_with(self, minutes, interval, statuses, clock=None, processes=None):
        clock = clock or FakeClock()
        statuses = iter(statuses)
        out = io.StringIO()
        written = soak_report.run_sampler(
            minutes, interval, out,
            fetch_status=lambda: next(statuses),
            list_processes=lambda: processes or [],
            read_meminfo=lambda: MEMINFO,
            clock=clock, sleep=clock.sleep,
        )
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        return written, lines, clock

    def test_one_minute_every_five_seconds_is_thirteen_samples(self):
        statuses = [{"state": "connected", "rx_bps": 1, "tx_bps": 2}] * 13
        written, lines, clock = self.run_with(1, 5, statuses)
        self.assertEqual(written, 13)
        self.assertEqual([line["t"] for line in lines], [1000.0 + 5 * i for i in range(13)])
        self.assertEqual(clock.sleeps, [5.0] * 12)

    def test_samples_never_run_past_the_end(self):
        statuses = [{"state": "idle"}] * 3
        written, lines, _ = self.run_with(0.1, 3, statuses)  # 6 s total: t=0, 3, 6
        self.assertEqual(written, 3)
        self.assertEqual(lines[-1]["t"], 1006.0)

    def test_slow_polls_do_not_drift_the_schedule(self):
        clock = FakeClock()

        def slow_processes():
            clock.now += 0.75  # the ps call takes time
            return []

        out = io.StringIO()
        written = soak_report.run_sampler(
            0.5, 2, out,  # 30 s every 2 s: samples at 0, 2, ... 30
            fetch_status=lambda: {"state": "connected"},
            list_processes=slow_processes,
            read_meminfo=lambda: MEMINFO,
            clock=clock, sleep=clock.sleep,
        )
        times = [json.loads(line)["t"] for line in out.getvalue().splitlines()]
        self.assertEqual(written, 16)
        self.assertEqual(times, [1000.0 + 2 * i for i in range(16)])

    def test_unreachable_service_is_recorded_not_fatal(self):
        written, lines, _ = self.run_with(0.05, 3, [None, {"state": "connected", "rx_bps": 5}])  # 3 s: t=0, 3
        self.assertEqual(written, 2)
        self.assertEqual(lines[0]["state"], "unreachable")
        self.assertEqual(lines[1]["rx_bps"], 5)

    def test_processes_feed_rss_and_orphans(self):
        processes = [
            {"pid": 10, "ppid": 1, "rss_kb": 3000, "args": "python3 qtetherd.py"},
            {"pid": 11, "ppid": 77, "rss_kb": 700, "args": "gnirehtet run X"},
        ]
        _, lines, _ = self.run_with(0.05, 3, [{"state": "connected"}] * 2, processes=processes)
        self.assertEqual(lines[0]["app_rss_kb"], 3000)
        self.assertEqual(lines[0]["relay_rss_kb"], 700)
        self.assertEqual(lines[0]["orphans"], 1)

    def test_bad_arguments_raise(self):
        for minutes, interval in ((0, 5), (5, 0), (-1, 5)):
            with self.assertRaises(ValueError):
                self.run_with(minutes, interval, [])


class FetchStatus(unittest.TestCase):
    def with_fake_service(self, request):
        fake = types.ModuleType("qtetherd")
        fake.request = request
        saved = sys.modules.get("qtetherd")
        sys.modules["qtetherd"] = fake
        try:
            return soak_report._fetch_status()
        finally:
            if saved is None:
                del sys.modules["qtetherd"]
            else:
                sys.modules["qtetherd"] = saved

    def test_returns_reply(self):
        seen = {}

        def request(cmd, timeout):
            seen["cmd"] = cmd
            seen["timeout"] = timeout
            return {"state": "connected"}

        self.assertEqual(self.with_fake_service(request), {"state": "connected"})
        self.assertEqual(seen["cmd"], {"cmd": "status"})

    def test_refused_is_none(self):
        def request(cmd, timeout):
            raise ConnectionRefusedError()

        self.assertIsNone(self.with_fake_service(request))

    def test_bad_json_is_none(self):
        def request(cmd, timeout):
            raise ValueError("bad json")

        self.assertIsNone(self.with_fake_service(request))


class ListProcesses(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ps"), "needs ps")
    def test_sees_this_process(self):
        rows = soak_report._list_processes()
        self.assertTrue(any(r["pid"] == os.getpid() for r in rows))

    def test_missing_ps_gives_empty_list(self):
        saved = os.environ.get("PATH")
        os.environ["PATH"] = tempfile.gettempdir() + os.sep + "no-such-dir-for-ps"
        try:
            self.assertEqual(soak_report._list_processes(), [])
        finally:
            os.environ["PATH"] = saved


class MainCommands(unittest.TestCase):
    def test_report_prints_block_and_log_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            samples = os.path.join(tmp, "s.jsonl")
            log = os.path.join(tmp, "app.log")
            with open(samples, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"t": 5, "state": "connected", "rx_bps": 10}) + "\n")
            with open(log, "w", encoding="utf-8") as handle:
                handle.write("16:00:00 [INFO] Tethering active!\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = soak_report.main(["report", samples, "--log", log])
        self.assertEqual(code, 0)
        self.assertIn("Samples: 1 (connected 1, unreachable 0)", out.getvalue())
        self.assertIn("Connects: 1", out.getvalue())

    def test_report_on_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            samples = os.path.join(tmp, "empty.jsonl")
            open(samples, "w", encoding="utf-8").close()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(soak_report.main(["report", samples]), 0)
        self.assertEqual(out.getvalue().strip(), "Samples: 0\nSpan: none")

    def test_sample_rejects_non_positive_minutes(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            soak_report.main(["sample", "--minutes", "0", "--out", "x.jsonl"])
        self.assertIn("must be positive", err.getvalue())

    def test_sample_writes_file_with_fakes(self):
        real_fetch, real_list, real_mem = soak_report._fetch_status, soak_report._list_processes, soak_report._read_meminfo
        soak_report._fetch_status = lambda: {"state": "idle", "rx_bps": 0.0}
        soak_report._list_processes = lambda: []
        soak_report._read_meminfo = lambda: MEMINFO
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = os.path.join(tmp, "out.jsonl")
                # 0.001 minutes at 0.05 s is 0 .. 0.06 s: two samples, no real wait beyond that.
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = soak_report.main(["sample", "--minutes", "0.001", "--interval", "0.05", "--out", target])
                self.assertEqual(code, 0)
                with open(target, encoding="utf-8") as handle:
                    records = soak_report.read_records(handle.read())
        finally:
            soak_report._fetch_status, soak_report._list_processes, soak_report._read_meminfo = real_fetch, real_list, real_mem
        self.assertGreaterEqual(len(records), 2)
        self.assertEqual(records[0]["state"], "idle")
        self.assertIn("Wrote", out.getvalue())


if __name__ == "__main__":
    unittest.main()
