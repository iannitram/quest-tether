"""Battery temperature: zones and dumpsys parse (no headset, no adb): python -m unittest discover tests"""
import subprocess
import sys
import unittest

sys.path.insert(0, ".")
import tether_manager  # noqa: E402
from tether_manager import TetherManager, TetherStats  # noqa: E402


class FakeAdb:
    def __init__(self, dumpsys):
        self.dumpsys = dumpsys
        self.calls = []

    def get_tun0_stats(self, serial):
        return None

    def run_adb(self, *args, **kw):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=self.dumpsys, stderr="")


def manager_with(dumpsys):
    m = TetherManager.__new__(TetherManager)  # skip __init__: no java/gnirehtet lookup
    m.adb = FakeAdb(dumpsys)
    m.stats = TetherStats()
    m._start_time = None
    m._prev_rx = m._prev_tx = 0
    m._prev_stat_time = 0
    m._last_battery_time = 0.0
    m._on_stats_update = None
    return m


class Zones(unittest.TestCase):
    def test_zone_table(self):
        for c, zone in [(37.9, "green"), (38.0, "yellow"), (42.0, "yellow"),
                        (42.1, "red"), (44.5, "red")]:
            self.assertEqual(tether_manager.temperature_zone(c), zone, c)


class Parse(unittest.TestCase):
    def test_default_is_none(self):
        self.assertIsNone(TetherStats().battery_temp_c)

    def test_temperature_385_is_38_5(self):
        m = manager_with("  level: 80\n  status: 2\n  temperature: 385\n")
        m._update_stats("SER")
        self.assertEqual(m.stats.battery_temp_c, 38.5)
        self.assertEqual(len(m.adb.calls), 1)  # still one dumpsys battery call

    def test_unparsable_temperature_is_ignored(self):
        m = manager_with("  level: 80\n  temperature: abc\n")
        m._update_stats("SER")
        self.assertIsNone(m.stats.battery_temp_c)


if __name__ == "__main__":
    unittest.main()
