"""No guarded test may reach the real relay kill (#138).

A live gnirehtet relay on the developer's machine must survive the test suite.
connect() sweeps stale relays with platform.stale_relay_pids() and SIGKILLs them
with platform.kill(). This guard patches both on the real LinuxPlatform class to
fail loudly, then runs the TetherManager test modules that build a real platform.
Any test that reaches the real sweep or kill errors, and this test fails. The
patched kill raises before os.kill, so a live relay is never signalled here.
"""

import io
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qt_platform.linux import LinuxPlatform  # noqa: E402

GUARDED_MODULES = ("tests.test_awake_liveness", "tests.test_tun0_no_client_heal")
MESSAGE = "a test reached the real relay kill (#138)"


@unittest.skipUnless(sys.platform.startswith("linux"),
                     "the real relay kill is LinuxPlatform; Windows CI uses WindowsPlatform")
class NoRealRelayKill(unittest.TestCase):
    def test_guarded_modules_never_reach_real_kill(self):
        kill = Mock(side_effect=AssertionError(MESSAGE))
        stale = Mock(side_effect=AssertionError(MESSAGE))
        with patch.object(LinuxPlatform, "kill", kill), \
                patch.object(LinuxPlatform, "stale_relay_pids", stale):
            suite = unittest.TestSuite(
                unittest.TestLoader().loadTestsFromName(name) for name in GUARDED_MODULES)
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        # The call counts catch a reach even if connect() swallowed the raise.
        self.assertEqual(kill.call_count + stale.call_count, 0, MESSAGE)
        problems = result.failures + result.errors
        self.assertEqual(problems, [], "\n".join(str(text) for _, text in problems))


if __name__ == "__main__":
    unittest.main()
