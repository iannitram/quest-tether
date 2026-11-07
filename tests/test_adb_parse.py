"""adb devices parsing and the Linux udev rule files (no adb, no headset): python -m unittest discover tests"""
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


class AmStartClient(unittest.TestCase):
    def make_manager(self):
        platform = Mock()
        platform.no_window_flags.return_value = 0
        return adb_manager.ADBManager(adb_path="adb", platform=platform)

    @staticmethod
    def result(returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_start_refused_with_activity_error_returns_false(self):
        manager = self.make_manager()
        outputs = [
            self.result(),
            self.result(),  # wake (#64)
            self.result(
                stdout="Starting: Intent { ... }\n"
                "Error: Activity class {com.genymobile.gnirehtet/"
                "com.genymobile.gnirehtet.GnirehtetActivity} does not exist.\n"
            ),
        ]
        with patch.object(manager, "run_adb", side_effect=outputs):
            self.assertFalse(manager.start_gnirehtet_client("quest-serial"))
        self.assertEqual(
            manager.last_client_error,
            "Error: Activity class {com.genymobile.gnirehtet/"
            "com.genymobile.gnirehtet.GnirehtetActivity} does not exist.",
        )

    def test_start_refused_with_error_type_3_returns_false(self):
        manager = self.make_manager()
        outputs = [self.result(), self.result(), self.result(stderr="Error type 3\n")]
        with patch.object(manager, "run_adb", side_effect=outputs):
            self.assertFalse(manager.start_gnirehtet_client("quest-serial"))
        self.assertEqual(manager.last_client_error, "Error type 3")

    def test_start_with_normal_output_returns_true(self):
        manager = self.make_manager()
        outputs = [
            self.result(),
            self.result(),  # wake (#64)
            self.result(
                stdout="Starting: Intent { act=com.genymobile.gnirehtet.START "
                "cmp=com.genymobile.gnirehtet/.GnirehtetActivity }\n"
            ),
        ]
        with patch.object(manager, "run_adb", side_effect=outputs):
            self.assertTrue(manager.start_gnirehtet_client("quest-serial"))
        self.assertIsNone(manager.last_client_error)

    def test_start_with_empty_output_keeps_the_return_code(self):
        manager = self.make_manager()
        with patch.object(
            manager, "run_adb", side_effect=[self.result(), self.result(), self.result()]
        ):
            self.assertTrue(manager.start_gnirehtet_client("quest-serial"))

    def test_start_wakes_the_headset_before_the_start_intent(self):
        manager = self.make_manager()
        with patch.object(manager, "run_adb", return_value=self.result()) as run:
            manager.start_gnirehtet_client("quest-serial")
        argvs = [c.args for c in run.call_args_list]
        wake = argvs.index(("-s", "quest-serial", "shell", "input", "keyevent", "224"))
        start = next(i for i, a in enumerate(argvs) if "com.genymobile.gnirehtet.START" in a)
        self.assertLess(wake, start)

    def test_stop_wakes_the_headset_before_the_stop_intent(self):
        manager = self.make_manager()
        with patch.object(manager, "run_adb", return_value=self.result()) as run:
            self.assertTrue(manager.stop_gnirehtet_client("quest-serial"))
        argvs = [c.args for c in run.call_args_list]
        self.assertEqual(argvs[0], ("-s", "quest-serial", "shell", "input", "keyevent", "224"))
        self.assertIn("com.genymobile.gnirehtet.STOP", argvs[1])

    def test_failed_wake_does_not_stop_the_stop_intent(self):
        manager = self.make_manager()
        outputs = [self.result(returncode=1, stderr="device offline"), self.result()]
        with patch.object(manager, "run_adb", side_effect=outputs) as run:
            self.assertTrue(manager.stop_gnirehtet_client("quest-serial"))
        self.assertEqual(run.call_count, 2)

    def test_stop_refused_returns_false(self):
        manager = self.make_manager()
        with patch.object(
            manager,
            "run_adb",
            return_value=self.result(stdout="Error: not installed\n"),
        ):
            self.assertFalse(manager.stop_gnirehtet_client("quest-serial"))
        self.assertEqual(manager.last_client_error, "Error: not installed")


class VpnAgentState(unittest.TestCase):
    @staticmethod
    def make_manager():
        platform = Mock()
        platform.no_window_flags.return_value = 0
        return adb_manager.ADBManager(adb_path="adb", platform=platform)

    def test_reads_the_state_word_from_dumpsys(self):
        manager = self.make_manager()
        dumpsys = "NetworkAgentInfo{network{99}} ni{VPN CONNECTED sessionId=Gnirehtet}\n"
        with patch.object(manager, "run_adb", return_value=subprocess.CompletedProcess([], 0, dumpsys, "")):
            self.assertEqual(manager.vpn_agent_state("quest-serial"), "CONNECTED")

    def test_unreadable_dumpsys_is_none(self):
        manager = self.make_manager()
        with patch.object(manager, "run_adb", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertIsNone(manager.vpn_agent_state("quest-serial"))


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
