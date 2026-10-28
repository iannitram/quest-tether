"""Windows implementation of the platform layer (docs/code-map.md, "The platform layer").

Moved, not rewritten: each method's first line names the function it was copied from.
The originals stay in tether_manager.py, adb_manager.py, net_sync.py and main.py until
their callers are switched (#15). Only the signatures changed: parameters instead of
module globals, return values instead of side effects. The `os.name != "nt"` guards are
gone because this class only exists on Windows.
"""
import logging
import os
import re
import subprocess
import sys
import time
from typing import List, Optional, Sequence, Tuple

from .base import Platform

try:
    import winreg
except ImportError:  # not Windows; tests inject a fake
    winreg = None

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = "QuestTether"


def _netsh(*args, timeout=8) -> str:
    # from net_sync._netsh
    try:
        r = subprocess.run(["netsh", "wlan", *args], capture_output=True, text=True,
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _get_pc_ssid() -> Optional[str]:
    # from net_sync.get_pc_ssid (minus the os.name guard)
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
    # from net_sync._get_pc_credentials (returns (security, passphrase); pc_wifi drops security)
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


def _autostart_command() -> str:
    # new for M6: the Run value is "<exe>" --minimized; from source, exe is the python + main.py pair
    if getattr(sys, "frozen", False):
        exe = f'"{sys.executable}"'
    else:
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        exe = f'"{sys.executable}" "{main_py}"'
    return f"{exe} --minimized"


_instance_mutex = None  # held for the life of the process


class WindowsPlatform(Platform):
    name = "windows"

    def data_dir(self) -> str:
        # from tether_manager.TetherManager.LOG_DIR (created here, as the interface promises)
        path = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "QuestTether")
        os.makedirs(path, exist_ok=True)
        return path

    def tool_path(self, name: str, base_dirs: Sequence[str]) -> Optional[str]:
        # from adb_manager.ADBManager._find_adb (local-folder check, then `where`); None instead of "adb"
        for base in base_dirs:
            for p in (os.path.join(base, name + ".exe"), os.path.join(base, name)):
                if os.path.isfile(p):
                    return p
        try:
            result = subprocess.run(
                ["where", name],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                return result.stdout.strip().split("\n")[0].strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def no_window_flags(self) -> int:
        # from tether_manager._NO_WINDOW (subprocess.CREATE_NO_WINDOW)
        return CREATE_NO_WINDOW

    def _find_java(self) -> str:
        # from tether_manager.TetherManager._find_java
        try:
            result = subprocess.run(
                ["where", "java"],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                return result.stdout.strip().split("\n")[0].strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return "java"

    def relay_command(self, tools_dir: str, serial: str) -> List[str]:
        # from tether_manager.TetherManager._spawn_relay: cmd = [java, "-jar", gnirehtet.jar, "run", serial]
        return [self._find_java(), "-jar", os.path.join(tools_dir, "gnirehtet.jar"), "run", serial]

    @staticmethod
    def _is_gnirehtet_pid(pid: int) -> bool:
        # from tether_manager.TetherManager._is_gnirehtet_pid
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return True
            return "gnirehtet" in r.stdout.lower()
        except Exception:
            return True

    def stale_relay_pids(self, port: int) -> List[int]:
        # from tether_manager.TetherManager._kill_stale_process (the netstat scan; the kill is kill())
        pids: List[int] = []
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            for line in result.stdout.split("\n"):
                if str(port) in line and "LISTENING" in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if not self._is_gnirehtet_pid(pid):
                        logger.info(f"Port {port} is held by PID {pid}, which is not gnirehtet — leaving it alone")
                        continue
                    pids.append(pid)
        except Exception as e:
            logger.warning(f"Warning: could not check for stale process: {e}")
        return pids

    def kill(self, pid: int) -> None:
        # from tether_manager.TetherManager._kill_stale_process (taskkill, then its 0.5 s wait)
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=5,
                       creationflags=CREATE_NO_WINDOW)
        time.sleep(0.5)

    def pc_wifi(self) -> Optional[Tuple[str, str]]:
        # from net_sync.get_pc_ssid + net_sync._get_pc_credentials
        ssid = _get_pc_ssid()
        if ssid is None:
            return None
        creds = _get_pc_credentials(ssid)
        if creds is None:
            return None
        return ssid, creds[1]

    def single_instance(self, name: str) -> bool:
        # from main._acquire_single_instance (mutex "Local\" + name; window title "Quest Tether")
        global _instance_mutex
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, "Local\\" + name)
        err = kernel32.GetLastError()
        # 183 = already exists; 5 = exists but owned by a differently-elevated process
        if err in (183, 5):
            try:
                user32 = ctypes.windll.user32
                hwnd = user32.FindWindowW(None, "Quest Tether")
                if hwnd:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return False
        _instance_mutex = handle
        return True

    def autostart(self) -> bool:
        # new for M6: present HKCU Run value "QuestTether" means on
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, AUTOSTART_VALUE)
            return True
        except OSError:
            return False

    def set_autostart(self, on: bool) -> bool:
        # new for M6: writes or deletes the HKCU Run value "QuestTether"
        if on:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, AUTOSTART_VALUE, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, AUTOSTART_VALUE)
            except FileNotFoundError:
                pass
        return self.autostart()
