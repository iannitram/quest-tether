"""LinuxPlatform data_dir, tools, relay, stale relay, kill, Wi-Fi (all mocked): python -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, ".")
from qt_platform import linux  # noqa: E402
from qt_platform.linux import LinuxPlatform  # noqa: E402

SS = ('State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n'
      'LISTEN 0 50 127.0.0.1:31416 0.0.0.0:* users:(("gnirehtet",pid=4242,fd=7))\n'
      'LISTEN 0 50 [::]:31416 [::]:* users:(("java",pid=77,fd=9))\n')
CMDLINES = {4242: "/t/gnirehtet run X", 77: "/usr/bin/sshd -D"}


class Basics(unittest.TestCase):
    def test_name_and_no_window_flags(self):
        self.assertEqual(LinuxPlatform.name, "linux")
        self.assertEqual(LinuxPlatform().no_window_flags(), 0)

    def test_data_dir_is_created_under_xdg_state_home(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
            path = LinuxPlatform().data_dir()
            self.assertEqual(path, tmp + "/quest-tether")
            self.assertTrue(os.path.isdir(path))

    def test_relay_command_is_the_native_relay(self):
        self.assertEqual(LinuxPlatform().relay_command("/t", "S1"), ["/t/gnirehtet", "run", "S1"])


class ToolPath(unittest.TestCase):
    def test_first_base_dir_with_the_file_wins(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for d in (a, b):
                p = os.path.join(d, "adb")
                open(p, "w").close()
                os.chmod(p, 0o755)
            with mock.patch.object(linux, "_run") as run:
                self.assertEqual(LinuxPlatform().tool_path("adb", [a, b]), os.path.join(a, "adb"))
                run.assert_not_called()

    def test_falls_back_to_which(self):
        with tempfile.TemporaryDirectory() as a, mock.patch.object(linux, "_run", return_value="/usr/bin/adb\n") as run:
            self.assertEqual(LinuxPlatform().tool_path("adb", [a]), "/usr/bin/adb")
            run.assert_called_once_with(["which", "adb"])

    def test_missing_everywhere_is_none(self):
        with tempfile.TemporaryDirectory() as a, mock.patch.object(linux, "_run", return_value=""):
            self.assertIsNone(LinuxPlatform().tool_path("adb", [a]))


class StaleRelayAndKill(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(linux, "_read_cmdline", side_effect=lambda pid: CMDLINES.get(pid, ""))
        p.start()
        self.addCleanup(p.stop)

    def test_stale_relay_pids_keeps_only_gnirehtet(self):
        with mock.patch.object(linux, "_run", return_value=SS) as run:
            self.assertEqual(LinuxPlatform().stale_relay_pids(31416), [4242])
            run.assert_called_once_with(["ss", "-ltnp"])

    def test_stale_relay_pids_empty_when_ss_fails(self):
        with mock.patch.object(linux, "_run", return_value=""):
            self.assertEqual(LinuxPlatform().stale_relay_pids(31416), [])

    def test_kill_sends_sigkill_to_a_gnirehtet_pid(self):
        with mock.patch.object(linux.os, "kill") as kill:
            LinuxPlatform().kill(4242)
            kill.assert_called_once_with(4242, 9)

    def test_kill_refuses_a_pid_that_is_not_gnirehtet(self):
        with mock.patch.object(linux.os, "kill") as kill:
            LinuxPlatform().kill(77)
            LinuxPlatform().kill(999999)
            kill.assert_not_called()


class PcWifi(unittest.TestCase):
    def fake_run(self, answers):
        return mock.patch.object(linux, "_run", side_effect=lambda args, *a, **k: answers.get(tuple(args), ""))

    ACTIVE = ("nmcli", "-t", "-f", "active,ssid", "dev", "wifi")
    PSK = ("nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", "ExampleNet")

    def test_ssid_and_key(self):
        with self.fake_run({self.ACTIVE: "no:Other\nyes:ExampleNet\n", self.PSK: "example-pass\n"}):
            self.assertEqual(LinuxPlatform().pc_wifi(), ("ExampleNet", "example-pass"))

    def test_not_on_wifi_is_none(self):
        with self.fake_run({self.ACTIVE: "no:Other\n"}):
            self.assertIsNone(LinuxPlatform().pc_wifi())

    def test_no_saved_key_is_none(self):
        with self.fake_run({self.ACTIVE: "yes:ExampleNet\n", self.PSK: "\n"}):
            self.assertIsNone(LinuxPlatform().pc_wifi())


if __name__ == "__main__":
    unittest.main()
