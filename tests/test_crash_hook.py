"""Crash hook: an uncaught exception reaches the sink exactly once (no files, no network): python -m unittest discover -s tests"""
import contextlib
import io
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


class HookTests(unittest.TestCase):
    def setUp(self):
        self.seen, self.prev_sys, self.prev_thread = [], [], []
        self.orig_sys, self.orig_thread = sys.excepthook, threading.excepthook
        sys.excepthook = lambda *a: self.prev_sys.append(a[1])
        threading.excepthook = lambda args: self.prev_thread.append(args.exc_value)
        self.recorders = (sys.excepthook, threading.excepthook)
        self.addCleanup(self.restore)
        crash_hook.install(lambda t, e, tb: self.seen.append((t, e, tb)))

    def restore(self):
        crash_hook.uninstall()
        sys.excepthook, threading.excepthook = self.orig_sys, self.orig_thread

    def test_main_thread_exception_reaches_the_sink_once(self):
        t, e, tb = boom()
        sys.excepthook(t, e, tb)
        self.assertEqual(self.seen, [(ValueError, e, tb)])

    def test_the_previous_hook_still_runs(self):
        t, e, tb = boom()
        sys.excepthook(t, e, tb)
        self.assertEqual(self.prev_sys, [e])

    def test_the_same_exception_twice_is_one_report(self):
        t, e, tb = boom()
        sys.excepthook(t, e, tb)
        sys.excepthook(t, e, tb)
        self.assertEqual(len(self.seen), 1)

    def test_two_different_exceptions_are_two_reports(self):
        sys.excepthook(*boom("a"))
        sys.excepthook(*boom("b"))
        self.assertEqual([str(x[1]) for x in self.seen], ["a", "b"])

    def test_ctrl_c_and_exit_are_not_reported(self):
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        sys.excepthook(SystemExit, SystemExit(1), None)
        self.assertEqual(self.seen, [])

    def test_worker_thread_exception_reaches_the_sink(self):
        def work():
            raise RuntimeError("worker")
        th = threading.Thread(target=work)
        th.start()
        th.join(5)
        self.assertEqual([str(x[1]) for x in self.seen], ["worker"])
        self.assertEqual([str(e) for e in self.prev_thread], ["worker"])

    def test_both_original_hooks_run_after_the_sink(self):
        t, e, tb = boom("main")
        sys.excepthook(t, e, tb)
        th = threading.Thread(
            target=lambda: (_ for _ in ()).throw(RuntimeError("worker"))
        )
        th.start()
        th.join(5)
        self.assertEqual([str(x) for x in self.prev_sys], ["main"])
        self.assertEqual([str(x) for x in self.prev_thread], ["worker"])

    def test_the_hook_waits_for_the_upload_after_reporting(self):
        crash_hook.uninstall()
        waited = []
        self.addCleanup(crash_hook.uninstall)
        crash_hook.install(lambda t, e, tb: None, lambda: waited.append(1))
        t, e, tb = boom()
        sys.excepthook(t, e, tb)
        self.assertEqual(waited, [1])

    def test_tk_callback_reaches_the_sink(self):
        t, e, tb = boom()
        with contextlib.redirect_stderr(io.StringIO()):
            crash_hook.tk_callback(lambda t, e, tb: self.seen.append(e))(None, t, e, tb)
        self.assertEqual(self.seen, [e])

    def test_tk_callback_still_prints_the_traceback(self):
        t, e, tb = boom("tk boom")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            crash_hook.tk_callback(lambda t, e, tb: None)(None, t, e, tb)
        self.assertIn("ValueError: tk boom", err.getvalue())

    def test_a_failing_sink_never_breaks_the_hook(self):
        crash_hook.uninstall()
        crash_hook.install(lambda t, e, tb: 1 / 0)
        t, e, tb = boom()
        sys.excepthook(t, e, tb)
        self.assertEqual(self.prev_sys, [e])

    def test_uninstall_restores_the_previous_hooks(self):
        crash_hook.uninstall()
        self.assertIs(sys.excepthook, self.recorders[0])
        self.assertIs(threading.excepthook, self.recorders[1])


if __name__ == "__main__":
    unittest.main()
