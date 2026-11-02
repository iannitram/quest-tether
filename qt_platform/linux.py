"""Linux implementation of the platform layer (docs/code-map.md, "The platform layer").

Filled in for data_dir, tool_path, no_window_flags, relay_command, stale_relay_pids, kill and
pc_wifi (#33). single_instance, autostart and set_autostart stay stubs until #14d.
The parsing and path rules live in linux_parse.py and linux_paths.py (pure, no system calls).
fcntl is not imported here: it is Unix-only and this module must import on Windows too.
"""
import logging
import os
import subprocess
from typing import List, Optional, Sequence, Tuple

from . import linux_paths
from .base import Platform
from .linux_parse import (cmdline_mentions_gnirehtet, parse_active_wifi_connection, parse_psk,
                          parse_ss_pids, parse_ssid_field)

log = logging.getLogger(__name__)


def _run(args: List[str], timeout: float = 8) -> str:
    """Run a command and return its stdout; "" if it fails, times out or is not installed."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return done.stdout if done.returncode == 0 else ""


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

    def stale_relay_pids(self, port: int) -> List[int]:
        out = _run(["ss", "-ltnp"])
        if not out:
            return []
        return [pid for pid in parse_ss_pids(out, port)
                if cmdline_mentions_gnirehtet(_read_cmdline(pid))]

    def kill(self, pid: int) -> None:
        if not cmdline_mentions_gnirehtet(_read_cmdline(pid)):
            log.info("kill: pid %d is not a gnirehtet process; not killing it", pid)
            return
        os.kill(pid, 9)

    def pc_wifi(self) -> Optional[Tuple[str, str]]:
        name = parse_active_wifi_connection(_run(["nmcli", "-t", "-f", "name,type",
                                                  "connection", "show", "--active"]))
        if not name:
            return None
        ssid = parse_ssid_field(_run(["nmcli", "-g", "802-11-wireless.ssid",
                                      "connection", "show", name]))
        if not ssid:
            return None
        key = parse_psk(_run(["nmcli", "-s", "-g", "802-11-wireless-security.psk",
                              "connection", "show", name]))
        if not key:
            return None
        return (ssid, key)
