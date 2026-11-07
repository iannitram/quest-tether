"""Linux implementation of the platform layer (docs/code-map.md, "The platform layer").

Filled in for data_dir, tool_path, no_window_flags, relay_command, stale_relay_pids, kill and
pc_wifi (#33), and for single_instance, autostart and set_autostart (#34).
The parsing and path rules live in linux_parse.py and linux_paths.py (pure, no system calls).
fcntl is not imported at the top: it is Unix-only and this module must import on Windows too.
"""

import logging
import os
import subprocess
import sys
from typing import List, Optional, Sequence, Tuple

from . import linux_paths
from .base import Platform
from .linux_parse import (
    cmdline_mentions_gnirehtet,
    parse_active_wifi_connection,
    parse_psk,
    parse_security,
    parse_ss_pids,
    parse_ssid_field,
)

log = logging.getLogger(__name__)

SERVICE_UNIT = "quest-tether.service"

_lock_handle = (
    None  # the single-instance lock file, kept open for the life of the process
)


def _run(args: List[str], timeout: float = 8) -> str:
    """Run a command and return its stdout; "" if it fails, times out or is not installed."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return done.stdout if done.returncode == 0 else ""


# `systemctl stop` waits for the unit's SIGTERM stop, which runs the disconnect
# (up to 20 s) and may take the unit's TimeoutStopSec (30 s) (#110).
SERVICE_STOP_TIMEOUT = 40


def _systemctl(*args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    """Run one systemctl --user command; a missing systemctl becomes a failed run."""
    cmd = ["systemctl", "--user"] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def _read_cmdline(pid: int) -> str:
    """The command line of pid with NULs turned into spaces; "" if it cannot be read."""
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace")


class LinuxPlatform(Platform):
    name = "linux"

    def data_dir(self) -> str:
        path = linux_paths.data_dir_path(os.environ, os.path.expanduser("~"))
        os.makedirs(path, exist_ok=True)
        return path

    def tool_path(self, name: str, base_dirs: Sequence[str]) -> Optional[str]:
        for base in base_dirs:
            candidate = os.path.join(base, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        lines = _run(["which", name]).splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
        return None

    def no_window_flags(self) -> int:
        return 0

    def relay_command(self, tools_dir: str, serial: str) -> List[str]:
        return linux_paths.relay_command(tools_dir, serial)

    def relay_server_command(self, tools_dir: str, port: int) -> List[str]:
        return linux_paths.relay_server_command(tools_dir, port)

    def port_holders(self, port: int) -> List[Tuple[int, bool]]:
        out = _run(["ss", "-ltnp"])
        if not out:
            return []
        return [
            (pid, cmdline_mentions_gnirehtet(_read_cmdline(pid)))
            for pid in parse_ss_pids(out, port)
        ]

    def relay_candidates(self) -> List[Tuple[str, bool]]:
        return [("gnirehtet", False)]

    def stale_relay_pids(self, port: int) -> List[int]:
        out = _run(["ss", "-ltnp"])
        if not out:
            return []
        return [
            pid
            for pid in parse_ss_pids(out, port)
            if cmdline_mentions_gnirehtet(_read_cmdline(pid))
        ]

    def kill(self, pid: int) -> None:
        if not cmdline_mentions_gnirehtet(_read_cmdline(pid)):
            log.info("kill: pid %d is not a gnirehtet process; not killing it", pid)
            return
        os.kill(pid, 9)

    def kill_tree(self, pid: int) -> None:
        os.kill(pid, 9)

    def pc_ssid(self) -> Optional[str]:
        name = parse_active_wifi_connection(
            _run(["nmcli", "-t", "-f", "name,type", "connection", "show", "--active"])
        )
        if not name:
            return None
        return (
            parse_ssid_field(
                _run(
                    ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", name]
                )
            )
            or None
        )

    def pc_wifi(self) -> Optional[Tuple[str, str, str]]:
        name = parse_active_wifi_connection(
            _run(["nmcli", "-t", "-f", "name,type", "connection", "show", "--active"])
        )
        if not name:
            return None
        ssid = parse_ssid_field(
            _run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", name])
        )
        if not ssid:
            return None
        key = parse_psk(
            _run(
                [
                    "nmcli",
                    "-s",
                    "-g",
                    "802-11-wireless-security.psk",
                    "connection",
                    "show",
                    name,
                ]
            )
        )
        if not key:
            return None
        security = parse_security(
            _run(
                [
                    "nmcli",
                    "-g",
                    "802-11-wireless-security.key-mgmt",
                    "connection",
                    "show",
                    name,
                ]
            )
        )
        if not security:
            return None
        return (ssid, key, security)

    def single_instance(self, name: str) -> bool:
        global _lock_handle
        import fcntl  # Unix only; imported here so this module still imports on Windows

        handle = open(os.path.join(self.data_dir(), name + ".lock"), "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        _lock_handle = handle
        return True

    def uses_tether_service(self) -> bool:
        return True

    def autostart(self) -> bool:
        return os.path.exists(
            linux_paths.autostart_path(os.environ, os.path.expanduser("~"))
        )

    def set_autostart(self, on: bool) -> bool:
        path = linux_paths.autostart_path(os.environ, os.path.expanduser("~"))
        if on:
            main_py = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"
            )
            command = linux_paths.autostart_command(
                sys.executable, getattr(sys, "frozen", False), main_py
            )
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(linux_paths.autostart_entry(command))
        else:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        return self.autostart()

    def service(self, action: str) -> dict:
        """Run a systemctl --user action on the tether unit (the qtetherd service)."""
        if action not in ("status", "start", "stop"):
            return {"ok": False, "error": "bad service action: " + str(action)}
        if action in ("start", "stop"):
            if action == "stop":
                result = _systemctl(action, SERVICE_UNIT, timeout=SERVICE_STOP_TIMEOUT)
            else:
                result = _systemctl(action, SERVICE_UNIT)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                return {
                    "ok": False,
                    "action": action,
                    "running": None,
                    "detail": detail or "systemctl %s failed" % action,
                }
        state = _systemctl("is-active", SERVICE_UNIT)
        detail = (state.stdout or "").strip()
        if not detail:
            return {
                "ok": False,
                "action": action,
                "running": False,
                "detail": (state.stderr or "").strip() or "systemctl is-active failed",
            }
        return {
            "ok": True,
            "action": action,
            "running": detail == "active",
            "detail": detail,
        }
