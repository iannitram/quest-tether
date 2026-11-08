"""Command layer for the quest-tether CLI (#74).

Every function takes a Backend and returns a plain dict, so the argparse CLI
(cli.py) and any future front end share one implementation. A Backend picks
the path once per command: qtetherd on 127.0.0.1 when it answers, else the
in-process TetherManager. Everything that differs by OS stays in qt_platform.
"""

import glob
import os
import socket
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional

import crash_queue as crash_queue_store
import crash_report
import first_run
import qt_platform
import qtetherd
from adb_manager import ADBManager
from tether_manager import TetherManager

SERVICE_ACTIONS = ("status", "start", "stop")

SETTING_ALIASES = {
    "auto_reconnect": "auto_reconnect",
    "auto-reconnect": "auto_reconnect",
    "wifi_mode": "wifi_mode",
    "wifi-mode": "wifi_mode",
    "crash_reports": "crash_reports",
    "crash-reports": "crash_reports",
    "autostart": "autostart",
}

_TRUE = ("1", "true", "on", "yes", "enable", "enabled")
_FALSE = ("0", "false", "off", "no", "disable", "disabled")


def _usage(detail: str) -> dict:
    """A bad-usage result, which cli_format.exit_code maps to 2."""
    return {"ok": False, "error": "bad usage", "detail": detail}


def _reply(reply: Any, what: str) -> dict:
    """Coerce a backend reply to a dict, or make a useful error for a bad one."""
    if isinstance(reply, dict):
        return reply
    return {"ok": False, "error": "bad %s reply" % what}


class Backend:
    """One command's route to the tether.

    Subclasses answer the same methods, so the command functions above never
    test the OS or whether a service is running.
    """

    mode = "in-process"

    def __init__(self, platform=None, adb=None, tether=None):
        self.platform = platform or qt_platform.current()
        self._adb = adb
        self._tether = tether

    @property
    def adb(self):
        if self._adb is None:
            self._adb = ADBManager(platform=self.platform)
        return self._adb

    @property
    def tether(self):
        if self._tether is None:
            self._tether = TetherManager(self.adb, platform=self.platform)
        return self._tether

    def status(self):
        raise NotImplementedError

    def connect(self):
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def wifi_sync(self):
        raise NotImplementedError

    def logs(self, source: str, n: int):
        raise NotImplementedError

    def settings_get(self):
        raise NotImplementedError

    def settings_set(self, updates: Dict[str, Any]):
        raise NotImplementedError


class ServiceBackend(Backend):
    """The running qtetherd owns the tether; ask it over loopback JSON."""

    mode = "service"

    def __init__(self, platform=None, request=None, adb=None, tether=None):
        super().__init__(platform, adb, tether)
        self._request = request or qtetherd.request

    def _call(self, cmd: dict, timeout: float = 10.0) -> dict:
        try:
            reply = self._request(cmd, timeout=timeout)
        except OSError as exc:
            return {"ok": False, "error": "service not reachable: %s" % exc}
        except ValueError as exc:
            return {"ok": False, "error": "bad service reply: %s" % exc}
        return _reply(reply, "service")

    def status(self):
        return self._call({"cmd": "status"}, timeout=5.0)

    def connect(self):
        return self._call({"cmd": "connect"}, timeout=120.0)

    def disconnect(self):
        return self._call({"cmd": "disconnect"}, timeout=90.0)

    def wifi_sync(self):
        return self._call({"cmd": "wifi-sync"}, timeout=90.0)

    def logs(self, source: str, n: int):
        return self._call({"cmd": "logs", "source": source, "n": n}, timeout=10.0)

    def settings_get(self):
        return self._call({"cmd": "settings-get"}, timeout=10.0)

    def settings_set(self, updates: Dict[str, Any]):
        return self._call({"cmd": "settings-set", "settings": updates}, timeout=10.0)


class LocalBackend(Backend):
    """No service answered: this process runs the tether through TetherManager."""

    mode = "in-process"

    def _handle(self, req: dict) -> dict:
        return qtetherd.handle_request(self.tether, self.adb.find_serial, req)

    def status(self):
        return _reply(self._handle({"cmd": "status"}), "status")

    def connect(self):
        return _reply(self._handle({"cmd": "connect"}), "connect")

    def disconnect(self):
        reply = _reply(self._handle({"cmd": "disconnect"}), "disconnect")
        # A `connect` in an earlier process has no TetherManager here any more,
        # so the disconnect above skipped the headset. Its relay may still hold
        # the port, or may already be dead with the headset's VPN still up and
        # Wi-Fi still off. Clean the headset either way, then the relay.
        try:
            if not self.tether.is_relay_running():
                pids = self.platform.stale_relay_pids(TetherManager.RELAY_PORT)
                self._clean_headset()
                for pid in pids:
                    self.platform.kill(pid)
        except Exception:
            pass
        return reply

    def _clean_headset(self):
        serial = self.adb.find_serial()
        if not serial:
            return
        for step in (
            self.adb.stop_gnirehtet_client,
            self.adb.force_stop_gnirehtet,
            self.adb.remove_all_reverse_tunnels,
            getattr(self.tether, "restore_wifi_if_flagged", None),
        ):
            if step is None:
                continue
            try:
                step(serial)
            except Exception:
                pass

    def wifi_sync(self):
        return _reply(self._handle({"cmd": "wifi-sync"}), "wifi-sync")

    def logs(self, source: str, n: int):
        return _reply(self._handle({"cmd": "logs", "source": source, "n": n}), "logs")

    def settings_get(self):
        return _reply(self._handle({"cmd": "settings-get"}), "settings")

    def settings_set(self, updates: Dict[str, Any]):
        return _reply(
            self._handle({"cmd": "settings-set", "settings": updates}), "settings"
        )


# The probe errors that mean "no service here": a refused or reset connection,
# or qtetherd.request's ConnectionError("no answer") for an empty reply.
SERVICE_ABSENT_ERRORS = (ConnectionError,)

# A probe timeout (socket.timeout is TimeoutError on Python 3.10+) is a service
# that is there but slow. It is not "no service" for a state-changing command.
SERVICE_SLOW_ERRORS = (socket.timeout,)

# The commands that change the tether, the headset or the saved settings.
STATE_CHANGING_COMMANDS = ("connect", "disconnect", "wifi-sync", "settings-set")

BUSY_ERROR = "service busy"
BUSY_DETAIL = (
    "qtetherd did not answer in time; it may be busy. "
    "Nothing was changed. Try again in a few seconds."
)


class BusyBackend(Backend):
    """The service is there but slow: every call reports it busy (exit code 1)."""

    mode = "service"

    def _busy(self, *args, **kwargs) -> dict:
        return {"ok": False, "error": BUSY_ERROR, "detail": BUSY_DETAIL}

    status = connect = disconnect = wifi_sync = logs = settings_get = _busy
    settings_set = _busy


def make_backend(
    platform=None, request=None, adb=None, tether=None, state_changing=False
) -> Backend:
    """Pick the service or in-process path once, for one command.

    A refused, reset or silent connection means "no service": LocalBackend.
    A timed-out probe means a slow service: a read-only command falls back to
    LocalBackend, but a state-changing one (state_changing=True) gets a
    BusyBackend, because an in-process disconnect would kill the service's
    relay (#125). Any other socket error, or an answer that is not JSON, keeps
    the service path, so the command reports that error instead of silently
    starting a second core next to a service that is there.
    """
    platform = platform or qt_platform.current()
    probe = request or qtetherd.request
    try:
        probe({"cmd": "status"}, timeout=1)
    except SERVICE_SLOW_ERRORS:
        if state_changing:
            return BusyBackend(platform=platform, adb=adb, tether=tether)
        return LocalBackend(platform=platform, adb=adb, tether=tether)
    except SERVICE_ABSENT_ERRORS:
        return LocalBackend(platform=platform, adb=adb, tether=tether)
    except (OSError, ValueError):
        pass  # the service path reports this error on the command's own call
    return ServiceBackend(platform=platform, request=probe, adb=adb, tether=tether)


choose_backend = make_backend
select_backend = make_backend
get_backend = make_backend


def status(backend: Backend) -> dict:
    """The tether status: state, rates, uptime, battery/temp/zone, reconnects, serial."""
    return _reply(backend.status(), "status")


def connect(backend: Backend) -> dict:
    """Bring the tether up (the service keeps it up; in-process needs --hold to stay)."""
    return _reply(backend.connect(), "connect")


def disconnect(backend: Backend) -> dict:
    """Bring the tether down."""
    return _reply(backend.disconnect(), "disconnect")


def wifi_sync(backend: Backend) -> dict:
    """Copy the PC's Wi-Fi network to the headset."""
    return _reply(backend.wifi_sync(), "wifi-sync")


def logs(backend: Backend, relay: bool = False, n: int = 50) -> dict:
    """The last n lines of the app log, or of relay.log when relay is true."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return _usage("line count must be a number")
    if n < 0:
        return _usage("line count must not be negative")
    source = "relay" if relay else "app"
    return _reply(backend.logs(source, n), "logs")


def _autostart(backend: Backend) -> bool:
    try:
        return bool(backend.platform.autostart())
    except Exception:
        return False


def settings_get(backend: Backend) -> dict:
    """Every persisted setting: service settings plus the platform autostart flag."""
    reply = _reply(backend.settings_get(), "settings")
    if reply.get("ok") is False or reply.get("error"):
        return reply
    settings = dict(reply.get("settings") or {})
    settings["autostart"] = _autostart(backend)
    return {"ok": True, "settings": settings}


def _parse_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def _parse_setting(key: str, value: Any):
    """(canonical key, parsed value, error dict or None) for one settings set."""
    canonical = SETTING_ALIASES.get(str(key).strip().lower())
    if canonical is None:
        return None, None, _usage("unknown setting: %s" % key)
    if canonical == "wifi_mode":
        if value not in ("off", "sync", "leave"):
            return None, None, _usage("wifi_mode must be off, sync or leave")
        return canonical, value, None
    parsed = _parse_bool(value)
    if parsed is None:
        return None, None, _usage("%s must be on or off" % canonical)
    return canonical, parsed, None


def settings_set(backend: Backend, key: str, value: Any) -> dict:
    """Set one persisted setting and return the full settings block."""
    canonical, parsed, error = _parse_setting(key, value)
    if error:
        return error
    if canonical == "autostart":
        try:
            backend.platform.set_autostart(parsed)
        except Exception as exc:
            return {"ok": False, "error": "autostart: %s" % exc}
        return settings_get(backend)
    reply = _reply(backend.settings_set({canonical: parsed}), "settings")
    if reply.get("ok") is False or reply.get("error"):
        return reply
    return settings_get(backend)


def _tools_dir() -> str:
    """The packaged tools folder next to the program, as the core finds it."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "tools")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _udev_rule(dirs=None) -> str:
    """First udev rule that mentions Meta's USB vendor id 2833, or ""."""
    folders = dirs or (
        "/etc/udev/rules.d",
        "/usr/lib/udev/rules.d",
        "/run/udev/rules.d",
    )
    for folder in folders:
        for path in sorted(glob.glob(os.path.join(folder, "*.rules"))):
            try:
                with open(path, "r", errors="replace") as stream:
                    if "2833" in stream.read():
                        return path
            except OSError:
                continue
    return ""


def doctor(
    backend: Backend,
    tools_dir: Optional[str] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    udev_dirs=None,
) -> dict:
    """First-run checks: tools, adb, relay, udev (Linux), device, service."""
    platform = backend.platform
    checks: List[dict] = []

    missing: List[str] = []
    if platform.name in ("windows", "linux"):
        folder = tools_dir or _tools_dir()
        lookup = which or (lambda name: platform.tool_path(name, []))
        try:
            missing = first_run.missing_tools(platform.name, folder, lookup)
        except Exception as exc:
            missing = ["tool check failed: %s" % exc]
    checks.append(_check("tools", not missing, "; ".join(missing) or "all present"))

    adb = backend.adb
    try:
        adb_ok = bool(adb.is_adb_available())
    except Exception:
        adb_ok = False
    checks.append(_check("adb", adb_ok, str(getattr(adb, "adb_path", "adb"))))

    tether = backend.tether
    try:
        relay_ok = bool(tether.is_relay_available())
    except Exception:
        relay_ok = False
    relay_path = str(getattr(tether, "gnirehtet_path", "") or "")
    checks.append(_check("relay", relay_ok, relay_path or "gnirehtet relay not found"))

    if platform.name == "linux":
        rule = _udev_rule(udev_dirs)
        checks.append(
            _check(
                "udev",
                bool(rule),
                rule or "no rule for Meta vendor 2833",
            )
        )

    quest = None
    try:
        quest = adb.find_quest()
    except Exception:
        quest = None
    if not quest:
        checks.append(_check("device", False, "no headset connected"))
    elif quest.get("status", "device") != "device":
        checks.append(
            _check(
                "device",
                False,
                "%s is %s"
                % (
                    quest.get("serial") or "headset",
                    quest.get("status") or "unavailable",
                ),
            )
        )
    else:
        checks.append(
            _check(
                "device",
                True,
                "%s (%s)" % (quest.get("model") or "Quest", quest.get("serial")),
            )
        )

    service_up = getattr(backend, "mode", "in-process") == "service"
    checks.append(
        _check(
            "service",
            service_up,
            "qtetherd answers"
            if service_up
            else "no service; using the in-process tether",
        )
    )

    required = [c for c in checks if c["name"] != "service"]
    return {
        "ok": all(c["ok"] for c in required),
        "platform": platform.name,
        "checks": checks,
    }


def install_apk(backend: Backend) -> dict:
    """Push the gnirehtet client APK to the headset with `gnirehtet install`."""
    adb = backend.adb
    quest = adb.find_quest()
    if not quest:
        return {"ok": False, "error": "no headset found"}
    if quest.get("status", "device") != "device":
        return {
            "ok": False,
            "error": "headset is %s" % (quest.get("status") or "unavailable"),
        }
    serial = quest.get("serial")
    if not serial:
        return {"ok": False, "error": "headset serial is unknown"}

    tether = backend.tether
    relay = str(getattr(tether, "gnirehtet_path", "") or "")
    tools_dir = os.path.dirname(relay)
    if not relay or not tools_dir:
        return {"ok": False, "error": "gnirehtet relay not found"}

    cmd = list(backend.platform.relay_command(tools_dir, serial))
    if len(cmd) < 2:
        return {"ok": False, "error": "cannot build the install command"}
    cmd[-2:] = ["install", serial]

    env = os.environ.copy()
    adb_path = str(getattr(adb, "adb_path", "") or "")
    if adb_path:
        env["ADB"] = adb_path
    apk = os.path.join(tools_dir, "gnirehtet.apk")
    if os.path.isfile(apk):
        env["GNIREHTET_APK"] = apk

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            creationflags=backend.platform.no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": "install failed: %s" % exc}

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return {
            "ok": False,
            "error": "install failed (exit %d)" % result.returncode,
            "serial": serial,
            "output": output,
        }
    return {"ok": True, "serial": serial, "output": output}


def crash_queue(
    backend: Backend, flush: bool = False, reporter=None, queue=None
) -> dict:
    """List queued crash reports, or retry them with flush."""
    folder = os.path.join(
        backend.platform.data_dir(), crash_report.REPORT_FOLDER, "queue"
    )
    store = queue if queue is not None else crash_queue_store.Queue(folder)
    pending = list(store.pending())
    sent = 0

    if flush:
        current = reporter if reporter is not None else crash_report.current()
        if current is None:
            try:
                current = crash_report.Reporter(backend.platform.data_dir())
            except Exception as exc:
                return {"ok": False, "error": "crash reports: %s" % exc}
        try:
            sent = current.retry_pending()
        except Exception as exc:
            return {"ok": False, "error": "crash reports: %s" % exc}
        pending = list(store.pending())

    reply = {
        "ok": True,
        "queued": pending,
        "count": len(pending),
    }
    if flush:
        reply["sent"] = sent
    return reply


def service(backend: Backend, action: str) -> dict:
    """Status, start or stop the platform's tether service."""
    if action not in SERVICE_ACTIONS:
        return _usage("service action must be status, start or stop")
    try:
        reply = backend.platform.service(action)
    except Exception as exc:
        return {"ok": False, "error": "service: %s" % exc}
    return _reply(reply, "service")


def gui(backend: Backend = None) -> dict:
    """Start the desktop window (the backend argument is unused)."""
    import main as window

    window.main()
    return {"ok": True}


cmd_status = status
cmd_connect = connect
cmd_disconnect = disconnect
cmd_wifi_sync = wifi_sync
cmd_logs = logs
cmd_settings_get = settings_get
cmd_settings_set = settings_set
cmd_doctor = doctor
cmd_install_apk = install_apk
cmd_crash_queue = crash_queue
cmd_service = service
cmd_gui = gui
