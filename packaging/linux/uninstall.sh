#!/bin/sh

PREFIX="$HOME/.local/share/quest-tether"
BIN="$HOME/.local/bin"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/quest-tether.service"

systemctl --user disable --now quest-tether.service || true
rm -f "$UNIT"
systemctl --user daemon-reload || true
# Remove the command link only when it is ours; leave any other file alone.
if [ -L "$BIN/quest-tether" ] && [ "$(readlink "$BIN/quest-tether")" = "$PREFIX/quest-tether" ]; then
  rm -f "$BIN/quest-tether"
fi
rm -rf "$PREFIX"

echo "Removing the USB rule needs one administrator step."
sudo sh -c 'rm -f /etc/udev/rules.d/51-quest-tether.rules && udevadm control --reload-rules'
