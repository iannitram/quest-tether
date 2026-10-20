"""
ADB Device Manager
Handles Quest detection, ADB commands, and USB state monitoring.
"""

import subprocess
import sys
import threading
import time
import os
import re
from typing import Optional, Callable, Dict, List

# Suppress console windows for all subprocess calls on Windows
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class ADBManager:
    """Manages ADB connections to Quest devices."""

    QUEST_MODELS = {
        "Quest 2": ["Quest 2", "Hollywood"],
        "Quest 3": ["Quest 3", "Eureka"],
        "Quest Pro": ["Quest Pro", "Seacliff"],
        "Quest 3S": ["Quest 3S"],
    }

    def __init__(self, adb_path: Optional[str] = None):
        self.adb_path = adb_path or self._find_adb()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitoring = False
        self._on_device_connected: Optional[Callable] = None
        self._on_device_disconnected: Optional[Callable] = None
        self._current_serial: Optional[str] = None

    def _find_adb(self) -> str:
        """Locate ADB binary. Check next to exe (frozen builds) first,
        then next to script, then PATH."""
        local_paths = []
        # Frozen exe (PyInstaller): check next to the real .exe first
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            local_paths.append(os.path.join(exe_dir, "tools", "adb.exe"))
        # Script mode: check next to the .py file
        local_paths += [
            os.path.join(os.path.dirname(__file__), "tools", "adb.exe"),
            os.path.join(os.path.dirname(__file__), "tools", "platform-tools", "adb.exe"),
            os.path.join(os.path.dirname(__file__), "tools", "adb"),
        ]
        for p in local_paths:
            if os.path.isfile(p):
                return p

        try:
            result = subprocess.run(
                ["where", "adb"] if os.name == "nt" else ["which", "adb"],
                capture_output=True, text=True, timeout=5,
                creationflags=_NO_WINDOW,
            )
            if result.returncode == 0:
                return result.stdout.strip().split("\n")[0].strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return "adb"

    def is_adb_available(self) -> bool:
        """Check if ADB is reachable."""
        try:
            result = subprocess.run(
                [self.adb_path, "version"],
                capture_output=True, text=True, timeout=5,
                creationflags=_NO_WINDOW,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def run_adb(self, *args, timeout=10) -> subprocess.CompletedProcess:
        """Run an ADB command and return the result."""
        cmd = [self.adb_path] + list(args)
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                  creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, 1, "", "Timeout")
        except FileNotFoundError:
            return subprocess.CompletedProcess(cmd, 1, "", "ADB not found")

    def get_devices(self) -> List[Dict[str, str]]:
        """List connected ADB devices with their status."""
        result = self.run_adb("devices", "-l")
        devices = []
        if result.returncode != 0:
            return devices

        for line in result.stdout.strip().split("\n")[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                status = parts[1]
                info = {}
                for part in parts[2:]:
                    if ":" in part:
                        key, val = part.split(":", 1)
                        info[key] = val
                devices.append({
                    "serial": serial,
                    "status": status,
                    "model": info.get("model", "Unknown"),
                    "device": info.get("device", "Unknown"),
                    "product": info.get("product", "Unknown"),
                    "transport_id": info.get("transport_id", ""),
                })
        return devices

    def find_quest(self) -> Optional[Dict[str, str]]:
        """Find a connected Quest device."""
        devices = self.get_devices()
        for dev in devices:
            model = dev.get("model", "").lower()
            product = dev.get("product", "").lower()
            device = dev.get("device", "").lower()
            combined = f"{model} {product} {device}"

            if any(q.lower() in combined for variants in self.QUEST_MODELS.values() for q in variants):
                return dev
            if "quest" in combined or "oculus" in combined or "meta" in combined:
                return dev

        usb_devices = [d for d in devices if d["status"] == "device" and not d["serial"].startswith("emulator")]
        if len(usb_devices) == 1:
            return usb_devices[0]

        return None

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
                    status_map = {"1": "Unknown", "2": "Charging", "3": "Discharging",
                                  "4": "Not Charging", "5": "Full"}
                    info["battery_status"] = status_map.get(status_code, status_code)

        # VPN IP
        result = self.run_adb("-s", serial, "shell", "ip", "addr", "show", "tun0")
        if result.returncode == 0 and "inet " in result.stdout:
            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
            if match:
                info["vpn_ip"] = match.group(1)

        return info

    def stop_gnirehtet_client(self, serial: str) -> bool:
        """Stop the gnirehtet VPN client on the device."""
        result = self.run_adb(
            "-s", serial, "shell",
            "am", "start",
            "-n", "com.genymobile.gnirehtet/.GnirehtetActivity",
            "-a", "com.genymobile.gnirehtet.STOP"
        )
        return result.returncode == 0

    def start_gnirehtet_client(self, serial: str, port: int = 31416) -> bool:
        """(Re)start the Quest-side VPN client: reverse tunnel + START intent.

        Safe to call while the relay is running. `gnirehtet run` only tries this
        once at launch, so a missed attempt leaves the relay up with no tunnel.
        """
        self.run_adb("-s", serial, "reverse", "localabstract:gnirehtet", f"tcp:{port}")
        result = self.run_adb(
            "-s", serial, "shell",
            "am", "start",
            "-n", "com.genymobile.gnirehtet/.GnirehtetActivity",
            "-a", "com.genymobile.gnirehtet.START"
        )
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

    def force_stop_gnirehtet(self, serial: str) -> bool:
        """Force-stop the gnirehtet app."""
        result = self.run_adb(
            "-s", serial, "shell",
            "am", "force-stop", "com.genymobile.gnirehtet"
        )
        return result.returncode == 0

    def check_wifi_connectivity(self, serial: str) -> bool:
        """Check Wi-Fi routing AND DNS are restored after VPN teardown."""
        # Raw IP connectivity
        result = self.run_adb(
            "-s", serial, "shell", "sh", "-c",
            "echo -n | timeout 3 toybox nc -w 2 8.8.8.8 443 && echo OK || echo FAIL",
            timeout=8
        )
        if result.returncode != 0 or "OK" not in result.stdout:
            return False

        # DNS resolution
        result = self.run_adb(
            "-s", serial, "shell", "sh", "-c",
            "echo -n | timeout 3 toybox nc -w 2 connectivitycheck.gstatic.com 80 && echo OK || echo FAIL",
            timeout=8
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

    def start_monitoring(self, on_connected=None, on_disconnected=None, poll_interval=2.0):
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
                if quest and quest["status"] == "device":
                    if self._current_serial != quest["serial"]:
                        self._current_serial = quest["serial"]
                        if self._on_device_connected:
                            self._on_device_connected(quest)
                elif quest and quest["status"] == "unauthorized":
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
