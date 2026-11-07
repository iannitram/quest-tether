"""Split relay mode (#66): the relay runs alone, we wait for its port, then we start the
Quest client ourselves. Fakes only (no process, socket or headset): python -m unittest discover -s tests
"""

import itertools
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherManager, TetherState  # noqa: E402

GNIREHTET = "/tools/gnirehtet"
SPLIT_CMD = ["/tools/gnirehtet", "relay", "-p", "31416"]
RUN_CMD = ["/tools/gnirehtet", "run", "quest-serial"]


class FakePlatform:
    """The platform calls the split flow makes, with the answers set by each test."""

    name = "fake"

    def __init__(self, data_dir, holders=()):
        self._data_dir = data_dir
        self.holders = list(holders)  # (pid, is_gnirehtet) listening on the relay port
        self.stale = []  # gnirehtet PIDs _kill_stale_process may kill
        self.kill = Mock()

    def data_dir(self):
        return self._data_dir

    def tool_path(self, name, base_dirs):
        return None

    def no_window_flags(self):
        return 0

    def relay_command(self, tools_dir, serial):
        return [tools_dir + "/gnirehtet", "run", serial]

    def relay_server_command(self, tools_dir, port):
        return [tools_dir + "/gnirehtet", "relay", "-p", str(port)]

    def port_holders(self, port):
        return list(self.holders)

    def stale_relay_pids(self, port):
        return list(self.stale)

    def relay_candidates(self):
        return [("gnirehtet", False)]


class SplitRelayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.popen_cmds = []
        self.logs = []

        for patcher in (
            patch("tether_manager.time.sleep", return_value=None),
            patch.dict(os.environ, {}, clear=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("QT_RELAY_MODE", None)

    def make_manager(self, holders=(), exit_code=None, output=b""):
        """A manager on fakes. The relay process reports exit_code (None = running)
        and writes output to its log, as the real relay does."""
        self.adb = Mock()
        self.adb.adb_path = "adb"
        self.adb.is_vpn_up.return_value = True
        self.adb.get_device_details.return_value = {}
        self.adb.start_gnirehtet_client.return_value = True
        self.adb.last_client_error = ""
        self.adb.last_client_exit_code = None
        self.platform = FakePlatform(self.tmp, holders)
        self.manager = TetherManager(
            self.adb, gnirehtet_path=GNIREHTET, platform=self.platform
        )
        self.manager.restore_wifi_if_flagged = Mock()
        self.manager._apply_wifi_policy = Mock()
        self.manager._start_health_monitor = Mock()
        self.manager.set_callbacks(on_log=self.logs.append)
        self.addCleanup(self._close_relay_log)

        self.proc = Mock(pid=4242)
        self.proc.poll.return_value = exit_code

        def start(cmd, **kwargs):
            self.popen_cmds.append(cmd)
            out = kwargs.get("stdout")
            if output and hasattr(out, "write"):
                out.write(output)
                out.flush()
            return self.proc

        popen = patch("tether_manager.subprocess.Popen", side_effect=start)
        popen.start()
        self.addCleanup(popen.stop)

    def _close_relay_log(self):
        if self.manager._relay_log:
            self.manager._relay_log.close()

    def test_split_is_the_default_mode(self):
        self.make_manager()
        self.assertEqual(self.manager.relay_mode, "split")

    def test_env_run_selects_the_old_path(self):
        os.environ["QT_RELAY_MODE"] = "run"
        self.make_manager()
        self.assertEqual(self.manager.relay_mode, "run")

    def test_unknown_env_value_falls_back_to_split(self):
        os.environ["QT_RELAY_MODE"] = "bogus"
        self.make_manager()
        self.assertEqual(self.manager.relay_mode, "split")

    def test_split_spawn_runs_the_relay_alone_without_a_serial(self):
        self.make_manager()
        self.manager._spawn_relay("quest-serial")
        self.assertEqual(self.popen_cmds, [SPLIT_CMD])

    def test_run_spawn_keeps_the_serial(self):
        self.make_manager()
        self.manager.relay_mode = "run"
        self.manager._spawn_relay("quest-serial")
        self.assertEqual(self.popen_cmds, [RUN_CMD])

    def test_connect_starts_the_client_only_after_the_port_listens(self):
        self.make_manager()
        events = []
        self.adb.start_gnirehtet_client.side_effect = lambda serial, port: (
            events.append(("client", port)) or True
        )
        with patch.object(
            TetherManager,
            "_port_open",
            side_effect=lambda port: events.append(("listen", port)) or True,
        ):
            self.assertTrue(self.manager.connect("quest-serial"))

        self.assertEqual(events, [("listen", 31416), ("client", 31416)])
        self.assertEqual(self.popen_cmds, [SPLIT_CMD])
        self.assertEqual(self.manager.stats.state, TetherState.CONNECTED)

    def test_connect_refuses_a_port_held_by_another_program(self):
        self.make_manager(holders=[(9999, False)])

        self.assertFalse(self.manager.connect("quest-serial"))

        self.assertEqual(self.popen_cmds, [], "no relay may be started")
        self.platform.kill.assert_not_called()
        self.assertEqual(self.manager.stats.state, TetherState.ERROR)
        self.assertIn("31416", self.manager.stats.last_error)
        self.assertIn("9999", self.manager.stats.last_error)

    def test_connect_replaces_our_own_stale_relay(self):
        self.make_manager(holders=[(4242, True)])
        self.platform.stale = [4242]
        with patch.object(TetherManager, "_port_open", return_value=True):
            self.assertTrue(self.manager.connect("quest-serial"))

        self.platform.kill.assert_called_once_with(4242)
        self.assertEqual(self.popen_cmds, [SPLIT_CMD])

    def test_relay_dying_at_startup_reports_its_last_log_line(self):
        self.make_manager(
            exit_code=1, output=b"ERROR: Bind failed: Address already in use\n"
        )

        self.assertFalse(self.manager.connect("quest-serial"))

        error = self.manager.stats.last_error
        self.assertIn("Bind failed: Address already in use", error)
        self.assertNotIn("Process exited immediately", error)
        self.adb.start_gnirehtet_client.assert_not_called()

    def test_relay_dying_with_no_output_says_so(self):
        self.make_manager(exit_code=1)

        self.assertFalse(self.manager.connect("quest-serial"))

        self.assertIn("no output in relay.log", self.manager.stats.last_error)

    def test_probe_timeout_kills_the_relay_it_started(self):
        self.make_manager()
        with (
            patch.object(TetherManager, "_port_open", return_value=False),
            patch(
                "tether_manager.time.monotonic",
                side_effect=itertools.count(0.0, 2.0),
            ),
        ):
            self.assertFalse(self.manager.connect("quest-serial"))

        self.proc.terminate.assert_called()
        self.assertIn("did not listen on port 31416", self.manager.stats.last_error)
        self.adb.start_gnirehtet_client.assert_not_called()

    def test_refused_client_start_stops_the_relay_and_fails(self):
        self.make_manager()
        self.adb.start_gnirehtet_client.return_value = False
        self.adb.last_client_error = "Error type 3"
        with patch.object(TetherManager, "_port_open", return_value=True):
            self.assertFalse(self.manager.connect("quest-serial"))

        self.proc.terminate.assert_called()
        self.assertEqual(self.manager.stats.state, TetherState.ERROR)
        self.assertIn("client start refused: Error type 3", self.manager.stats.last_error)

    def test_reconnect_with_a_foreign_holder_kills_nothing(self):
        self.make_manager(holders=[(9000, False)])
        self.adb.find_quest.return_value = {"serial": "quest-serial", "status": "device"}
        self.manager._running = True

        self.manager._attempt_reconnect("quest-serial")

        self.platform.kill.assert_not_called()
        self.assertEqual(self.popen_cmds, [])
        self.assertTrue(any("31416" in line and "9000" in line for line in self.logs))

    def test_reconnect_in_split_mode_restarts_relay_then_client(self):
        self.make_manager()
        self.adb.find_quest.return_value = {"serial": "quest-serial", "status": "device"}
        self.manager._running = True
        with patch.object(TetherManager, "_port_open", return_value=True):
            self.manager._attempt_reconnect("quest-serial")

        self.assertEqual(self.popen_cmds, [SPLIT_CMD])
        self.adb.start_gnirehtet_client.assert_called_once_with("quest-serial", 31416)
        self.assertEqual(self.manager.stats.state, TetherState.CONNECTED)

    def test_wait_until_listening_polls_until_the_port_opens(self):
        self.make_manager()
        self.manager._spawn_relay("quest-serial")
        with patch.object(
            TetherManager, "_port_open", side_effect=[False, False, True]
        ) as port_open:
            self.assertTrue(self.manager._wait_until_listening(31416))
        self.assertEqual(port_open.call_count, 3)

    def test_wait_until_listening_stops_as_soon_as_the_relay_dies(self):
        self.make_manager(exit_code=1)
        self.manager._spawn_relay("quest-serial")
        with patch.object(TetherManager, "_port_open", return_value=False) as port_open:
            self.assertFalse(self.manager._wait_until_listening(31416))
        port_open.assert_not_called()

    def test_relay_log_last_line_stops_at_this_starts_marker(self):
        self.make_manager()
        path = os.path.join(self.tmp, "relay.log")
        with open(path, "wb") as f:
            f.write(b"ERROR: an old failure\n=== 2026-11-07 12:00:00 relay start ===\n")
        self.assertEqual(self.manager._relay_log_last_line(), "")

        with open(path, "ab") as f:
            f.write(b"ERROR: Bind failed\n\n")
        self.assertEqual(self.manager._relay_log_last_line(), "ERROR: Bind failed")

    def test_relay_log_last_line_is_empty_without_a_log(self):
        self.make_manager()
        self.assertEqual(self.manager._relay_log_last_line(), "")


if __name__ == "__main__":
    unittest.main()
