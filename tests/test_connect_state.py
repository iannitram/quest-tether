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


if __name__ == "__main__":
    unittest.main()
