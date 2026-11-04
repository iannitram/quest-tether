"""Pure parser for the gnirehtet VPN agent state in `dumpsys connectivity` output (#64)."""

_STATE = "ni{VPN "


def gnirehtet_vpn_state(text: str | None) -> str | None:
    """State word of the gnirehtet VPN agent in `dumpsys connectivity` output ("CONNECTED", "CONNECTING", "DISCONNECTED", ...), or None if no gnirehtet VPN agent is listed."""
    if not text:
        return None
    for line in text.splitlines():
        if _STATE not in line or "sessionId=Gnirehtet" not in line:
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
    """True when no gnirehtet VPN agent is up: gnirehtet_vpn_state(text) is None or "DISCONNECTED"."""
    state = gnirehtet_vpn_state(text)
    return state is None or state == "DISCONNECTED"
