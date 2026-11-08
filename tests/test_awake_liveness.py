"""Awake-gated liveness in the health loop (#65), on a fake clock: no headset needed.

The clock advances only through the patched time.sleep, so each health cycle
lands on a known second. A sleep hook ends the loop at a chosen time.
"""
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import heal_policy  # noqa: E402
from adb_manager import ADBManager  # noqa: E402
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
        self.awake_at = lambda t: False       # headset state by time
        self.tun_up_at = lambda t: False      # tun0 presence by time
        self.probe_results = []
        self.adb = Mock()
        self.adb.adb_path = "adb"
        self.adb.is_headset_awake.side_effect = self._probe

        self.manager = TetherManager(self.adb, gnirehtet_path="fake.jar")
        # No real subprocess may run under the fake clock: patching
        # tether_manager.time patches the shared time module, so subprocess's own
        # wait loop sleeps on it and the clock drifts by 1 ms (flake). So build the
        # manager (its `which java` lookup) first, and stub the port check (`ss`).
        self.manager.platform = Mock(wraps=self.manager.platform)
        self.manager.platform.port_holders.return_value = []
        # Tests must never find or kill a real relay (#138): a live gnirehtet
        # relay on the developer's machine would otherwise be SIGKILLed by
        # connect()'s stale-process sweep. Stub the sweep and the kill.
        self.manager.platform.stale_relay_pids.return_value = []
        self.manager.platform.kill = Mock()
        # monotonic too: the VPN-close wait sleeps to a monotonic deadline, and a
        # real remainder fed to the fake sleep made the clock drift (CI flake).
        for target, fake in (("tether_manager.time.time", self.clock.time),
                             ("tether_manager.time.monotonic", self.clock.time),
                             ("tether_manager.time.sleep", self.clock.sleep)):
            patcher = patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

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

    def test_an_unplugged_headset_with_no_reading_gets_no_restart_and_no_error(self):
        self.awake_at = lambda t: None
        self.tun_up_at = lambda t: False
        self.adb.device_listed.return_value = False   # not in the device list: #86 owns it

        self.run_until(600)

        self.assertTrue(self.adb.is_headset_awake.called, "the headset should still be asked")
        self.assertTrue(self.adb.device_listed.called, "the device list decides once the bound has passed")
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

    def test_an_awake_reading_then_no_reading_does_not_reuse_the_stale_awake_clock(self):
        self.awake_at = lambda t: True if t < 20 else None
        self.tun_up_at = lambda t: False
        self.adb.device_listed.return_value = False   # unplugged after the awake reading
        self.manager.HEAL_FINAL_RESTARTS = 99

        self.run_until(300)

        self.assertEqual(self.heal_times(), [10], "only the heal before the readings stopped")

    def test_a_listed_headset_with_no_reading_gets_an_adb_restart_after_the_bound(self):
        self.awake_at = lambda t: None            # offline / unauthorized / wedged adb
        self.tun_up_at = lambda t: False
        self.adb.device_listed.return_value = True    # listed as offline
        self.manager.HEAL_FINAL_RESTARTS = 99

        self.run_until(200)

        self.assertEqual(self.heal_times()[:3], [15, 75, 195], "bound, then 60 s and 120 s backoff")
        self.assertEqual(self.heals[0][1], Heal.RESTART_ADB_THEN_CLIENT,
                         "the first step restarts the adb server")

    def test_a_listed_headset_with_no_reading_ends_in_one_error_at_the_cap(self):
        self.awake_at = lambda t: None
        self.tun_up_at = lambda t: False
        self.adb.device_listed.return_value = True    # listed as unauthorized
        self.manager.HEAL_FINAL_RESTARTS = 3

        self.run_until(1000)

        self.assertEqual(self.heal_times(), [15, 75, 195])
        self.assertEqual(self.states.count(TetherState.ERROR), 1)
        self.assertIn("Tether keeps dropping", self.manager.stats.last_error)
        self.assertLess(self.clock.now, 1000, "the loop ended on its own at the cap")

    def test_a_reading_after_the_no_reading_run_goes_back_to_the_awake_gate(self):
        self.awake_at = lambda t: None if t < 8 else False   # unreadable, then asleep
        self.tun_up_at = lambda t: False
        self.adb.device_listed.return_value = True

        self.run_until(300)

        self.assertEqual(self.heals, [], "an asleep reading ends the no-reading run")


class ListedHeadsetThroughRealAdbParsing(LivenessHarness):
    """The unreadable-heal gate reads a fake `adb devices -l` through the real
    ADBManager (run_adb faked), so offline and failed-call cases are not mocked away."""

    SERIAL = "quest-serial"

    def setUp(self):
        super().setUp()
        platform = Mock()
        platform.no_window_flags.return_value = 0
        self.real = ADBManager(adb_path="adb", platform=platform)
        self.devices_reply = None
        self.real.run_adb = Mock(side_effect=self._run_adb)
        self.real.is_headset_awake = Mock(side_effect=self._probe)   # always no reading below
        self.real.restart_adb_server = Mock()
        self.manager.adb = self.real
        self.awake_at = lambda t: None
        self.tun_up_at = lambda t: False
        self.manager.HEAL_FINAL_RESTARTS = 99

    def _run_adb(self, *args, **kwargs):
        self.assertEqual(args, ("devices", "-l"), "only the device list is read")
        return self.devices_reply

    def reply(self, *lines, returncode=0):
        text = "List of devices attached\n" + "".join(line + "\n" for line in lines)
        return subprocess.CompletedProcess([], returncode, text, "")

    def test_an_offline_line_for_the_serial_gets_the_adb_restart_after_the_bound(self):
        self.devices_reply = self.reply(f"{self.SERIAL}\toffline usb:1-1 transport_id:3")

        self.run_until(100)

        self.assertEqual(self.heal_times()[:2], [15, 75])
        self.assertEqual(self.heals[0][1], Heal.RESTART_ADB_THEN_CLIENT)

    def test_an_unauthorized_line_for_the_serial_gets_the_adb_restart(self):
        self.devices_reply = self.reply(f"{self.SERIAL}\tunauthorized usb:1-1 transport_id:3")

        self.run_until(100)

        self.assertEqual(self.heal_times()[:1], [15])
        self.assertEqual(self.heals[0][1], Heal.RESTART_ADB_THEN_CLIENT)

    def test_a_failed_devices_call_is_a_wedged_adb_and_gets_the_adb_restart(self):
        self.devices_reply = subprocess.CompletedProcess([], 1, "", "Timeout")

        self.run_until(100)

        self.assertEqual(self.heal_times()[:2], [15, 75])
        self.assertEqual(self.heals[0][1], Heal.RESTART_ADB_THEN_CLIENT)

    def test_a_list_without_the_serial_gets_no_restart_and_no_error(self):
        self.devices_reply = self.reply("OTHERSERIAL\tdevice product:x model:Pixel device:x transport_id:1")

        self.run_until(600)

        self.assertEqual(self.heals, [])
        self.assertNotIn(TetherState.ERROR, self.states)

    def test_an_empty_list_gets_no_restart(self):
        self.devices_reply = self.reply()

        self.run_until(600)

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
        self.assertLess(self.clock.now, 1000, "the loop ended on its own, not on the test's stop time")

    def test_giving_up_after_a_relay_death_skips_the_wifi_sync(self):
        self.manager.is_relay_running = lambda: False
        self.tun_up_at = lambda t: False
        self.manager.HEAL_FINAL_RESTARTS = 0       # give up at the first relay death
        self.manager._wifi_mode = "off"
        self.manager.WIFI_SYNC_INTERVAL = 0
        self.manager._apply_wifi_policy = Mock()

        self.run_until(1000)

        self.assertEqual(self.states.count(TetherState.ERROR), 1)
        self.manager._apply_wifi_policy.assert_not_called()

    def test_a_heal_that_raises_still_starts_the_backoff(self):
        self.awake_at = lambda t: True
        self.tun_up_at = lambda t: False
        self.manager.HEAL_FINAL_RESTARTS = 99
        attempts = []

        def heal_that_raises_once(serial, action):
            attempts.append(self.clock.now)
            if len(attempts) == 1:
                raise OSError("adb spawn failed")
            return False
        self.manager._heal = Mock(side_effect=heal_that_raises_once)
        patcher = patch("tether_manager.logger.exception")   # the loop logs the error
        patcher.start()
        self.addCleanup(patcher.stop)

        self.run_until(200)

        self.assertEqual(attempts[:3], [10, 70, 190], "60 s then 120 s, not every 5 s")
        self.assertEqual(self.manager._liveness_now().restarts, len(attempts))

    def test_a_reconnect_that_raises_after_a_relay_death_still_starts_the_backoff(self):
        self.manager.is_relay_running = lambda: False
        self.tun_up_at = lambda t: False
        self.manager.HEAL_FINAL_RESTARTS = 99
        calls = []

        def reconnect_that_raises_once(serial):
            calls.append(self.clock.now)
            if len(calls) == 1:
                raise OSError("spawn failed")
        self.manager._attempt_reconnect = Mock(side_effect=reconnect_that_raises_once)
        patcher = patch("tether_manager.logger.exception")   # the loop logs the error
        patcher.start()
        self.addCleanup(patcher.stop)

        self.run_until(200)

        self.assertEqual(calls[:3], [0, 60, 180])

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
        relay = self.manager._process
        self.manager._process = None  # the last session's relay is gone (#107)

        def start_run_relay(serial):
            self.manager._process = relay
            return True

        self.manager._start_run_relay = Mock(side_effect=start_run_relay)
        self.manager._apply_wifi_policy = Mock()
        self.manager._start_health_monitor = Mock()
        self.adb.is_vpn_up.return_value = True
        self.adb.get_device_details.return_value = {}

        self.assertTrue(self.manager.connect("quest-serial"))

        self.assertEqual(self.manager._liveness_now(), heal_policy.Liveness())


if __name__ == "__main__":
    unittest.main()


class ReplugWakesAndHeals(LivenessHarness):
    """#123: a replug restarts the client at once, even with the headset asleep."""

    def _replug_at(self, when, serial="quest-serial", end=None):
        done = []

        def hook(now):
            if now >= when and not done:
                done.append(now)
                self.manager.note_replug(serial)
            if end is not None and now >= end:
                self.manager._running = False
        return hook

    def _run(self, hook):
        self.clock.on_sleep = hook
        self.manager._running = True
        self.manager._health_loop("quest-serial")

    def test_an_asleep_replug_heals_within_one_health_tick(self):
        self.awake_at = lambda t: False
        self.tun_up_at = lambda t: False

        self._run(self._replug_at(100, end=300))

        self.assertEqual(len(self.heals), 1, "one try, then the sleep-aware rules")
        when, action = self.heals[0]
        self.assertGreaterEqual(when, 100)
        self.assertLessEqual(when, 100 + TetherManager.HEALTH_CHECK_INTERVAL)
        self.assertIs(action, Heal.RESTART_CLIENT)
        self.assertEqual(self.manager._liveness_now().restarts, 1)

    def test_no_replug_means_no_heal_while_asleep(self):
        self.awake_at = lambda t: False
        self.run_until(300)
        self.assertEqual(self.heals, [])

    def test_a_tunnel_that_came_back_clears_the_pending_replug(self):
        self.awake_at = lambda t: False
        self.tun_up_at = lambda t: t < 200   # up at the replug, gone later

        self._run(self._replug_at(100, end=400))

        self.assertEqual(self.heals, [])

    def test_a_replug_for_another_serial_does_nothing(self):
        self.awake_at = lambda t: False
        self._run(self._replug_at(100, serial="other", end=300))
        self.assertEqual(self.heals, [])

    def test_the_restart_cap_still_applies(self):
        self.awake_at = lambda t: False
        self.manager._restart_cap_reached = lambda: True
        self.manager._give_up_healing = Mock()

        self._run(self._replug_at(100, end=300))

        self.assertEqual(self.heals, [])
        self.manager._give_up_healing.assert_called_once()

    def test_connect_clears_a_stale_replug(self):
        self.manager.note_replug("quest-serial")
        self.manager._spawn_relay = Mock(side_effect=RuntimeError("stop here"))
        try:
            self.manager.connect("quest-serial")
        except Exception:
            pass
        self.assertFalse(self.manager._take_replug("quest-serial"))

    def test_a_relay_death_reconnect_handles_a_pending_replug(self):
        self.manager._auto_reconnect = True
        self.manager._running = True
        self.manager._attempt_reconnect = Mock()
        self.manager._wait_backoff = Mock()
        self.manager.note_replug("quest-serial")

        self.manager._relay_died("quest-serial")

        self.assertFalse(self.manager._take_replug("quest-serial"))
