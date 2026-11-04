"""vpn_state: pure parser for the gnirehtet VPN agent state in dumpsys connectivity (#64): python -m unittest tests.test_vpn_state -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import vpn_state  # noqa: E402

LINE = "  NetworkAgentInfo{{network{{100}}  ni{{VPN {state} extra: }} nc{{[ Transports: VPN TransportInfo: <VpnTransportInfo{{type=1, sessionId={sid}, bypassable=false}}> ]}}}}"


class GnirehtetVpnState(unittest.TestCase):
    def test_connected(self):
        self.assertEqual(
            vpn_state.gnirehtet_vpn_state(LINE.format(state="CONNECTED", sid="Gnirehtet")),
            "CONNECTED",
        )

    def test_disconnected(self):
        self.assertEqual(
            vpn_state.gnirehtet_vpn_state(LINE.format(state="DISCONNECTED", sid="Gnirehtet")),
            "DISCONNECTED",
        )

    def test_connecting(self):
        self.assertEqual(
            vpn_state.gnirehtet_vpn_state(LINE.format(state="CONNECTING", sid="Gnirehtet")),
            "CONNECTING",
        )

    def test_other_session_id_never_counts(self):
        self.assertIsNone(vpn_state.gnirehtet_vpn_state(LINE.format(state="CONNECTED", sid="OtherVpn")))

    def test_non_vpn_agent_never_counts(self):
        self.assertIsNone(vpn_state.gnirehtet_vpn_state("  NetworkAgentInfo{ ni{WIFI CONNECTED extra: } }"))

    def test_empty_and_none(self):
        self.assertIsNone(vpn_state.gnirehtet_vpn_state(""))
        self.assertIsNone(vpn_state.gnirehtet_vpn_state(None))

    def test_first_counting_line_wins(self):
        text = LINE.format(state="CONNECTED", sid="OtherVpn") + "\n" + LINE.format(state="DISCONNECTED", sid="Gnirehtet")
        self.assertEqual(vpn_state.gnirehtet_vpn_state(text), "DISCONNECTED")

    def test_empty_state_run_is_skipped(self):
        self.assertIsNone(vpn_state.gnirehtet_vpn_state("ni{VPN  extra} sessionId=Gnirehtet"))


class VpnClosed(unittest.TestCase):
    def test_none_is_closed(self):
        self.assertTrue(vpn_state.vpn_closed(None))

    def test_disconnected_is_closed(self):
        self.assertTrue(vpn_state.vpn_closed(LINE.format(state="DISCONNECTED", sid="Gnirehtet")))

    def test_connected_is_not_closed(self):
        self.assertFalse(vpn_state.vpn_closed(LINE.format(state="CONNECTED", sid="Gnirehtet")))

    def test_connecting_is_not_closed(self):
        self.assertFalse(vpn_state.vpn_closed(LINE.format(state="CONNECTING", sid="Gnirehtet")))

    def test_other_vpn_agent_means_closed(self):
        self.assertTrue(vpn_state.vpn_closed(LINE.format(state="CONNECTED", sid="OtherVpn")))


if __name__ == "__main__":
    unittest.main()
