#!/usr/bin/env bash
# Build the Quest Tether Decky plugin and install it into Decky Loader.
# Run from the decky/ directory:  ./install.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

NAME="Quest Tether"                       # must match plugin.json "name"
DEST="$HOME/homebrew/plugins/$NAME"
OWN="$(stat -c '%U:%G' "$HOME/homebrew/plugins" 2>/dev/null || echo root:root)"

echo "==> building frontend"
if [ ! -d node_modules ]; then pnpm install; fi
pnpm run build

echo "==> installing to: $DEST (owner $OWN)"
sudo rm -rf "$DEST"
sudo mkdir -p "$DEST"
sudo cp -r plugin.json package.json main.py dist py_modules "$DEST"/
[ -d assets ] && sudo cp -r assets "$DEST"/
sudo chown -R "$OWN" "$DEST"

echo "==> restarting Decky loader"
sudo systemctl restart plugin_loader

echo "Done. Open the Decky (Quick Access) menu in Game Mode → 'Quest Tether'."
echo "Backend log: ~/homebrew/logs/$NAME/"
