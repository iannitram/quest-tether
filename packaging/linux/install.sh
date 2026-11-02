#!/bin/sh
set -e

PKG="${QT_PKG:-$(cd "$(dirname "$0")" && pwd)}"
PREFIX="$HOME/.local/share/quest-tether"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/quest-tether.service"

mkdir -p "$PREFIX" "$(dirname "$UNIT")"
cp -r "$PKG/quest-tether" "$PKG/qtetherd" "$PKG/quest-tether-decky.zip" "$PKG/tools" "$PREFIX/"
chmod +x "$PREFIX/quest-tether" "$PREFIX/qtetherd"
cp "$PKG/quest-tether.service" "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now quest-tether.service

echo "Quest Tether needs one administrator step: the USB rule that lets the headset work without root."
sudo sh -c 'cp "$1" /etc/udev/rules.d/51-quest-tether.rules && udevadm control --reload-rules && udevadm trigger' sh "$PKG/51-quest-tether.rules"
