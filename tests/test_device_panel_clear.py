"""DeviceInfoPanel.clear resets every field colour, not just the text (no display)."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class DeviceInfoPanelClearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctk = ModuleType("customtkinter")
        ctk.CTk = object
        ctk.CTkFrame = object
        ctk.CTkLabel = object
        ctk.CTkFont = Mock()
        ctk.set_appearance_mode = Mock()
        ctk.set_default_color_theme = Mock()
        # Block pystray before import: installed backends may require a display.
        with patch.dict(sys.modules, {"customtkinter": ctk, "pystray": None}):
            spec = importlib.util.spec_from_file_location(
                "device_panel_clear_main", Path(__file__).resolve().parents[1] / "main.py"
            )
            cls.main = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.main)

    def test_clear_resets_text_and_colour_of_every_field(self):
        fields = {name: Mock() for name in
                  ["Model", "Serial", "Battery", "Temperature", "VPN IP"]}
        panel = SimpleNamespace(_fields=fields)

        self.main.DeviceInfoPanel.clear(panel)

        for name, field in fields.items():
            field.configure.assert_called_once_with(
                text="—", text_color=self.main.COLORS["text_primary"])


if __name__ == "__main__":
    unittest.main()
