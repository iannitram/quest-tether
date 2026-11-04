"""Crash hook: a sink that raises even a BaseException still lets the previous hook run (no files, no network): python -m unittest discover -s tests"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import crash_hook  # noqa: E402


def boom(msg="boom"):
    try:
        raise ValueError(msg)
    except ValueError:
        return sys.exc_info()


class BaseExceptionSinkTests(unittest.TestCase):
    def setUp(self):
        self.prev_sys = []
        self.orig_sys, self.orig_thread = sys.excepthook, threading.excepthook
        sys.excepthook = lambda *a: self.prev_sys.append(a[1])
        self.addCleanup(self.restore)

    def restore(self):
        crash_hook.uninstall()
        sys.excepthook, threading.excepthook = self.orig_sys, self.orig_thread

    def fire(self, t, e, tb):
        # A BaseException escaping the hook would end the test runner; turn it into a failure.
        try:
            sys.excepthook(t, e, tb)
        except BaseException as escaped:
            self.fail(f"exception from the sink escaped the hook: {escaped!r}")

    def test_sink_raising_system_exit_still_runs_the_previous_hook(self):
        def sink(t, e, tb):
            raise SystemExit(3)
        crash_hook.install(sink)
        t, e, tb = boom()
        self.fire(t, e, tb)
        self.assertEqual(self.prev_sys, [e])

    def test_sink_raising_keyboard_interrupt_still_runs_the_previous_hook(self):
        def sink(t, e, tb):
            raise KeyboardInterrupt()
        crash_hook.install(sink)
        t, e, tb = boom()
        self.fire(t, e, tb)
        self.assertEqual(self.prev_sys, [e])


if __name__ == "__main__":
    unittest.main()
