"""am_result: pure parser for the first Error line of an am start reply (#63): python -m unittest tests.test_am_result -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import am_result  # noqa: E402


class AmStartError(unittest.TestCase):
    def test_plain_start_has_no_error(self):
        out = ("Starting: Intent { act=com.genymobile.gnirehtet.START "
               "cmp=com.genymobile.gnirehtet/.GnirehtetActivity }\n")
        self.assertIsNone(am_result.am_start_error(out, ""))

    def test_error_line_in_stdout(self):
        out = ("Starting: Intent { ... }\n"
               "Error: Activity class {com.genymobile.gnirehtet/com.genymobile.gnirehtet.GnirehtetActivity} does not exist.\n")
        self.assertEqual(
            am_result.am_start_error(out, ""),
            "Error: Activity class {com.genymobile.gnirehtet/com.genymobile.gnirehtet.GnirehtetActivity} does not exist.")

    def test_error_type_in_stderr(self):
        self.assertEqual(am_result.am_start_error("Starting: Intent { ... }\n", "Error type 3\n"), "Error type 3")

    def test_empty_inputs(self):
        self.assertIsNone(am_result.am_start_error("", ""))

    def test_none_inputs(self):
        self.assertIsNone(am_result.am_start_error(None, None))

    def test_warning_is_not_an_error(self):
        out = "Warning: Activity not started, its current task has been brought to the front\n"
        self.assertIsNone(am_result.am_start_error(out, ""))

    def test_stdout_error_wins_over_stderr_and_indented(self):
        self.assertEqual(am_result.am_start_error("   Error: indented\n", "Error: second\n"), "Error: indented")

    def test_lowercase_error_does_not_count(self):
        self.assertIsNone(am_result.am_start_error("error: lowercase\n", ""))


if __name__ == "__main__":
    unittest.main()
