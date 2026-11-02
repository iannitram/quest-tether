"""Pure Linux paths, native relay command and autostart entry text."""
import posixpath
from typing import List, Mapping


def data_dir_path(env: Mapping[str, str], home: str) -> str:
    base = env.get("XDG_STATE_HOME") or posixpath.join(home, ".local", "state")
    return posixpath.join(base, "quest-tether")


def relay_command(tools_dir: str, serial: str) -> List[str]:
    return [posixpath.join(tools_dir, "gnirehtet"), "run", serial]


def autostart_path(env: Mapping[str, str], home: str) -> str:
    base = env.get("XDG_CONFIG_HOME") or posixpath.join(home, ".config")
    return posixpath.join(base, "autostart", "quest-tether.desktop")


def autostart_command(exe: str, frozen: bool, main_py: str) -> str:
    if frozen:
        return f'"{exe}" --minimized'
    return f'"{exe}" "{main_py}" --minimized'


def autostart_entry(command: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Quest Tether\n"
        f"Exec={command}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
