"""Pure parser for `adb shell am start` output (#63)."""


def am_start_error(stdout: str | None, stderr: str | None) -> str | None:
    """Return the first line (stripped) of stdout+stderr that starts with "Error" (after leading whitespace), else None."""
    for text in (stdout, stderr):
        if not text:
            continue
        for line in text.splitlines():
            if line.lstrip().startswith("Error"):
                return line.strip()
    return None
