"""
ADB Device Manager
Handles Quest detection, ADB commands, and USB state monitoring.
"""

import logging
import subprocess
import sys
import threading
import time
import os
import re
from typing import Optional, Callable, Dict, List

import am_result
import qt_platform
import vpn_state

logger = logging.getLogger(__name__)


def parse_devices(text: str) -> List[Dict[str, str]]:
    """Parse output of 'adb devices' or 'adb devices -l'."""
    devices = []
    if not text:
        return devices

    for line in text.splitlines():
        line = line.strip()
        if (
            not line
            or line.startswith("List of devices attached")
            or line.startswith("* daemon")
        ):
            continue
        parts = line.split()
        if not parts:
            continue
        serial = parts[0]
        if len(parts) >= 2 and (
            (parts[1] == "no" and len(parts) >= 3 and parts[2].startswith("permission"))
            or parts[1].startswith("no-permission")
            or "no permissions" in line
        ):
            devices.append(
                {
                    "serial": serial,
                    "state": "no-permission",
                }
            )
            continue

        if len(parts) >= 2:
            status = parts[1]
            info = {}
            for part in parts[2:]:
                if ":" in part:
                    key, val = part.split(":", 1)
                    info[key] = val
            devices.append(
                {
                    "serial": serial,
                    "status": status,
                    "model": info.get("model", "Unknown"),
                    "device": info.get("device", "Unknown"),
                    "product": info.get("product", "Unknown"),
                    "transport_id": info.get("transport_id", ""),
                }
            )

    return devices


class ADBManager:
    """Manages ADB connections to Quest devices."""

    QUEST_MODELS = {
        "Quest 2": ["Quest 2", "Hollywood"],
        "Quest 3": ["Quest 3", "Eureka"],
        "Quest Pro": ["Quest Pro", "Seacliff"],
        "Quest 3S": ["Quest 3S"],
    }

    def __init__(self, adb_path: Optional[str] = None, platform=None):
        self.platform = platform or qt_platform.current()
        self._no_window = self.platform.no_window_flags()
        self.adb_path = adb_path or self._find_adb()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitoring = False
        self._on_device_connected: Optional[Callable] = None
        self._on_device_disconnected: Optional[Callable] = None
        self._current_serial: Optional[str] = None
        # The Error line of the last `am start` reply, if Android refused it.
        self.last_client_error: Optional[str] = None
        # The exit code of that same call, for failures with no Error line.
        self.last_client_exit_code: Optional[int] = None

    def _find_adb(self) -> str:
        """Locate ADB binary. Check next to exe (frozen builds) first,
        then next to script, then PATH."""
        base_dirs = []
        # Frozen exe (PyInstaller): check next to the real .exe first
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            base_dirs.append(os.path.join(exe_dir, "tools"))
        # Script mode: check next to the .py file
        here = os.path.dirname(__file__)
        base_dirs += [
            os.path.join(here, "tools"),
            os.path.join(here, "tools", "platform-tools"),
        ]
        return self.platform.tool_path("adb", base_dirs) or "adb"

    def is_adb_available(self) -> bool:
        """Check if ADB is reachable."""
        try:
            result = subprocess.run(
                [self.adb_path, "version"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=self._no_window,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def run_adb(self, *args, timeout=10) -> subprocess.CompletedProcess:
        """Run an ADB command and return the result."""
        cmd = [self.adb_path] + list(args)
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=self._no_window,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, 1, "", "Timeout")
        except FileNotFoundError:
            return subprocess.CompletedProcess(cmd, 1, "", "ADB not found")

    def get_devices(self) -> List[Dict[str, str]]:
        """List connected ADB devices with their status."""
        result = self.run_adb("devices", "-l")
        if result.returncode != 0:
            return []
        return parse_devices(result.stdout)

    def find_quest(self) -> Optional[Dict[str, str]]:
        """Find a connected Quest device."""
        devices = self.get_devices()
        for dev in devices:
            model = dev.get("model", "").lower()
            product = dev.get("product", "").lower()
            device = dev.get("device", "").lower()
            combined = f"{model} {product} {device}"

            if any(
                q.lower() in combined
                for variants in self.QUEST_MODELS.values()
                for q in variants
            ):
                return dev
            if "quest" in combined or "oculus" in combined or "meta" in combined:
                return dev

        usb_devices = [
            d
            for d in devices
            if d.get("status") == "device"
            and not d.get("serial", "").startswith("emulator")
        ]
        if len(usb_devices) == 1:
            return usb_devices[0]

        return None

    def find_serial(self) -> Optional[str]:
        """Serial of the ready Quest, or None (no headset, unauthorized, or offline)."""
        quest = self.find_quest()
        if not quest:
            return None
        if quest.get("status", "device") != "device":
            return None
        return quest.get("serial") or None

    def get_device_details(self, serial: str) -> Dict[str, str]:
        """Get device model, battery, and VPN IP. Minimal ADB calls."""
        info = {}

        # Model — single getprop call
        result = self.run_adb("-s", serial, "shell", "getprop", "ro.product.model")
        if result.returncode == 0:
            info["model"] = result.stdout.strip()

        # Battery
        result = self.run_adb("-s", serial, "shell", "dumpsys", "battery")
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "level:" in line:
                    info["battery_level"] = line.split(":")[-1].strip() + "%"
                elif "status:" in line:
                    status_code = line.split(":")[-1].strip()
                    status_map = {
                        "1": "Unknown",
                        "2": "Charging",
                        "3": "Discharging",
                        "4": "Not Charging",
                        "5": "Full",
                    }
                    info["battery_status"] = status_map.get(status_code, status_code)

        # VPN IP
        result = self.run_adb("-s", serial, "shell", "ip", "addr", "show", "tun0")
        if result.returncode == 0 and "inet " in result.stdout:
            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
            if match:
                info["vpn_ip"] = match.group(1)

        return info

    def _am_start_error(self, result: subprocess.CompletedProcess) -> Optional[str]:
        # Pattern from gnirehtet-gui (Apache-2.0): `am start` exits 0 even when
        # Android refuses the intent, so the reply text is the real verdict.
        self.last_client_exit_code = result.returncode
        error = am_result.am_start_error(result.stdout, result.stderr)
        self.last_client_error = error
        if error:
            logger.warning("%s", error)
        return error

    # KEYCODE_WAKEUP. The wake-before-intent step is from quest-vd-wired
    # (Apache-2.0, see NOTICE; #64).
    KEYCODE_WAKEUP = 224

    def wake_headset(self, serial: str, timeout: float = 3.0) -> bool:
        """Wake the headset screen so the next client intent is delivered (#64).

        Best effort: the caller goes on whether or not this works, because
        `am start` to a sleeping headset can be dropped silently.
        """
        result = self.run_adb(
            "-s",
            serial,
            "shell",
            "input",
            "keyevent",
            str(self.KEYCODE_WAKEUP),
            timeout=timeout,
        )
        return result.returncode == 0

    def stop_gnirehtet_client(self, serial: str, timeout: float = 10.0) -> bool:
        """Stop the gnirehtet VPN client on the device (wakes the headset first, #64)."""
        self.wake_headset(serial, timeout=min(timeout, 3.0))
        result = self.run_adb(
            "-s",
            serial,
            "shell",
            "am",
            "start",
            "-n",
            "com.genymobile.gnirehtet/.GnirehtetActivity",
            "-a",
            "com.genymobile.gnirehtet.STOP",
            timeout=timeout,
        )
        if self._am_start_error(result):
            return False
        return result.returncode == 0

    def start_gnirehtet_client(self, serial: str, port: int = 31416) -> bool:
        """(Re)start the Quest-side VPN client: reverse tunnel + START intent.

        Safe to call while the relay is running. `gnirehtet run` only tries this
        once at launch, so a missed attempt leaves the relay up with no tunnel.
        """
        self.run_adb("-s", serial, "reverse", "localabstract:gnirehtet", f"tcp:{port}")
        self.wake_headset(serial)
        result = self.run_adb(
            "-s",
            serial,
            "shell",
            "am",
            "start",
            "-n",
            "com.genymobile.gnirehtet/.GnirehtetActivity",
            "-a",
            "com.genymobile.gnirehtet.START",
        )
        if self._am_start_error(result):
            return False
        return result.returncode == 0

    def restart_adb_server(self) -> None:
        """Kill and restart the ADB server (clears wedged transports)."""
        self.run_adb("kill-server", timeout=8)
        time.sleep(1.0)
        self.run_adb("start-server", timeout=15)

    def is_vpn_up(self, serial: str) -> bool:
        """Check if tun0 exists and has an IP — 1 ADB call."""
        result = self.run_adb("-s", serial, "shell", "ip", "addr", "show", "tun0")
        return result.returncode == 0 and "inet " in result.stdout

    def is_vpn_down(self, serial: str) -> bool:
        """Check if the gnirehtet VPN interface (tun0) is gone."""
        result = self.run_adb("-s", serial, "shell", "ip", "link", "show", "tun0")
        return result.returncode != 0 or "does not exist" in result.stderr

    def vpn_agent_state(self, serial: str) -> Optional[str]:
        """The gnirehtet VPN agent's state word from `dumpsys connectivity`, or None
        when it is not listed or the read failed (#64). Diagnostic: the disconnect
        log uses it when tun0 outlives the STOP intent."""
        result = self.run_adb("-s", serial, "shell", "dumpsys", "connectivity", timeout=5)
        if result.returncode != 0:
            return None
        return vpn_state.gnirehtet_vpn_state(result.stdout)

    def force_stop_gnirehtet(self, serial: str) -> bool:
        """Force-stop the gnirehtet app."""
        result = self.run_adb(
            "-s", serial, "shell", "am", "force-stop", "com.genymobile.gnirehtet"
        )
        return result.returncode == 0

    def check_wifi_connectivity(self, serial: str) -> bool:
        """Check Wi-Fi routing AND DNS are restored after VPN teardown."""
        # Raw IP connectivity
        result = self.run_adb(
            "-s",
            serial,
            "shell",
            "sh",
            "-c",
            "echo -n | timeout 3 toybox nc -w 2 8.8.8.8 443 && echo OK || echo FAIL",
            timeout=8,
        )
        if result.returncode != 0 or "OK" not in result.stdout:
            return False

        # DNS resolution
        result = self.run_adb(
            "-s",
            serial,
            "shell",
            "sh",
            "-c",
            "echo -n | timeout 3 toybox nc -w 2 connectivitycheck.gstatic.com 80 && echo OK || echo FAIL",
            timeout=8,
        )
        return result.returncode == 0 and "OK" in result.stdout

    def remove_all_reverse_tunnels(self, serial: str) -> bool:
        """Remove ALL ADB reverse tunnels."""
        result = self.run_adb("-s", serial, "reverse", "--remove-all")
        return result.returncode == 0

    def toggle_wifi(self, serial: str, wait_seconds: float = 4.0) -> bool:
        """Toggle Wi-Fi off/on to force DHCP re-acquisition (restores DNS)."""
        self.run_adb("-s", serial, "shell", "svc", "wifi", "disable")
        time.sleep(1.5)
        self.run_adb("-s", serial, "shell", "svc", "wifi", "enable")
        time.sleep(wait_seconds)
        return True

    def get_tun0_stats(self, serial: str) -> Optional[Dict[str, int]]:
        """Read tun0 rx/tx byte counters from /proc/net/dev. 1 ADB call."""
        result = self.run_adb("-s", serial, "shell", "cat", "/proc/net/dev")
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "tun0" in line:
                    parts = line.split()
                    if len(parts) >= 10:
                        return {
                            "rx_bytes": int(parts[1]),
                            "tx_bytes": int(parts[9]),
                        }
        return None

    # --- USB Monitoring ---

    def start_monitoring(
        self, on_connected=None, on_disconnected=None, poll_interval=2.0
    ):
        """Start background thread that polls for Quest USB connection."""
        self._on_device_connected = on_connected
        self._on_device_disconnected = on_disconnected
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, args=(poll_interval,), daemon=True
        )
        self._monitor_thread.start()

    def stop_monitoring(self):
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

    def _monitor_loop(self, interval: float):
        while self._monitoring:
            try:
                quest = self.find_quest()
                if quest and quest.get("status") == "device":
                    if self._current_serial != quest["serial"]:
                        self._current_serial = quest["serial"]
                        if self._on_device_connected:
                            self._on_device_connected(quest)
                elif quest and quest.get("status") == "unauthorized":
                    if self._current_serial != quest["serial"]:
                        self._current_serial = quest["serial"]
                        quest["_unauthorized"] = True
                        if self._on_device_connected:
                            self._on_device_connected(quest)
                else:
                    if self._current_serial is not None:
                        self._current_serial = None
                        if self._on_device_disconnected:
                            self._on_device_disconnected()
            except Exception:
                pass
            time.sleep(interval)
