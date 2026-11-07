import unittest

from heal_policy import Liveness, parse_wakefulness, restart_backoff


class ParseWakefulnessTest(unittest.TestCase):
    def test_awake(self):
        self.assertIs(
            parse_wakefulness("Power Manager State:\n  mWakefulness=Awake\n"), True
        )

    def test_asleep(self):
        self.assertIs(parse_wakefulness("  mWakefulness=Asleep\n"), False)

    def test_dozing(self):
        self.assertIs(parse_wakefulness("mWakefulness=Dozing"), False)

    def test_changing_line_ignored(self):
        self.assertIsNone(parse_wakefulness("mWakefulnessChanging=false\n"))

    def test_empty_and_none(self):
        self.assertIsNone(parse_wakefulness(""))
        self.assertIsNone(parse_wakefulness(None))

    def test_first_line_wins_value_stripped(self):
        self.assertIs(
            parse_wakefulness("mWakefulness=Awake \r\nmWakefulness=Asleep"), True
        )


class RestartBackoffTest(unittest.TestCase):
    def test_sequence(self):
        self.assertEqual(
            [restart_backoff(n) for n in (0, 1, 2, 3, 4, 5, 50)],
            [60.0, 60.0, 120.0, 240.0, 300.0, 300.0, 300.0],
        )


class LivenessTest(unittest.TestCase):
    def test_asleep_silence_never_restarts(self):
        l = Liveness().silent(0.0)
        for t in range(0, 1801, 15):
            l = l.observed(False, float(t))
            self.assertFalse(l.restart_due(float(t)))

    def test_awake_silence_restarts_after_grace(self):
        l = Liveness().silent(0.0).observed(True, 0.0)
        self.assertFalse(l.restart_due(9.9))
        self.assertTrue(l.restart_due(10.0))

    def test_awake_but_not_silent(self):
        self.assertFalse(Liveness().observed(True, 0.0).restart_due(100.0))

    def test_backoff_after_restart(self):
        l = Liveness().silent(0.0).observed(True, 0.0).restarted(10.0)
        self.assertEqual(l.restarts, 1)
        self.assertEqual(l.silent_since, 10.0)
        self.assertIsNone(l.awake_since)
        self.assertEqual(l.next_probe_at, 15.0)
        self.assertEqual(l.next_restart_at, 70.0)

        l2 = l.observed(True, 15.0)
        self.assertFalse(l2.restart_due(69.0))
        self.assertTrue(l2.restart_due(70.0))

        l3 = l2.restarted(70.0)
        self.assertEqual(l3.next_restart_at, 190.0)

    def test_observed_keeps_first_awake_time(self):
        self.assertEqual(
            Liveness().observed(True, 0.0).observed(True, 5.0).awake_since, 0.0
        )
        self.assertIsNone(Liveness().observed(True, 0.0).observed(False, 6.0).awake_since)

    def test_probe_due(self):
        self.assertTrue(Liveness().probe_due(0.0))
        awake = Liveness().observed(True, 0.0)
        self.assertFalse(awake.probe_due(4.9))
        self.assertTrue(awake.probe_due(5.0))
        self.assertFalse(Liveness().observed(False, 0.0).probe_due(14.9))

    def test_connected_resets(self):
        self.assertEqual(
            Liveness().silent(0.0).observed(True, 0.0).restarted(10.0).connected(),
            Liveness(),
        )

    def test_ineligible_keeps_backoff(self):
        l = Liveness().silent(0.0).observed(True, 0.0).restarted(10.0).ineligible()
        self.assertEqual(l.restarts, 1)
        self.assertEqual(l.next_restart_at, 70.0)
        self.assertIsNone(l.silent_since)
        self.assertIsNone(l.awake_since)
        self.assertIsNone(l.next_probe_at)

    def test_unreadable_drops_the_awake_clock_and_keeps_the_silence_clock(self):
        l = Liveness().silent(0.0).observed(True, 0.0).unreadable(5.0)
        self.assertEqual(l.silent_since, 0.0)
        self.assertIsNone(l.awake_since)
        self.assertIsNone(l.next_probe_at)
        self.assertEqual(l.unreadable_since, 5.0)
        self.assertEqual(l.unreadable(10.0).unreadable_since, 5.0, "the first no-reading time stays")

    def test_a_reading_ineligible_and_restarted_end_the_no_reading_run(self):
        l = Liveness().silent(0.0).unreadable(5.0)
        self.assertIsNone(l.observed(False, 10.0).unreadable_since)
        self.assertIsNone(l.ineligible().unreadable_since)
        self.assertIsNone(l.restarted(20.0).unreadable_since)

    def test_unreadable_restart_due_needs_silence_no_reading_and_the_backoff(self):
        self.assertFalse(Liveness().unreadable_restart_due(100.0))
        self.assertFalse(Liveness().silent(0.0).unreadable_restart_due(100.0), "needs a no-reading run")
        l = Liveness().silent(0.0).unreadable(0.0)
        self.assertFalse(l.unreadable_restart_due(14.0))
        self.assertTrue(l.unreadable_restart_due(15.0))
        self.assertFalse(l.restarted(15.0).silent(15.0).unreadable(15.0).unreadable_restart_due(40.0),
                         "inside the backoff")
        self.assertTrue(l.restarted(15.0).unreadable(16.0).unreadable_restart_due(75.0))

    def test_unreadable_never_makes_the_awake_restart_due(self):
        l = Liveness().silent(0.0).observed(True, 0.0).unreadable(5.0)
        self.assertFalse(l.restart_due(100.0))

    def test_immutability(self):
        a = Liveness()
        b = a.silent(1.0)
        self.assertIsNone(a.silent_since)
        self.assertEqual(b.silent_since, 1.0)


if __name__ == "__main__":
    unittest.main()
