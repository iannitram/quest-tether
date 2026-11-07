"""The relay's client count drives Connected (#62). Fakes only: no relay, no adb, no headset."""

import io
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherManager, TetherState  # noqa: E402
from heal import Heal  # noqa: E402

GNIREHTET = "/tools/gnirehtet"
JAVA_CONNECTED = b"2026-01-01 12:00:00.000 INFO TunnelServer: Client #3 connected\n"
JAVA_DISCONNECTED = b"2026-01-01 12:00:05.000 INFO TunnelServer: Client #3 disconnected\n"
RUST_CONNECTED = b"2026-09-27 03:29:44.592 I TunnelServer: Client #1 connected\n"


class FakePlatform:
    name = "fake"

    def __init__(self, data_dir):
        self._data_dir = data_dir
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
        return []

    def stale_relay_pids(self, port):
        return []

    def relay_candidates(self):
        return [("gnirehtet", False)]


class RelayClientTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.output = b""
        self.procs = []
        for patcher in (
            patch("tether_manager.time.sleep", return_value=None),
            patch("tether_manager.TetherManager._port_open", return_value=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.adb = Mock()
        self.adb.adb_path = "adb"
        self.adb.get_device_details.return_value = {}
        self.adb.start_gnirehtet_client.return_value = True
        self.adb.last_client_error = ""
        self.adb.last_client_exit_code = None
        self.manager = TetherManager(self.adb, gnirehtet_path=GNIREHTET, platform=FakePlatform(self.tmp))
        self.manager.relay_mode = "split"
        self.manager.restore_wifi_if_flagged = Mock()
        self.manager._apply_wifi_policy = Mock()
        self.manager._start_health_monitor = Mock()
        self.manager._kill_stale_process = Mock()
        self.addCleanup(self._close_relay_log)

        popen = patch("tether_manager.subprocess.Popen", side_effect=self._start)
        popen.start()
        self.addCleanup(popen.stop)

    def _start(self, cmd, **kwargs):
        """A relay whose output is the test's `self.output`, read through the pump."""
        proc = Mock(pid=4000 + len(self.procs))
        proc.poll.return_value = None
        proc.stdout = io.BytesIO(self.output)
        self.procs.append(proc)
        return proc

    def _close_relay_log(self):
        if self.manager._relay_log:
            self.manager._relay_log.close()

    def _tun0_up_after_relay_output(self, serial):
        # The pump reads the relay's lines on its own thread. Wait for it, as a real
        # relay would have printed its lines before the tunnel comes up.
        self.manager._join_relay_pump()
        return True

    def connect_with_tun0(self):
        self.adb.is_vpn_up.side_effect = self._tun0_up_after_relay_output
        return self.manager.connect("quest-serial")

    def test_a_connected_client_and_tun0_reach_connected(self):
        self.output = RUST_CONNECTED

        self.assertTrue(self.connect_with_tun0())

        self.assertEqual(self.manager.stats.relay_clients, 1)
        self.assertEqual(self.manager.stats.state, TetherState.CONNECTED)

    def test_a_relay_that_prints_no_client_line_is_connected_on_tun0_alone(self):
        self.output = b"2026-01-01 INFO Relay: Relay server started\n"

        self.assertTrue(self.connect_with_tun0())

        self.assertIsNone(self.manager.stats.relay_clients)
        self.assertEqual(self.manager.stats.state, TetherState.CONNECTED)

    def test_a_client_that_left_keeps_tun0_from_being_connected(self):
        self.output = JAVA_CONNECTED + JAVA_DISCONNECTED

        self.assertTrue(self.connect_with_tun0())

        self.assertEqual(self.manager.stats.relay_clients, 0)
        self.assertEqual(self.manager.stats.state, TetherState.STARTING)

    def test_the_health_check_promotes_once_a_client_connects(self):
        self.output = JAVA_CONNECTED + JAVA_DISCONNECTED
        self.connect_with_tun0()
        self.assertEqual(self.manager.stats.state, TetherState.STARTING)

        self.manager._on_relay_line(self.manager._relay_gen, "2026-01-01 INFO TunnelServer: Client #4 connected")
        self.manager._check_tunnel("quest-serial", True, time.time())

        self.assertEqual(self.manager.stats.relay_clients, 1)
        self.assertEqual(self.manager.stats.state, TetherState.CONNECTED)

    def test_every_relay_line_reaches_relay_log(self):
        self.output = JAVA_CONNECTED + b"something else\n"

        self.connect_with_tun0()

        with open(os.path.join(self.tmp, "relay.log"), "rb") as f:
            log = f.read()
        self.assertIn(b"relay start quest-serial", log)
        self.assertIn(JAVA_CONNECTED.rstrip(b"\n"), log)
        self.assertIn(b"something else", log)

    def test_a_line_from_a_killed_relay_is_ignored(self):
        self.output = RUST_CONNECTED
        self.connect_with_tun0()
        old_gen = self.manager._relay_gen

        self.manager._kill_process()
        self.manager._on_relay_line(old_gen, "2026-09-27 I TunnelServer: Client #1 connected")

        self.assertIsNone(self.manager.stats.relay_clients)

    def test_a_new_relay_starts_with_no_clients_counted(self):
        self.output = RUST_CONNECTED
        self.connect_with_tun0()
        self.assertEqual(self.manager.stats.relay_clients, 1)

        self.manager._kill_process()
        self.output = b""  # the new relay prints no client line
        self.manager._spawn_relay("quest-serial")
        self.manager._join_relay_pump()

        self.assertIsNone(self.manager.stats.relay_clients)

    def test_a_reconnect_whose_relay_counts_no_client_is_starting(self):
        self.output = JAVA_CONNECTED + JAVA_DISCONNECTED
        self.connect_with_tun0()
        self.adb.find_quest.return_value = {"serial": "quest-serial", "status": "device"}
        self.manager._running = True
        logs = []
        self.manager.set_callbacks(on_log=logs.append)

        self.manager._attempt_reconnect("quest-serial")

        self.assertEqual(self.manager.stats.state, TetherState.STARTING)
        self.assertIn("Reconnect: tun0 up, no relay client yet — waiting for one", logs)

    def test_a_heal_whose_relay_counts_no_client_is_not_connected(self):
        self.output = JAVA_CONNECTED + JAVA_DISCONNECTED
        self.connect_with_tun0()
        self.adb.find_quest.return_value = {"serial": "quest-serial", "status": "device"}

        self.manager._heal("quest-serial", Heal.RESTART_CLIENT)

        self.assertEqual(self.manager.stats.state, TetherState.STARTING)

    def test_a_heal_with_a_connected_client_is_connected(self):
        self.output = JAVA_CONNECTED
        self.connect_with_tun0()
        self.adb.find_quest.return_value = {"serial": "quest-serial", "status": "device"}

        self.manager._heal("quest-serial", Heal.RESTART_CLIENT)

        self.assertEqual(self.manager.stats.state, TetherState.CONNECTED)

    def test_the_pipe_is_drained_when_no_ui_is_open(self):
        """More output than a pipe holds, read on the pump thread: no stall, every line in relay.log."""
        line = b"2026-01-01 INFO Relay: keep-alive padding padding padding padding\n"
        count = 4000  # about 280 KB, well past a 64 KB pipe buffer
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, "rb")
        self.addCleanup(stream.close)
        proc = Mock(pid=5000)
        proc.stdout = stream
        log_path = os.path.join(self.tmp, "relay.log")

        def writer():
            with os.fdopen(write_fd, "wb") as w:
                for _ in range(count):
                    w.write(line)

        with open(log_path, "wb") as log:
            self.manager._start_relay_pump(proc, log, self.manager._reset_relay_clients())
            writer_thread = threading.Thread(target=writer, daemon=True)
            writer_thread.start()
            writer_thread.join(timeout=10)
            self.assertFalse(writer_thread.is_alive(), "the writer stalled: the pipe was not drained")
            self.manager._join_relay_pump(timeout=10)

        with open(log_path, "rb") as f:
            self.assertEqual(f.read().count(line), count)


if __name__ == "__main__":
    unittest.main()
