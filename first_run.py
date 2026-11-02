"""Report missing first-run tools without running them."""

import os
from typing import Callable, List, Optional


def missing_tools(
    platform_name: str, tools_dir: str, which: Callable[[str], Optional[str]]
) -> List[str]:
    """List missing packaged files and runtimes in platform check order."""
    if platform_name == "windows":
        names = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll",
                 "gnirehtet.jar", "gnirehtet.apk")
    elif platform_name == "linux":
        names = ("adb", "gnirehtet", "gnirehtet.apk")
    else:
        raise ValueError(f"Unsupported platform: {platform_name}")

    missing = []
    for name in names:
        if os.path.isfile(os.path.join(tools_dir, name)):
            continue
        if name in ("adb", "adb.exe") and which("adb") is not None:
            continue
        missing.append(f"{name} is missing from {tools_dir}")

    if platform_name == "windows" and which("java") is None:
        missing.append("Java was not found on PATH (the Windows relay needs it)")
    return missing
