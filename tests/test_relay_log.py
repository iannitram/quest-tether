"""The relay "Client #N connected/disconnected" parser, no relay needed."""

import unittest

from relay_log import apply_change, parse_client_change


class ParseClientChangeTests(unittest.TestCase):
    def test_a_connected_line(self):
        self.assertEqual(
            parse_client_change("2026-01-01 12:00:00.000 INFO TunnelServer: Client #3 connected"),
            ("connected", 3),
        )

    def test_a_disconnected_line(self):
        self.assertEqual(
            parse_client_change("2026-01-01 12:00:00.000 INFO TunnelServer: Client #3 disconnected"),
            ("disconnected", 3),
        )

    def test_a_multi_digit_id_and_a_trailing_newline(self):
        self.assertEqual(parse_client_change("TunnelServer: Client #12 disconnected\n"), ("disconnected", 12))

    def test_client_zero_connects(self):
        self.assertEqual(parse_client_change("2026-09-27 03:29:44.592 I TunnelServer: Client #0 connected"), ("connected", 0))

    def test_text_after_the_word_is_not_a_change(self):
        self.assertIsNone(parse_client_change("Client #3 connected, sending"))

    def test_a_missing_id_is_not_a_change(self):
        self.assertIsNone(parse_client_change("Client # connected"))

    def test_an_unrelated_relay_line_is_not_a_change(self):
        self.assertIsNone(parse_client_change("2026-09-27 03:29:44.592 INFO Relay: Relay server started"))

    def test_the_empty_line_is_not_a_change(self):
        self.assertIsNone(parse_client_change(""))

    def test_none_is_not_a_change(self):
        self.assertIsNone(parse_client_change(None))


class ApplyChangeTests(unittest.TestCase):
    def test_connecting_adds_the_id(self):
        self.assertEqual(apply_change(frozenset(), ("connected", 1)), frozenset({1}))

    def test_disconnecting_removes_the_id(self):
        self.assertEqual(apply_change(frozenset({1, 2}), ("disconnected", 1)), frozenset({2}))

    def test_disconnecting_a_missing_id_changes_nothing(self):
        self.assertEqual(apply_change(frozenset({2}), ("disconnected", 5)), frozenset({2}))

    def test_no_change_leaves_the_set_alone(self):
        self.assertEqual(apply_change(frozenset({2}), None), frozenset({2}))


if __name__ == "__main__":
    unittest.main()
