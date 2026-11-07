"""quest-tether soak-run tools (#3).

Pure pieces: log counters (summarize, format_summary), a throughput accumulator,
/proc and ps parsers, and the record and summary builders. The sampler polls
the running service (qtetherd status) and the process table, and writes one
JSON line per sample. Commands:

  python3 soak_report.py sample --minutes 15 --out soak.jsonl [--interval 5]
  python3 soak_report.py report soak.jsonl [--log app.log]

Tests: python -m unittest tests.test_soak_report tests.test_soak_sampler -v
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time

# A logged line is "[YYYY-MM-DD ]HH:MM:SS[,msms] [LEVEL] message".
# The time is the HH:MM:SS sitting just before " [".
_LINE_RE = re.compile(
    r'(?P<time>\d{2}:\d{2}:\d{2})'
    r'(?:[.,]\d+)?'
    r' \[[^\]]*\] '
    r'(?P<message>.*)$'
)

DEFAULT_INTERVAL_S = 5.0
_APP_NAMES = ("qtetherd", "quest-tether")
_INTERPRETERS = ("python", "java")


def summarize(text: str) -> dict:
    """Counts from a quest-tether log for a soak run (#3): connects, drops, reconnects, refusals, first/last time."""
    result = {
        "connects": 0,
        "drops": 0,
        "reconnects": 0,
        "refusals": 0,
        "disconnects": 0,
        "first": None,
        "last": None,
        "lines": 0,
    }
    if not text:
        return result
    for line in text.splitlines():
        match = _LINE_RE.search(line)
        if not match:
            continue
        message = match.group("message")
        if "Tethering active!" in message:
            result["connects"] += 1
        if "gnirehtet process died!" in message:
            result["drops"] += 1
        if "Reconnected!" in message:
            result["reconnects"] += 1
        if "client start refused" in message:
            result["refusals"] += 1
        if message == "Disconnected":
            result["disconnects"] += 1
        time = match.group("time")
        result["lines"] += 1
        if result["first"] is None:
            result["first"] = time
        result["last"] = time
    return result


def format_summary(s: dict) -> str:
    """Plain-text block for a summarize() result, one 'Label: value' per line."""
    if s["first"] is None:
        span = "Log span: none"
    else:
        span = f"Log span: {s['first']} to {s['last']}"
    lines = [
        span,
        f"Connects: {s['connects']}",
        f"Drops: {s['drops']}",
        f"Reconnects: {s['reconnects']}",
        f"Refusals: {s['refusals']}",
        f"Disconnects: {s['disconnects']}",
        f"Unrecovered drops: {max(s['drops'] - s['reconnects'], 0)}",
    ]
    return "\n".join(lines)


def _number(value):
    """A finite int or float, or None (bools, strings, None, NaN and infinities are not readings)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


class Throughput:
    """Peak and integer average of bytes-per-second samples; an empty session gives 0.

    Missing or non-numeric readings are skipped, not counted as zero. Negative
    readings count as zero.
    """

    def __init__(self):
        self.count = 0
        self.total = 0
        self.peak = 0

    def add(self, bps) -> None:
        value = _number(bps)
        if value is None:
            return
        value = max(0, int(value))
        self.count += 1
        self.total += value
        if value > self.peak:
            self.peak = value

    @property
    def average(self) -> int:
        return self.total // self.count if self.count else 0


def mem_used_kb(meminfo: str):
    """MemTotal minus MemAvailable from /proc/meminfo text, in kB; None if either line is missing."""
    values = {}
    for line in (meminfo or "").splitlines():
        match = re.match(r"(MemTotal|MemAvailable):\s+([0-9]+)\s*kB", line)
        if match:
            values[match.group(1)] = int(match.group(2))
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    return max(values["MemTotal"] - values["MemAvailable"], 0)


def parse_ps(text: str) -> list:
    """Rows of `ps -eo pid=,ppid=,rss=,args=` as dicts; malformed rows are skipped."""
    rows = []
    for line in (text or "").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, rss = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        rows.append({"pid": pid, "ppid": ppid, "rss_kb": rss, "args": parts[3]})
    return rows


def _basename(path: str) -> str:
    return os.path.basename(path.replace("\\", "/")).lower()


def process_role(args: str):
    """'app' for the quest-tether service or CLI, 'relay' for gnirehtet, else None.

    Matches the program's own name, or the script after a python or java
    interpreter, so a shell command that mentions the path does not count.
    """
    tokens = (args or "").split()
    if not tokens:
        return None
    name = _basename(tokens[0])
    if name.startswith(_INTERPRETERS):
        script = None
        for token in tokens[1:]:
            if not token.startswith("-"):
                script = token
                break
        if script is None:
            return None
        name = _basename(script)
    if name.endswith(".py"):
        name = name[:-3]
    if "gnirehtet" in name:
        return "relay"
    if name in _APP_NAMES:
        return "app"
    return None


def count_orphans(processes: list) -> int:
    """Relay processes whose parent is not a running app process (left behind after the app exited)."""
    app_pids = {p["pid"] for p in processes if process_role(p["args"]) == "app"}
    return sum(
        1
        for p in processes
        if process_role(p["args"]) == "relay" and p["ppid"] not in app_pids
    )


def _rss_total(processes: list, role: str) -> int:
    return sum(p["rss_kb"] for p in processes if process_role(p["args"]) == role)


def build_record(now: float, status, processes: list, meminfo) -> dict:
    """One sample as a JSON-ready dict. status is the qtetherd status reply, or None when the service did not answer."""
    reachable = status is not None
    status = status if reachable else {}
    return {
        "t": round(float(now), 3),
        "state": status.get("state") if reachable else "unreachable",
        "rx_bps": status.get("rx_bps") if reachable else None,
        "tx_bps": status.get("tx_bps") if reachable else None,
        "reconnects": status.get("reconnects") if reachable else None,
        "mem_used_kb": mem_used_kb(meminfo),
        "app_rss_kb": _rss_total(processes, "app"),
        "relay_rss_kb": _rss_total(processes, "relay"),
        "orphans": count_orphans(processes),
    }


def _ends(records: list, key: str):
    """First and last numeric value of one key across the records, or (None, None)."""
    values = [v for v in (_number(r.get(key)) for r in records) if v is not None]
    if not values:
        return None, None
    return values[0], values[-1]


def _change(start, end):
    if start is None or end is None:
        return None
    return end - start


def summarize_samples(records: list) -> dict:
    """Throughput peak and average, memory and RSS change, reconnects and orphans from sample records."""
    down, up = Throughput(), Throughput()
    for r in records:
        down.add(r.get("rx_bps"))
        up.add(r.get("tx_bps"))
    times = [v for v in (_number(r.get("t")) for r in records) if v is not None]
    mem_start, mem_end = _ends(records, "mem_used_kb")
    app_start, app_end = _ends(records, "app_rss_kb")
    relay_start, relay_end = _ends(records, "relay_rss_kb")
    re_start, re_end = _ends(records, "reconnects")
    orphan_values = [v for v in (_number(r.get("orphans")) for r in records) if v is not None]
    return {
        "samples": len(records),
        "first_t": times[0] if times else None,
        "last_t": times[-1] if times else None,
        "duration_s": round(times[-1] - times[0], 1) if times else 0.0,
        "connected_samples": sum(1 for r in records if r.get("state") == "connected"),
        "unreachable_samples": sum(1 for r in records if r.get("state") == "unreachable"),
        "rx_peak": down.peak,
        "rx_average": down.average,
        "tx_peak": up.peak,
        "tx_average": up.average,
        "reconnects": _change(re_start, re_end),
        "mem_start_kb": mem_start,
        "mem_end_kb": mem_end,
        "mem_delta_kb": _change(mem_start, mem_end),
        "app_rss_start_kb": app_start,
        "app_rss_end_kb": app_end,
        "app_rss_delta_kb": _change(app_start, app_end),
        "relay_rss_start_kb": relay_start,
        "relay_rss_end_kb": relay_end,
        "relay_rss_delta_kb": _change(relay_start, relay_end),
        "orphans_max": max(orphan_values) if orphan_values else None,
        "orphans_last": orphan_values[-1] if orphan_values else None,
    }


def _clock_text(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _signed(value) -> str:
    return "n/a" if value is None else f"{value:+d}"


def _pair(start, end, delta, unit) -> str:
    if start is None and end is None:
        return "n/a"
    return f"start {start if start is not None else 'n/a'} {unit}, end {end if end is not None else 'n/a'} {unit}, change {_signed(delta)} {unit}"


def format_report(s: dict) -> str:
    """Plain-text block for a summarize_samples() result, one 'Label: value' per line."""
    if s["samples"] == 0:
        return "Samples: 0\nSpan: none"
    lines = [
        f"Samples: {s['samples']} (connected {s['connected_samples']}, unreachable {s['unreachable_samples']})",
        f"Span: {_clock_text(s['first_t'])} to {_clock_text(s['last_t'])} ({s['duration_s']} s)",
        f"Down throughput: peak {s['rx_peak']} B/s, average {s['rx_average']} B/s",
        f"Up throughput: peak {s['tx_peak']} B/s, average {s['tx_average']} B/s",
        f"Reconnects during run: {'n/a' if s['reconnects'] is None else s['reconnects']}",
        "Memory used (system): " + _pair(s["mem_start_kb"], s["mem_end_kb"], s["mem_delta_kb"], "kB"),
        "App memory (RSS): " + _pair(s["app_rss_start_kb"], s["app_rss_end_kb"], s["app_rss_delta_kb"], "kB"),
        "Relay memory (RSS): " + _pair(s["relay_rss_start_kb"], s["relay_rss_end_kb"], s["relay_rss_delta_kb"], "kB"),
        f"Orphan relay processes: max {'n/a' if s['orphans_max'] is None else s['orphans_max']}, "
        f"last {'n/a' if s['orphans_last'] is None else s['orphans_last']}",
    ]
    return "\n".join(lines)


def read_records(text: str) -> list:
    """JSON-lines sample file to a list of dicts; blank and malformed lines are skipped."""
    records = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def run_sampler(minutes: float, interval: float, out, *, fetch_status, list_processes,
                read_meminfo, clock=time.time, sleep=time.sleep) -> int:
    """Write one JSON line per sample from t=0 to t=minutes, every interval seconds. Returns the sample count.

    Every collaborator is passed in, so tests run with fakes and no real waiting.
    Sample times follow a fixed schedule, so slow polls do not drift the run.
    """
    if minutes <= 0 or interval <= 0:
        raise ValueError("minutes and interval must be positive")
    start = clock()
    total = minutes * 60.0
    count = int(total // interval) + 1
    written = 0
    for i in range(count):
        target = start + i * interval
        wait = target - clock()
        if wait > 0:
            sleep(wait)
        record = build_record(clock(), fetch_status(), list_processes(), read_meminfo())
        out.write(json.dumps(record) + "\n")
        out.flush()
        written += 1
    return written


def _fetch_status():
    """qtetherd status reply, or None when the service does not answer (not running, refused, timeout)."""
    try:
        import qtetherd  # lazy: the service module pulls in the tether stack
        return qtetherd.request({"cmd": "status"}, timeout=3.0)
    except (OSError, ValueError):
        return None


def _list_processes() -> list:
    """Process table from ps (Linux). Without ps the list is empty and orphan and RSS figures read 0."""
    try:
        done = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,rss=,args="],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ps(done.stdout)


def _read_meminfo():
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _cmd_sample(args) -> int:
    with open(args.out, "w", encoding="utf-8") as out:
        try:
            written = run_sampler(
                args.minutes, args.interval, out,
                fetch_status=_fetch_status,
                list_processes=_list_processes,
                read_meminfo=_read_meminfo,
            )
        except KeyboardInterrupt:
            # Each sample is flushed as it is written, so the file keeps every sample taken so far.
            print(f"Stopped early; the samples taken so far are in {args.out}", file=sys.stderr)
            return 130
    print(f"Wrote {written} samples to {args.out}")
    return 0


def _cmd_report(args) -> int:
    with open(args.file, encoding="utf-8") as handle:
        records = read_records(handle.read())
    print(format_report(summarize_samples(records)))
    if args.log:
        with open(args.log, encoding="utf-8", errors="replace") as handle:
            print()
            print(format_summary(summarize(handle.read())))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="quest-tether soak sampler and report (#3)")
    sub = parser.add_subparsers(dest="command", required=True)
    sample = sub.add_parser("sample", help="poll the service and the process table; write JSON lines")
    sample.add_argument("--minutes", type=float, required=True, help="how long to sample")
    sample.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S, help="seconds between samples")
    sample.add_argument("--out", required=True, help="JSON-lines output file (overwritten)")
    sample.set_defaults(func=_cmd_sample)
    report = sub.add_parser("report", help="summarize a sample file")
    report.add_argument("file", help="JSON-lines file written by sample")
    report.add_argument("--log", help="optional app log to summarize as well")
    report.set_defaults(func=_cmd_report)
    args = parser.parse_args(argv)
    if getattr(args, "minutes", 1) <= 0 or getattr(args, "interval", 1) <= 0:
        parser.error("--minutes and --interval must be positive")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
