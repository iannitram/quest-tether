#!/usr/bin/env bash
#
# Install a udev rule that grants the logged-in Steam Deck user ADB access to
# Meta Quest headsets (USB vendor 0x2833), then reload udev and restart the
# bundled adb server so it re-scans the bus.
#
# SteamOS has a read-only /usr but a writable /etc (persists across updates),
# so the rule lives in /etc/udev/rules.d. The `uaccess` tag lets systemd-logind
# grant the active seat user access without needing a `plugdev` group (SteamOS
# doesn't ship one).
#
# Run:  scripts/setup-udev.sh
#
set -euo pipefail

RULE_PATH=/etc/udev/rules.d/51-quest-tether.rules
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADB="$HERE/tools/adb"

echo "Quest Tether — USB access setup"
echo "  Writing $RULE_PATH (requires sudo)..."
sudo tee "$RULE_PATH" >/dev/null <<'EOF'
# Quest Tether — Meta / Oculus headsets (vendor 0x2833).
# Grant ADB (USB) access to the active local user via systemd-logind uaccess.
SUBSYSTEM=="usb", ATTR{idVendor}=="2833", MODE="0660", TAG+="uaccess"
EOF

# SteamOS 3.6+ performs atomic A/B OS updates and moves user-created /etc files
# to /etc/previous/ unless they're whitelisted. Register the rule so it survives.
KEEP_PATH=/etc/atomic-update.conf.d/quest-tether.conf
echo "  Registering $RULE_PATH to survive SteamOS updates ($KEEP_PATH)..."
echo "$RULE_PATH" | sudo tee "$KEEP_PATH" >/dev/null

echo "  Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger

if [[ -x "$ADB" ]]; then
    echo "  Restarting adb server..."
    "$ADB" kill-server  >/dev/null 2>&1 || true
    "$ADB" start-server >/dev/null 2>&1 || true
fi

echo
echo "Done. Now:"
echo "  1. Unplug and re-plug the Quest via USB."
echo "  2. Put on the headset and accept the 'Allow USB debugging' prompt"
echo "     (tick 'Always allow from this computer')."
echo "  3. Run:  ./quest-tether doctor"
