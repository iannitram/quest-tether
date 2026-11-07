"""settings.json helper and config folder paths (#117), in temp folders: python -m unittest discover -s tests"""

import glob
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qt_platform import settings_file  # noqa: E402
from qt_platform.linux_paths import autostart_path, config_dir_path  # noqa: E402

FROZEN = types.MappingProxyType


class ConfigDirPath(unittest.TestCase):
    def test_xdg_config_home_wins(self):
        self.assertEqual(config_dir_path(FROZEN({"XDG_CONFIG_HOME": "/x/cfg"}), "/home/u"),
                         "/x/cfg/quest-tether")

    def test_unset_falls_back_to_home_config(self):
        self.assertEqual(config_dir_path(FROZEN({}), "/home/u"),
                         "/home/u/.config/quest-tether")

    def test_empty_xdg_is_ignored(self):
        self.assertEqual(config_dir_path(FROZEN({"XDG_CONFIG_HOME": ""}), "/home/u"),
                         "/home/u/.config/quest-tether")

    def test_autostart_still_uses_the_same_base(self):
        env = FROZEN({"XDG_CONFIG_HOME": "/x/cfg"})
        self.assertEqual(autostart_path(env, "/home/u"),
                         "/x/cfg/autostart/quest-tether.desktop")


class Path(unittest.TestCase):
    def test_settings_file_is_in_the_config_folder(self):
        self.assertEqual(settings_file.settings_path("/x/quest-tether"),
                         os.path.join("/x/quest-tether", "settings.json"))


class Validation(unittest.TestCase):
    def test_wifi_mode_values(self):
        for mode in ("off", "sync", "leave"):
            self.assertTrue(settings_file.valid_value("wifi_mode", mode))
        for bad in ("bogus", "", None, 1, ["sync"]):
            self.assertFalse(settings_file.valid_value("wifi_mode", bad), bad)

    def test_booleans_must_be_real_booleans(self):
        for key in ("auto_reconnect", "crash_reports"):
            self.assertTrue(settings_file.valid_value(key, True))
            for bad in ("yes", 1, 0, None):
                self.assertFalse(settings_file.valid_value(key, bad), (key, bad))

    def test_unknown_key_is_never_valid(self):
        self.assertFalse(settings_file.valid_value("volume", True))


class RoundTrip(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.path = os.path.join(self.dir, "settings.json")

    def test_save_then_load(self):
        self.assertTrue(settings_file.save_settings(
            self.path, {"auto_reconnect": False, "wifi_mode": "leave", "crash_reports": True}))
        self.assertEqual(settings_file.load_settings(self.path),
                         {"auto_reconnect": False, "wifi_mode": "leave"})

    def test_saved_file_is_versioned_json_without_crash_reports(self):
        settings_file.save_settings(self.path, {"auto_reconnect": True, "wifi_mode": "sync",
                                                "crash_reports": False})
        with open(self.path, encoding="utf-8") as stream:
            data = json.load(stream)
        self.assertEqual(data, {"version": 1, "auto_reconnect": True, "wifi_mode": "sync"})

    def test_save_creates_the_folder(self):
        nested = os.path.join(self.dir, "a", "b", "settings.json")
        self.assertTrue(settings_file.save_settings(nested, {"wifi_mode": "off"}))
        self.assertEqual(settings_file.load_settings(nested), {"wifi_mode": "off"})

    def test_save_writes_only_valid_persisted_values(self):
        settings_file.save_settings(self.path, {"wifi_mode": "bogus", "auto_reconnect": False})
        self.assertEqual(settings_file.load_settings(self.path), {"auto_reconnect": False})

    def test_no_temp_file_is_left_after_a_good_save(self):
        settings_file.save_settings(self.path, {"wifi_mode": "sync"})
        self.assertEqual(glob.glob(os.path.join(self.dir, "*.tmp")), [])


class BadFiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "settings.json")

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as stream:
            stream.write(text)

    def test_missing_file_gives_nothing(self):
        self.assertEqual(settings_file.load_settings(self.path), {})

    def test_garbage_text_gives_nothing(self):
        self._write("{not json")
        self.assertEqual(settings_file.load_settings(self.path), {})

    def test_empty_file_gives_nothing(self):
        self._write("")
        self.assertEqual(settings_file.load_settings(self.path), {})

    def test_json_list_gives_nothing(self):
        self._write('[{"wifi_mode": "leave"}]')
        self.assertEqual(settings_file.load_settings(self.path), {})

    def test_bad_wifi_mode_is_dropped_and_the_rest_kept(self):
        self._write('{"wifi_mode": "bogus", "auto_reconnect": false}')
        self.assertEqual(settings_file.load_settings(self.path), {"auto_reconnect": False})

    def test_string_auto_reconnect_is_dropped(self):
        self._write('{"auto_reconnect": "yes", "wifi_mode": "sync"}')
        self.assertEqual(settings_file.load_settings(self.path), {"wifi_mode": "sync"})

    def test_unknown_and_future_keys_are_ignored(self):
        self._write('{"version": 9, "future": 1, "crash_reports": false, "wifi_mode": "leave"}')
        self.assertEqual(settings_file.load_settings(self.path), {"wifi_mode": "leave"})

    def test_a_directory_at_the_path_gives_nothing(self):
        os.mkdir(self.path)
        self.assertEqual(settings_file.load_settings(self.path), {})


class AtomicSave(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.path = os.path.join(self.dir, "settings.json")
        settings_file.save_settings(self.path, {"wifi_mode": "off", "auto_reconnect": True})

    def test_failed_replace_keeps_the_old_file_and_removes_the_temp(self):
        before = open(self.path, encoding="utf-8").read()
        with patch("os.replace", side_effect=OSError("disk gone")):
            self.assertFalse(settings_file.save_settings(self.path, {"wifi_mode": "leave"}))
        self.assertEqual(open(self.path, encoding="utf-8").read(), before)
        self.assertEqual(glob.glob(os.path.join(self.dir, "*.tmp")), [])

    def test_unwritable_folder_returns_false_without_raising(self):
        # A plain file where the folder should be: makedirs fails, root or not.
        blocker = os.path.join(self.dir, "blocker")
        open(blocker, "w").close()
        self.assertFalse(settings_file.save_settings(
            os.path.join(blocker, "settings.json"), {"wifi_mode": "sync"}))


if __name__ == "__main__":
    unittest.main()
