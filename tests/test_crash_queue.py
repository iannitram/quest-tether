"""Crash report rate limit and offline queue (temp folders only): python -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crash_queue import Limiter, Queue  # noqa: E402

DAY = 86400.0


class TempCase(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = t.name


class LimiterTests(TempCase):
    def make(self, per_day=5):
        return Limiter(os.path.join(self.dir, "limit.json"), per_day)

    def test_allows_five_then_refuses_the_sixth(self):
        lim = self.make()
        for i in range(5):
            self.assertTrue(lim.allow(1000.0 + i), i)
            lim.record(1000.0 + i)
        self.assertFalse(lim.allow(1010.0))

    def test_allow_alone_does_not_use_up_the_budget(self):
        lim = self.make(per_day=1)
        self.assertTrue(lim.allow(1000.0))
        self.assertTrue(lim.allow(1001.0))

    def test_window_is_the_last_24_hours(self):
        lim = self.make(per_day=1)
        lim.record(1000.0)
        self.assertFalse(lim.allow(1000.0 + DAY - 1))
        self.assertTrue(lim.allow(1000.0 + DAY + 1))

    def test_remembered_across_instances(self):
        self.make(per_day=1).record(1000.0)
        self.assertFalse(self.make(per_day=1).allow(1001.0))

    def test_unreadable_file_means_no_history(self):
        with open(os.path.join(self.dir, "limit.json"), "w") as f:
            f.write("{not json")
        self.assertTrue(self.make(per_day=1).allow(1000.0))

    def test_missing_folder_is_created_on_record(self):
        lim = Limiter(os.path.join(self.dir, "sub", "limit.json"), 5)
        lim.record(1.0)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "sub", "limit.json")))


class QueueTests(TempCase):
    def make(self, max_items=10):
        return Queue(os.path.join(self.dir, "queue"), max_items)

    def test_empty_queue(self):
        self.assertEqual(self.make().pending(), [])

    def test_add_read_pending_remove(self):
        q = self.make()
        q.add("b.zip", b"BBB")
        q.add("a.zip", b"AAA")
        self.assertEqual(q.pending(), ["a.zip", "b.zip"])
        self.assertEqual(q.read("a.zip"), b"AAA")
        q.remove("a.zip")
        self.assertEqual(q.pending(), ["b.zip"])

    def test_remove_missing_is_quiet(self):
        self.make().remove("nope.zip")

    def test_keeps_at_most_ten_and_drops_the_oldest(self):
        q = self.make()
        for i in range(12):
            q.add("r%02d.zip" % i, b"x")
        self.assertEqual(q.pending(), ["r%02d.zip" % i for i in range(2, 12)])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "queue", "r00.zip")))

    def test_max_items_is_a_parameter(self):
        q = self.make(max_items=2)
        for name in ("a.zip", "b.zip", "c.zip"):
            q.add(name, b"x")
        self.assertEqual(q.pending(), ["b.zip", "c.zip"])

    def test_survives_a_new_instance(self):
        self.make().add("a.zip", b"AAA")
        self.assertEqual(self.make().read("a.zip"), b"AAA")


if __name__ == "__main__":
    unittest.main()
