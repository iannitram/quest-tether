"""Pure parsers for NetworkManager, socket and process command-line output."""
import re
from typing import List, Optional


def parse_active_ssid(text: str) -> Optional[str]:
    """Return the first active SSID, decoding nmcli terse-mode escapes."""
    for line in text.splitlines():
        if line.startswith("yes:"):
            return re.sub(r"\\(.)", r"\1", line[4:]) or None
    return None


def parse_psk(text: str) -> Optional[str]:
    """Return the first key line, preserving its leading and trailing spaces."""
    lines = text.splitlines()
    return (lines[0] or None) if lines else None


def parse_ss_pids(text: str, port: int) -> List[int]:
    """Return unique PIDs in output order for the exact local port."""
    pids: List[int] = []
    seen = set()
    suffix = ":" + str(port)
    for line in text.splitlines():
        columns = line.split()
        if len(columns) < 4 or columns[0] == "State":
            continue
        if not columns[3].endswith(suffix):
            continue
        for match in re.finditer(r"pid=([0-9]+)", line):
            pid = int(match.group(1))
            if pid not in seen:
                seen.add(pid)
                pids.append(pid)
    return pids


def cmdline_mentions_gnirehtet(cmdline: str) -> bool:
    """Check for the relay name, including Java and NUL-separated commands."""
    return "gnirehtet" in cmdline.lower()
