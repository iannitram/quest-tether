"""LinuxPlatform data_dir, tools, relay, stale relay, kill, Wi-Fi (all mocked): python -m unittest discover -s tests"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qt_platform import linux  # noqa: E402
from qt_platform.linux import LinuxPlatform  # noqa: E402

SS = (
    "State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
    'LISTEN 0 50 127.0.0.1:31416 0.0.0.0:* users:(("gnirehtet",pid=4242,fd=7))\n'
    'LISTEN 0 50 [::]:31416 [::]:* users:(("java",pid=77,fd=9))\n'
    'LISTEN 0 50 [::]:31416 [::]:* users:(("other",pid=9000,fd=9))\n'
)
CMDLINES = {4242: "/t/gnirehtet run X", 77: "/usr/bin/sshd -D"}


class Basics(unittest.TestCase):
    def test_name_and_no_window_flags(self):
        self.assertEqual(LinuxPlatform.name, "linux")
        self.assertEqual(LinuxPlatform().no_window_flags(), 0)

    def test_data_dir_is_created_under_xdg_state_home(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
        ):
            path = LinuxPlatform().data_dir()
            self.assertEqual(path, tmp + "/quest-tether")
            self.assertTrue(os.path.isdir(path))

    def test_config_dir_is_created_under_xdg_config_home(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}),
        ):
            path = LinuxPlatform().config_dir()
            self.assertEqual(path, tmp + "/quest-tether")
            self.assertTrue(os.path.isdir(path))

    def test_relay_command_is_the_native_relay(self):
        self.assertEqual(
            LinuxPlatform().relay_command("/t", "S1"), ["/t/gnirehtet", "run", "S1"]
        )

    def test_relay_server_command_is_the_relay_alone(self):
        self.assertEqual(
            LinuxPlatform().relay_server_command("/t", 31416),
            ["/t/gnirehtet", "relay", "-p", "31416"],
        )


class ToolPath(unittest.TestCase):
    def test_first_base_dir_with_the_file_wins(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for d in (a, b):
                p = os.path.join(d, "adb")
                open(p, "w").close()
                os.chmod(p, 0o755)
            with mock.patch.object(linux, "_run") as run:
                self.assertEqual(
                    LinuxPlatform().tool_path("adb", [a, b]), os.path.join(a, "adb")
                )
                run.assert_not_called()

    def test_falls_back_to_which(self):
        with (
            tempfile.TemporaryDirectory() as a,
            mock.patch.object(linux, "_run", return_value="/usr/bin/adb\n") as run,
        ):
            self.assertEqual(LinuxPlatform().tool_path("adb", [a]), "/usr/bin/adb")
            run.assert_called_once_with(["which", "adb"])

    def test_missing_everywhere_is_none(self):
        with (
            tempfile.TemporaryDirectory() as a,
            mock.patch.object(linux, "_run", return_value=""),
        ):
            self.assertIsNone(LinuxPlatform().tool_path("adb", [a]))


class StaleRelayAndKill(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(
            linux, "_read_cmdline", side_effect=lambda pid: CMDLINES.get(pid, "")
        )
        p.start()
        self.addCleanup(p.stop)

    def test_stale_relay_pids_keeps_only_gnirehtet(self):
        with mock.patch.object(linux, "_run", return_value=SS) as run:
            self.assertEqual(LinuxPlatform().stale_relay_pids(31416), [4242])
            run.assert_called_once_with(["ss", "-ltnp"])

    def test_stale_relay_pids_fails_closed_when_the_command_line_is_unreadable(self):
        # 4242 is gnirehtet, 77 is another program and 9000 has no readable
        # command line: only the first may be treated as ours.
        with mock.patch.object(linux, "_run", return_value=SS):
            self.assertEqual(LinuxPlatform().stale_relay_pids(31416), [4242])

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

    def test_kill_tree_sends_sigkill(self):
        with mock.patch.object(linux.os, "kill") as kill:
            LinuxPlatform().kill_tree(4242)
            kill.assert_called_once_with(4242, 9)


class PcWifi(unittest.TestCase):
    def fake_run(self, answers):
        return mock.patch.object(
            linux,
            "_run",
            side_effect=lambda args, *a, **k: answers.get(tuple(args), ""),
        )

    ACTIVE = ("nmcli", "-t", "-f", "name,type", "connection", "show", "--active")

    @staticmethod
    def ssid(name):
        return ("nmcli", "-g", "802-11-wireless.ssid", "connection", "show", name)

    @staticmethod
    def psk(name):
        return (
            "nmcli",
            "-s",
            "-g",
            "802-11-wireless-security.psk",
            "connection",
            "show",
            name,
        )

    @staticmethod
    def security(name):
        return (
            "nmcli",
            "-g",
            "802-11-wireless-security.key-mgmt",
            "connection",
            "show",
            name,
        )

    def test_ssid_key_and_security(self):
        with self.fake_run(
            {
                self.ACTIVE: "lo:loopback\nExampleNet:802-11-wireless\n",
                self.ssid("ExampleNet"): "ExampleNet\n",
                self.psk("ExampleNet"): "example-pass\n",
                self.security("ExampleNet"): "wpa-psk\n",
            }
        ):
            self.assertEqual(
                LinuxPlatform().pc_wifi(),
                ("ExampleNet", "example-pass", "wpa2"),
            )

    def test_wpa3_connection_gives_wpa3(self):
        with self.fake_run(
            {
                self.ACTIVE: "ExampleNet:802-11-wireless\n",
                self.ssid("ExampleNet"): "ExampleNet\n",
                self.psk("ExampleNet"): "example-pass\n",
                self.security("ExampleNet"): "sae\n",
            }
        ):
            self.assertEqual(
                LinuxPlatform().pc_wifi(),
                ("ExampleNet", "example-pass", "wpa3"),
            )

    def test_connection_name_differs_from_ssid(self):
        with self.fake_run(
            {
                self.ACTIVE: "Wi-Fi 2:802-11-wireless\n",
                self.ssid("Wi-Fi 2"): "ExampleNet\n",
                self.psk("Wi-Fi 2"): "example-pass\n",
                self.security("Wi-Fi 2"): "wpa-psk\n",
            }
        ):
            self.assertEqual(
                LinuxPlatform().pc_wifi(),
                ("ExampleNet", "example-pass", "wpa2"),
            )

    def test_not_on_wifi_is_none(self):
        with self.fake_run({self.ACTIVE: "Wired:802-3-ethernet\n"}):
            self.assertIsNone(LinuxPlatform().pc_wifi())

    def test_no_ssid_is_none(self):
        with self.fake_run(
            {self.ACTIVE: "ExampleNet:802-11-wireless\n", self.ssid("ExampleNet"): "\n"}
        ):
            self.assertIsNone(LinuxPlatform().pc_wifi())

    def test_no_saved_key_is_none(self):
        with self.fake_run(
            {
                self.ACTIVE: "ExampleNet:802-11-wireless\n",
                self.ssid("ExampleNet"): "ExampleNet\n",
                self.psk("ExampleNet"): "\n",
            }
        ):
            self.assertIsNone(LinuxPlatform().pc_wifi())

    def test_no_known_security_is_none(self):
        with self.fake_run(
            {
                self.ACTIVE: "ExampleNet:802-11-wireless\n",
                self.ssid("ExampleNet"): "ExampleNet\n",
                self.psk("ExampleNet"): "example-pass\n",
                self.security("ExampleNet"): "none\n",
            }
        ):
            self.assertIsNone(LinuxPlatform().pc_wifi())


class PortHolders(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(
            linux, "_read_cmdline", side_effect=lambda pid: CMDLINES.get(pid, "")
        )
        p.start()
        self.addCleanup(p.stop)

    def test_lists_every_listener_with_whether_it_is_gnirehtet(self):
        # 4242 is gnirehtet, 77 is sshd, 9000 has no readable command line (not ours)
        with mock.patch.object(linux, "_run", return_value=SS) as run:
            self.assertEqual(
                LinuxPlatform().port_holders(31416),
                [(4242, True), (77, False), (9000, False)],
            )
            run.assert_called_once_with(["ss", "-ltnp"])

    def test_free_port_has_no_holders(self):
        with mock.patch.object(linux, "_run", return_value=SS):
            self.assertEqual(LinuxPlatform().port_holders(5555), [])

    def test_no_holders_when_ss_fails(self):
        with mock.patch.object(linux, "_run", return_value=""):
            self.assertEqual(LinuxPlatform().port_holders(31416), [])


if __name__ == "__main__":
    unittest.main()
