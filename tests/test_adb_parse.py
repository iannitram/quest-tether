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

    def test_start_wakes_the_headset_before_the_reverse_mapping(self):
        manager = self.make_manager()
        with patch.object(manager, "run_adb", return_value=self.result()) as run:
            manager.start_gnirehtet_client("quest-serial")
        argvs = [c.args for c in run.call_args_list]
        wake = argvs.index(("-s", "quest-serial", "shell", "input", "keyevent", "224"))
        reverse = next(i for i, a in enumerate(argvs) if "reverse" in a)
        self.assertLess(wake, reverse)

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


class HeadsetAwake(unittest.TestCase):
    """is_headset_awake (#65): one dumpsys power call; None when unknown; never raises."""

    def make_manager(self):
        platform = Mock()
        platform.no_window_flags.return_value = 0
        return adb_manager.ADBManager(adb_path="adb", platform=platform)

    @staticmethod
    def result(returncode=0, stdout=""):
        return subprocess.CompletedProcess([], returncode, stdout, "")

    def probe(self, *reply):
        manager = self.make_manager()
        with patch.object(manager, "run_adb", side_effect=list(reply)) as run:
            value = manager.is_headset_awake("quest-serial")
        return value, run

    def test_awake_is_true(self):
        value, _ = self.probe(self.result(stdout="  mWakefulness=Awake\n"))
        self.assertIs(value, True)

    def test_asleep_and_dozing_are_false(self):
        self.assertIs(self.probe(self.result(stdout="mWakefulness=Asleep\n"))[0], False)
        self.assertIs(self.probe(self.result(stdout="mWakefulness=Dozing\n"))[0], False)

    def test_no_wakefulness_line_is_unknown(self):
        value, _ = self.probe(self.result(stdout="Power Manager state\n"))
        self.assertIsNone(value)

    def test_failed_or_timed_out_read_is_unknown(self):
        self.assertIsNone(self.probe(self.result(returncode=1, stdout="mWakefulness=Awake"))[0])
        self.assertIsNone(self.probe(self.result(returncode=1, stdout="Timeout"))[0])

    def test_an_exception_from_run_adb_is_unknown(self):
        manager = self.make_manager()
        with patch.object(manager, "run_adb", side_effect=OSError("boom")):
            self.assertIsNone(manager.is_headset_awake("quest-serial"))

    def test_one_adb_call_with_a_short_timeout(self):
        _, run = self.probe(self.result(stdout="mWakefulness=Awake"))
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args, ("-s", "quest-serial", "shell", "dumpsys", "power"))
        self.assertEqual(kwargs, {"timeout": 3.0})


class DeviceListed(unittest.TestCase):
    """device_listed (#65): the serial in `adb devices -l` in any state; None when the call fails."""

    OFFLINE = "1WMHH111111111\toffline usb:1-1 transport_id:3"
    UNAUTH = "1WMHH111111111\tunauthorized usb:1-1 transport_id:3"

    def listed(self, reply, serial="1WMHH111111111"):
        manager = HeadsetAwake.make_manager(None)
        with patch.object(manager, "run_adb", return_value=reply) as run:
            value = manager.device_listed(serial)
        run.assert_called_once_with("devices", "-l")
        return value

    @staticmethod
    def reply(*lines, returncode=0):
        text = "List of devices attached\n" + "".join(line + "\n" for line in lines)
        return subprocess.CompletedProcess([], returncode, text, "")

    def test_a_ready_headset_is_listed(self):
        self.assertIs(self.listed(self.reply(DEVICE_LINE)), True)

    def test_an_offline_line_is_listed_though_find_quest_cannot_see_it(self):
        reply = self.reply(self.OFFLINE)
        self.assertIs(self.listed(reply), True)
        manager = HeadsetAwake.make_manager(None)
        with patch.object(manager, "run_adb", return_value=reply):
            self.assertIsNone(manager.find_quest(), "the line has no model or product")

    def test_an_unauthorized_line_is_listed(self):
        self.assertIs(self.listed(self.reply(self.UNAUTH)), True)

    def test_a_no_permission_line_is_listed(self):
        self.assertIs(self.listed(self.reply(NO_PERM_LINE), serial="1WMHH000000000"), True)

    def test_a_list_without_the_serial_is_not_listed(self):
        self.assertIs(self.listed(self.reply(DEVICE_LINE), serial="OTHER"), False)
        self.assertIs(self.listed(self.reply()), False)

    def test_a_failed_or_timed_out_devices_call_is_unknown(self):
        failed = subprocess.CompletedProcess([], 1, "", "Timeout")
        self.assertIsNone(self.listed(failed))

    def test_an_exception_from_run_adb_is_unknown(self):
        manager = HeadsetAwake.make_manager(None)
        with patch.object(manager, "run_adb", side_effect=OSError("boom")):
            self.assertIsNone(manager.device_listed("1WMHH111111111"))


class GnirehtetReverseOnly(unittest.TestCase):
    """Disconnect removes gnirehtet's reverse mapping and leaves other tools' alone (#142)."""

    OTHER = "UsbFfs localabstract:other tcp:9222\n"
    LIST = OTHER + "UsbFfs localabstract:gnirehtet tcp:31416\n"

    def make_manager(self):
        platform = Mock()
        platform.no_window_flags.return_value = 0
        return adb_manager.ADBManager(adb_path="adb", platform=platform)

    @staticmethod
    def result(returncode=0, stdout=""):
        return subprocess.CompletedProcess([], returncode, stdout, "")

    def test_list_parser(self):
        self.assertTrue(adb_manager.has_gnirehtet_reverse(self.LIST))
        self.assertFalse(adb_manager.has_gnirehtet_reverse(self.OTHER))
        self.assertFalse(adb_manager.has_gnirehtet_reverse(
            "UsbFfs localabstract:gnirehtet-extra tcp:31416\n"))
        self.assertFalse(adb_manager.has_gnirehtet_reverse(""))
        self.assertFalse(adb_manager.has_gnirehtet_reverse(None))

    def test_removes_only_the_gnirehtet_mapping(self):
        manager = self.make_manager()
        outputs = [self.result(stdout=self.LIST), self.result()]
        with patch.object(manager, "run_adb", side_effect=outputs) as run:
            self.assertTrue(manager.remove_gnirehtet_reverse("quest-serial"))
        self.assertEqual([c.args for c in run.call_args_list], [
            ("-s", "quest-serial", "reverse", "--list"),
            ("-s", "quest-serial", "reverse", "--remove", "localabstract:gnirehtet"),
        ])

    def test_nothing_removed_when_gnirehtet_is_not_listed(self):
        manager = self.make_manager()
        with patch.object(manager, "run_adb", side_effect=[self.result(stdout=self.OTHER)]) as run:
            self.assertFalse(manager.remove_gnirehtet_reverse("quest-serial"))
        self.assertEqual(run.call_count, 1)

    def test_nothing_removed_when_the_list_fails(self):
        manager = self.make_manager()
        with patch.object(manager, "run_adb", side_effect=[self.result(returncode=1)]) as run:
            self.assertFalse(manager.remove_gnirehtet_reverse("quest-serial"))
        self.assertEqual(run.call_count, 1)

    def test_remove_all_is_never_called(self):
        for name in os.listdir(ROOT):
            if name.endswith(".py"):
                with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                    self.assertNotIn('"--remove-all"', f.read(), name)


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
