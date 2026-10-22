"""
ADB Device Manager
Handles Quest detection, ADB commands, and USB state monitoring.

Cross-platform: the bundled binary is adb.exe on Windows and adb on
Linux/macOS. Every ``adb shell`` command runs on the Quest (Android), so the
device-side logic is identical regardless of host OS.
"""

import threading
import time
import re
from typing import Optional, Callable, Dict, List

from . import platform_support as ps


class ADBManager:
    """Manages ADB connections to Quest devices."""

    QUEST_MODELS = {
        "Quest 2": ["Quest 2", "Hollywood"],
        "Quest 3": ["Quest 3", "Eureka"],
        "Quest Pro": ["Quest Pro", "Seacliff"],
        "Quest 3S": ["Quest 3S", "Panther"],
    }

    def __init__(self, adb_path: Optional[str] = None):
        self.adb_path = adb_path or self._find_adb()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitoring = False
        self._on_device_connected: Optional[Callable] = None
        self._on_device_disconnected: Optional[Callable] = None
        self._current_key = None  # (serial, status) of the last-reported device

    def _find_adb(self) -> str:
        """Locate the ADB binary: bundled tools/ first, then PATH."""
        if ps.IS_WINDOWS:
            candidates = ["adb.exe", "platform-tools/adb.exe"]
        else:
            candidates = ["adb", "platform-tools/adb"]
        return ps.find_binary(candidates, env_var="ADB")

    def is_adb_available(self) -> bool:
        """Check if ADB is reachable."""
        result = ps.run([self.adb_path, "version"], timeout=5)
        return result.returncode == 0

    def run_adb(self, *args, timeout=10):
        """Run an ADB command and return the CompletedProcess."""
        return ps.run([self.adb_path] + list(args), timeout=timeout)

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

        usb_devices = [d for d in devices if not d["serial"].startswith("emulator")]
        authorized = [d for d in usb_devices if d["status"] == "device"]
        if len(authorized) == 1:
            return authorized[0]
        # A lone device that isn't fully 'device' yet (unauthorized/offline) is
        # almost certainly the Quest the user just plugged in — return it so the
        # caller can surface the real status instead of "no device".
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

    def start_gnirehtet_client(self, serial: str, relay_port: int = 31416) -> bool:
        """(Re)start the gnirehtet VPN client: wake device, reverse tunnel, start intent.

        gnirehtet run only attempts this once at launch; if the headset is asleep
        or adb hiccups during USB enumeration, that attempt fails silently and the
        relay sits there with no tunnel. This lets us retry it ourselves.
        """
        self.run_adb("-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
        self.run_adb("-s", serial, "reverse",
                     "localabstract:gnirehtet", f"tcp:{relay_port}")
        result = self.run_adb(
            "-s", serial, "shell",
            "am", "start",
            "-n", "com.genymobile.gnirehtet/.GnirehtetActivity",
            "-a", "com.genymobile.gnirehtet.START"
        )
        return result.returncode == 0

    def stop_gnirehtet_client(self, serial: str) -> bool:
        """Stop the gnirehtet VPN client on the device."""
        result = self.run_adb(
            "-s", serial, "shell",
            "am", "start",
            "-n", "com.genymobile.gnirehtet/.GnirehtetActivity",
            "-a", "com.genymobile.gnirehtet.STOP"
        )
        return result.returncode == 0

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
                if quest and quest["status"] in ("device", "unauthorized"):
                    # Key on (serial, status), not serial alone: an in-place
                    # unauthorized -> device transition (user accepts the ADB
                    # prompt without replugging) must re-fire on_connected so the
                    # UI leaves its "unauthorized" state.
                    key = (quest["serial"], quest["status"])
                    if self._current_key != key:
                        self._current_key = key
                        if quest["status"] == "unauthorized":
                            quest["_unauthorized"] = True
                        if self._on_device_connected:
                            self._on_device_connected(quest)
                else:
                    if self._current_key is not None:
                        self._current_key = None
                        if self._on_device_disconnected:
                            self._on_device_disconnected()
            except Exception:
                pass
            time.sleep(interval)
