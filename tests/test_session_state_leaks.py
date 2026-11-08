"""Session state must not leak from one session into the next (#129).

Fakes only: no headset, no relay, no real sleeps. Covers the three numbered fixes:

1. a failed wifi-off (the tunnel check) is cleared by connect(), and the wifi-off
   runs only once the tunnel is Connected, never while Starting;
2. the tun0 rate baseline (_prev_rx, _prev_tx, _prev_stat_time) is reset by connect(),
   by disconnect() and when tun0 is missing, and a rate is never negative;
3. in 'leave' mode the Wi-Fi toggle in disconnect() and in the error restore runs
   only when the wifi-off flag exists, so the user's own Wi-Fi state is kept.

python3 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tether_manager import TetherManager, TetherState  # noqa: E402

SERIAL = "quest-serial"
NO_OUTPUT = SimpleNamespace(returncode=1, stdout="")


def make_adb():
    adb = Mock()
    adb.adb_path = "adb"
    adb.find_serial.return_value = SERIAL
    adb.is_vpn_up.return_value = True
    adb.is_vpn_down.return_value = True
    adb.check_wifi_connectivity.return_value = True
    adb.stop_gnirehtet_client.return_value = True
    adb.get_device_details.return_value = {}
    adb.run_adb.return_value = NO_OUTPUT
    return adb


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qt129-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.adb = make_adb()
        self.m = TetherManager(self.adb, gnirehtet_path="fake.jar")
        self.m._data_dir = self.tmp
        self.m._wifi_flag = os.path.join(self.tmp, "wifi-off.flag")
        sleep = patch("tether_manager.time.sleep", return_value=None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def wifi_disabled(self):
        return any(c.args[:2] == ("-s", SERIAL) and c.args[2:] == ("shell", "svc", "wifi", "disable")
                   for c in self.adb.run_adb.call_args_list)

    def wifi_enabled(self):
        return any(c.args[2:] == ("shell", "svc", "wifi", "enable")
                   for c in self.adb.run_adb.call_args_list)


class WifiOffFailureIsPerSession(Base):
    def test_connect_clears_a_failed_wifi_off_from_an_earlier_session(self):
        # Fix 1: one failed tunnel check must not disable the 'off' policy for good.
        self.m._wifi_mode = "off"
        self.m._wifi_off_failed = True
        self.m._spawn_relay = Mock(side_effect=lambda serial: setattr(
            self.m, "_process", Mock(pid=7, poll=Mock(return_value=None))))
        self.m.relay_mode = "run"
        self.m._kill_stale_process = Mock()
        self.m._start_health_monitor = Mock()

        self.assertTrue(self.m.connect(SERIAL))

        self.assertEqual(self.m.stats.state, TetherState.CONNECTED)
        self.assertTrue(self.wifi_disabled(), "wifi-off was still blocked by an old failure")

    def test_wifi_off_does_not_run_while_starting(self):
        # Fix 1: the relay has no counted client yet while Starting, so Wi-Fi stays on.
        self.m._wifi_mode = "off"
        self.m._set_state(TetherState.STARTING)

        self.m._apply_wifi_policy(SERIAL)

        self.assertFalse(self.wifi_disabled(), "Wi-Fi was cut while the tunnel was Starting")
        self.assertFalse(self.m._wifi_off_failed)

    def test_wifi_off_runs_once_connected(self):
        self.m._wifi_mode = "off"
        self.m._set_state(TetherState.CONNECTED)

        self.m._apply_wifi_policy(SERIAL)

        self.assertTrue(self.wifi_disabled())


class RateBaselineIsPerSession(Base):
    def _sample(self, rx, tx, at):
        self.adb.get_tun0_stats.return_value = {"rx_bytes": rx, "tx_bytes": tx}
        with patch("tether_manager.time.time", return_value=at):
            self.m._update_stats(SERIAL)

    def test_disconnect_then_connect_gives_no_negative_rate(self):
        # Fix 2: a sample from before a disconnect must not set a rate after it.
        self._sample(10_000_000_000, 5_000_000_000, at=1000.0)
        self._sample(10_000_000_100, 5_000_000_100, at=1001.0)
        self.m._kill_process = Mock()
        self.m.disconnect(headset_present=False)

        self._sample(1_000, 500, at=2000.0)

        self.assertGreaterEqual(self.m.stats.rx_rate_bps, 0.0)
        self.assertGreaterEqual(self.m.stats.tx_rate_bps, 0.0)
        self.assertEqual(self.m.stats.rx_rate_bps, 0.0)

    def test_connect_resets_the_baseline_from_an_old_session(self):
        # Fix 2: connect() starts with no baseline, so the first sample has no rate.
        self.m._prev_rx = 9_000_000_000
        self.m._prev_tx = 9_000_000_000
        self.m._prev_stat_time = 999.0
        self.m._spawn_relay = Mock(side_effect=lambda serial: setattr(
            self.m, "_process", Mock(pid=8, poll=Mock(return_value=None))))
        self.m.relay_mode = "run"
        self.m._kill_stale_process = Mock()
        self.m._start_health_monitor = Mock()
        self.assertTrue(self.m.connect(SERIAL))

        self._sample(1_000, 500, at=1000.0)

        self.assertEqual(self.m.stats.rx_rate_bps, 0.0)
        self.assertEqual(self.m.stats.tx_rate_bps, 0.0)

    def test_missing_tun0_resets_the_baseline(self):
        # Fix 2: a recreated tun0 (counters back at zero) must not give a negative rate.
        self._sample(1_000_000, 500_000, at=1000.0)
        self.adb.get_tun0_stats.return_value = None
        with patch("tether_manager.time.time", return_value=1005.0):
            self.m._update_stats(SERIAL)

        self._sample(50, 20, at=1010.0)

        self.assertEqual(self.m.stats.rx_rate_bps, 0.0)
        self.assertEqual(self.m.stats.tx_rate_bps, 0.0)

    def test_a_counter_that_goes_backwards_never_gives_a_negative_rate(self):
        # Fix 2: clamp, so no negative throughput is ever shown.
        self._sample(1_000_000, 500_000, at=1000.0)

        self._sample(500, 100, at=1010.0)

        self.assertGreaterEqual(self.m.stats.rx_rate_bps, 0.0)
        self.assertGreaterEqual(self.m.stats.tx_rate_bps, 0.0)

    def test_a_normal_rate_is_still_reported(self):
        self._sample(1_000, 500, at=1000.0)

        self._sample(11_000, 1_500, at=1010.0)

        self.assertAlmostEqual(self.m.stats.rx_rate_bps, 1000.0)
        self.assertAlmostEqual(self.m.stats.tx_rate_bps, 100.0)


class LeaveModeKeepsTheUsersWifi(Base):
    def setUp(self):
        super().setUp()
        self.m._kill_process = Mock()
        self.m._wifi_mode = "leave"

    def test_disconnect_in_leave_mode_does_not_toggle_wifi_without_a_flag(self):
        # Fix 3: the user had Wi-Fi off; a toggle would turn it on.
        self.m.stats.device_serial = SERIAL
        self.m._set_state(TetherState.CONNECTED)

        self.m.disconnect()

        self.adb.stop_gnirehtet_client.assert_called()
        self.adb.toggle_wifi.assert_not_called()
        self.assertFalse(self.wifi_enabled())

    def test_error_restore_in_leave_mode_does_not_toggle_wifi_without_a_flag(self):
        # Fix 3: same rule on the error path.
        self.m._restore_headset_best_effort(SERIAL)

        self.adb.stop_gnirehtet_client.assert_called()
        self.adb.toggle_wifi.assert_not_called()

    def test_disconnect_in_leave_mode_restores_wifi_when_our_flag_exists(self):
        # Fix 3 guard: a flag we wrote means we turned Wi-Fi off, so we turn it back on.
        with open(self.m._wifi_flag, "w") as f:
            f.write("x")
        self.m.stats.device_serial = SERIAL

        self.m.disconnect()

        self.adb.toggle_wifi.assert_called_once()
        self.assertFalse(os.path.exists(self.m._wifi_flag))

    def test_error_restore_in_leave_mode_restores_wifi_when_our_flag_exists(self):
        with open(self.m._wifi_flag, "w") as f:
            f.write("x")

        self.m._restore_headset_best_effort(SERIAL)

        self.adb.toggle_wifi.assert_called_once()
        self.assertFalse(os.path.exists(self.m._wifi_flag))

    def test_disconnect_in_off_mode_still_toggles_wifi(self):
        # Fix 3 guard: outside leave mode the DNS toggle is unchanged.
        self.m._wifi_mode = "off"
        self.m.stats.device_serial = SERIAL

        self.m.disconnect()

        self.adb.toggle_wifi.assert_called_once()


if __name__ == "__main__":
    unittest.main()
