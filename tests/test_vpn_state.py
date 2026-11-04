"""vpn_state: pure parser for the gnirehtet VPN agent state in dumpsys connectivity (#64): python -m unittest tests.test_vpn_state -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import vpn_state  # noqa: E402

LINE = "  NetworkAgentInfo{{network{{100}}  ni{{VPN {state} extra: }} nc{{[ Transports: VPN TransportInfo: <VpnTransportInfo{{type=1, sessionId={sid}, bypassable=false}}> ]}}}}"
REAL = "  NetworkAgentInfo{network{100}  handle{432902426637}  ni{VPN CONNECTED extra: } created=2026-11-04T21:00:10.055Z Score(Policies : EVER_EVALUATED&IS_VPN&EVER_VALIDATED&IS_VALIDATED ; KeepConnected : 0)  lp{{InterfaceName: tun0 LinkAddresses: [ 10.0.0.2/32 ] MTU: 16384}}  nc{[ Transports: VPN Capabilities: INTERNET&NOT_RESTRICTED&TRUSTED&VALIDATED&NOT_SUSPENDED TransportInfo: <VpnTransportInfo{type=1, sessionId=Gnirehtet, bypassable=false longLivedTcpConnectionsExpensive=false}> Uids: <{0-99999}> UnderlyingNetworks: Null]}  factorySerialNumber=3}"


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

    def test_real_quest2_line_is_connected(self):
        self.assertEqual(vpn_state.gnirehtet_vpn_state(REAL), "CONNECTED")

    def test_session_id_must_match_exactly(self):
        self.assertIsNone(vpn_state.gnirehtet_vpn_state(LINE.format(state="CONNECTED", sid="GnirehtetFoo")))

    def test_nc_part_alone_does_not_count(self):
        self.assertIsNone(vpn_state.gnirehtet_vpn_state("nc{[ Transports: VPN TransportInfo: <VpnTransportInfo{type=1, sessionId=Gnirehtet, bypassable=false}> ]}"))

    def test_lowercase_state_word_does_not_count(self):
        self.assertIsNone(vpn_state.gnirehtet_vpn_state("ni{VPN connected extra: } sessionId=Gnirehtet,"))


class VpnClosed(unittest.TestCase):
    def test_none_is_not_closed(self):
        self.assertFalse(vpn_state.vpn_closed(None))

    def test_empty_read_is_not_closed(self):
        self.assertFalse(vpn_state.vpn_closed(""))
        self.assertFalse(vpn_state.vpn_closed("   \n"))

    def test_real_read_without_gnirehtet_agent_is_closed(self):
        self.assertTrue(vpn_state.vpn_closed("  NetworkAgentInfo{ ni{WIFI CONNECTED extra: } }"))

    def test_real_quest2_line_is_not_closed(self):
        self.assertFalse(vpn_state.vpn_closed(REAL))

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
