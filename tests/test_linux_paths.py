"""Linux paths, relay command, autostart entry (pure, no filesystem): python -m unittest discover -s tests"""
import sys
import types
import unittest

sys.path.insert(0, ".")
from qt_platform.linux_paths import (  # noqa: E402
    autostart_command, autostart_entry, autostart_path, data_dir_path, relay_command)

FROZEN = types.MappingProxyType


class DataDir(unittest.TestCase):
    def test_xdg_state_home_wins(self):
        self.assertEqual(data_dir_path(FROZEN({"XDG_STATE_HOME": "/x/state"}), "/home/u"),
                         "/x/state/quest-tether")

    def test_default_under_home(self):
        self.assertEqual(data_dir_path(FROZEN({}), "/home/u"), "/home/u/.local/state/quest-tether")

    def test_empty_xdg_is_ignored(self):
        self.assertEqual(data_dir_path(FROZEN({"XDG_STATE_HOME": ""}), "/home/u"),
                         "/home/u/.local/state/quest-tether")


class RelayCommand(unittest.TestCase):
    def test_native_relay_run_serial(self):
        self.assertEqual(relay_command("/opt/qt/tools", "1WMHH0000"),
                         ["/opt/qt/tools/gnirehtet", "run", "1WMHH0000"])


class Autostart(unittest.TestCase):
    def test_path_uses_xdg_config_home(self):
        self.assertEqual(autostart_path(FROZEN({"XDG_CONFIG_HOME": "/x/cfg"}), "/home/u"),
                         "/x/cfg/autostart/quest-tether.desktop")

    def test_path_default(self):
        self.assertEqual(autostart_path(FROZEN({}), "/home/u"),
                         "/home/u/.config/autostart/quest-tether.desktop")

    def test_path_empty_xdg_is_ignored(self):
        self.assertEqual(autostart_path(FROZEN({"XDG_CONFIG_HOME": ""}), "/home/u"),
                         "/home/u/.config/autostart/quest-tether.desktop")

    def test_command_frozen_binary(self):
        self.assertEqual(autostart_command("/opt/qt/quest-tether", True, "/src/main.py"),
                         '"/opt/qt/quest-tether" --minimized')

    def test_command_from_source(self):
        self.assertEqual(autostart_command("/usr/bin/python3", False, "/src/main.py"),
                         '"/usr/bin/python3" "/src/main.py" --minimized')

    def test_entry_text(self):
        self.assertEqual(
            autostart_entry('"/opt/qt/quest-tether" --minimized'),
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Quest Tether\n"
            'Exec="/opt/qt/quest-tether" --minimized\n'
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n")


if __name__ == "__main__":
    unittest.main()
