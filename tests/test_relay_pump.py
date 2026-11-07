"""The relay output pump (#62): every line to the log and the callback, the pipe always drained. Fakes only."""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from relay_pump import pump_lines  # noqa: E402


class FailingLog:
    def write(self, data):
        raise OSError("disk full")

    def flush(self):
        pass


class PumpLinesTests(unittest.TestCase):
    def test_each_line_goes_to_the_log_byte_for_byte_and_the_callback_without_its_ending(self):
        stream = io.BytesIO(b"first\nsecond\r\nthird")
        log = io.BytesIO()
        seen = []

        pump_lines(stream, log, seen.append)

        self.assertEqual(log.getvalue(), b"first\nsecond\r\nthird")
        self.assertEqual(seen, ["first", "second", "third"])

    def test_a_failed_log_write_stops_the_copy_but_still_drains_the_pipe(self):
        stream = io.BytesIO(b"one\ntwo\nthree\n")
        seen = []

        pump_lines(stream, FailingLog(), seen.append)

        self.assertEqual(seen, ["one", "two", "three"], "every line still reaches the callback")
        self.assertEqual(stream.read(), b"", "the stream is read to EOF")

    def test_a_failing_callback_skips_only_its_own_line(self):
        def handler(text):
            if text == "bad":
                raise ValueError("boom")
            seen.append(text)

        seen = []
        stream = io.BytesIO(b"good\nbad\nafter\n")

        pump_lines(stream, None, handler)

        self.assertEqual(seen, ["good", "after"])

    def test_no_log_still_feeds_the_callback(self):
        seen = []

        pump_lines(io.BytesIO(b"Client #1 connected\n"), None, seen.append)

        self.assertEqual(seen, ["Client #1 connected"])

    def test_undecodable_bytes_are_replaced_not_fatal(self):
        seen = []

        pump_lines(io.BytesIO(b"caf\xe9 ok\nnext\n"), None, seen.append)

        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1], "next")

    def test_no_stream_returns_at_once(self):
        seen = []

        pump_lines(None, io.BytesIO(), seen.append)

        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
