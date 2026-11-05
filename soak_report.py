"""Pure summary counters for a quest-tether soak-run log (#3)."""
import re

# A logged line is "[YYYY-MM-DD ]HH:MM:SS[,msms] [LEVEL] message".
# The time is the HH:MM:SS sitting just before " [".
_LINE_RE = re.compile(
    r'(?P<time>\d{2}:\d{2}:\d{2})'
    r'(?:[.,]\d+)?'
    r' \[[^\]]*\] '
    r'(?P<message>.*)$'
)


def summarize(text: str) -> dict:
    """Counts from a quest-tether log for a soak run (#3): connects, drops, reconnects, refusals, first/last time."""
    result = {
        "connects": 0,
        "drops": 0,
        "reconnects": 0,
        "refusals": 0,
        "disconnects": 0,
        "first": None,
        "last": None,
        "lines": 0,
    }
    if not text:
        return result
    for line in text.splitlines():
        match = _LINE_RE.search(line)
        if not match:
            continue
        message = match.group("message")
        if "Tethering active!" in message:
            result["connects"] += 1
        if "gnirehtet process died!" in message:
            result["drops"] += 1
        if "Reconnected!" in message:
            result["reconnects"] += 1
        if "client start refused" in message:
            result["refusals"] += 1
        if message == "Disconnected":
            result["disconnects"] += 1
        time = match.group("time")
        result["lines"] += 1
        if result["first"] is None:
            result["first"] = time
        result["last"] = time
    return result


def format_summary(s: dict) -> str:
    """Plain-text block for a summarize() result, one 'Label: value' per line."""
    if s["first"] is None:
        span = "Log span: none"
    else:
        span = f"Log span: {s['first']} to {s['last']}"
    lines = [
        span,
        f"Connects: {s['connects']}",
        f"Drops: {s['drops']}",
        f"Reconnects: {s['reconnects']}",
        f"Refusals: {s['refusals']}",
        f"Disconnects: {s['disconnects']}",
        f"Unrecovered drops: {max(s['drops'] - s['reconnects'], 0)}",
    ]
    return "\n".join(lines)
