"""CLI command layer and entry point against fake services and a fake tether (#74):
python -m unittest discover -s tests"""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cli  # noqa: E402
import cli_commands  # noqa: E402
import cli_format  # noqa: E402
import crash_queue as crash_queue_store  # noqa: E402
import qtetherd  # noqa: E402
from qt_platform import linux, windows  # noqa: E402
from qt_platform.linux import LinuxPlatform  # noqa: E402
from qt_platform.windows import WindowsPlatform  # noqa: E402
from tether_manager import TetherStats, TetherState  # noqa: E402

SERIAL = "1WMHH0000"


def stats(**kw):
    base = dict(
        state=TetherState.CONNECTED,
        uptime_seconds=125.0,
        rx_rate_bps=2000.0,
        tx_rate_bps=500.0,
        reconnect_count=2,
        battery_level="81",
        battery_status="discharging",
        battery_temp_c=37.5,
        device_serial=SERIAL,
        device_model="Quest 3",
        vpn_ip="10.0.0.2",
        last_error="",
    )
    base.update(kw)
    return TetherStats(**base)


def status_reply(**kw):
    base = {
        "state": "connected",
        "rx_bps": 2000.0,
        "tx_bps": 500.0,
        "uptime_s": 125.0,
        "battery_level": "81",
        "reconnects": 2,
        "temp_c": 37.5,
        "zone": "green",
        "device_serial": SERIAL,
        "device_model": "Quest 3",
        "vpn_ip": "10.0.0.2",
        "battery_status": "discharging",
        "error": "",
    }
    base.update(kw)
    return base


class FakePlatform:
    name = "linux"

    def __init__(self, data_dir):
        self._data_dir = data_dir
        self.autostart_on = False
        self.service_calls = []
        self.stale = []
        self.killed = []
        self.service_reply = {
            "ok": True,
            "action": "status",
            "running": True,
            "detail": "active",
        }

    def data_dir(self):
        return self._data_dir

    def tool_path(self, name, base_dirs):
        return None

    def no_window_flags(self):
        return 0

    def relay_command(self, tools_dir, serial):
        return [os.path.join(tools_dir, "gnirehtet"), "run", serial]

    def autostart(self):
        return self.autostart_on

    def set_autostart(self, on):
        self.autostart_on = bool(on)
        return self.autostart_on

    def service(self, action):
        self.service_calls.append(action)
        reply = dict(self.service_reply)
        reply["action"] = action
        return reply

    def stale_relay_pids(self, port):
        return list(self.stale)

    def kill(self, pid):
        self.killed.append(pid)


class FakeADB:
    def __init__(self, adb_path="adb"):
        self.adb_path = adb_path
        self.quest = {"serial": SERIAL, "status": "device", "model": "Quest 3"}
        self.available = True
        self.calls = []

    def is_adb_available(self):
        return self.available

    def find_quest(self):
        return self.quest

    def find_serial(self):
        if not self.quest:
            return None
        if self.quest.get("status", "device") != "device":
            return None
        return self.quest.get("serial")

    def stop_gnirehtet_client(self, serial):
        self.calls.append(("stop", serial))
        return True

    def force_stop_gnirehtet(self, serial):
        self.calls.append(("force-stop", serial))
        return True

    def remove_all_reverse_tunnels(self, serial):
        self.calls.append(("reverse", serial))
        return True


class FakeTether:
    def __init__(self, data_dir, adb):
        self._data_dir = data_dir
        self.adb = adb
        self.stats = stats()
        self.auto_reconnect = True
        self.wifi_mode = "off"
        self.gnirehtet_path = os.path.join(data_dir, "gnirehtet")
        self.relay_available = True
        self.calls = []

    def connect(self, serial):
        self.calls.append(("connect", serial))
        self.stats.state = TetherState.CONNECTED
        self.stats.device_serial = serial
        return True

    def disconnect(self):
        self.calls.append(("disconnect",))
        self.stats = TetherStats()

    def is_relay_available(self):
        return self.relay_available

    def is_relay_running(self):
        return False


class FakeRequest:
    def __init__(self, replies=None, error=None):
        self.replies = replies or {}
        self.error = error
        self.calls = []

    def __call__(self, cmd, timeout=5.0):
        self.calls.append((cmd, timeout))
        if self.error is not None:
            raise self.error
        reply = self.replies.get(cmd.get("cmd"), {"ok": True})
        return reply(cmd) if callable(reply) else reply


def make_env(replies=None):
    tmp = tempfile.TemporaryDirectory()
    platform = FakePlatform(tmp.name)
    adb = FakeADB()
    tether = FakeTether(tmp.name, adb)
    request = FakeRequest(replies)
    service = cli_commands.ServiceBackend(
        platform=platform, request=request, adb=adb, tether=tether
    )
    local = cli_commands.LocalBackend(platform=platform, adb=adb, tether=tether)
    return SimpleNamespace(
        tmp=tmp,
        dir=tmp.name,
        platform=platform,
        adb=adb,
        tether=tether,
        request=request,
        service=service,
        local=local,
    )


class BackendSelection(unittest.TestCase):
    def test_service_that_answers_is_used(self):
        platform = FakePlatform(tempfile.mkdtemp())
        request = FakeRequest({"status": {"state": "connected"}})
        backend = cli_commands.make_backend(platform=platform, request=request)
        self.assertIsInstance(backend, cli_commands.ServiceBackend)
        self.assertEqual(request.calls, [({"cmd": "status"}, 1)])

    def test_refused_connection_falls_back_in_process(self):
        platform = FakePlatform(tempfile.mkdtemp())
        request = FakeRequest(error=ConnectionRefusedError("no service"))
        backend = cli_commands.make_backend(platform=platform, request=request)
        self.assertIsInstance(backend, cli_commands.LocalBackend)

    def test_timeout_falls_back_in_process(self):
        platform = FakePlatform(tempfile.mkdtemp())
        request = FakeRequest(error=socket.timeout("slow"))
        backend = cli_commands.make_backend(platform=platform, request=request)
        self.assertIsInstance(backend, cli_commands.LocalBackend)

    def test_silent_service_falls_back_in_process(self):
        platform = FakePlatform(tempfile.mkdtemp())
        request = FakeRequest(error=ConnectionError("no answer"))
        backend = cli_commands.make_backend(platform=platform, request=request)
        self.assertIsInstance(backend, cli_commands.LocalBackend)

    def test_reset_connection_falls_back_in_process(self):
        platform = FakePlatform(tempfile.mkdtemp())
        request = FakeRequest(error=ConnectionResetError("reset"))
        backend = cli_commands.make_backend(platform=platform, request=request)
        self.assertIsInstance(backend, cli_commands.LocalBackend)

    def test_other_oserror_keeps_the_service_path(self):
        # Not "no service": the command reports the socket error instead of
        # starting a second core.
        platform = FakePlatform(tempfile.mkdtemp())
        request = FakeRequest(error=OSError("no service"))
        backend = cli_commands.make_backend(platform=platform, request=request)
        self.assertIsInstance(backend, cli_commands.ServiceBackend)
        result = backend.status()
        self.assertFalse(result["ok"])
        self.assertIn("service not reachable", result["error"])

    def test_a_malformed_answer_keeps_the_service_path(self):
        platform = FakePlatform(tempfile.mkdtemp())
        request = FakeRequest(error=ValueError("bad json"))
        backend = cli_commands.make_backend(platform=platform, request=request)
        self.assertIsInstance(backend, cli_commands.ServiceBackend)
        result = backend.status()
        self.assertEqual(result["ok"], False)
        self.assertIn("bad service reply", result["error"])


class ServicePath(unittest.TestCase):
    def test_status_comes_from_the_service(self):
        env = make_env({"status": status_reply()})
        self.assertEqual(cli_commands.status(env.service), status_reply())
        self.assertEqual(env.request.calls[0][0], {"cmd": "status"})

    def test_connect_and_disconnect_use_the_service(self):
        env = make_env({"connect": {"ok": True}, "disconnect": {"ok": True}})
        self.assertEqual(cli_commands.connect(env.service), {"ok": True})
        self.assertEqual(cli_commands.disconnect(env.service), {"ok": True})
        self.assertEqual(
            [call[0] for call in env.request.calls],
            [{"cmd": "connect"}, {"cmd": "disconnect"}],
        )

    def test_wifi_sync_uses_the_service(self):
        env = make_env({"wifi-sync": {"ok": True, "result": "switched"}})
        result = cli_commands.wifi_sync(env.service)
        self.assertEqual(result["result"], "switched")
        self.assertEqual(env.request.calls[0][0], {"cmd": "wifi-sync"})

    def test_logs_pass_the_source_and_line_count(self):
        env = make_env(
            {"logs": {"ok": True, "source": "relay", "lines": ["one", "two"]}}
        )
        result = cli_commands.logs(env.service, relay=True, n=2)
        self.assertEqual(result["lines"], ["one", "two"])
        self.assertEqual(
            env.request.calls[0][0], {"cmd": "logs", "source": "relay", "n": 2}
        )

    def test_settings_get_adds_the_platform_autostart(self):
        env = make_env(
            {
                "settings-get": {
                    "ok": True,
                    "settings": {
                        "auto_reconnect": True,
                        "wifi_mode": "off",
                        "crash_reports": True,
                    },
                }
            }
        )
        result = cli_commands.settings_get(env.service)
        self.assertEqual(
            result["settings"],
            {
                "auto_reconnect": True,
                "wifi_mode": "off",
                "crash_reports": True,
                "autostart": False,
            },
        )

    def test_settings_set_forwards_a_canonical_key(self):
        env = make_env(
            {
                "settings-set": {
                    "ok": True,
                    "settings": {
                        "auto_reconnect": False,
                        "wifi_mode": "off",
                        "crash_reports": True,
                    },
                },
                "settings-get": {
                    "ok": True,
                    "settings": {
                        "auto_reconnect": False,
                        "wifi_mode": "off",
                        "crash_reports": True,
                    },
                },
            }
        )
        result = cli_commands.settings_set(env.service, "auto-reconnect", "off")
        self.assertTrue(result["ok"])
        self.assertFalse(result["settings"]["auto_reconnect"])
        self.assertIn(
            ({"cmd": "settings-set", "settings": {"auto_reconnect": False}}, 10.0),
            env.request.calls,
        )

    def test_settings_set_autostart_uses_the_platform(self):
        env = make_env()
        result = cli_commands.settings_set(env.service, "autostart", "on")
        self.assertTrue(result["ok"])
        self.assertTrue(env.platform.autostart_on)


class InProcessPath(unittest.TestCase):
    def test_status_reads_the_fake_tether(self):
        env = make_env()
        result = cli_commands.status(env.local)
        self.assertEqual(result["state"], "connected")
        self.assertEqual(result["device_serial"], SERIAL)
        self.assertEqual(result["zone"], "green")
        self.assertEqual(result["reconnects"], 2)

    def test_connect_finds_the_serial_and_starts_the_tether(self):
        env = make_env()
        env.tether.stats.state = TetherState.IDLE
        result = cli_commands.connect(env.local)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(env.tether.calls, [("connect", SERIAL)])

    def test_disconnect_stops_the_tether(self):
        env = make_env()
        result = cli_commands.disconnect(env.local)
        self.assertEqual(result, {"ok": True})
        self.assertIn(("disconnect",), env.tether.calls)

    def test_disconnect_cleans_up_a_relay_left_by_an_earlier_process(self):
        env = make_env()
        env.platform.stale = [4242]
        result = cli_commands.disconnect(env.local)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(env.platform.killed, [4242])
        self.assertIn(("stop", SERIAL), env.adb.calls)
        self.assertIn(("reverse", SERIAL), env.adb.calls)

    def test_wifi_sync_runs_in_process(self):
        env = make_env()
        with patch.object(
            qtetherd.net_sync, "sync_quest_wifi", return_value="same"
        ) as sync:
            result = cli_commands.wifi_sync(env.local)
        self.assertEqual(result, {"ok": True, "result": "same"})
        sync.assert_called_once()

    def test_logs_read_the_app_log_from_the_data_folder(self):
        env = make_env()
        with open(os.path.join(env.dir, "quest-tether.log"), "w") as stream:
            stream.write("one\ntwo\nthree\n")
        result = cli_commands.logs(env.local, n=2)
        self.assertEqual(result["lines"], ["two", "three"])

    def test_settings_get_and_set_touch_the_tether(self):
        env = make_env()
        self.assertEqual(
            cli_commands.settings_get(env.local)["settings"]["auto_reconnect"], True
        )
        result = cli_commands.settings_set(env.local, "auto_reconnect", "off")
        self.assertTrue(result["ok"])
        self.assertFalse(env.tether.auto_reconnect)
        self.assertFalse(result["settings"]["auto_reconnect"])

    def test_setting_wifi_mode_and_autostart(self):
        env = make_env()
        result = cli_commands.settings_set(env.local, "wifi-mode", "sync")
        self.assertTrue(result["ok"])
        self.assertEqual(env.tether.wifi_mode, "sync")
        cli_commands.settings_set(env.local, "autostart", "true")
        self.assertTrue(env.platform.autostart_on)

    def test_bad_setting_value_is_bad_usage(self):
        env = make_env()
        result = cli_commands.settings_set(env.local, "wifi_mode", "sometimes")
        self.assertEqual(result["error"], "bad usage")
        self.assertEqual(cli_format.exit_code(result), 2)

    def test_unknown_setting_is_bad_usage(self):
        env = make_env()
        result = cli_commands.settings_set(env.local, "colour", "blue")
        self.assertEqual(result["error"], "bad usage")
        self.assertEqual(cli_format.exit_code(result), 2)


class Doctor(unittest.TestCase):
    def _run(self, backend, env):
        tools = os.path.join(env.dir, "tools")
        os.makedirs(tools, exist_ok=True)
        for name in ("adb", "gnirehtet", "gnirehtet.apk"):
            open(os.path.join(tools, name), "w").close()
        rules = os.path.join(env.dir, "rules")
        os.makedirs(rules, exist_ok=True)
        with open(os.path.join(rules, "51-quest-tether.rules"), "w") as stream:
            stream.write('SUBSYSTEM=="usb", ATTR{idVendor}=="2833"')
        return cli_commands.doctor(
            backend,
            tools_dir=tools,
            which=lambda name: "/usr/bin/" + name,
            udev_dirs=[rules],
        )

    def test_every_check_passes_on_both_paths(self):
        for mode in ("service", "local"):
            env = make_env()
            result = self._run(getattr(env, mode), env)
            self.assertTrue(result["ok"], result)
            names = [c["name"] for c in result["checks"]]
            self.assertEqual(
                names, ["tools", "adb", "relay", "udev", "device", "service"]
            )

    def test_missing_device_fails(self):
        env = make_env()
        env.adb.quest = None
        result = self._run(env.service, env)
        self.assertFalse(result["ok"])
        device = [c for c in result["checks"] if c["name"] == "device"][0]
        self.assertFalse(device["ok"])

    def test_unauthorized_headset_fails(self):
        env = make_env()
        env.adb.quest = {"serial": SERIAL, "status": "unauthorized"}
        result = self._run(env.local, env)
        device = [c for c in result["checks"] if c["name"] == "device"][0]
        self.assertFalse(device["ok"])
        self.assertIn("unauthorized", device["detail"])

    def test_service_check_is_informational_on_the_in_process_path(self):
        env = make_env()
        result = self._run(env.local, env)
        service = [c for c in result["checks"] if c["name"] == "service"][0]
        self.assertFalse(service["ok"])
        self.assertTrue(result["ok"])


class InstallApk(unittest.TestCase):
    def test_install_uses_the_relay_install_command_on_both_paths(self):
        for mode in ("service", "local"):
            env = make_env()
            with patch.object(cli_commands.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, "ok\n", "")
                result = cli_commands.install_apk(getattr(env, mode))
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["serial"], SERIAL)
            cmd = run.call_args[0][0]
            self.assertEqual(
                cmd, [os.path.join(env.dir, "gnirehtet"), "install", SERIAL]
            )
            self.assertEqual(run.call_args[1]["env"]["ADB"], "adb")

    def test_install_failure_is_reported(self):
        env = make_env()
        with patch.object(cli_commands.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 7, "", "nope")
            result = cli_commands.install_apk(env.service)
        self.assertFalse(result["ok"])
        self.assertEqual(result["output"], "nope")
        self.assertIn("exit 7", result["error"])

    def test_no_headset_is_an_error(self):
        env = make_env()
        env.adb.quest = None
        self.assertEqual(
            cli_commands.install_apk(env.local),
            {"ok": False, "error": "no headset found"},
        )


class CrashQueue(unittest.TestCase):
    def _queue_folder(self, env):
        folder = os.path.join(env.dir, "crash-reports", "queue")
        os.makedirs(folder, exist_ok=True)
        for name in ("20261104-a.zip", "20261104-b.zip"):
            with open(os.path.join(folder, name), "wb") as stream:
                stream.write(b"zip")
        return folder

    def test_lists_queued_reports_on_both_paths(self):
        for mode in ("service", "local"):
            env = make_env()
            self._queue_folder(env)
            result = cli_commands.crash_queue(getattr(env, mode))
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["queued"], ["20261104-a.zip", "20261104-b.zip"])
            self.assertNotIn("sent", result)

    def test_flush_retries_through_the_reporter(self):
        class Reporter:
            def retry_pending(self):
                return 1

        env = make_env()
        self._queue_folder(env)
        result = cli_commands.crash_queue(env.local, flush=True, reporter=Reporter())
        self.assertTrue(result["ok"])
        self.assertEqual(result["sent"], 1)

    def test_empty_queue_is_ok(self):
        env = make_env()
        result = cli_commands.crash_queue(env.service)
        self.assertEqual(result["queued"], [])
        self.assertEqual(result["count"], 0)


class ServiceCommand(unittest.TestCase):
    def test_status_start_and_stop_go_through_the_platform(self):
        for mode in ("service", "local"):
            env = make_env()
            for action in ("status", "start", "stop"):
                result = cli_commands.service(getattr(env, mode), action)
                self.assertTrue(result["ok"])
                self.assertEqual(result["action"], action)
            self.assertEqual(env.platform.service_calls, ["status", "start", "stop"])

    def test_bad_action_is_bad_usage(self):
        env = make_env()
        result = cli_commands.service(env.service, "restart")
        self.assertEqual(result["error"], "bad usage")
        self.assertEqual(cli_format.exit_code(result), 2)

    def test_platform_failure_is_an_error(self):
        env = make_env()
        env.platform.service_reply = {"ok": False, "error": "systemctl failed"}
        result = cli_commands.service(env.local, "start")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "systemctl failed")


class Gui(unittest.TestCase):
    def test_gui_starts_the_window(self):
        fake = types.ModuleType("main")
        fake.called = False

        def main():
            fake.called = True

        fake.main = main
        with patch.dict(sys.modules, {"main": fake}):
            result = cli_commands.gui(None)
        self.assertTrue(result["ok"])
        self.assertTrue(fake.called)


class PlatformService(unittest.TestCase):
    def _completed(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def test_linux_status_reports_running(self):
        with patch.object(
            linux, "_systemctl", return_value=self._completed(0, "active\n")
        ):
            result = LinuxPlatform().service("status")
        self.assertEqual(
            result,
            {"ok": True, "action": "status", "running": True, "detail": "active"},
        )

    def test_linux_status_reports_inactive_as_not_running(self):
        with patch.object(
            linux, "_systemctl", return_value=self._completed(3, "inactive\n")
        ):
            result = LinuxPlatform().service("status")
        self.assertTrue(result["ok"])
        self.assertFalse(result["running"])

    def test_linux_start_and_stop_use_systemctl_user(self):
        with patch.object(
            linux,
            "_systemctl",
            side_effect=[
                self._completed(0),
                self._completed(0, "active\n"),
                self._completed(0),
                self._completed(3, "inactive\n"),
            ],
        ) as systemctl:
            started = LinuxPlatform().service("start")
            stopped = LinuxPlatform().service("stop")
        self.assertTrue(started["running"])
        self.assertFalse(stopped["running"])
        self.assertEqual(
            [call[0] for call in systemctl.call_args_list],
            [
                ("start", "quest-tether.service"),
                ("is-active", "quest-tether.service"),
                ("stop", "quest-tether.service"),
                ("is-active", "quest-tether.service"),
            ],
        )

    def test_linux_failed_start_is_reported(self):
        with patch.object(
            linux, "_systemctl", return_value=self._completed(1, "", "denied")
        ):
            result = LinuxPlatform().service("start")
        self.assertFalse(result["ok"])
        self.assertEqual(result["detail"], "denied")

    def test_linux_missing_systemctl_is_reported(self):
        with patch.object(
            linux,
            "_systemctl",
            return_value=self._completed(1, "", "systemctl: not found"),
        ):
            result = LinuxPlatform().service("status")
        self.assertFalse(result["ok"])
        self.assertIn("systemctl", result["detail"])

    def test_linux_bad_action(self):
        result = LinuxPlatform().service("restart")
        self.assertFalse(result["ok"])
        self.assertIn("bad service action", result["error"])

    def test_windows_has_no_service(self):
        self.assertEqual(
            WindowsPlatform().service("status"),
            {"ok": False, "error": "no service on Windows"},
        )


class ConnectHold(unittest.TestCase):
    def test_hold_service_mode_exits_zero_immediately(self):
        env = make_env({"connect": {"ok": True}})
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli_commands, "make_backend", return_value=env.service):
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["connect", "--hold"])
        self.assertEqual(code, 0)
        self.assertIn("The service keeps the tether up; nothing to hold.", out.getvalue())

    def test_hold_service_mode_json_exits_zero(self):
        env = make_env({"connect": {"ok": True}})
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli_commands, "make_backend", return_value=env.service):
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["connect", "--hold", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"ok": True})

    def test_hold_in_process_handles_sigint_and_disconnects(self):
        env = make_env()
        env.tether.stats.state = TetherState.IDLE
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli_commands, "make_backend", return_value=env.local):
            with patch("time.sleep", side_effect=KeyboardInterrupt):
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["connect", "--hold"])
        self.assertEqual(code, 0)
        self.assertIn(("connect", SERIAL), env.tether.calls)
        self.assertIn(("disconnect",), env.tether.calls)
        self.assertIn("Holding the tether; press Ctrl+C to stop.", out.getvalue())

    def test_hold_in_process_json_prints_json_to_stdout(self):
        env = make_env()
        env.tether.stats.state = TetherState.IDLE
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli_commands, "make_backend", return_value=env.local):
            with patch("time.sleep", side_effect=KeyboardInterrupt):
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["connect", "--hold", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"ok": True})
        self.assertIn("Holding the tether", err.getvalue())
        self.assertIn(("disconnect",), env.tether.calls)

    def test_hold_aborts_when_connect_fails(self):
        env = make_env()
        env.adb.quest = None
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli_commands, "make_backend", return_value=env.local):
            with patch("time.sleep", side_effect=AssertionError("should not sleep")):
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["connect", "--hold"])
        self.assertEqual(code, 1)
        self.assertNotIn(("disconnect",), env.tether.calls)


class EntryPoint(unittest.TestCase):
    class StubBackend:
        def __init__(self, reply=None, mode="in-process"):
            self.mode = mode
            self.reply = reply or status_reply()
            self.platform = FakePlatform(tempfile.mkdtemp())
            self.adb = FakeADB()
            self.tether = FakeTether(self.platform.data_dir(), self.adb)

        def status(self):
            return dict(self.reply)

        def settings_get(self):
            return {"ok": True, "settings": {}}

        def connect(self):
            return dict(self.reply) if "ok" in self.reply else {"ok": True}

        def disconnect(self):
            return {"ok": True}

        def wifi_sync(self):
            return {"ok": True, "result": "same"}

        def logs(self, source, n):
            return {"ok": True, "source": source, "lines": []}

        def settings_set(self, updates):
            return {"ok": True, "settings": {}}

    def _run(self, argv, backend):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli_commands, "make_backend", return_value=backend):
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_status_json_is_one_object_and_exits_zero(self):
        code, out, _ = self._run(["status", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "connected")

    def test_failure_exits_one(self):
        backend = self.StubBackend({"ok": False, "error": "no headset found"})
        code, out, _ = self._run(["status", "--json"], backend)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error"], "no headset found")

    def test_unknown_command_is_bad_usage(self):
        code, _, _ = self._run(["nonsense"], self.StubBackend())
        self.assertEqual(code, 2)

    def test_missing_command_is_bad_usage(self):
        code, _, _ = self._run([], self.StubBackend())
        self.assertEqual(code, 2)

    def test_bad_setting_exits_two_with_json(self):
        code, out, _ = self._run(
            ["settings", "set", "colour", "blue", "--json"], self.StubBackend()
        )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"], "bad usage")

    def test_nested_service_command_accepts_json(self):
        code, out, _ = self._run(["service", "status", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["running"], True)

    def test_text_status_uses_the_formatters(self):
        code, out, _ = self._run(["status"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertIn("State:      connected", out)
        self.assertIn("Serial:     " + SERIAL, out)

    def test_connect_and_disconnect_json(self):
        backend = self.StubBackend()
        code, out, _ = self._run(["connect", "--json"], backend)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True})

        code, out, _ = self._run(["disconnect", "--json"], backend)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True})

    def test_wifi_sync_json(self):
        code, out, _ = self._run(["wifi-sync", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True, "result": "same"})

    def test_logs_json(self):
        code, out, _ = self._run(["logs", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"lines": [], "ok": True, "source": "app"})

    def test_crash_queue_json(self):
        code, out, _ = self._run(["crash-queue", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertNotIn("sent", payload)

    def test_crash_queue_text_without_flush(self):
        code, out, _ = self._run(["crash-queue"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertNotIn("Retried", out)

    def test_settings_get_json(self):
        code, out, _ = self._run(["settings", "get", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_service_start_and_stop_json(self):
        backend = self.StubBackend()
        code, out, _ = self._run(["service", "start", "--json"], backend)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])
        code, out, _ = self._run(["service", "stop", "--json"], backend)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_doctor_json(self):
        with patch.object(
            cli_commands, "doctor", return_value={"ok": True, "checks": [], "platform": "linux"}
        ):
            code, out, _ = self._run(["doctor", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["platform"], "linux")

    def test_install_apk_json(self):
        with patch.object(
            cli_commands, "install_apk", return_value={"ok": True, "serial": SERIAL, "output": "ok"}
        ):
            code, out, _ = self._run(["install-apk", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["serial"], SERIAL)

    def test_gui_json(self):
        with patch.object(cli_commands, "gui", return_value={"ok": True}):
            code, out, _ = self._run(["gui", "--json"], self.StubBackend())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True})


class McpCommand(unittest.TestCase):
    """`quest-tether mcp` (#75): starts the stdio server and builds no backend itself."""

    def test_mcp_is_a_subcommand_without_json(self):
        args = cli.build_parser().parse_args(["mcp"])
        self.assertEqual(args.command, "mcp")
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["mcp", "--json"])

    def test_mcp_runs_the_server_and_builds_no_backend(self):
        import mcp_server

        with patch.object(mcp_server, "serve", return_value=0) as serve, patch.object(
            cli_commands, "make_backend"
        ) as make:
            code = cli.main(["mcp"])
        self.assertEqual(code, 0)
        serve.assert_called_once_with()
        make.assert_not_called()

    def test_the_server_exit_code_is_returned(self):
        import mcp_server

        with patch.object(mcp_server, "serve", return_value=3):
            self.assertEqual(cli.main(["mcp"]), 3)


if __name__ == "__main__":
    unittest.main()
