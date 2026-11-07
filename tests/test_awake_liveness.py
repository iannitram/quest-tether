"""Awake-gated liveness in the health loop (#65), on a fake clock: no headset needed.

The clock advances only through the patched time.sleep, so each health cycle
lands on a known second. A sleep hook ends the loop at a chosen time.
"""
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import heal_policy  # noqa: E402
from heal import Heal  # noqa: E402
from tether_manager import TetherManager, TetherState  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self.now)


class LivenessHarness(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        for target, fake in (("tether_manager.time.time", self.clock.time),
                             ("tether_manager.time.sleep", self.clock.sleep)):
            patcher = patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.awake_at = lambda t: False       # headset state by time
        self.tun_up_at = lambda t: False      # tun0 presence by time
        self.probe_results = []
        self.adb = Mock()
        self.adb.adb_path = "adb"
        self.adb.is_headset_awake.side_effect = self._probe

        self.manager = TetherManager(self.adb, gnirehtet_path="fake.jar")
        self.manager.POST_CONNECT_COOLDOWN = 0
        self.manager._wifi_mode = "leave"
        self.manager._auto_reconnect = True
        self.manager._process = Mock()
        self.manager._process.poll.return_value = None
        self.manager._update_stats = lambda serial: self.tun_up_at(self.clock.now)

        self.heals = []
        self.reconnects = []
        self.manager._heal = Mock(side_effect=self._record_heal)
        self.manager._attempt_reconnect = Mock(side_effect=self._record_reconnect)
        self.states = []
        self.manager.set_callbacks(on_state_change=lambda state, stats: self.states.append(state))

    def _probe(self, serial):
        awake = self.awake_at(self.clock.now)
        self.probe_results.append((self.clock.now, awake))
        return awake

    def _record_heal(self, serial, action):
        self.heals.append((self.clock.now, action))
        return False

    def _record_reconnect(self, serial):
        self.reconnects.append(self.clock.now)

    def run_until(self, end):
        def stop_at_end(now):
            if now >= end:
                self.manager._running = False
        self.clock.on_sleep = stop_at_end
        self.manager._running = True
        self.manager._health_loop("quest-serial")

    def heal_times(self):
        return [t for t, _ in self.heals]


class AsleepAndAwake(LivenessHarness):
    def test_thirty_minutes_asleep_makes_no_heal_and_no_breaker_trip(self):
        self.awake_at = lambda t: False

        self.run_until(1800)

        self.assertEqual(self.heals, [])
        self.assertEqual(self.reconnects, [])
        self.assertFalse(self.manager._healer.tripped)
        self.assertNotIn(TetherState.ERROR, self.states)
        self.assertTrue(self.probe_results, "the headset should still be probed")
        self.assertLessEqual(len(self.probe_results), 1800 // 15 + 1,
                             "asleep, the probe runs about every 15 s")

    def test_the_first_heal_comes_one_grace_after_the_headset_wakes(self):
        self.awake_at = lambda t: t >= 600
        self.tun_up_at = lambda t: False

        self.run_until(700)

        self.assertTrue(self.heals)
        woke = min(t for t, awake in self.probe_results if awake)
        delay = self.heal_times()[0] - woke
        self.assertGreaterEqual(delay, heal_policy.GRACE)
        self.assertLessEqual(delay, heal_policy.GRACE + 5)

    def test_restarts_back_off_60_then_120_then_240_seconds(self):
        self.awake_at = lambda t: True
        self.tun_up_at = lambda t: False

        self.run_until(500)

        times = self.heal_times()
        self.assertEqual([b - a for a, b in zip(times, times[1:])], [60, 120, 240])
        self.assertEqual([action for _, action in self.heals][:3], [
            Heal.RESTART_CLIENT, Heal.RESTART_ADB_THEN_CLIENT, Heal.FULL_RECONNECT,
        ])

    def test_a_missing_headset_reading_is_unknown_and_causes_no_heal(self):
        self.awake_at = lambda t: None
        self.tun_up_at = lambda t: False

        self.run_until(300)

        self.assertTrue(self.adb.is_headset_awake.called, "the headset should still be asked")
        self.assertEqual(self.heals, [])
        self.assertFalse(self.manager._healer.tripped)
        self.assertNotIn(TetherState.ERROR, self.states)

    def test_a_healthy_tunnel_makes_no_headset_calls(self):
        self.tun_up_at = lambda t: True

        self.run_until(300)

        self.adb.is_headset_awake.assert_not_called()
        self.assertEqual(self.heals, [])
        self.assertEqual(self.manager.stats.state, TetherState.CONNECTED)

    def test_tunnel_back_for_the_grace_period_resets_the_restart_count(self):
        self.awake_at = lambda t: True
        self.tun_up_at = lambda t: t >= 12   # first heal at 10 s, then tun0 returns

        self.run_until(40)

        self.assertEqual(self.heal_times(), [10])
        self.assertEqual(self.manager._liveness_now().restarts, 0)


    def test_a_short_tunnel_blip_restarts_the_grace_clocks(self):
        # tun0 is gone at 0-14 s, back for 5 s, gone again from 20 s. The second
        # outage must earn its own 10 s grace instead of reusing the first one.
        self.awake_at = lambda t: True
        self.tun_up_at = lambda t: 15 <= t < 20

        self.run_until(60)

        self.assertEqual(self.heal_times()[:1], [10], "first outage heals after the grace")
        self.assertEqual(self.manager._liveness_now().restarts, 1)
        # restart at 10 backs off to 70, so no second heal inside this run; the
        # point is the clocks: after the blip silent_since is the new outage start.
        self.assertEqual(self.manager._liveness_now().silent_since, 20.0)

    def test_a_blip_does_not_make_the_next_miss_an_instant_heal(self):
        self.awake_at = lambda t: True
        self.tun_up_at = lambda t: 5 <= t < 10 or t >= 1000   # one short blip, then a long gap
        self.manager.HEAL_FINAL_RESTARTS = 99

        self.run_until(1000)

        # First outage 0-5 s: no heal yet (under the grace). Blip. Second outage
        # starts at 10 s and heals one grace later, not at 10 s.
        self.assertEqual(self.heal_times()[0], 20)

    def test_an_awake_reading_then_no_reading_causes_no_heal(self):
        self.awake_at = lambda t: True if t < 20 else None
        self.tun_up_at = lambda t: False
        self.manager.HEAL_FINAL_RESTARTS = 99

        self.run_until(300)

        self.assertEqual(self.heal_times(), [10], "only the heal before the readings stopped")

    def test_no_reading_from_the_start_after_awake_reading_blocks_restart_due(self):
        self.awake_at = lambda t: True if t < 5 else None   # awake once, then unknown
        self.tun_up_at = lambda t: False

        self.run_until(300)

        self.assertEqual(self.heals, [])


class CapAndRelayDeath(LivenessHarness):
    def test_the_final_restart_cap_sets_error_once_and_stops(self):
        self.manager.HEAL_FINAL_RESTARTS = 2
        self.awake_at = lambda t: True
        self.tun_up_at = lambda t: False

        self.run_until(1000)

        self.assertEqual(self.heal_times(), [10, 70])
        self.assertEqual(self.states.count(TetherState.ERROR), 1)
        self.assertEqual(self.manager.stats.state, TetherState.ERROR)
        self.assertIn("Tether keeps dropping", self.manager.stats.last_error)
        self.assertFalse(self.manager._running)

    def test_relay_death_waits_out_the_backoff_before_each_reconnect(self):
        self.manager.is_relay_running = lambda: False
        self.tun_up_at = lambda t: False

        self.run_until(200)

        self.assertEqual(self.reconnects[:3], [0, 60, 180])

    def test_the_state_shows_reconnecting_while_the_relay_backs_off(self):
        self.manager.is_relay_running = lambda: False
        self.tun_up_at = lambda t: False
        seen = []
        self.clock.on_sleep = lambda now: (
            seen.append(self.manager.stats.state),
            setattr(self.manager, "_running", now < 30),
        )
        self.manager._running = True
        self.manager._health_loop("quest-serial")

        self.assertIn(TetherState.RECONNECTING, seen)

    def test_disconnect_during_relay_backoff_ends_the_wait_at_once(self):
        self.manager.is_relay_running = lambda: False
        self.tun_up_at = lambda t: False

        def stop_during_backoff(now):
            if now >= 30:
                self.manager._running = False
        self.clock.on_sleep = stop_during_backoff
        self.manager._running = True
        self.manager._health_loop("quest-serial")

        self.assertEqual(self.reconnects, [0], "no reconnect after the stop")
        self.assertIn(1.0, self.clock.sleeps, "the backoff sleeps in 1 s steps")
        self.assertLessEqual(max(self.clock.sleeps), 5.0)


class ConnectResetsLiveness(LivenessHarness):
    def test_connect_starts_with_a_fresh_liveness_state(self):
        self.manager._set_liveness(heal_policy.Liveness().restarted(0.0).restarted(1.0))
        self.manager.relay_mode = "run"
        self.manager.restore_wifi_if_flagged = Mock()
        self.manager._kill_stale_process = Mock()
        self.manager._start_run_relay = Mock(return_value=True)
        self.manager._apply_wifi_policy = Mock()
        self.manager._start_health_monitor = Mock()
        self.adb.is_vpn_up.return_value = True
        self.adb.get_device_details.return_value = {}

        self.assertTrue(self.manager.connect("quest-serial"))

        self.assertEqual(self.manager._liveness_now(), heal_policy.Liveness())


if __name__ == "__main__":
    unittest.main()
