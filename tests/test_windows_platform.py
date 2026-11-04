"""Windows platform layer, no headset needed: python -m unittest discover -s tests

Everything here is mocked: subprocess (never a real netsh, netstat, taskkill or where),
the registry (a fake winreg), ctypes.windll. These run on Linux CI too.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qt_platform import windows  # noqa: E402
from qt_platform.windows import WindowsPlatform  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def completed(args, code=0, out=""):
    return subprocess.CompletedProcess(args, code, out, "")


NETSH_CONNECTED = """There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Example Wireless Adapter
    GUID                   : 00000000-0000-0000-0000-000000000000
    Physical address       : 00:00:00:00:00:00
    Interface type         : Primary
    State                  : connected
    SSID                   : ExampleNet
    BSSID                  : 00:00:00:00:00:01
    Network type           : Infrastructure
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Channel                : 6
    Profile                : ExampleNet
"""

NETSH_PROFILE = """Profile ExampleNet on interface Wi-Fi:
=======================================================================

Applied: All User Profile

Profile information
-------------------
    Version                : 1
    Type                   : Wireless LAN
    Name                   : ExampleNet

Security settings
-----------------
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Security key           : Present
    Key Content            : example-pass
"""


def netsh_fake(interfaces_out=NETSH_CONNECTED, profile_out=NETSH_PROFILE, calls=None):
    """subprocess.run stand-in: answers only the two netsh commands pc_wifi uses."""

    def run(args, **kw):
        if calls is not None:
            calls.append(list(args))
        if args[:2] == ["netsh", "wlan"] and args[2:4] == ["show", "interfaces"]:
            return completed(args, 0, interfaces_out)
        if args[:2] == ["netsh", "wlan"] and args[2:4] == ["show", "profile"]:
            return completed(args, 0, profile_out)
        raise AssertionError(f"unexpected command: {args}")

    return run


class RelayCommand(unittest.TestCase):
    def test_relay_command_is_java_jar_run_serial(self):
        with mock.patch.object(WindowsPlatform, "_find_java", return_value="java"):
            cmd = WindowsPlatform().relay_command("C:\\tools", "SERIAL123")
        self.assertEqual(
            cmd,
            [
                "java",
                "-jar",
                os.path.join("C:\\tools", "gnirehtet.jar"),
                "run",
                "SERIAL123",
            ],
        )

    def test_java_found_by_where_is_used(self):
        with mock.patch(
            "subprocess.run",
            return_value=completed(["where"], 0, "C:\\Java\\bin\\java.exe\r\n"),
        ):
            cmd = WindowsPlatform().relay_command("C:\\tools", "S1")
        self.assertEqual(cmd[0], "C:\\Java\\bin\\java.exe")


class ToolPath(unittest.TestCase):
    def test_finds_exe_in_base_dir_without_running_where(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "adb.exe")
            open(exe, "wb").close()
            with mock.patch("subprocess.run") as run:
                self.assertEqual(WindowsPlatform().tool_path("adb", [d]), exe)
            run.assert_not_called()

    def test_falls_back_to_path_via_where(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch(
                "subprocess.run",
                return_value=completed(["where", "adb"], 0, "C:\\sdk\\adb.exe\r\n"),
            ) as run,
        ):
            self.assertEqual(
                WindowsPlatform().tool_path("adb", [d]), "C:\\sdk\\adb.exe"
            )
        self.assertEqual(run.call_args[0][0], ["where", "adb"])

    def test_none_when_absent(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch("subprocess.run", side_effect=FileNotFoundError),
        ):
            self.assertIsNone(WindowsPlatform().tool_path("adb", [d]))

    def test_none_when_where_returns_nonzero(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch("subprocess.run", return_value=completed(["where"], 1, "")),
        ):
            self.assertIsNone(WindowsPlatform().tool_path("adb", [d]))


class Flags(unittest.TestCase):
    def test_no_window_flags_is_create_no_window(self):
        self.assertEqual(WindowsPlatform().no_window_flags(), 0x08000000)
        self.assertEqual(
            WindowsPlatform().no_window_flags(),
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )

    def test_data_dir_is_local_appdata_quest_tether(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(os.environ, {"LOCALAPPDATA": d}),
        ):
            got = WindowsPlatform().data_dir()
            self.assertEqual(got, os.path.join(d, "QuestTether"))
            self.assertTrue(os.path.isdir(got))


class PcWifi(unittest.TestCase):
    def test_disconnected_fixture_gives_none(self):
        path = os.path.join(FIXTURES, "netsh_interfaces_disconnected.txt")
        with open(path, encoding="utf-8", newline="") as f:
            fixture = f.read()
        calls = []
        with mock.patch(
            "subprocess.run",
            side_effect=netsh_fake(interfaces_out=fixture, calls=calls),
        ):
            self.assertIsNone(WindowsPlatform().pc_wifi())
            self.assertEqual(len(calls), 1)  # no SSID, so no profile lookup

    def test_connected_gives_ssid_key_and_security(self):
        calls = []
        with mock.patch("subprocess.run", side_effect=netsh_fake(calls=calls)):
            self.assertEqual(
                WindowsPlatform().pc_wifi(),
                ("ExampleNet", "example-pass", "wpa2"),
            )
        self.assertIn(
            ["netsh", "wlan", "show", "profile", "name=ExampleNet", "key=clear"], calls
        )

    def test_wpa3_profile_gives_wpa3(self):
        profile = NETSH_PROFILE.replace("WPA2-Personal", "WPA3-Personal")
        with mock.patch("subprocess.run", side_effect=netsh_fake(profile_out=profile)):
            self.assertEqual(
                WindowsPlatform().pc_wifi(),
                ("ExampleNet", "example-pass", "wpa3"),
            )

    def test_no_key_content_gives_none(self):
        profile = NETSH_PROFILE.replace("example-pass", "")
        with mock.patch("subprocess.run", side_effect=netsh_fake(profile_out=profile)):
            self.assertIsNone(WindowsPlatform().pc_wifi())

    def test_netsh_failures_give_none(self):
        cases = {
            "nonzero exit": lambda args, **kw: completed(args, 1, NETSH_CONNECTED),
            "not installed": mock.Mock(side_effect=FileNotFoundError),
            "timeout": mock.Mock(side_effect=subprocess.TimeoutExpired(["netsh"], 8)),
        }
        for label, fake in cases.items():
            with self.subTest(label), mock.patch("subprocess.run", side_effect=fake):
                self.assertIsNone(WindowsPlatform().pc_wifi())


def _netstat(lines):
    header = "Active Connections\n\n  Proto  Local Address          Foreign Address        State           PID\n"
    return header + "\n".join(lines) + "\n"


def _fake_run(netstat_text, cmdlines, killed):
    """subprocess.run stand-in for netstat, the PowerShell command-line lookup and taskkill."""

    def run(args, **kw):
        if args[0] == "netstat":
            return completed(args, 0, netstat_text)
        if args[0] == "powershell":
            pid = int(re.search(r"ProcessId=(\d+)", args[-1]).group(1))
            return completed(args, 0, cmdlines.get(pid, "") + "\n")
        if args[0] == "taskkill":
            killed.append(int(args[-1]))
            return completed(args, 0, "")
        raise AssertionError(f"unexpected command: {args}")

    return run


class StaleRelayPids(unittest.TestCase):
    NETSTAT = _netstat(
        [
            "  TCP    127.0.0.1:31416        0.0.0.0:0              LISTENING       4242",
            "  TCP    127.0.0.1:31416        127.0.0.1:52000        ESTABLISHED     4242",
            "  TCP    127.0.0.1:31416        0.0.0.0:0              LISTENING       5151",
            "  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING       7070",
            "  TCP    0.0.0.0:445            0.0.0.0:0              LISTENING       4",
        ]
    )
    CMDLINES = {
        4242: '"C:\\Program Files\\Java\\bin\\java.exe" -jar C:\\Quest Tether\\tools\\gnirehtet.jar run EXAMPLE1',
        5151: "C:\\Program Files\\OtherApp\\other.exe --port 31416",
    }

    def test_only_gnirehtet_pids_on_the_port(self):
        killed = []
        with mock.patch(
            "subprocess.run", side_effect=_fake_run(self.NETSTAT, self.CMDLINES, killed)
        ):
            self.assertEqual(WindowsPlatform().stale_relay_pids(31416), [4242])
        self.assertEqual(killed, [])  # lookup only; killing is kill()

    def test_lookup_failure_counts_as_gnirehtet(self):
        """The command-line lookup fails open: no command line means it is treated as gnirehtet."""
        netstat = _netstat(
            [
                "  TCP    127.0.0.1:31416        0.0.0.0:0              LISTENING       6000"
            ]
        )
        with mock.patch("subprocess.run", side_effect=_fake_run(netstat, {}, [])):
            self.assertEqual(WindowsPlatform().stale_relay_pids(31416), [6000])

    def test_netstat_failure_propagates_to_the_caller(self):
        """The caller (_kill_stale_process) logs the warning to the UI; do not swallow it here."""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                WindowsPlatform().stale_relay_pids(31416)


class KillTree(unittest.TestCase):
    def test_kill_tree_uses_taskkill_tree_flag(self):
        with mock.patch(
            "subprocess.run", return_value=completed(["taskkill"], 0, "")
        ) as run:
            WindowsPlatform().kill_tree(4242)
        self.assertEqual(run.call_args[0][0], ["taskkill", "/F", "/T", "/PID", "4242"])


class FakeWinreg:
    HKEY_CURRENT_USER = 0x80000001
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1

    class _Key:
        def __init__(self, sub):
            self.sub = sub

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self):
        self.values = {}  # (subkey, name) -> (value, type)

    def OpenKey(self, root, sub, reserved, access):
        assert root == self.HKEY_CURRENT_USER
        return FakeWinreg._Key(sub)

    def SetValueEx(self, key, name, reserved, typ, value):
        self.values[(key.sub, name)] = (value, typ)

    def QueryValueEx(self, key, name):
        if (key.sub, name) not in self.values:
            raise FileNotFoundError(name)
        return self.values[(key.sub, name)]

    def DeleteValue(self, key, name):
        if (key.sub, name) not in self.values:
            raise FileNotFoundError(name)
        del self.values[(key.sub, name)]


class Autostart(unittest.TestCase):
    def test_on_writes_value_with_minimized_and_off_removes_it(self):
        reg = FakeWinreg()
        main_py = os.path.join(REPO_ROOT, "main.py")
        with mock.patch.object(windows, "winreg", reg):
            p = WindowsPlatform()
            self.assertFalse(p.autostart())
            self.assertTrue(p.set_autostart(True))
            value, typ = reg.values[(windows.RUN_KEY, "QuestTether")]
            self.assertEqual(typ, reg.REG_SZ)
            self.assertEqual(value, f'"{sys.executable}" "{main_py}" --minimized')
            self.assertTrue(p.autostart())
            self.assertFalse(p.set_autostart(False))
            self.assertNotIn((windows.RUN_KEY, "QuestTether"), reg.values)
            self.assertFalse(p.autostart())

    def test_off_when_not_set_is_fine(self):
        with mock.patch.object(windows, "winreg", FakeWinreg()):
            self.assertFalse(WindowsPlatform().set_autostart(False))

    def test_frozen_build_uses_the_exe(self):
        reg = FakeWinreg()
        exe = "C:\\Program Files\\Quest Tether\\QuestTether.exe"
        with (
            mock.patch.object(windows, "winreg", reg),
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", exe),
        ):
            WindowsPlatform().set_autostart(True)
        self.assertEqual(
            reg.values[(windows.RUN_KEY, "QuestTether")][0], f'"{exe}" --minimized'
        )


class SingleInstance(unittest.TestCase):
    def test_first_instance_takes_the_mutex(self):
        ctypes_fake = mock.MagicMock()
        ctypes_fake.windll.kernel32.GetLastError.return_value = 0
        ctypes_fake.windll.kernel32.CreateMutexW.return_value = 0x1234
        with mock.patch("ctypes.windll", ctypes_fake.windll, create=True):
            self.assertTrue(
                WindowsPlatform().single_instance("QuestTetherSingleInstance")
            )
        ctypes_fake.windll.kernel32.CreateMutexW.assert_called_once_with(
            None, False, "Local\\QuestTetherSingleInstance"
        )
        windows._instance_mutex = None

    def test_second_instance_is_refused(self):
        ctypes_fake = mock.MagicMock()
        ctypes_fake.windll.kernel32.GetLastError.return_value = 183
        ctypes_fake.windll.user32.FindWindowW.return_value = 0
        with mock.patch("ctypes.windll", ctypes_fake.windll, create=True):
            self.assertFalse(
                WindowsPlatform().single_instance("QuestTetherSingleInstance")
            )


if __name__ == "__main__":
    unittest.main()
