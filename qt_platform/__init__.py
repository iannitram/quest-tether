"""Platform layer: everything that differs between Windows and Linux (docs/code-map.md).

Callers use `current()` instead of testing `os.name`. The Windows and Linux modules are
stubs until their issues fill them in; nothing calls this package yet.
"""
import sys

from .base import Platform


def current() -> Platform:
    """The platform module for this OS: Windows or Linux. Anything else is unsupported."""
    if sys.platform == "win32":
        from .windows import WindowsPlatform
        return WindowsPlatform()
    if sys.platform.startswith("linux"):
        from .linux import LinuxPlatform
        return LinuxPlatform()
    raise RuntimeError(f"Quest Tether supports Windows and Linux, not {sys.platform}")
