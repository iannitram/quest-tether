"""A disconnect request stops a running connect instead of waiting behind it (#126) (no headset, fakes only): python -m unittest discover -s tests"""

import os
import sys
import threading
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qtetherd  # noqa: E402
from tether_manager import TetherManager, TetherState  # noqa: E402


def slow_connect_tether(entered: threading.Event) -> TetherManager:
    """A real core whose connect() loops on a fake adb that never sees tun0."""
    adb = Mock()

    def is_vpn_up(serial):
        entered.set()
        time.sleep(0.1)
        return False

    adb.is_vpn_up.side_effect = is_vpn_up
    tether = TetherManager(adb, gnirehtet_path="fake.jar")
    tether._start_split_relay = Mock(return_value=None)
    tether._kill_stale_process = Mock()
    tether._kill_process = Mock()
    tether._process = Mock()
    tether._process.poll.return_value = None
    return tether


class DisconnectInterruptsConnect(unittest.TestCase):
    def test_disconnect_request_aborts_a_running_connect_within_seconds(self):
        entered = threading.Event()
        tether = slow_connect_tether(entered)
        lock = threading.Lock()
        connect_reply = []
        t = threading.Thread(
            target=lambda: connect_reply.append(
                qtetherd.handle_request(tether, lambda: "S", {"cmd": "connect"}, lock=lock)
            ),
            daemon=True,
        )
        t.start()
        self.assertTrue(entered.wait(5), "connect is inside its tunnel wait")

        started = time.monotonic()
        reply = qtetherd.handle_request(tether, lambda: "S", {"cmd": "disconnect"}, lock=lock)
        elapsed = time.monotonic() - started
        t.join(5)

        self.assertEqual(reply, {"ok": True})
        self.assertLess(elapsed, 8, "the disconnect did not wait out the 15-poll tunnel wait")
        self.assertFalse(t.is_alive())
        self.assertEqual(connect_reply[0]["ok"], False)
        self.assertEqual(tether.stats.state, TetherState.IDLE)
        self.assertFalse(lock.locked())

    def test_request_stop_signals_without_the_lock_or_any_state_change(self):
        tether = TetherManager(Mock(), gnirehtet_path="fake.jar")
        tether._running = True
        tether._session_started = True
        gen = tether._session_gen
        tether.request_stop()
        self.assertFalse(tether._running)
        self.assertEqual(tether._session_gen, gen + 1)
        self.assertEqual(tether.stats.state, TetherState.IDLE)

    def test_disconnect_request_signals_the_stop_before_taking_the_lock(self):
        order = []
        tether = Mock()
        tether.request_stop.side_effect = lambda: order.append(("stop", lock.locked()))
        tether.disconnect.side_effect = lambda **kw: order.append(("disconnect", lock.locked()))
        lock = threading.Lock()
        qtetherd.handle_request(tether, lambda: "S", {"cmd": "disconnect"}, lock=lock)
        self.assertEqual(order, [("stop", False), ("disconnect", True)])


class MonitorConnectOffTheMonitorThread(unittest.TestCase):
    def test_plug_event_returns_at_once_while_a_connect_runs(self):
        release = threading.Event()
        running = threading.Event()
        tether = Mock()
        tether.auto_reconnect = True
        tether.stats.state = TetherState.IDLE

        def connect(serial):
            running.set()
            release.wait(5)
            return True

        tether.connect.side_effect = connect
        on_connected, on_disconnected = qtetherd._monitor_callbacks(tether)

        started = time.monotonic()
        on_connected({"serial": "S", "status": "device"})
        self.assertLess(time.monotonic() - started, 1, "the monitor thread is not held in connect()")
        self.assertTrue(running.wait(5))
        # More events keep flowing while the connect is still running.
        started = time.monotonic()
        on_disconnected()
        on_connected({"serial": "S", "status": "device"})
        self.assertLess(time.monotonic() - started, 1)
        release.set()
        on_connected.drain()
        self.assertGreaterEqual(tether.connect.call_count, 1)


if __name__ == "__main__":
    unittest.main()
