"""Pure parser for the relay's "Client #N connected/disconnected" lines.

Nothing here touches the relay or the log file: one line in, a change (or
None) out, and a new connected-client set after each change.
"""
# Pattern derived from gnirehtet-gui (Apache-2.0), apps/desktop/src-tauri/src/relay_output.rs.

CONNECTED = "connected"
DISCONNECTED = "disconnected"


def parse_client_change(line: str | None) -> tuple[str, int] | None:
    """Return ("connected", n) or ("disconnected", n) for a relay "Client #n connected/disconnected" line, else None."""
    if not line:
        return None
    marker = line.find("Client #")
    if marker < 0:
        return None
    rest = line[marker + len("Client #"):]
    n = 0
    i = 0
    while i < len(rest) and rest[i].isascii() and rest[i].isdigit():
        n = n * 10 + int(rest[i])
        i += 1
    if i == 0:
        return None
    word = rest[i:].strip()
    if word == CONNECTED:
        return (CONNECTED, n)
    if word == DISCONNECTED:
        return (DISCONNECTED, n)
    return None


def apply_change(clients: frozenset[int], change: tuple[str, int] | None) -> frozenset[int]:
    """Return the new set of connected client ids after one change (None leaves it unchanged)."""
    if change is None:
        return clients
    action, client_id = change
    if action == CONNECTED:
        return clients | {client_id}
    if action == DISCONNECTED:
        return clients - {client_id}
    return clients
