"""Relay output pump (#62): drains the relay's stdout pipe on a thread.

Every line goes to relay.log, and then to a callback that reads the client lines.
The drain never stops early, so the pipe cannot fill up, even with no UI open.
"""
# Pattern derived from gnirehtet-gui (Apache-2.0), apps/desktop/src-tauri/src/session.rs (pump_output).

import logging

logger = logging.getLogger(__name__)


def pump_lines(stream, log, on_line):
    """Read byte lines from stream until EOF.

    Each line is written and flushed to log (a binary file, or None for no copy),
    then passed to on_line as text without its line ending. A failed log write only
    stops the copy. A failing on_line only skips that line. Either way the stream is
    still drained to EOF.
    """
    if stream is None:
        return
    try:
        for raw in stream:
            if log is not None:
                try:
                    log.write(raw)
                    log.flush()
                except (OSError, ValueError):
                    log = None
            try:
                on_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            except Exception:
                logger.exception("relay output line handler failed")
    except Exception:
        logger.debug("relay output stream ended", exc_info=True)
