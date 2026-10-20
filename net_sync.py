"""
Wi-Fi Sync
Keeps the headset on the same Wi-Fi network as the PC so Virtual Desktop /
Steam Link discovery works. The PC is the source of truth: if the two SSIDs
differ, the headset is joined to the PC's network over ADB.

The headset's `cmd wifi` has no "switch to saved network" command, only
`connect-network <ssid> <security> <passphrase>`. The passphrase is read from
the PC's own saved Wi-Fi profile (needs an elevated process) and handed
straight to ADB. It is never logged, stored, or returned to callers.
"""

import os
import re
import subprocess
import time
from typing import Optional, Callable

from adb_manager import ADBManager

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _netsh(*args, timeout=8) -> str:
    try:
        r = subprocess.run(["netsh", "wlan", *args], capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WINDOW)
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def get_pc_ssid() -> Optional[str]:
    """SSID the PC's Wi-Fi is connected to, or None (Ethernet-only, Wi-Fi off)."""
    if os.name != "nt":
        return None
    out = _netsh("show", "interfaces")
    connected = False
    for line in out.splitlines():
        m = re.match(r"\s*State\s*:\s*(.+)", line)
        if m:
            connected = m.group(1).strip().lower() == "connected"
        m = re.match(r"\s*SSID\s*:\s*(.*)", line)  # BSSID lines start with "BSSID"
        if m and connected and m.group(1).strip():
            return m.group(1).strip()
    return None


def _get_pc_credentials(ssid: str):
    """Return (security, passphrase) for a saved PSK profile, or None.

    security is the `cmd wifi` keyword: wpa2 or wpa3. Open and enterprise
    profiles return None (the caller falls back to a manual-switch message).
    """
    out = _netsh("show", "profile", f"name={ssid}", "key=clear")
    if not out:
        return None
    auths = re.findall(r"Authentication\s*:\s*(.+)", out)
    key = re.search(r"Key Content\s*:\s*(.*)", out)
    if not key or not key.group(1).strip():
        return None
    auths = [a.strip().lower() for a in auths]
    if any(a == "wpa2-personal" for a in auths):
        sec = "wpa2"  # covers WPA2 and WPA2/WPA3 transition profiles
    elif any(a == "wpa3-personal" for a in auths):
        sec = "wpa3"
    else:
        return None
    return sec, key.group(1).strip()


def _shq(s: str) -> str:
    """Quote for the headset's sh (adb shell re-joins args into one string)."""
    return "'" + s.replace("'", "'\\''") + "'"


def get_quest_ssid(adb: ADBManager, serial: str) -> Optional[str]:
    r = adb.run_adb("-s", serial, "shell", "cmd", "wifi", "status")
    if r.returncode != 0:
        return None
    m = re.search(r'Wifi is connected to "([^"]*)"', r.stdout)
    return m.group(1) if m else None


def sync_quest_wifi(adb: ADBManager, serial: str,
                    log: Callable[[str], None] = lambda m: None,
                    settle_seconds: float = 25.0) -> str:
    """Join the headset to the PC's Wi-Fi network. Returns a status string:
    'same' | 'switched' | 'no-pc-wifi' | 'no-quest-wifi' | 'manual' | 'failed'.
    """
    pc = get_pc_ssid()
    if not pc:
        log("Wi-Fi sync: PC is not on Wi-Fi — skipping")
        return "no-pc-wifi"
    quest = get_quest_ssid(adb, serial)
    if quest is None:
        log("Wi-Fi sync: headset Wi-Fi is off or unreadable — skipping")
        return "no-quest-wifi"
    if quest == pc:
        log(f"Wi-Fi sync: PC and headset both on \"{pc}\"")
        return "same"

    log(f"Wi-Fi sync: PC is on \"{pc}\" but headset is on \"{quest}\" — switching headset")
    creds = _get_pc_credentials(pc)
    if not creds:
        log(f"Wi-Fi sync: can't read a WPA2/WPA3 passphrase for \"{pc}\" "
            f"(open/enterprise network, or app not elevated). "
            f"Join \"{pc}\" on the headset manually: Settings > Wi-Fi.")
        return "manual"
    sec, passphrase = creds

    cmd = f"cmd wifi connect-network {_shq(pc)} {sec} {_shq(passphrase)}"
    r = adb.run_adb("-s", serial, "shell", cmd, timeout=20)
    del passphrase, cmd  # keep the secret out of any later frame/locals dump
    if r.returncode != 0 or "invalid" in (r.stdout + r.stderr).lower():
        log("Wi-Fi sync: headset rejected the connect command")
        return "failed"

    deadline = time.time() + settle_seconds
    while time.time() < deadline:
        time.sleep(2.0)
        if get_quest_ssid(adb, serial) == pc:
            log(f"Wi-Fi sync: headset now on \"{pc}\"")
            return "switched"
    log(f"Wi-Fi sync: headset didn't join \"{pc}\" within {int(settle_seconds)}s "
        f"(out of range, or wrong saved password?)")
    return "failed"
