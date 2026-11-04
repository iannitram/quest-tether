"""main.py's qtetherd client fallback, without a display or headset: python -m unittest discover -s tests"""

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_main():
    ctk = ModuleType("customtkinter")
    ctk.CTk = object
    ctk.CTkFrame = object
    ctk.CTkFont = Mock()
    ctk.set_appearance_mode = Mock()
    ctk.set_default_color_theme = Mock()
    with patch.dict(sys.modules, {"customtkinter": ctk, "pystray": None}):
        spec = importlib.util.spec_from_file_location(
            "main_service_fallback", Path(__file__).resolve().parents[1] / "main.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


MAIN = None


def setUpModule():
    global MAIN
    MAIN = _load_main()


def window(**kw):
    base = dict(
        _quitting=False,
        _connected=False,
        _service_mode=True,
        _last_status={},
        _service_refused=False,
        _status_in_flight=False,
        _auto_connect_pending=False,
        _connect_btn=Mock(),
        _log_panel=Mock(),
        after=lambda ms, fn=None, *a: fn(*a) if fn else None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _ImmediateThread:
    """Run worker targets inline so tests do not race the Tk thread."""

    def __init__(self, target, daemon=None, **kwargs):
        self.target = target

    def start(self):
        self.target()


class Reprobes(unittest.TestCase):
    def test_live_service_is_used_and_status_is_shown(self):
        w = window(
            _probe_service=Mock(return_value={"state": "connected"}),
            _fall_back_to_process=Mock(),
            _apply_service_status=Mock(),
        )
        self.assertTrue(MAIN.QuestTetherApp._reprobe_service.__get__(w)())
        w._apply_service_status.assert_called_once_with({"state": "connected"})
        w._fall_back_to_process.assert_not_called()

    def test_dead_service_falls_back_to_the_process(self):
        w = window(
            _probe_service=Mock(return_value=None),
            _fall_back_to_process=Mock(),
            _service_refused=True,
        )
        self.assertFalse(MAIN.QuestTetherApp._reprobe_service.__get__(w)())
        w._fall_back_to_process.assert_called_once_with()

    def test_slow_service_timeout_does_not_fall_back(self):
        w = window(
            _probe_service=Mock(return_value=None),
            _fall_back_to_process=Mock(),
            _service_refused=False,
        )
        self.assertTrue(MAIN.QuestTetherApp._reprobe_service.__get__(w)())
        w._fall_back_to_process.assert_not_called()


class Probes(unittest.TestCase):
    def test_refused_connection_marks_the_service_gone(self):
        w = window()
        with patch.object(
            MAIN.qtetherd, "request", side_effect=ConnectionRefusedError()
        ):
            self.assertIsNone(MAIN.QuestTetherApp._probe_service.__get__(w)())
        self.assertTrue(w._service_refused)

    def test_timeout_keeps_the_service_alive(self):
        w = window()
        with patch.object(MAIN.qtetherd, "request", side_effect=TimeoutError()):
            self.assertIsNone(MAIN.QuestTetherApp._probe_service.__get__(w)())
        self.assertFalse(w._service_refused)


class Fallback(unittest.TestCase):
    def test_creates_and_monitors_the_local_core(self):
        w = window(
            tether=None,
            adb=None,
            _on_state_change=Mock(),
            _on_stats_update=Mock(),
            _on_log=Mock(),
            _on_device_connected=Mock(),
            _on_device_disconnected=Mock(),
        )
        adb = Mock()
        tether = Mock()
        with (
            patch.object(MAIN, "ADBManager", return_value=adb),
            patch.object(MAIN, "TetherManager", return_value=tether),
        ):
            MAIN.QuestTetherApp._fall_back_to_process.__get__(w)()

        self.assertFalse(w._service_mode)
        self.assertIs(w.adb, adb)
        self.assertIs(w.tether, tether)
        adb.start_monitoring.assert_called_once_with(
            on_connected=w._on_device_connected,
            on_disconnected=w._on_device_disconnected,
        )


class Actions(unittest.TestCase):
    def test_connect_reprobes_then_falls_through_to_the_local_core(self):
        w = window(
            _reprobe_service=Mock(return_value=False),
            _run_service_command=Mock(),
            adb=Mock(),
            tether=Mock(),
        )
        w.adb.find_quest.return_value = {"status": "device", "serial": "HEADSET"}

        MAIN.QuestTetherApp._do_connect.__get__(w)()

        w._reprobe_service.assert_called_once_with()
        w._run_service_command.assert_not_called()
        w.tether.connect.assert_called_once_with("HEADSET")

    def test_disconnect_reprobes_then_falls_through_to_the_local_core(self):
        w = window(
            _reprobe_service=Mock(return_value=False),
            _run_service_command=Mock(),
            tether=Mock(),
        )

        MAIN.QuestTetherApp._do_disconnect.__get__(w)()

        w._reprobe_service.assert_called_once_with()
        w._run_service_command.assert_not_called()
        w.tether.disconnect.assert_called_once_with()

    def test_connect_uses_the_service_while_it_answers(self):
        w = window(
            _reprobe_service=Mock(return_value=True),
            _run_service_command=Mock(),
            tether=Mock(),
        )

        MAIN.QuestTetherApp._do_connect.__get__(w)()

        w._run_service_command.assert_called_once_with({"cmd": "connect"})
        w.tether.connect.assert_not_called()

    def test_setting_change_reprobes_the_service_in_a_worker(self):
        w = window(
            _reprobe_service=Mock(return_value=False),
            _service_request=Mock(),
        )

        with patch.object(MAIN.threading, "Thread", _ImmediateThread):
            answer = MAIN.QuestTetherApp._push_setting.__get__(w)(
                "auto_reconnect", True
            )

        self.assertTrue(answer)
        w._reprobe_service.assert_called_once_with()
        w._service_request.assert_not_called()

    def test_setting_change_is_sent_through_the_service(self):
        w = window(
            _reprobe_service=Mock(return_value=True),
            _service_request=Mock(return_value={"ok": True}),
        )

        with patch.object(MAIN.threading, "Thread", _ImmediateThread):
            answer = MAIN.QuestTetherApp._push_setting.__get__(w)(
                "auto_reconnect", True
            )

        self.assertTrue(answer)
        w._service_request.assert_called_once_with(
            {"cmd": "settings-set", "settings": {"auto_reconnect": True}},
            timeout=5.0,
        )

    def test_ui_refresh_polls_status_off_the_tk_thread(self):
        w = window(
            _service_request=Mock(return_value={"state": "idle"}),
            _apply_service_status=Mock(),
            _adb_status=Mock(),
            _update_tray=Mock(),
            _ui_refresh_id=None,
        )
        w._fetch_service_status = MAIN.QuestTetherApp._fetch_service_status.__get__(w)
        w._show_service_status = MAIN.QuestTetherApp._show_service_status.__get__(w)
        w._ui_refresh_loop = Mock()
        w.after = lambda ms, fn=None, *a: fn(*a) if fn and ms == 0 else None

        with patch.object(MAIN.threading, "Thread", _ImmediateThread):
            MAIN.QuestTetherApp._ui_refresh_loop.__get__(w)()

        w._service_request.assert_called_once_with(
            {"cmd": "status"}, timeout=1.0, quiet=True
        )
        w._apply_service_status.assert_called_once_with({"state": "idle"})


class Reenable(unittest.TestCase):
    def _run(self, answer):
        w = window(
            _service_request=Mock(side_effect=[answer, {"state": "idle"}]),
            _apply_service_status=Mock(),
        )
        MAIN.QuestTetherApp._run_service_command.__get__(w)({"cmd": "connect"})
        return w

    def test_connect_button_is_reenabled_after_a_good_reply(self):
        w = self._run({"ok": True})
        w._connect_btn.configure.assert_called_with(state="normal")

    def test_connect_button_is_reenabled_after_an_error_reply(self):
        w = self._run({"ok": False, "error": "no headset found"})
        w._connect_btn.configure.assert_called_with(state="normal")
        w._log_panel.append.assert_any_call("no headset found")

    def test_connect_button_is_reenabled_after_no_reply(self):
        w = self._run(None)
        w._connect_btn.configure.assert_called_with(state="normal")


if __name__ == "__main__":
    unittest.main()
