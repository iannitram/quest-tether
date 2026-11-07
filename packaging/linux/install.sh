#!/bin/sh
set -e

PKG="${QT_PKG:-$(cd "$(dirname "$0")" && pwd)}"
PREFIX="$HOME/.local/share/quest-tether"
BIN="$HOME/.local/bin"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/quest-tether.service"

mkdir -p "$PREFIX" "$(dirname "$UNIT")"
cp -r "$PKG/quest-tether" "$PKG/qtetherd" "$PKG/tools" "$PREFIX/"
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

# Register the MCP server with Claude Code for this user, when `claude` is on PATH (#75).
# A failure here does not stop the install; the hint says what to run by hand.
if command -v claude >/dev/null 2>&1; then
  claude mcp remove quest-tether --scope user >/dev/null 2>&1 || true
  if claude mcp add --scope user quest-tether -- "$PREFIX/quest-tether" mcp >/dev/null 2>&1; then
    echo "Registered the quest-tether MCP server with Claude Code (check with: claude mcp list)."
  else
    echo "Could not register the MCP server. To do it by hand: claude mcp add --scope user quest-tether -- $PREFIX/quest-tether mcp"
  fi
fi

cp "$PKG/quest-tether.service" "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now quest-tether.service

echo "Quest Tether needs one administrator step: the USB rule that lets the headset work without root."
sudo sh -c 'cp "$1" /etc/udev/rules.d/51-quest-tether.rules && udevadm control --reload-rules && udevadm trigger' sh "$PKG/51-quest-tether.rules"

# The Decky panel is a zip that Decky loads. The package does not put it into Decky's own
# folder (that needs a second sudo prompt), so the player installs it from Decky's settings.
if [ -f "$PKG/quest-tether-decky.zip" ]; then
  cp "$PKG/quest-tether-decky.zip" "$PREFIX/"
  echo "Decky panel: the plugin zip is at $PREFIX/quest-tether-decky.zip."
  echo "To load it, install that zip from Decky's developer settings. Quest Tether does not do it for you."
else
  echo "This package has no Decky zip (pnpm was missing when it was built), so it has no Decky panel."
fi
