"""The qtetherd systemd user unit file (read only, no systemd): python -m unittest discover -s tests"""
import configparser
import os
import unittest

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "packaging", "linux", "quest-tether.service")


def unit():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    with open(PATH, encoding="utf-8") as f:
        cp.read_file(f)
    return cp


class UserUnit(unittest.TestCase):
    def test_has_the_three_sections(self):
        self.assertEqual(unit().sections(), ["Unit", "Service", "Install"])

    def test_describes_itself(self):
        self.assertEqual(unit()["Unit"]["Description"], "Quest Tether service")

    def test_runs_the_packaged_service_binary_from_the_users_data_folder(self):
        self.assertEqual(unit()["Service"]["ExecStart"], "%h/.local/share/quest-tether/qtetherd")

    def test_is_a_plain_foreground_service_restarted_on_failure(self):
        svc = unit()["Service"]
        self.assertEqual(svc["Type"], "simple")
        self.assertEqual(svc["Restart"], "on-failure")
        self.assertEqual(svc["RestartSec"], "5")

    def test_starts_at_login_of_the_user_manager(self):
        self.assertEqual(unit()["Install"]["WantedBy"], "default.target")

    def test_is_a_user_unit_not_a_system_unit(self):
        text = open(PATH, encoding="utf-8").read()
        self.assertNotIn("User=", text)
        self.assertNotIn("multi-user.target", text)


if __name__ == "__main__":
    unittest.main()
