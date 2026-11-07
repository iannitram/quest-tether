"""tun0 up with no relay client heals (#102), on a fake clock: no headset, no relay.

The relay has counted its client away this spawn, so tun0 alone is not a live
tunnel. The health loop must take the missing-tunnel branch, so the awake-gated
heal re-issues the client start, instead of sitting in Starting. The clock moves
only through the patched time.sleep, as in tests/test_awake_liveness.py.
"""
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import heal_policy  # noqa: E402
from heal import Heal  # noqa: E402
from tether_manager import TetherManager, TetherState  # noqa: E402

CLIENT_CONNECTED = "2026-09-27 03:29:44.592 I TunnelServer: Client #1 connected"
CLIENT_DISCONNECTED = "2026-09-27 03:30:10.000 I TunnelServer: Client #1 disconnected"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self.now)


class TunnelWithoutClient(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        for target, fake in (("tether_manager.time.time", self.clock.time),
                             ("tether_manager.time.sleep", self.clock.sleep)):
            patcher = patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.awake_at = lambda t: True        # headset state by time
        self.adb = Mock()
        self.adb.adb_path = "adb"
        self.adb.is_headset_awake.side_effect = self._probe
        self.adb.device_listed.return_value = True

        self.manager = TetherManager(self.adb, gnirehtet_path="fake.jar")
        self.manager.POST_CONNECT_COOLDOWN = 0
        self.manager._wifi_mode = "leave"
        self.manager._auto_reconnect = True
        self.manager._process = Mock()
        self.manager._process.poll.return_value = None
        self.manager._update_stats = lambda serial: True   # tun0 always up

        self.heals = []
        self.reconnects = []
        self.manager._heal = Mock(side_effect=self._record_heal)
        self.manager._attempt_reconnect = Mock(side_effect=self._record_reconnect)
        self.states = []
        self.manager.set_callbacks(on_state_change=lambda state, stats: self.states.append(state))

        self.manager._running = True
        self.manager.stats.state = TetherState.CONNECTED   # as after a connect

        self.scheduled = []                    # (time, action), run from the sleep hook
        self.stop_at = None

    def _probe(self, serial):
        return self.awake_at(self.clock.now)

    def _record_heal(self, serial, action):
        self.heals.append((self.clock.now, action))
        return False

    def _record_reconnect(self, serial):
        self.reconnects.append(self.clock.now)

    def client_connects(self):
        self.manager._on_relay_line(self.manager._relay_gen, CLIENT_CONNECTED)

    def client_leaves(self):
        self.manager._on_relay_line(self.manager._relay_gen, CLIENT_DISCONNECTED)

    def at(self, when, action):
        self.scheduled.append((when, action))

    def _on_sleep(self, now):
        for item in list(self.scheduled):
            when, action = item
            if now >= when:
                self.scheduled.remove(item)
                action()
        if self.stop_at is not None and now >= self.stop_at:
            self.manager._running = False

    def run_until(self, end):
        self.stop_at = end
        self.clock.on_sleep = self._on_sleep
        self.manager._health_loop("quest-serial")


class ClientGoneHeals(TunnelWithoutClient):
    def test_tun0_up_client_gone_heals_within_grace(self):
        self.client_connects()
        self.client_leaves()          # relay_clients is now 0: the client went away

        self.run_until(600)

        self.assertTrue(self.heals, "tun0 up with no client must heal, not wait in Starting")
        when, action = self.heals[0]
        self.assertIs(action, Heal.RESTART_CLIENT)
        self.assertLessEqual(
            when,
            heal_policy.GRACE + heal_policy.AWAKE_PROBE_INTERVAL + self.manager.HEALTH_CHECK_INTERVAL,
        )
        self.assertIs(self.manager.stats.state, TetherState.STARTING)

    def test_client_returns_before_grace_cancels_heal(self):
        self.client_connects()
        self.client_leaves()
        self.at(6.0, self.client_connects)   # back before the first check past the grace

        self.run_until(600)

        self.assertEqual(self.heals, [])
        self.assertIs(self.manager.stats.state, TetherState.CONNECTED)

    def test_asleep_headset_no_heal(self):
        self.awake_at = lambda t: False
        self.client_connects()
        self.client_leaves()

        self.run_until(600)

        self.assertEqual(self.heals, [])
        self.assertFalse(self.manager._healer.tripped)
        self.assertNotIn(TetherState.ERROR, self.states)

    def test_unreadable_unplugged_waits(self):
        self.awake_at = lambda t: None
        self.adb.device_listed.return_value = False   # unplugged: #86 owns that case
        self.client_connects()
        self.client_leaves()

        self.run_until(600)

        self.assertTrue(self.adb.device_listed.called)
        self.assertEqual(self.heals, [])
        self.assertNotIn(TetherState.ERROR, self.states)

    def test_unreadable_but_listed_heals_with_adb_restart(self):
        self.awake_at = lambda t: None
        self.adb.device_listed.return_value = True    # listed but unreadable: wedged adb
        self.client_connects()
        self.client_leaves()

        self.run_until(600)

        self.assertTrue(self.heals)
        self.assertIs(self.heals[0][1], Heal.RESTART_ADB_THEN_CLIENT)

    def test_auto_reconnect_off_stays_starting_without_heal(self):
        self.manager._auto_reconnect = False
        self.client_connects()
        self.client_leaves()

        self.run_until(600)

        self.assertEqual(self.heals, [])
        self.assertIs(self.manager.stats.state, TetherState.STARTING)


class NoClientLineEver(TunnelWithoutClient):
    def test_no_client_line_ever_never_heals(self):
        self.assertIsNone(self.manager.stats.relay_clients)

        self.run_until(600)

        self.assertEqual(self.heals, [])
        self.assertIs(self.manager.stats.state, TetherState.CONNECTED)


if __name__ == "__main__":
    unittest.main()
