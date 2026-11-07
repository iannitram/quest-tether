"""qtetherd settings persistence and load order (#117), in temp folders, no headset: python -m unittest discover -s tests"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qtetherd  # noqa: E402
from test_qtetherd_requests import FakeTether, SERIAL, stats  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.dir = tmp
        self.path = os.path.join(tmp, "settings.json")
        # Never the real config folder: settings-set saves settings.json.
        patcher = patch("qtetherd._settings_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as stream:
            json.dump(data, stream)

    def read(self):
        with open(self.path, encoding="utf-8") as stream:
            return json.load(stream)

    def set(self, tether, settings):
        return qtetherd.handle_request(
            tether, SERIAL, {"cmd": "settings-set", "settings": settings}
        )


class LoadAtStart(Base):
    def test_file_values_are_applied(self):
        self.write({"version": 1, "auto_reconnect": False, "wifi_mode": "leave"})
        t = FakeTether(stats())
        qtetherd.apply_saved_settings(t, env={})
        self.assertFalse(t.auto_reconnect)
        self.assertEqual(t.wifi_mode, "leave")

    def test_valid_env_wifi_mode_blocks_the_saved_one(self):
        # TetherManager reads QT_WIFI_MODE itself; here the file must not overwrite it.
        self.write({"auto_reconnect": False, "wifi_mode": "leave"})
        t = FakeTether(stats())
        applied = qtetherd.apply_saved_settings(t, env={"QT_WIFI_MODE": "sync"})
        self.assertEqual(t.wifi_mode, "off")
        self.assertNotIn("wifi_mode", applied)

    def test_auto_reconnect_still_comes_from_the_file_when_env_is_set(self):
        self.write({"auto_reconnect": False, "wifi_mode": "leave"})
        t = FakeTether(stats())
        qtetherd.apply_saved_settings(t, env={"QT_WIFI_MODE": "sync"})
        self.assertFalse(t.auto_reconnect)

    def test_invalid_env_is_ignored_and_the_file_is_used(self):
        self.write({"wifi_mode": "leave"})
        t = FakeTether(stats())
        qtetherd.apply_saved_settings(t, env={"QT_WIFI_MODE": "sideways"})
        self.assertEqual(t.wifi_mode, "leave")

    def test_no_file_leaves_the_defaults(self):
        t = FakeTether(stats())
        applied = qtetherd.apply_saved_settings(t, env={})
        self.assertEqual(applied, {})
        self.assertTrue(t.auto_reconnect)
        self.assertEqual(t.wifi_mode, "off")

    def test_bad_file_leaves_the_defaults_and_keeps_good_keys(self):
        self.write({"wifi_mode": "bogus", "auto_reconnect": "no"})
        t = FakeTether(stats())
        qtetherd.apply_saved_settings(t, env={})
        self.assertTrue(t.auto_reconnect)
        self.assertEqual(t.wifi_mode, "off")

    def test_load_returns_what_it_applied(self):
        self.write({"auto_reconnect": False, "wifi_mode": "sync"})
        t = FakeTether(stats())
        self.assertEqual(qtetherd.apply_saved_settings(t, env={}),
                         {"auto_reconnect": False, "wifi_mode": "sync"})


class SetPersists(Base):
    def test_set_writes_the_effective_values_and_reports_persisted(self):
        t = FakeTether(stats())
        answer = self.set(t, {"wifi-mode": "leave"})
        self.assertEqual(answer["persisted"], True)
        self.assertTrue(answer["ok"])
        self.assertEqual(self.read(), {"version": 1, "auto_reconnect": True, "wifi_mode": "leave"})

    def test_a_later_set_keeps_the_other_settings_in_the_file(self):
        t = FakeTether(stats())
        self.set(t, {"auto_reconnect": False})
        self.set(t, {"wifi_mode": "sync"})
        self.assertEqual(self.read(), {"version": 1, "auto_reconnect": False, "wifi_mode": "sync"})

    def test_rejected_request_writes_nothing(self):
        t = FakeTether(stats())
        self.set(t, {"wifi_mode": "bogus"})
        self.assertFalse(os.path.exists(self.path))

    def test_crash_reports_only_request_does_not_touch_the_file(self):
        t = FakeTether(stats())
        with patch("qtetherd.crash_report.set_enabled") as set_enabled:
            answer = self.set(t, {"crash_reports": False})
        set_enabled.assert_called_once_with(False)
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(answer["ok"])

    def test_save_failure_keeps_the_live_value_and_warns(self):
        t = FakeTether(stats())
        with patch("qtetherd.settings_file.save_settings", return_value=False):
            answer = self.set(t, {"wifi_mode": "leave"})
        self.assertTrue(answer["ok"])
        self.assertFalse(answer["persisted"])
        self.assertIn("warning", answer)
        self.assertEqual(t.wifi_mode, "leave")
        self.assertEqual(answer["settings"]["wifi_mode"], "leave")

    def test_restart_gives_the_same_settings(self):
        a = FakeTether(stats())
        self.set(a, {"auto_reconnect": False, "wifi_mode": "sync"})
        b = FakeTether(stats())
        qtetherd.apply_saved_settings(b, env={})
        self.assertEqual(qtetherd._settings(b), qtetherd._settings(a))
        self.assertEqual(b.wifi_mode, "sync")
        self.assertFalse(b.auto_reconnect)

    def test_concurrent_sets_leave_one_valid_snapshot(self):
        tethers = [FakeTether(stats()) for _ in range(8)]
        modes = ["off", "sync", "leave"]
        barrier = threading.Barrier(len(tethers))
        errors = []

        def worker(i):
            try:
                barrier.wait()
                for _ in range(20):
                    self.set(tethers[i], {"wifi_mode": modes[i % 3],
                                          "auto_reconnect": bool(i % 2)})
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(tethers))]
        for th in threads:
            th.start()
        for th in threads:
            th.join(10)
        self.assertEqual(errors, [])
        data = self.read()
        self.assertIn(data["wifi_mode"], modes)
        self.assertIsInstance(data["auto_reconnect"], bool)
        self.assertEqual(sorted(data), ["auto_reconnect", "version", "wifi_mode"])
        self.assertEqual(os.listdir(self.dir), ["settings.json"])


class ServeOrder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.path = os.path.join(tmp, "settings.json")
        patches = [
            patch("qtetherd._settings_path", return_value=self.path),
            patch("qtetherd.crash_report.install"),
            patch("qtetherd.logging.basicConfig"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_serve(self, server=None, tether=None):
        adb = Mock()
        adb.find_serial = Mock(return_value="SERIAL")
        tether = tether or SimpleNamespace(auto_reconnect=True, wifi_mode="off")
        server = server or Mock()
        with (
            patch("qtetherd.ADBManager", return_value=adb),
            patch("qtetherd.TetherManager", return_value=tether),
            patch("qtetherd.make_server", return_value=server),
        ):
            qtetherd.serve()
        return adb, tether

    def test_saved_auto_reconnect_off_stops_the_monitor_from_starting(self):
        with open(self.path, "w", encoding="utf-8") as stream:
            json.dump({"auto_reconnect": False}, stream)
        adb, tether = self.run_serve()
        self.assertFalse(tether.auto_reconnect)
        adb.start_monitoring.assert_not_called()

    def test_default_auto_reconnect_starts_the_monitor(self):
        adb, tether = self.run_serve()
        self.assertTrue(tether.auto_reconnect)
        adb.start_monitoring.assert_called_once()

    def test_a_busy_port_exits_before_touching_the_settings(self):
        with (
            patch("qtetherd.apply_saved_settings") as apply,
            patch("qtetherd.make_server", side_effect=OSError("in use")),
            patch("qtetherd.ADBManager"),
            patch("qtetherd.TetherManager"),
            patch("sys.stderr"),
        ):
            with self.assertRaises(SystemExit):
                qtetherd.serve()
        apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
