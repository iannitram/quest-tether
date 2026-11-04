"""Pure parser for the gnirehtet VPN agent state in `dumpsys connectivity` output (#64)."""
import re

_STATE = "ni{VPN "
_SESSION = re.compile(r"sessionId=Gnirehtet[,\s}>]")


def gnirehtet_vpn_state(text: str | None) -> str | None:
    """State word of the gnirehtet VPN agent in `dumpsys connectivity` output ("CONNECTED", "CONNECTING", "DISCONNECTED", ...), or None if no gnirehtet VPN agent is listed."""
    if not text:
        return None
    for line in text.splitlines():
        if _STATE not in line or not _SESSION.search(line):
            continue
        rest = line.split(_STATE, 1)[1]
        state = ""
        for ch in rest:
            if ch.isascii() and ch.isupper() or ch == "_":
                state += ch
            else:
                break
        if state:
            return state
    return None


def vpn_closed(text: str | None) -> bool:
    """True when a real read shows no gnirehtet VPN agent up: gnirehtet_vpn_state(text) is None or "DISCONNECTED". None or text that is empty after .strip() is not proof the VPN is closed: False."""
    if text is None or not text.strip():
        return False
    state = gnirehtet_vpn_state(text)
    return state is None or state == "DISCONNECTED"
