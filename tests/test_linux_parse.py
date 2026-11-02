"""Linux output parsers (no headset, no nmcli, no ss): python -m unittest discover -s tests"""
import sys
import unittest

sys.path.insert(0, ".")
from qt_platform.linux_parse import (  # noqa: E402
    cmdline_mentions_gnirehtet, parse_active_ssid, parse_active_wifi_connection, parse_psk,
    parse_ss_pids, parse_ssid_field)

SS = (
    "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
    'LISTEN 0      50         127.0.0.1:31416     0.0.0.0:*     users:(("gnirehtet",pid=4242,fd=7))\n'
    'LISTEN 0      128          0.0.0.0:22        0.0.0.0:*     users:(("sshd",pid=900,fd=3))\n'
    'LISTEN 0      50              [::]:31416        [::]:*     users:(("java",pid=77,fd=9),("x",pid=78,fd=3))\n'
    'LISTEN 0      50         127.0.0.1:131416    0.0.0.0:*     users:(("other",pid=5,fd=1))\n'
    'LISTEN 0      50         127.0.0.1:31416     0.0.0.0:*     users:(("gnirehtet",pid=4242,fd=8))\n'
)


class ActiveSsid(unittest.TestCase):
    def test_picks_the_yes_line(self):
        self.assertEqual(parse_active_ssid("no:Other\nyes:ExampleNet\nno:Third\n"), "ExampleNet")

    def test_first_yes_line_wins(self):
        self.assertEqual(parse_active_ssid("yes:A\nyes:B\n"), "A")

    def test_unescapes_colon_and_backslash(self):
        self.assertEqual(parse_active_ssid("yes:My\\:Net\\\\2\n"), "My:Net\\2")

    def test_no_active_line_is_none(self):
        self.assertIsNone(parse_active_ssid("no:Other\nno:Third\n"))

    def test_empty_ssid_is_none(self):
        self.assertIsNone(parse_active_ssid("yes:\n"))

    def test_empty_text_is_none(self):
        self.assertIsNone(parse_active_ssid(""))


class ActiveWifiConnection(unittest.TestCase):
    def test_picks_the_wifi_line_not_ethernet(self):
        self.assertEqual(parse_active_wifi_connection("Wired:802-3-ethernet\nWi-Fi 2:802-11-wireless\n"), "Wi-Fi 2")

    def test_unescapes_colon_in_the_name(self):
        self.assertEqual(parse_active_wifi_connection("Home\\:5G:802-11-wireless\n"), "Home:5G")

    def test_no_wifi_line_is_none(self):
        self.assertIsNone(parse_active_wifi_connection("lo:loopback\n"))

    def test_empty_name_is_none(self):
        self.assertIsNone(parse_active_wifi_connection(":802-11-wireless\n"))

    def test_empty_text_is_none(self):
        self.assertIsNone(parse_active_wifi_connection(""))


class SsidField(unittest.TestCase):
    def test_returns_first_line(self):
        self.assertEqual(parse_ssid_field("ExampleNet\n"), "ExampleNet")

    def test_unescapes_colon_and_backslash(self):
        self.assertEqual(parse_ssid_field("My\\:Net\\\\2\n"), "My:Net\\2")

    def test_empty_line_is_none(self):
        self.assertIsNone(parse_ssid_field("\n"))

    def test_empty_text_is_none(self):
        self.assertIsNone(parse_ssid_field(""))


class Psk(unittest.TestCase):
    def test_returns_the_line_without_newline(self):
        self.assertEqual(parse_psk("example-pass\n"), "example-pass")

    def test_keeps_inner_and_edge_spaces(self):
        self.assertEqual(parse_psk(" a b \r\n"), " a b ")

    def test_empty_is_none(self):
        self.assertIsNone(parse_psk(""))
        self.assertIsNone(parse_psk("\n"))


class SsPids(unittest.TestCase):
    def test_exact_port_all_pids_in_order_no_duplicates(self):
        self.assertEqual(parse_ss_pids(SS, 31416), [4242, 77, 78])

    def test_port_must_match_whole_number(self):
        self.assertEqual(parse_ss_pids(SS, 1416), [])
        self.assertEqual(parse_ss_pids(SS, 131416), [5])

    def test_other_port(self):
        self.assertEqual(parse_ss_pids(SS, 22), [900])

    def test_header_only_and_empty(self):
        self.assertEqual(parse_ss_pids(SS.splitlines()[0], 31416), [])
        self.assertEqual(parse_ss_pids("", 31416), [])


class Cmdline(unittest.TestCase):
    def test_nul_separated_native_relay(self):
        self.assertTrue(cmdline_mentions_gnirehtet("/t/gnirehtet\0run\0SERIAL\0"))

    def test_java_relay_by_jar_name_any_case(self):
        self.assertTrue(cmdline_mentions_gnirehtet("java -jar /t/Gnirehtet.jar run X"))

    def test_other_program_is_false(self):
        self.assertFalse(cmdline_mentions_gnirehtet("/usr/bin/sshd\0-D\0"))

    def test_empty_is_false(self):
        self.assertFalse(cmdline_mentions_gnirehtet(""))


if __name__ == "__main__":
    unittest.main()
