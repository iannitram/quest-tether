"""The heal ladder and flap breaker, with `now` passed in: no headset needed."""

import unittest

from heal import Heal, Healer


def miss(healer, t):
    return healer.observe(t, True, False)


class LadderTests(unittest.TestCase):
    def test_a_good_tunnel_needs_nothing(self):
        self.assertIs(Healer().observe(0, True, True), Heal.NONE)

    def test_the_third_miss_in_a_row_restarts_the_client(self):
        h = Healer()
        self.assertIs(miss(h, 1), Heal.NONE)
        self.assertIs(miss(h, 2), Heal.NONE)
        self.assertIs(miss(h, 3), Heal.RESTART_CLIENT)

    def test_a_good_reading_resets_the_miss_count(self):
        h = Healer()
        miss(h, 1)
        miss(h, 2)
        self.assertIs(h.observe(3, True, True), Heal.NONE)
        self.assertIs(miss(h, 4), Heal.NONE)
        self.assertIs(miss(h, 5), Heal.NONE)

    def test_heals_are_at_least_thirty_seconds_apart_and_climb_the_ladder(self):
        h = Healer()
        for t in (1, 2, 3):
            miss(h, t)
        for t in (10, 11, 12, 32):
            self.assertIs(miss(h, t), Heal.NONE)
        self.assertIs(miss(h, 33), Heal.RESTART_ADB_THEN_CLIENT)
        for t in (34, 35, 36):
            self.assertIs(miss(h, t), Heal.NONE)
        self.assertIs(miss(h, 63), Heal.FULL_RECONNECT)
        self.assertEqual(h.reconnects, 1)

    def test_a_recovery_puts_the_ladder_back_at_the_start(self):
        h = Healer()
        for t in (1, 2, 3):
            miss(h, t)
        self.assertIs(h.observe(10, True, True), Heal.NONE)
        for t in (40, 41):
            self.assertIs(miss(h, t), Heal.NONE)
        self.assertIs(miss(h, 42), Heal.RESTART_CLIENT)

    def test_a_dead_relay_is_a_miss_even_with_tun0_present(self):
        h = Healer()
        self.assertIs(h.observe(1, False, True), Heal.NONE)
        self.assertIs(h.observe(2, False, False), Heal.NONE)
        self.assertIs(h.observe(3, False, True), Heal.RESTART_CLIENT)


class BreakerTests(unittest.TestCase):
    def test_more_than_six_heals_in_ten_minutes_gives_up_for_good(self):
        h = Healer()
        for k in range(6):
            t = k * 40
            miss(h, t)
            miss(h, t + 1)
            self.assertIs(miss(h, t + 2), Heal.RESTART_CLIENT)
            self.assertIs(h.observe(t + 3, True, True), Heal.NONE)
        miss(h, 240)
        miss(h, 241)
        self.assertIs(miss(h, 242), Heal.GIVE_UP)
        self.assertEqual(h.trip_reason, "flap")
        self.assertIs(h.observe(300, True, True), Heal.GIVE_UP)

    def test_ten_full_reconnects_is_the_limit(self):
        h = Healer()
        t = 0
        heals = 0
        while True:
            miss(h, t)
            miss(h, t + 1)
            got = miss(h, t + 2)
            t += 120
            if got is Heal.GIVE_UP:
                break
            self.assertIsNot(got, Heal.NONE)
            heals += 1
            self.assertLess(heals, 100, "never gave up")
        self.assertEqual(h.reconnects, 10)
        self.assertEqual(heals, 32)
        self.assertEqual(h.trip_reason, "reconnects")

    def test_reconnects_outside_the_ladder_count_toward_the_flap_breaker(self):
        h = Healer()
        for t in range(6):
            self.assertLessEqual(h.note_recovery(t), 6)
        self.assertFalse(h.tripped)
        self.assertEqual(h.note_recovery(6), 7)
        self.assertTrue(h.tripped)
        self.assertEqual(h.trip_reason, "flap")

    def test_an_external_recovery_can_trip_the_breaker_on_a_heal(self):
        h = Healer()
        for t in range(6):
            h.note_recovery(t)
        miss(h, 40)
        miss(h, 41)
        self.assertIs(miss(h, 42), Heal.GIVE_UP)

    def test_old_recoveries_fall_out_of_the_flap_window(self):
        h = Healer()
        for t in range(6):
            h.note_recovery(t)
        self.assertEqual(h.note_recovery(700), 1)
        self.assertFalse(h.tripped)

    def test_a_successful_reconnect_clears_the_reconnect_count(self):
        h = Healer()
        got = []
        for k in range(3):
            t = k * 120
            miss(h, t)
            miss(h, t + 1)
            got.append(miss(h, t + 2))
        self.assertEqual(
            got,
            [Heal.RESTART_CLIENT, Heal.RESTART_ADB_THEN_CLIENT, Heal.FULL_RECONNECT],
        )
        self.assertEqual(h.reconnects, 1)
        h.reset_after_reconnect()
        self.assertEqual(h.reconnects, 0)



class EscalateTests(unittest.TestCase):
    """escalate() takes a ladder step at once: the liveness policy (#65) has
    already decided that a restart is due, so the miss and spacing rules do not apply."""

    def test_steps_climb_the_ladder_with_no_misses_and_no_gap(self):
        h = Healer()
        self.assertIs(h.escalate(0), Heal.RESTART_CLIENT)
        self.assertIs(h.escalate(1), Heal.RESTART_ADB_THEN_CLIENT)
        self.assertIs(h.escalate(2), Heal.FULL_RECONNECT)
        self.assertIs(h.escalate(3), Heal.RESTART_CLIENT)
        self.assertEqual(h.reconnects, 1)

    def test_reconnect_cap_gives_up_on_the_next_full_reconnect(self):
        h = Healer(max_reconnects=1)
        got = [h.escalate(t) for t in range(6)]
        self.assertEqual(got, [
            Heal.RESTART_CLIENT, Heal.RESTART_ADB_THEN_CLIENT, Heal.FULL_RECONNECT,
            Heal.RESTART_CLIENT, Heal.RESTART_ADB_THEN_CLIENT, Heal.GIVE_UP,
        ])
        self.assertEqual(h.trip_reason, "reconnects")

    def test_flap_max_none_never_trips_the_flap_breaker(self):
        h = Healer(flap_max=None)
        for t in range(50):
            h.note_recovery(t)
        self.assertFalse(h.tripped)
        self.assertIsNone(h.trip_reason)
        self.assertIsNot(h.escalate(100), Heal.GIVE_UP)

    def test_default_flap_max_still_trips(self):
        h = Healer()
        for t in range(7):
            h.note_recovery(t)
        self.assertTrue(h.tripped)
        self.assertEqual(h.trip_reason, "flap")


if __name__ == "__main__":
    unittest.main()
