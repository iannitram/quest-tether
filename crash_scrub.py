"""Remove private values from crash report text without side effects."""

import re
from typing import Sequence


def scrub(text: str, home: str, hostname: str, extra: Sequence[str] = ()) -> str:
    """Redact known secrets, device identifiers, and local host details."""
    for value in extra:
        if value:
            text = text.replace(value, "<redacted>")
    text = re.sub(
        r"\b(password|passwd|passphrase|psk|secret|token|api[_-]?key|"
        r"serialno|serial|key content)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"(?<!\S)-s\s+\S+", "-s <serial>", text)
    text = re.sub(
        r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b|"
        r"\b(?:[0-9a-f]{2}-){5}[0-9a-f]{2}\b",
        "<mac>", text, flags=re.IGNORECASE,
    )
    if hostname:
        text = re.sub(re.escape(hostname), "<host>", text, flags=re.IGNORECASE)
    if home and home not in ("/", "\\"):
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return text
