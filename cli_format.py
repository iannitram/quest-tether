"""Pure text formatters for the quest-tether CLI (#74)."""
import math


def format_rate(bps):
    """Format a rate in bytes/s as B/s, kB/s, MB/s or GB/s; None, negative, NaN or infinite gives '-'."""
    if bps is None or not math.isfinite(bps) or bps < 0:
        return "-"
    if bps < 999.5:
        return f"{int(round(bps))} B/s"
    if bps / 1000 < 999.95:
        return f"{bps / 1000:.1f} kB/s"
    if bps / 1_000_000 < 999.95:
        return f"{bps / 1_000_000:.1f} MB/s"
    return f"{bps / 1_000_000_000:.1f} GB/s"


def format_uptime(seconds):
    """Format an uptime in seconds as h/m/s; None or negative -> '-'."""
    if seconds is None or seconds < 0:
        return "-"
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def format_status(status):
    """Render a status dict as CLI text; rates in B/s, uptime in s, temp in C."""
    lines = [
        f"State:      {status.get('state') or 'unknown'}",
        f"Traffic:    down {format_rate(status.get('rx_bps'))}, "
        f"up {format_rate(status.get('tx_bps'))}",
        f"Uptime:     {format_uptime(status.get('uptime_s'))}",
    ]
    battery = status.get("battery_level")
    if battery is None or battery == "":
        line = "Battery:    -"
    else:
        batt_str = str(battery)
        if not batt_str.endswith("%"):
            batt_str += "%"
        line = f"Battery:    {batt_str}"
        if status.get("temp_c") is not None:
            line += f", {status['temp_c']:.1f} C ({status.get('zone') or 'unknown'})"
    lines.append(line)
    lines.append(f"Reconnects: {status.get('reconnects') or 0}")
    if status.get("error"):
        lines.append(f"Error:      {status['error']}")
    return "\n".join(lines)


def exit_code(result):
    """Exit code for a result dict: 2 bad usage, 1 any other non-empty error, 0 ok; non-dict -> 1."""
    if not isinstance(result, dict):
        return 1
    if result.get("error") == "bad usage":
        return 2
    if result.get("error"):
        return 1
    if result.get("ok") is False:
        return 1
    return 0
