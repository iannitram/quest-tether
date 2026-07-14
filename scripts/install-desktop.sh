#!/usr/bin/env bash
# Install the Quest Tether desktop launcher for SteamOS Desktop Mode (KDE Plasma).
# Writes a .desktop entry under $HOME (survives SteamOS's immutable /usr updates).
#   ./scripts/install-desktop.sh                 # add to the app launcher menu
#   ./scripts/install-desktop.sh --desktop-icon  # also drop an icon on the Desktop
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPS="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS/quest-tether.desktop"

mkdir -p "$APPS"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Quest Tether
GenericName=Quest USB Reverse Tether
Comment=Reverse-tether a Meta Quest through this machine over USB
Exec=$REPO/quest-tether gui
Icon=$REPO/assets/icon.png
Terminal=false
Categories=Network;
Keywords=quest;vr;tether;gnirehtet;adb;steam;deck;
StartupWMClass=quest-tether
StartupNotify=true
EOF
chmod +x "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS" 2>/dev/null || true
fi

echo "Installed launcher: $DESKTOP_FILE"
echo "  Exec: $REPO/quest-tether gui"

if [ "${1:-}" = "--desktop-icon" ]; then
  DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  mkdir -p "$DESK"
  cp "$DESKTOP_FILE" "$DESK/quest-tether.desktop"
  chmod +x "$DESK/quest-tether.desktop"
  # KDE marks freshly-dropped launchers untrusted; flag it executable-trusted if we can.
  command -v kwriteconfig5 >/dev/null 2>&1 || true
  echo "Desktop shortcut: $DESK/quest-tether.desktop"
  echo "  (KDE: right-click it once → 'Allow Launching' if it doesn't run on double-click)"
fi

# Gentle nudge if the USB rule isn't in place yet.
if ! grep -rqs 2833 /etc/udev/rules.d /usr/lib/udev/rules.d 2>/dev/null; then
  echo
  echo "NOTE: USB rule not found — run ./scripts/setup-udev.sh once so adb can see the Quest."
fi

echo
echo "Done. Find 'Quest Tether' in the application launcher (Network / Utility)."
