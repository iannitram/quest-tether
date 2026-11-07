"""MCP server over stdio (#75) against a fake backend: python -m unittest discover -s tests"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cli_commands  # noqa: E402
import mcp_server  # noqa: E402

SERIAL = "1WMHH0000"
READ_TOOLS = {"status", "doctor", "logs", "crash_queue", "settings_get", "service_status"}
WRITE_TOOLS = {
    "connect", "disconnect", "wifi_sync", "install_apk", "crash_queue_flush",
    "settings_set", "service",
}


class FakePlatform:
    name = "linux"

    def __init__(self, data_dir):
        self._data_dir = data_dir
        self.autostart_on = False
        self.service_calls = []

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
        return {"ok": True, "action": action, "running": action == "start", "detail": "fake"}

    def stale_relay_pids(self, port):
        return []

    def kill(self, pid):
        pass


class FakeADB:
    adb_path = "adb"

    def __init__(self, quest=None):
        self.quest = quest

    def is_adb_available(self):
        return True

    def find_quest(self):
        return self.quest

    def find_serial(self):
        return self.quest["serial"] if self.quest else None


class FakeTether:
    gnirehtet_path = ""

    def is_relay_available(self):
        return True

    def is_relay_running(self):
        return False


class FakeBackend(cli_commands.Backend):
    """Records each command; replies come from `replies` (an exception is raised)."""

    def __init__(self, data_dir, mode="service", replies=None, quest=None):
        super().__init__(
            platform=FakePlatform(data_dir), adb=FakeADB(quest), tether=FakeTether()
        )
        self.mode = mode
        self.replies = replies or {}
        self.calls = []

    def _answer(self, name, args, default):
        self.calls.append((name, args))
        value = self.replies.get(name, default)
        if isinstance(value, Exception):
            raise value
        return dict(value)

    def status(self):
        return self._answer("status", None, {
            "state": "connected", "rx_bps": 0, "tx_bps": 0, "uptime_s": 5,
            "reconnects": 0, "battery_level": "80", "temp_c": 30.0,
            "zone": "green", "error": "",
        })

    def connect(self):
        return self._answer("connect", None, {"ok": True})

    def disconnect(self):
        return self._answer("disconnect", None, {"ok": True})

    def wifi_sync(self):
        return self._answer("wifi_sync", None, {"ok": True, "result": "same"})

    def logs(self, source, n):
        return self._answer("logs", (source, n), {"ok": True, "source": source, "lines": ["a"]})

    def settings_get(self):
        return self._answer("settings_get", None, {
            "ok": True, "settings": {"auto_reconnect": True, "wifi_mode": "off", "crash_reports": True},
        })

    def settings_set(self, updates):
        return self._answer("settings_set", updates, {"ok": True, "settings": dict(updates)})


class Counting:
    """A backend factory that counts its calls and hands out one backend."""

    def __init__(self, backend):
        self.backend = backend
        self.count = 0

    def __call__(self):
        self.count += 1
        return self.backend


def request(method, params=None, request_id=1):
    msg = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def tool_call(name, arguments=None, request_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return request("tools/call", params, request_id)


def run(lines, factory):
    """Feed lines to serve(); return (exit code, parsed stdout lines, stderr text)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stderr(err):
        code = mcp_server.serve(
            stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out, backend_factory=factory
        )
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    return code, replies, err.getvalue()


def payload(reply):
    """The JSON object inside a tools/call result."""
    return json.loads(reply["result"]["content"][0]["text"])


class Protocol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend = FakeBackend(self.tmp.name)
        self.factory = Counting(self.backend)

    def test_initialize_names_the_protocol_and_the_tools_capability(self):
        _, replies, _ = run([request("initialize", {"protocolVersion": "2025-06-18"})], self.factory)
        result = replies[0]["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "quest-tether")
        self.assertEqual(self.factory.count, 0)

    def test_ping_answers_an_empty_result(self):
        _, replies, _ = run([request("ping", request_id="p")], self.factory)
        self.assertEqual(replies, [{"jsonrpc": "2.0", "id": "p", "result": {}}])

    def test_notifications_get_no_reply(self):
        line = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        _, replies, _ = run([line], self.factory)
        self.assertEqual(replies, [])

    def test_unknown_notification_is_ignored_too(self):
        line = json.dumps({"jsonrpc": "2.0", "method": "something/else"})
        _, replies, _ = run([line], self.factory)
        self.assertEqual(replies, [])

    def test_id_zero_is_echoed_not_dropped(self):
        _, replies, _ = run([request("ping", request_id=0)], self.factory)
        self.assertEqual(replies[0]["id"], 0)
        self.assertEqual(replies[0]["result"], {})

    def test_unknown_method_is_method_not_found(self):
        _, replies, _ = run([request("resources/list", request_id=7)], self.factory)
        self.assertEqual(replies[0]["id"], 7)
        self.assertEqual(replies[0]["error"]["code"], -32601)

    def test_parse_error_is_reported_and_the_loop_goes_on(self):
        _, replies, _ = run(["{not json", request("ping", request_id=2)], self.factory)
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertIsNone(replies[0]["id"])
        self.assertEqual(replies[1]["id"], 2)
        self.assertEqual(replies[1]["result"], {})

    def test_batches_are_rejected(self):
        _, replies, _ = run(["[" + request("ping") + "]"], self.factory)
        self.assertEqual(replies[0]["error"]["code"], -32600)

    def test_a_message_that_is_not_an_object_is_invalid(self):
        _, replies, _ = run(["42"], self.factory)
        self.assertEqual(replies[0]["error"]["code"], -32600)

    def test_a_request_without_a_method_is_invalid(self):
        _, replies, _ = run([json.dumps({"jsonrpc": "2.0", "id": 3})], self.factory)
        self.assertEqual(replies[0]["id"], 3)
        self.assertEqual(replies[0]["error"]["code"], -32600)

    def test_blank_lines_are_skipped(self):
        _, replies, _ = run(["", "   ", request("ping")], self.factory)
        self.assertEqual(len(replies), 1)

    def test_exit_code_is_zero_when_stdin_closes(self):
        code, _, _ = run([request("ping")], self.factory)
        self.assertEqual(code, 0)

    def test_stdout_carries_only_protocol_lines(self):
        def noisy_status():
            print("this must not reach stdout")
            return {"state": "down"}

        self.backend.status = noisy_status
        _, replies, err = run([tool_call("status")], self.factory)
        self.assertEqual(len(replies), 1)
        self.assertIn("this must not reach stdout", err)


class ToolList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.factory = Counting(FakeBackend(self.tmp.name))

    def listed(self):
        _, replies, _ = run([request("tools/list")], self.factory)
        return {tool["name"]: tool for tool in replies[0]["result"]["tools"]}

    def test_every_expected_tool_is_listed_and_no_other(self):
        self.assertEqual(set(self.listed()), READ_TOOLS | WRITE_TOOLS)

    def test_every_tool_has_an_object_schema(self):
        for name, tool in self.listed().items():
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object", name)
            self.assertIsInstance(schema["properties"], dict, name)
            self.assertIsInstance(schema["required"], list, name)
            self.assertFalse(schema["additionalProperties"], name)
            self.assertTrue(tool["description"], name)

    def test_write_tools_require_confirm_and_read_tools_do_not_take_it(self):
        tools = self.listed()
        for name in WRITE_TOOLS:
            schema = tools[name]["inputSchema"]
            self.assertIn("confirm", schema["required"], name)
            self.assertEqual(schema["properties"]["confirm"]["type"], "boolean", name)
            self.assertFalse(tools[name]["annotations"]["readOnlyHint"], name)
        for name in READ_TOOLS:
            self.assertNotIn("confirm", tools[name]["inputSchema"]["properties"], name)
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"], name)

    def test_settings_key_is_an_enum_of_the_four_settings(self):
        schema = self.listed()["settings_set"]["inputSchema"]
        self.assertEqual(
            set(schema["properties"]["key"]["enum"]),
            {"auto_reconnect", "wifi_mode", "crash_reports", "autostart"},
        )
        self.assertEqual(set(schema["required"]), {"key", "value", "confirm"})

    def test_logs_line_count_has_bounds(self):
        props = self.listed()["logs"]["inputSchema"]["properties"]
        self.assertEqual(props["n"]["minimum"], 0)
        self.assertEqual(props["n"]["maximum"], 1000)


class ReadTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend = FakeBackend(self.tmp.name)
        self.factory = Counting(self.backend)

    def call(self, name, arguments=None):
        _, replies, _ = run([tool_call(name, arguments)], self.factory)
        return replies[0]

    def test_status_returns_the_tether_state(self):
        reply = self.call("status")
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(payload(reply)["state"], "connected")
        self.assertEqual(self.backend.calls, [("status", None)])

    def test_doctor_runs_the_doctor_check(self):
        report = {"ok": True, "platform": "linux", "checks": []}
        with patch.object(cli_commands, "doctor", return_value=report) as doctor:
            reply = self.call("doctor")
        doctor.assert_called_once_with(self.backend)
        self.assertEqual(payload(reply), report)
        self.assertFalse(reply["result"]["isError"])

    def test_logs_pass_the_source_and_line_count(self):
        reply = self.call("logs", {"relay": True, "n": 5})
        self.assertEqual(self.backend.calls, [("logs", ("relay", 5))])
        self.assertEqual(payload(reply)["lines"], ["a"])

    def test_logs_default_to_the_app_log_and_fifty_lines(self):
        self.call("logs")
        self.assertEqual(self.backend.calls, [("logs", ("app", 50))])

    def test_logs_refuse_a_line_count_over_the_limit(self):
        reply = self.call("logs", {"n": 1001})
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertEqual(self.backend.calls, [])

    def test_logs_refuse_a_string_line_count(self):
        reply = self.call("logs", {"n": "5"})
        self.assertEqual(reply["error"]["code"], -32602)

    def test_crash_queue_lists_the_queue(self):
        reply = self.call("crash_queue")
        self.assertEqual(payload(reply), {"ok": True, "queued": [], "count": 0})

    def test_settings_get_adds_the_platform_autostart_flag(self):
        reply = self.call("settings_get")
        settings = payload(reply)["settings"]
        self.assertEqual(settings["crash_reports"], True)
        self.assertIs(settings["autostart"], False)

    def test_service_status_goes_through_the_platform(self):
        reply = self.call("service_status")
        self.assertEqual(self.backend.platform.service_calls, ["status"])
        self.assertFalse(reply["result"]["isError"])

    def test_a_failed_read_is_an_error_result(self):
        self.backend.replies["status"] = {"ok": False, "error": "service not reachable: refused"}
        reply = self.call("status")
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("service not reachable", reply["result"]["content"][0]["text"])


class WriteTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend = FakeBackend(self.tmp.name, quest={"serial": SERIAL, "status": "device"})
        self.factory = Counting(self.backend)

    def call(self, name, arguments=None):
        _, replies, _ = run([tool_call(name, arguments)], self.factory)
        return replies[0]

    def test_each_write_tool_is_refused_without_confirm(self):
        arguments = {
            "connect": {}, "disconnect": {}, "wifi_sync": {}, "install_apk": {},
            "crash_queue_flush": {}, "settings_set": {"key": "crash_reports", "value": "off"},
            "service": {"action": "stop"},
        }
        for name in WRITE_TOOLS:
            reply = self.call(name, arguments[name])
            self.assertTrue(reply["result"]["isError"], name)
            self.assertIn("confirm", reply["result"]["content"][0]["text"], name)
        self.assertEqual(self.factory.count, 0)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.platform.service_calls, [])

    def test_confirm_false_is_refused_too(self):
        reply = self.call("disconnect", {"confirm": False})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(self.backend.calls, [])

    def test_confirm_as_a_string_is_refused(self):
        reply = self.call("disconnect", {"confirm": "true"})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(self.backend.calls, [])

    def test_confirmed_connect_and_disconnect_run(self):
        self.assertFalse(self.call("connect", {"confirm": True})["result"]["isError"])
        self.assertFalse(self.call("disconnect", {"confirm": True})["result"]["isError"])
        self.assertEqual([c[0] for c in self.backend.calls], ["connect", "disconnect"])

    def test_confirmed_wifi_sync_runs(self):
        reply = self.call("wifi_sync", {"confirm": True})
        self.assertEqual(payload(reply)["result"], "same")
        self.assertEqual(self.backend.calls, [("wifi_sync", None)])

    def test_install_apk_without_a_headset_is_an_error_result(self):
        self.backend.adb.quest = None
        reply = self.call("install_apk", {"confirm": True})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(payload(reply)["error"], "no headset found")

    def test_install_apk_success_is_reported(self):
        with patch.object(cli_commands, "install_apk", return_value={"ok": True, "serial": SERIAL}):
            reply = self.call("install_apk", {"confirm": True})
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(payload(reply)["serial"], SERIAL)

    def test_settings_set_forwards_the_canonical_key_and_value(self):
        reply = self.call("settings_set", {"confirm": True, "key": "wifi_mode", "value": "sync"})
        self.assertFalse(reply["result"]["isError"])
        self.assertIn(("settings_set", {"wifi_mode": "sync"}), self.backend.calls)

    def test_settings_set_accepts_booleans_and_on_off_words(self):
        self.call("settings_set", {"confirm": True, "key": "crash_reports", "value": False})
        self.call("settings_set", {"confirm": True, "key": "auto_reconnect", "value": "on"})
        sent = [c[1] for c in self.backend.calls if c[0] == "settings_set"]
        self.assertEqual(sent, [{"crash_reports": False}, {"auto_reconnect": True}])

    def test_settings_set_autostart_uses_the_platform(self):
        self.call("settings_set", {"confirm": True, "key": "autostart", "value": "on"})
        self.assertTrue(self.backend.platform.autostart_on)
        self.assertNotIn("settings_set", [c[0] for c in self.backend.calls])

    def test_settings_set_bad_value_is_bad_usage_and_changes_nothing(self):
        reply = self.call("settings_set", {"confirm": True, "key": "wifi_mode", "value": "sideways"})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(self.backend.calls, [])

    def test_settings_set_unknown_key_is_refused_by_the_schema(self):
        reply = self.call("settings_set", {"confirm": True, "key": "volume", "value": "up"})
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertEqual(self.factory.count, 0)

    def test_crash_queue_flush_passes_flush_through(self):
        with patch.object(cli_commands, "crash_queue", return_value={"ok": True, "sent": 0}) as queue:
            reply = self.call("crash_queue_flush", {"confirm": True})
        queue.assert_called_once_with(self.backend, flush=True)
        self.assertFalse(reply["result"]["isError"])

    def test_service_stop_goes_through_the_platform(self):
        reply = self.call("service", {"confirm": True, "action": "stop"})
        self.assertEqual(self.backend.platform.service_calls, ["stop"])
        self.assertFalse(reply["result"]["isError"])

    def test_service_refuses_actions_outside_start_and_stop(self):
        reply = self.call("service", {"confirm": True, "action": "status"})
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertEqual(self.backend.platform.service_calls, [])

    def test_a_failed_write_is_an_error_result_with_the_message(self):
        self.backend.replies["disconnect"] = {"ok": False, "error": "tether busy"}
        reply = self.call("disconnect", {"confirm": True})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("tether busy", reply["result"]["content"][0]["text"])

    def test_a_raised_exception_is_an_error_result_and_the_server_goes_on(self):
        self.backend.replies["connect"] = RuntimeError("adb exploded")
        _, replies, _ = run(
            [tool_call("connect", {"confirm": True}, 1), request("ping", request_id=2)],
            self.factory,
        )
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("adb exploded", replies[0]["result"]["content"][0]["text"])
        self.assertEqual(replies[1]["result"], {})

    def test_a_factory_that_raises_is_an_error_result(self):
        def broken():
            raise OSError("no platform here")

        _, replies, _ = run([tool_call("status")], broken)
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("no platform here", replies[0]["result"]["content"][0]["text"])


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend = FakeBackend(self.tmp.name)
        self.factory = Counting(self.backend)

    def error_of(self, line):
        _, replies, _ = run([line], self.factory)
        return replies[0]["error"]["code"]

    def test_unknown_tool_is_invalid_params(self):
        self.assertEqual(self.error_of(tool_call("reboot_the_pc")), -32602)
        self.assertEqual(self.factory.count, 0)

    def test_missing_tool_name_is_invalid_params(self):
        self.assertEqual(self.error_of(request("tools/call", {"arguments": {}})), -32602)

    def test_tools_call_without_params_is_invalid_params(self):
        self.assertEqual(self.error_of(request("tools/call")), -32602)

    def test_arguments_that_are_not_an_object_are_invalid_params(self):
        line = request("tools/call", {"name": "status", "arguments": [1]})
        self.assertEqual(self.error_of(line), -32602)

    def test_unknown_argument_is_invalid_params(self):
        self.assertEqual(self.error_of(tool_call("status", {"verbose": True})), -32602)

    def test_the_factory_is_called_once_per_tool_call(self):
        lines = [tool_call("status", request_id=1), tool_call("status", request_id=2),
                 request("tools/list", request_id=3), tool_call("doctor", request_id=4)]
        with patch.object(cli_commands, "doctor", return_value={"ok": True, "checks": []}):
            run(lines, self.factory)
        self.assertEqual(self.factory.count, 3)

    def test_an_in_process_backend_is_kept_between_calls(self):
        # Without a service, a connect must still be there for the next status.
        handed_out = []

        def factory():
            backend = FakeBackend(self.tmp.name, mode="in-process")
            handed_out.append(backend)
            return backend

        run([tool_call("connect", {"confirm": True}, 1), tool_call("status", None, 2)], factory)
        self.assertEqual(len(handed_out), 2)
        self.assertEqual([c[0] for c in handed_out[0].calls], ["connect", "status"])
        self.assertEqual(handed_out[1].calls, [])

    def test_a_service_backend_is_asked_fresh_on_every_call(self):
        handed_out = []

        def factory():
            backend = FakeBackend(self.tmp.name, mode="service")
            handed_out.append(backend)
            return backend

        run([tool_call("status", None, 1), tool_call("status", None, 2)], factory)
        self.assertEqual(len(handed_out), 2)
        self.assertEqual([len(b.calls) for b in handed_out], [1, 1])


class LiveTether(FakeTether):
    """A tether whose relay is running."""

    def is_relay_running(self):
        return True


class ReconnectingTether(FakeTether):
    """No relay is up, but the tether is mid-reconnect."""

    def __init__(self):
        self.stats = type("Stats", (), {"state": mcp_server.TetherState.RECONNECTING})()
        self.disconnects = []

    def disconnect(self, headset_present=True):
        self.disconnects.append(headset_present)


class ShutdownTests(unittest.TestCase):
    """No relay may outlive the server (stdin closed, signal, closed stdout)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def local_backend(self, live):
        backend = FakeBackend(self.tmp.name, mode="in-process")
        backend._tether = LiveTether() if live else FakeTether()
        return backend

    def test_stdin_closing_disconnects_a_live_in_process_tether(self):
        backend = self.local_backend(live=True)
        run([tool_call("connect", {"confirm": True})], lambda: backend)
        self.assertEqual([c[0] for c in backend.calls], ["connect", "disconnect"])

    def test_a_tether_that_is_not_running_is_left_alone(self):
        backend = self.local_backend(live=False)
        run([tool_call("connect", {"confirm": True})], lambda: backend)
        self.assertEqual([c[0] for c in backend.calls], ["connect"])

    def test_reconnecting_without_a_relay_stops_the_tether_without_the_sweep(self):
        backend = FakeBackend(self.tmp.name, mode="in-process")
        backend._tether = ReconnectingTether()
        run([tool_call("connect", {"confirm": True})], lambda: backend)
        # local.disconnect() would run the stale-relay sweep and could kill a
        # relay that qtetherd started during the session.
        self.assertEqual([c[0] for c in backend.calls], ["connect"])
        self.assertEqual(backend._tether.disconnects, [False])

    def test_a_service_backend_is_never_disconnected_on_exit(self):
        backend = FakeBackend(self.tmp.name, mode="service")
        backend._tether = LiveTether()
        run([tool_call("status")], lambda: backend)
        self.assertEqual([c[0] for c in backend.calls], ["status"])

    def test_nothing_connected_means_no_disconnect(self):
        backend = self.local_backend(live=True)
        run([request("ping")], lambda: backend)
        self.assertEqual(backend.calls, [])

    def test_a_failing_disconnect_on_exit_does_not_raise(self):
        backend = self.local_backend(live=True)
        backend.replies["disconnect"] = RuntimeError("adb gone")
        code, _, _ = run([tool_call("connect", {"confirm": True})], lambda: backend)
        self.assertEqual(code, 0)

    def test_a_closed_stdout_ends_the_loop_and_still_cleans_up(self):
        class Closed:
            def write(self, text):
                raise BrokenPipeError()

            def flush(self):
                pass

        backend = self.local_backend(live=True)
        lines = [tool_call("connect", {"confirm": True}, 1), request("ping", None, 2)]
        with redirect_stderr(io.StringIO()):
            code = mcp_server.serve(
                stdin=io.StringIO("\n".join(lines) + "\n"),
                stdout=Closed(),
                backend_factory=lambda: backend,
            )
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in backend.calls], ["connect", "disconnect"])

    def test_a_signal_while_serving_cleans_up(self):
        backend = self.local_backend(live=True)

        class Lines:
            def __iter__(self):
                yield tool_call("connect", {"confirm": True})
                mcp_server._stop_on_signal(15, None)

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mcp_server.serve(stdin=Lines(), stdout=io.StringIO(), backend_factory=lambda: backend)
        self.assertEqual([c[0] for c in backend.calls], ["connect", "disconnect"])

    def test_signal_handlers_are_installed_and_restored(self):
        installed = {}

        def fake_signal(number, handler):
            previous = installed.get(number, "old-%s" % number)
            installed[number] = handler
            return previous

        with patch.object(mcp_server.signal, "signal", fake_signal):
            restore = mcp_server._install_signal_handlers()
            self.assertIs(installed[mcp_server.signal.SIGTERM], mcp_server._stop_on_signal)
            restore()
        self.assertEqual(installed[mcp_server.signal.SIGTERM], "old-%s" % int(mcp_server.signal.SIGTERM))


CHILD_SERVER = """
import subprocess, sys
sys.path.insert(0, {root!r})
import mcp_server

class Backend:
    mode = "service"
    def status(self):
        # A child that reads stdin to the end, as `adb shell` can.
        subprocess.run([sys.executable, "-c", "import sys; sys.stdin.read()"])
        return {{"state": "idle"}}

sys.exit(mcp_server.serve(backend_factory=lambda: Backend()))
"""


@unittest.skipUnless(os.name == "posix", "fd 0 handling is checked on Linux")
class ChildStdinTests(unittest.TestCase):
    def test_a_child_cannot_swallow_a_queued_request(self):
        import time

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.Popen(
            [sys.executable, "-c", CHILD_SERVER.format(root=root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            proc.stdin.write(tool_call("status", None, 1) + "\n")
            proc.stdin.flush()
            time.sleep(0.5)  # the child is running now
            out, _ = proc.communicate(request("ping", None, 2) + "\n", timeout=30)
        finally:
            proc.kill()
            proc.wait()
        ids = [json.loads(line)["id"] for line in out.splitlines()]
        self.assertEqual(ids, [1, 2])


if __name__ == "__main__":
    unittest.main()
