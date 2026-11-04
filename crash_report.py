"""Crash report names, fields, archives, scrubbing and automatic upload."""

import io
import json
import logging
import os
import platform as sys_platform
import re
import secrets
import socket
import sys
import threading
import traceback
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone

import crash_hook
import crash_scrub
from crash_queue import Limiter, Queue

logger = logging.getLogger(__name__)

APP_ID = "quest-tether"
APP_VERSION = "0.9.0"
GIT_COMMIT = os.environ.get("CRAFT_GIT_COMMIT", "unknown")

DROP_BASE = None
DROP_TOKEN = None
UPLOAD_TIMEOUT = 10.0
REPORT_FOLDER = "crash-reports"

_reporter = None
_enabled_default = None


def report_filename(platform: str, now, rand_hex: str) -> str:
    """Name a report using a supplied UTC time and eight lowercase hex digits."""
    if re.fullmatch(r"[0-9a-f]{8}", rand_hex) is None:
        raise ValueError("rand_hex must be exactly 8 lowercase hex characters")
    return f"quest-tether-{platform}-{now:%Y%m%dT%H%M%SZ}-{rand_hex}.zip"


def build_report(
    *,
    app_version,
    commit,
    os_name,
    kernel,
    python_version,
    exc_type,
    message,
    traceback_text,
    now,
    install_id,
) -> dict:
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


def data_dir() -> str:
    """Return the app's state folder, matching qt_platform's choices."""
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA", home), "QuestTether")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    return os.path.join(base, "quest-tether")


def platform_name() -> str:
    """Return the platform word used in report file names."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform.split("-")[0] or "unknown"


def drop_config():
    """Return (base, token) from build-time config, or None when unset."""
    base = os.environ.get("CRAFT_BUG_DROP_BASE") or DROP_BASE
    token = os.environ.get("CRAFT_BUG_DROP_TOKEN") or DROP_TOKEN
    if not (base and token):
        try:
            import bug_drop_config as baked
        except ImportError:
            baked = None
        if baked is not None:
            base = (
                base
                or getattr(baked, "BASE", None)
                or getattr(baked, "DROP_BASE", None)
            )
            token = (
                token
                or getattr(baked, "TOKEN", None)
                or getattr(baked, "DROP_TOKEN", None)
            )
    if not (base and token):
        return None
    return str(base).rstrip("/"), str(token)


def upload(
    base: str,
    token: str,
    name: str,
    data: bytes,
    timeout: float = UPLOAD_TIMEOUT,
    opener=None,
) -> bool:
    """PUT one zip to the shared File Drop, returning True on a 2xx answer."""
    url = (
        f"{base.rstrip('/')}/public.php/dav/files/"
        f"{urllib.parse.quote(token, safe='')}/{urllib.parse.quote(name, safe='')}"
    )
    request = urllib.request.Request(
        url, data=data, method="PUT", headers={"Content-Type": "application/zip"}
    )
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            return 200 <= int(status) < 300
    except Exception:
        return False


def read_log(path: str, n: int = 500) -> str:
    """Read the tail of the app log, or return an empty string."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            return last_lines(stream.read(), n)
    except OSError:
        return ""


class Reporter:
    """Build, scrub, rate-limit, queue and upload crash reports."""

    def __init__(
        self,
        data_dir_path=None,
        *,
        enabled=None,
        base=None,
        token=None,
        log_path=None,
        uploader=None,
        timeout=UPLOAD_TIMEOUT,
        app_version=APP_VERSION,
        commit=GIT_COMMIT,
        hostname=None,
        home=None,
        clock=None,
        rand_hex=None,
    ):
        self.data_dir = data_dir_path or data_dir()
        self.report_dir = os.path.join(self.data_dir, REPORT_FOLDER)
        configured = drop_config()
        self.base = (
            base if base is not None else (configured[0] if configured else None)
        )
        self.token = (
            token if token is not None else (configured[1] if configured else None)
        )
        self.log_path = log_path or os.path.join(self.data_dir, "quest-tether.log")
        self.uploader = uploader
        self.timeout = timeout
        self.limiter = Limiter(os.path.join(self.report_dir, "limit.json"), per_day=5)
        self.queue = Queue(os.path.join(self.report_dir, "queue"), max_items=10)
        self.home = os.path.expanduser("~") if home is None else home
        self.hostname = socket.gethostname() if hostname is None else hostname
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rand_hex = rand_hex or (lambda: secrets.token_hex(4))
        self.app_version = app_version
        self.commit = commit
        self.install_id = self._load_install_id()
        self.enabled_path = os.path.join(self.report_dir, "enabled.json")
        self.enabled = self._load_enabled() if enabled is None else bool(enabled)
        self._lock = threading.Lock()
        self._threads = []
        self._no_config_logged = False

    def _load_install_id(self) -> str:
        path = os.path.join(self.data_dir, "install-id")
        try:
            with open(path, encoding="utf-8") as stream:
                value = stream.read().strip()
            if value:
                return value
        except OSError:
            pass
        value = uuid.uuid4().hex
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(value)
        except OSError:
            pass
        return value

    def _load_enabled(self) -> bool:
        try:
            with open(
                os.path.join(self.report_dir, "enabled.json"), encoding="utf-8"
            ) as stream:
                value = json.load(stream)
            return bool(value.get("enabled", True)) if isinstance(value, dict) else True
        except (OSError, ValueError):
            return True

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        try:
            os.makedirs(self.report_dir, exist_ok=True)
            with open(self.enabled_path, "w", encoding="utf-8") as stream:
                json.dump({"enabled": self.enabled}, stream)
        except OSError:
            pass

    def _log_no_config(self) -> None:
        if not self._no_config_logged:
            logger.info("Crash reporting is off: no build-time drop address or token.")
            self._no_config_logged = True

    def _scrub(self, text: str) -> str:
        extra = (self.token,) if self.token else ()
        return crash_scrub.scrub(text or "", self.home, self.hostname, extra)

    def _make_zip(self, exc_type, exc, tb, now) -> bytes:
        report = build_report(
            app_version=self.app_version,
            commit=self.commit,
            os_name=f"{sys_platform.system()} {sys_platform.release()}".strip(),
            kernel=sys_platform.release(),
            python_version=sys_platform.python_version(),
            exc_type=getattr(exc_type, "__name__", type(exc).__name__),
            message=self._scrub(str(exc)),
            traceback_text=self._scrub(
                "".join(traceback.format_exception(exc_type, exc, tb))
            ),
            now=now,
            install_id=self.install_id,
        )
        return make_zip(report, self._scrub(read_log(self.log_path)))

    def report(self, exc_type, exc, tb) -> bool:
        """The crash sink: build one zip and send it from a daemon thread."""
        if not self.enabled:
            return False
        if not (self.base and self.token):
            self._log_no_config()
            return False
        now = self.clock()
        if not self.limiter.allow(now.timestamp()):
            return False
        try:
            if getattr(exc, "_crash_report_sent", False):
                return False
            setattr(exc, "_crash_report_sent", True)
        except Exception:
            pass
        name = report_filename(platform_name(), now, self.rand_hex())
        data = self._make_zip(exc_type, exc, tb, now)
        self._start(self._send, name, data)
        return True

    sink = report
    handle_exception = report

    def _upload(self, name: str, data: bytes) -> bool:
        try:
            if self.uploader is not None:
                return bool(self.uploader(name, data))
            return upload(self.base, self.token, name, data, self.timeout)
        except Exception:
            return False

    def _send(self, name: str, data: bytes) -> None:
        with self._lock:
            now = self.clock().timestamp()
            if not self.limiter.allow(now):
                return
            if self._upload(name, data):
                self.limiter.record(now)
            else:
                self.queue.add(name, data)

    def retry_pending(self) -> int:
        """Retry queued reports, oldest first; stop at the first failure."""
        if not self.enabled or not (self.base and self.token):
            return 0
        sent = 0
        for name in self.queue.pending():
            with self._lock:
                now = self.clock().timestamp()
                if not self.limiter.allow(now):
                    break
                try:
                    data = self.queue.read(name)
                except OSError:
                    self.queue.remove(name)
                    continue
                if not self._upload(name, data):
                    break
                self.limiter.record(now)
                self.queue.remove(name)
                sent += 1
        return sent

    def _start(self, target, *args):
        thread = threading.Thread(
            target=target, args=args, daemon=True, name="crash-report"
        )
        self._threads.append(thread)
        thread.start()
        return thread

    def wait(self, timeout: float = 5.0) -> None:
        """Join the upload threads (tests and shutdown only)."""
        for thread in list(self._threads):
            if thread.is_alive():
                thread.join(timeout)

    flush = wait


def install(data_dir_path=None, *, enabled=None, log_path=None, **kwargs):
    """Install the crash hooks and retry any queued reports in the background."""
    global _reporter
    if enabled is None and _enabled_default is not None:
        enabled = _enabled_default
    _reporter = Reporter(data_dir_path, enabled=enabled, log_path=log_path, **kwargs)
    crash_hook.install(_reporter.report)
    if _reporter.base and _reporter.token:
        _reporter._start(_reporter.retry_pending)
    else:
        _reporter._log_no_config()
    return _reporter


def current():
    """Return the installed reporter, if any."""
    return _reporter


def report_exception(exc_type, exc, tb) -> bool:
    """Report an exception through the installed reporter."""
    return _reporter.report(exc_type, exc, tb) if _reporter is not None else False


def set_enabled(enabled: bool) -> None:
    """Turn automatic crash reports on or off and remember the choice."""
    global _enabled_default
    _enabled_default = bool(enabled)
    if _reporter is not None:
        _reporter.set_enabled(enabled)


def is_enabled() -> bool:
    """Return the current automatic report setting (default on)."""
    if _reporter is not None:
        return _reporter.enabled
    return True if _enabled_default is None else _enabled_default


def retry_pending() -> int:
    """Retry the queue through the installed reporter."""
    return _reporter.retry_pending() if _reporter is not None else 0


CrashReporter = Reporter
