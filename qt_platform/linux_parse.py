"""Pure parsers for NetworkManager, socket and process command-line output."""

import re
from typing import List, Optional


def parse_active_ssid(text: str) -> Optional[str]:
    """Return the first active SSID, decoding nmcli terse-mode escapes."""
    for line in text.splitlines():
        if line.startswith("yes:"):
            return re.sub(r"\\(.)", r"\1", line[4:]) or None
    return None


def parse_active_wifi_connection(text: str) -> Optional[str]:
    """Return the name of the first active Wi-Fi connection from `nmcli -t -f name,type`."""
    suffix = ":802-11-wireless"
    for line in text.splitlines():
        if line.endswith(suffix) and len(line) > len(suffix):
            return re.sub(r"\\(.)", r"\1", line[: -len(suffix)])
    return None


def parse_ssid_field(text: str) -> Optional[str]:
    """Return the first line of `nmcli -g 802-11-wireless.ssid`, decoding terse-mode escapes."""
    lines = text.splitlines()
    return (re.sub(r"\\(.)", r"\1", lines[0]) or None) if lines else None


def parse_psk(text: str) -> Optional[str]:
    """Return the first key line, preserving its leading and trailing spaces."""
    lines = text.splitlines()
    return (lines[0] or None) if lines else None


def parse_security(text: str) -> Optional[str]:
    """Map NetworkManager key-mgmt to the headset's `cmd wifi` keyword: wpa3 or wpa2."""
    lines = text.splitlines()
    key_mgmt = lines[0].strip().lower() if lines else ""
    if key_mgmt == "sae":
        return "wpa3"
    if key_mgmt == "wpa-psk":
        return "wpa2"
    return None


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
