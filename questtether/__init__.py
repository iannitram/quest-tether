"""Quest Tether — reverse-tether a Meta Quest through the host over USB.

Cross-platform core. The Windows build drives Microsoft Remote Desktop over the
tether; the SteamOS build drives Valve's Steam Link. Either way this package only
provides the USB network path (via gnirehtet) — it does not stream anything itself.
"""

__version__ = "0.2.0-steamos"
