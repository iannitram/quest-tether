"""Exercise the real window state handler without a display or headset."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tether_manager import TetherManager, TetherState, TetherStats


class WindowStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctk = ModuleType("customtkinter")
        ctk.CTk = object
        ctk.CTkFrame = object
        ctk.CTkFont = Mock()
        ctk.set_appearance_mode = Mock()
        ctk.set_default_color_theme = Mock()
        # Block pystray before import: installed backends may require a display.
        # Its ImportError also skips main's optional PIL imports.
        with patch.dict(sys.modules, {"customtkinter": ctk, "pystray": None}):
            spec = importlib.util.spec_from_file_location(
                "window_state_main", Path(__file__).resolve().parents[1] / "main.py"
            )
            cls.main = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.main)

    def setUp(self):
        self.window = SimpleNamespace(
            _quitting=False,
            _connected=False,
            _auto_connect_pending=True,
            _status_indicator=Mock(),
            _connect_btn=Mock(),
            _log_panel=Mock(),
            _tray_icon=None,
            after=lambda ms, fn=None, *a: fn(*a) if fn else None,
        )
        self.on_state_change = self.main.QuestTetherApp._on_state_change.__get__(self.window)

    def test_starting_clears_connected_and_disables_button(self):
        self.window._connected = True

        self.on_state_change(TetherState.STARTING, TetherStats())

        self.assertFalse(self.window._connected,
                         "STARTING must clear the window's connected flag")
        button = self.window._connect_btn.configure.call_args.kwargs
        self.assertEqual(button["state"], "disabled")
        self.assertEqual(button["text"], "Connect")
        self.assertEqual(button["fg_color"], self.main.COLORS["accent"])
        self.assertEqual(button["hover_color"], "#c73e52")

    def test_connected_then_starting_resets_window_and_button(self):
        self.on_state_change(TetherState.CONNECTED, TetherStats())
        self.assertTrue(self.window._connected)
        self.assertEqual(self.window._connect_btn.configure.call_args.kwargs["text"],
                         "Disconnect")

        self.on_state_change(TetherState.STARTING, TetherStats())

        self.assertFalse(self.window._connected,
                         "STARTING after CONNECTED must clear the window's connected flag")
        button = self.window._connect_btn.configure.call_args.kwargs
        self.assertEqual(button["text"], "Connect")
        self.assertEqual(button["state"], "disabled")

    def test_connect_waits_for_health_poll_before_window_is_connected(self):
        adb = Mock()
        adb.adb_path = "adb"
        adb.is_vpn_up.return_value = False
        adb.get_device_details.return_value = {}
        manager = TetherManager(adb, gnirehtet_path="fake.jar")
        manager._process = Mock()
        manager._process.pid = 123
        manager._process.poll.return_value = None
        manager._spawn_relay = Mock()
        manager._kill_stale_process = Mock()
        manager.restore_wifi_if_flagged = Mock()
        manager._apply_wifi_policy = Mock()
        manager._start_health_monitor = Mock()
        manager._wifi_mode = "leave"
        manager.set_callbacks(on_state_change=self.on_state_change)
        self.addCleanup(setattr, manager, "_running", False)

        with patch("tether_manager.time.sleep", return_value=None):
            self.assertTrue(manager.connect("quest-serial"))

        self.assertFalse(self.window._connected,
                         "connect without tun0 must leave the window disconnected")
        displayed_states = [call.args[0] for call in
                            self.window._status_indicator.set_state.call_args_list]
        self.assertNotIn(TetherState.CONNECTED, displayed_states,
                         "window must never display CONNECTED without tun0")

        adb.get_tun0_stats.return_value = {"rx_bytes": 1, "tx_bytes": 2}
        manager.POST_CONNECT_COOLDOWN = 0
        with patch("tether_manager.time.sleep",
                   side_effect=lambda _: setattr(manager, "_running", False)):
            manager._health_loop("quest-serial")

        adb.get_tun0_stats.assert_called_once_with("quest-serial")
        self.assertTrue(self.window._connected,
                        "first health poll with tun0 must connect the window")
        self.window._status_indicator.set_state.assert_called_with(TetherState.CONNECTED)
        manager._start_health_monitor.assert_called_once_with("quest-serial")


if __name__ == "__main__":
    unittest.main()
