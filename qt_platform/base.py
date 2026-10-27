"""The interface every platform module implements (docs/code-map.md, "The platform layer")."""
from typing import List, Optional, Sequence, Tuple


class Platform:
    """One OS's way of doing the things the tether core needs."""

    name = "base"

    def data_dir(self) -> str:
        """Folder for logs and flags (relay.log, wifi-off.flag), created if missing."""
        raise NotImplementedError

    def tool_path(self, name: str, base_dirs: Sequence[str]) -> Optional[str]:
        """Full path of a tool ("adb", "gnirehtet", "java") from base_dirs, else from PATH; None if absent."""
        raise NotImplementedError

    def no_window_flags(self) -> int:
        """creationflags for subprocess calls so no console window flashes (0 where not needed)."""
        raise NotImplementedError

    def relay_command(self, tools_dir: str, serial: str) -> List[str]:
        """The command that runs `gnirehtet run <serial>` with this OS's relay."""
        raise NotImplementedError

    def stale_relay_pids(self, port: int) -> List[int]:
        """PIDs of gnirehtet processes listening on port (never another program's)."""
        raise NotImplementedError

    def kill(self, pid: int) -> None:
        """Force-stop one process."""
        raise NotImplementedError

    def pc_wifi(self) -> Optional[Tuple[str, str]]:
        """(SSID, key) of the PC's current Wi-Fi network, or None if not on Wi-Fi or unreadable."""
        raise NotImplementedError

    def single_instance(self, name: str) -> bool:
        """True if this is the only running copy; held for the life of the process."""
        raise NotImplementedError

    def autostart(self) -> bool:
        """True if the app starts (minimized) at login."""
        raise NotImplementedError

    def set_autostart(self, on: bool) -> bool:
        """Turn start-at-login on or off; returns the new state."""
        raise NotImplementedError
