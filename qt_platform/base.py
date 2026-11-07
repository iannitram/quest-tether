"""The interface every platform module implements (docs/code-map.md, "The platform layer")."""

from typing import List, Optional, Sequence, Tuple


class Platform:
    """One OS's way of doing the things the tether core needs."""

    name = "base"

    def data_dir(self) -> str:
        """Folder for logs and flags (relay.log, wifi-off.flag), created if missing."""
        raise NotImplementedError

    def config_dir(self) -> str:
        """Folder for settings.json (service settings, #117), created if missing."""
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

    def relay_server_command(self, tools_dir: str, port: int) -> List[str]:
        """The command that runs only the relay on port (`gnirehtet relay -p <port>`).

        No serial: the caller starts the Quest-side client itself (split mode, #66).
        """
        raise NotImplementedError

    def port_holders(self, port: int) -> List[Tuple[int, bool]]:
        """Every process listening on port as (pid, is_gnirehtet). Unreadable command lines count as not gnirehtet."""
        raise NotImplementedError

    def relay_candidates(self) -> List[Tuple[str, bool]]:
        """Accepted relay filenames in preference order, each with whether it needs Java."""
        raise NotImplementedError

    def stale_relay_pids(self, port: int) -> List[int]:
        """PIDs of gnirehtet processes listening on port (never another program's)."""
        raise NotImplementedError

    def kill(self, pid: int) -> None:
        """Force-stop one process."""
        raise NotImplementedError

    def kill_tree(self, pid: int) -> None:
        """Force-stop a process and its children."""
        raise NotImplementedError

    def pc_ssid(self) -> Optional[str]:
        """SSID of the PC's current Wi-Fi network, or None if not on Wi-Fi.

        Must not read the saved key: callers only fetch credentials when switching.
        """
        raise NotImplementedError

    def pc_wifi(self) -> Optional[Tuple[str, str, str]]:
        """(SSID, key, security) of the PC's current Wi-Fi network, or None if not on Wi-Fi or unreadable.

        security is the headset's `cmd wifi connect-network` keyword: wpa2 or wpa3.
        """
        raise NotImplementedError

    def single_instance(self, name: str) -> bool:
        """True if this is the only running copy; held for the life of the process."""
        raise NotImplementedError

    def uses_tether_service(self) -> bool:
        """True when a separate service owns the tether core and the window is only a client."""
        return False

    def autostart(self) -> bool:
        """True if the app starts (minimized) at login."""
        raise NotImplementedError

    def set_autostart(self, on: bool) -> bool:
        """Turn start-at-login on or off; returns the new state."""
        raise NotImplementedError

    def service(self, action: str) -> dict:
        """Status, start or stop the tether service.

        Returns {"ok": bool, "running": bool, "detail": str}; Windows has no
        service and answers {"ok": False, "error": "no service on Windows"}.
        """
        raise NotImplementedError
