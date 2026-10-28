"""adb devices parsing and the Linux udev rule files (no adb, no headset): python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, ".")
import adb_manager  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

DEVICE_LINE = "1WMHH111111111\tdevice product:hollywood model:Quest_2 device:hollywood transport_id:1"
NO_PERM_LINE = ("1WMHH000000000\tno permissions (missing udev rules? user is in the plugdev group); "
                "see [http://developer.android.com/tools/device.html]")
TEXT = "List of devices attached\n" + DEVICE_LINE + "\n" + NO_PERM_LINE + "\n"


class ParseDevices(unittest.TestCase):
    def test_one_device_entry_and_one_no_permission_entry(self):
        devices = adb_manager.parse_devices(TEXT)
        self.assertEqual(len(devices), 2)
        ok = [d for d in devices if d["serial"] == "1WMHH111111111"]
        bad = [d for d in devices if d["serial"] == "1WMHH000000000"]
        self.assertEqual(len(ok), 1)
        self.assertEqual(ok[0]["status"], "device")
        self.assertEqual(ok[0]["model"], "Quest_2")
        self.assertEqual(bad, [{"serial": "1WMHH000000000", "state": "no-permission"}])

    def test_input_is_not_changed(self):
        text = str(TEXT)
        adb_manager.parse_devices(text)
        self.assertEqual(text, TEXT)


class UdevFiles(unittest.TestCase):
    def test_rule_file_is_exactly_one_line(self):
        with open(os.path.join(ROOT, "packaging", "linux", "51-quest-tether.rules")) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines, ['SUBSYSTEM=="usb", ATTR{idVendor}=="2833", MODE="0660", TAG+="uaccess"'])

    def test_linux_guide_has_the_udev_section(self):
        with open(os.path.join(ROOT, "docs", "linux.md")) as f:
            text = f.read()
        for needle in ("USB access (udev rule)", "/etc/udev/rules.d/",
                       "sudo udevadm control --reload-rules && sudo udevadm trigger",
                       "/etc/udev/rules.d/51-quest-tether.rules"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
