"""Crash hook installation and reporting module."""

import sys
import threading
from typing import Callable, Optional, Any

# Module-level variables to store original hooks
_original_excepthook: Optional[Callable] = None
_original_threading_excepthook: Optional[Callable] = None


def install(sink: Callable[[type, BaseException, Any], None]) -> None:
    """Install crash hooks to report exceptions through the given sink."""
    global _original_excepthook, _original_threading_excepthook

    # Uninstall if already installed
    uninstall()

    # Store original hooks
    _original_excepthook = sys.excepthook
    _original_threading_excepthook = threading.excepthook

    # Install new hooks
    def sys_hook(exc_type, exc, tb):
        _report(sink, exc_type, exc, tb)
        # Call the original hook
        if _original_excepthook is not None:
            _original_excepthook(exc_type, exc, tb)

    def threading_hook(args):
        _report(sink, args.exc_type, args.exc_value, args.exc_traceback)
        # Call the original hook
        if _original_threading_excepthook is not None:
            _original_threading_excepthook(args)

    sys.excepthook = sys_hook
    threading.excepthook = threading_hook


def uninstall() -> None:
    """Uninstall crash hooks, restoring original hooks."""
    global _original_excepthook, _original_threading_excepthook

    if _original_excepthook is not None:
        sys.excepthook = _original_excepthook
        _original_excepthook = None

    if _original_threading_excepthook is not None:
        threading.excepthook = _original_threading_excepthook
        _original_threading_excepthook = None


def _report(sink: Callable[[type, BaseException, Any], None], exc_type: type, exc: BaseException, tb: Any) -> None:
    """Report exception through sink, with filtering and deduplication."""
    # Skip KeyboardInterrupt and SystemExit
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return

    # Skip if already reported
    try:
        if getattr(exc, '_crash_hook_reported', False):
            return
        setattr(exc, '_crash_hook_reported', True)
    except Exception:
        # If we can't mark as reported, continue anyway
        pass

    # Report through sink
    try:
        sink(exc_type, exc, tb)
    except BaseException:
        # If sink fails (even with SystemExit or KeyboardInterrupt), don't let it stop the original hook
        pass


def tk_callback(sink: Callable[[type, BaseException, Any], None]) -> Callable:
    """Returns a callback function for tkinter that reports exceptions."""
    def cb(self, exc_type, exc, tb):
        _report(sink, exc_type, exc, tb)
        return None
    return cb
