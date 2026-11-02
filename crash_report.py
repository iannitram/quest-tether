"""Pure helpers for crash report names, fields and in-memory archives."""

import io
import json
import re
import zipfile


def report_filename(platform: str, now, rand_hex: str) -> str:
    """Name a report using a supplied UTC time and eight lowercase hex digits."""
    if re.fullmatch(r"[0-9a-f]{8}", rand_hex) is None:
        raise ValueError("rand_hex must be exactly 8 lowercase hex characters")
    return f"quest-tether-{platform}-{now:%Y%m%dT%H%M%SZ}-{rand_hex}.zip"


def build_report(*, app_version, commit, os_name, kernel, python_version,
                 exc_type, message, traceback_text, now, install_id) -> dict:
    """Build the report fields; the caller supplies UTC time and scrubbed text."""
    return {
        "app": "quest-tether",
        "version": app_version,
        "commit": commit,
        "os": os_name,
        "kernel": kernel,
        "python": python_version,
        "exception_type": exc_type,
        "message": message,
        "traceback": traceback_text,
        "timestamp": f"{now:%Y-%m-%dT%H:%M:%SZ}",
        "install_id": install_id,
    }


def last_lines(text: str, n: int = 500) -> str:
    """Return at most the last n lines, joined with newlines."""
    return "\n".join(text.splitlines()[-n:]) if n > 0 else ""


def make_zip(report: dict, log_text: str) -> bytes:
    """Package the report and the last 500 log lines without writing files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("report.json", json.dumps(report, indent=2, sort_keys=True))
        archive.writestr("log.txt", last_lines(log_text))
    return buffer.getvalue()
