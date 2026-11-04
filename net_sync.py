"""
Wi-Fi Sync
Keeps the headset on the same Wi-Fi network as the PC so Virtual Desktop /
Steam Link discovery works. The PC is the source of truth: if the two SSIDs
differ, the headset is joined to the PC's network over ADB.

The headset's `cmd wifi` has no "switch to saved network" command, only
`connect-network <ssid> <security> <passphrase>`. The SSID, passphrase and
security keyword come from the platform layer (Windows Wi-Fi profiles,
NetworkManager on Linux) and go straight to ADB. The passphrase is never
logged, stored, or returned to callers.
"""

import re
import time
from typing import Callable, Optional, Tuple

import qt_platform
from adb_manager import ADBManager


def get_pc_wifi(platform=None) -> Optional[Tuple[str, str, str]]:
    """(SSID, key, security) of the PC's Wi-Fi, or None (Ethernet-only, Wi-Fi off)."""
    return (platform or qt_platform.current()).pc_wifi()


def get_pc_ssid(platform=None) -> Optional[str]:
    """SSID the PC's Wi-Fi is connected to, or None (Ethernet-only, Wi-Fi off)."""
    pc = get_pc_wifi(platform)
    return pc[0] if pc else None


def _shq(s: str) -> str:
    """Quote for the headset's sh (adb shell re-joins args into one string)."""
    return "'" + s.replace("'", "'\\''") + "'"


def get_quest_ssid(adb: ADBManager, serial: str) -> Optional[str]:
    r = adb.run_adb("-s", serial, "shell", "cmd", "wifi", "status")
    if r.returncode != 0:
        return None
    m = re.search(r'Wifi is connected to "([^"]*)"', r.stdout)
    return m.group(1) if m else None


def sync_quest_wifi(
    adb: ADBManager,
    serial: str,
    log: Callable[[str], None] = lambda m: None,
    settle_seconds: float = 25.0,
    platform=None,
) -> str:
    """Join the headset to the PC's Wi-Fi network. Returns a status string:
    'same' | 'switched' | 'no-pc-wifi' | 'no-quest-wifi' | 'manual' | 'failed'.
    """
    pc = get_pc_wifi(platform)
    if not pc:
        log("Wi-Fi sync: PC is not on Wi-Fi — skipping")
        return "no-pc-wifi"
    pc_ssid, passphrase, security = pc
    quest = get_quest_ssid(adb, serial)
    if quest is None:
        log("Wi-Fi sync: headset Wi-Fi is off or unreadable — skipping")
        return "no-quest-wifi"
    if quest == pc_ssid:
        log(f'Wi-Fi sync: PC and headset both on "{pc_ssid}"')
        return "same"

    log(
        f'Wi-Fi sync: PC is on "{pc_ssid}" but headset is on "{quest}" — switching headset'
    )
    cmd = f"cmd wifi connect-network {_shq(pc_ssid)} {security} {_shq(passphrase)}"
    r = adb.run_adb("-s", serial, "shell", cmd, timeout=20)
    del passphrase, cmd  # keep the secret out of any later frame/locals dump
    if r.returncode != 0 or "invalid" in (r.stdout + r.stderr).lower():
        log("Wi-Fi sync: headset rejected the connect command")
        return "failed"

    deadline = time.time() + settle_seconds
    while time.time() < deadline:
        time.sleep(2.0)
        if get_quest_ssid(adb, serial) == pc_ssid:
            log(f'Wi-Fi sync: headset now on "{pc_ssid}"')
            return "switched"
    log(
        f'Wi-Fi sync: headset didn\'t join "{pc_ssid}" within {int(settle_seconds)}s '
        f"(out of range, or wrong saved password?)"
    )
    return "failed"
