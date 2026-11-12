"""Keep the published status contract in step with the handler and CLI JSON."""

import io
import json
import os
from pathlib import Path
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cli  # noqa: E402
import cli_commands  # noqa: E402
import qtetherd  # noqa: E402
from adb_manager import ADBManager  # noqa: E402
from tether_manager import TetherState, TetherStats  # noqa: E402


class ClientContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = (Path(__file__).resolve().parents[1] / "docs/client-contract.md").read_text(
            encoding="utf-8"
        )

    def documented_names(self, section):
        table = self.document.split("## " + section + "\n", 1)[1].split("\n## ", 1)[0]
        names = re.findall(r"^\| `([^`]+)` \|", table, re.MULTILINE)
        self.assertTrue(names, "contract table must not be empty")
        self.assertEqual(len(names), len(set(names)), "duplicate contract rows")
        return set(names)

    def assert_cli_status(self, backend, expected):
        out, err = io.StringIO(), io.StringIO()
        with patch("cli_commands.make_backend", return_value=backend), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["status", "--json"])
        self.assertEqual(code, 1 if expected["error"] else 0)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        actual = json.loads(out.getvalue())
        self.assertEqual(set(actual), self.documented_names("Status fields"))
        self.assertEqual(actual, expected)

    def test_documented_fields_and_states_match_handler_and_both_cli_routes(self):
        self.assertEqual(
            self.documented_names("State values"), {state.value for state in TetherState}
        )
        version = int(re.search(r"Contract version: \*\*(\d+)\*\*", self.document).group(1))
        for state in TetherState:
            with self.subTest(state=state.value):
                adb = SimpleNamespace(adb_path="/opt/quest-tether/tools/adb", find_serial=Mock())
                tether = SimpleNamespace(
                    adb=adb,
                    stats=TetherStats(
                        state=state, last_error="tunnel failed" if state == TetherState.ERROR else ""
                    ),
                )

                def handle(req, **kwargs):
                    return qtetherd.handle_request(tether, adb.find_serial, req)

                reply = handle({"cmd": "status"})
                self.assertEqual(set(reply), self.documented_names("Status fields"))
                self.assertEqual(reply["state"], state.value)
                self.assertIs(type(reply["version"]), int)
                self.assertEqual(reply["version"], version)
                self.assertEqual(reply["adb_path"], adb.adb_path)
                self.assert_cli_status(cli_commands.LocalBackend(adb=adb, tether=tether), reply)
                self.assert_cli_status(cli_commands.ServiceBackend(request=handle), reply)
                adb.find_serial.assert_not_called()

    def test_adb_path_reuses_discovery_and_missing_path_is_null(self):
        for selected in ("C:/tools/adb.exe", "/opt/quest-tether/tools/adb", None):
            with self.subTest(selected=selected):
                platform = Mock()
                platform.tool_path.return_value = selected
                adb = ADBManager(platform=platform)
                self.assertEqual(adb.adb_path, selected or "adb")
                tether = SimpleNamespace(adb=adb, stats=TetherStats())
                reply = qtetherd.handle_request(tether, Mock(), {"cmd": "status"})
                self.assertEqual(reply["adb_path"], selected)
                self.assertEqual(reply["version"], 1)
                self.assertIsNone(reply["relay_clients"])
                self.assertIsNone(reply["temp_c"])
                self.assertEqual(reply["zone"], "unknown")
                self.assert_cli_status(cli_commands.LocalBackend(adb=adb, tether=tether), reply)
                platform.tool_path.assert_called_once()


if __name__ == "__main__":
    unittest.main()
