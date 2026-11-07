#!/bin/sh
set -e

PKG="${QT_PKG:-$(cd "$(dirname "$0")" && pwd)}"
PREFIX="$HOME/.local/share/quest-tether"
BIN="$HOME/.local/bin"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/quest-tether.service"

mkdir -p "$PREFIX" "$(dirname "$UNIT")"
cp -r "$PKG/quest-tether" "$PKG/qtetherd" "$PKG/quest-tether-decky.zip" "$PKG/tools" "$PREFIX/"
chmod +x "$PREFIX/quest-tether" "$PREFIX/qtetherd"

# quest-tether is the command line (cli.py); its `gui` subcommand opens the window.
mkdir -p "$BIN"
if [ -e "$BIN/quest-tether" ] && [ ! -L "$BIN/quest-tether" ]; then
  echo "Not linking $BIN/quest-tether: a file of that name is already there."
else
  ln -sfn "$PREFIX/quest-tether" "$BIN/quest-tether"
  case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Add $BIN to your PATH to run quest-tether from any folder." ;;
  esac
fi

cp "$PKG/quest-tether.service" "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now quest-tether.service

echo "Quest Tether needs one administrator step: the USB rule that lets the headset work without root."
sudo sh -c 'cp "$1" /etc/udev/rules.d/51-quest-tether.rules && udevadm control --reload-rules && udevadm trigger' sh "$PKG/51-quest-tether.rules"
