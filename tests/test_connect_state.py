"""Tunnel state decisions that can be checked without ADB or a headset."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherState, _state_for_tunnel


class ConnectStateTests(unittest.TestCase):
    def test_missing_tun0_is_not_connected_even_if_wifi_is_reachable(self):
        # Wi-Fi reachability is not evidence that the VPN tunnel is up.
        self.assertEqual(_state_for_tunnel(False), TetherState.STARTING)

    def test_tun0_present_is_connected(self):
        self.assertEqual(_state_for_tunnel(True), TetherState.CONNECTED)

    def test_no_client_line_yet_leaves_tun0_as_the_only_gate(self):
        # #62 default: a relay that never prints client lines still reaches Connected.
        self.assertEqual(_state_for_tunnel(True, None), TetherState.CONNECTED)

    def test_a_connected_client_with_tun0_is_connected(self):
        self.assertEqual(_state_for_tunnel(True, 1), TetherState.CONNECTED)

    def test_tun0_with_no_connected_client_is_not_connected(self):
        # The headset VPN dropped (sleep, teardown) while a stale tun0 still shows.
        self.assertEqual(_state_for_tunnel(True, 0), TetherState.STARTING)

    def test_a_client_without_tun0_is_not_connected(self):
        self.assertEqual(_state_for_tunnel(False, 2), TetherState.STARTING)


if __name__ == "__main__":
    unittest.main()
