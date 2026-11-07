#!/bin/sh
# Builds the Linux package (#21): quest-tether and qtetherd with PyInstaller, the Decky
# plugin zip (only when pnpm is on PATH), then dist/quest-tether-linux.tar.gz.
#
# The tarball unpacks to one folder, quest-tether/, with the binaries, tools/, install.sh,
# uninstall.sh, the systemd user unit, the udev rule and README.md. Run it from anywhere:
#     packaging/linux/build.sh
#
# Needs pyinstaller on PATH, and tools/adb, tools/gnirehtet and tools/gnirehtet.apk in the
# repo's tools/ folder (see tools/README.md). Nothing is installed on this machine.
#
# Overrides (the unit tests use them): QT_ROOT (repo root), QT_BUILD (work folder),
# QT_DIST (where the tarball goes), QT_TOOLS (tool folder), QT_DECKY (Decky plugin folder).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${QT_ROOT:-$(cd "$HERE/../.." && pwd)}"
BUILD="${QT_BUILD:-$ROOT/build/linux}"
DIST="${QT_DIST:-$ROOT/dist}"
TOOLS="${QT_TOOLS:-$ROOT/tools}"
DECKY="${QT_DECKY:-$ROOT/decky}"
NAME=quest-tether
STAGE="$BUILD/stage/$NAME"
TARBALL="$DIST/$NAME-linux.tar.gz"

die() {
  echo "error: $*" >&2
  exit 1
}

# Check everything that can fail cheaply before the slow steps.
command -v pyinstaller >/dev/null 2>&1 || die "pyinstaller is not on PATH (pip install pyinstaller)"
for t in adb gnirehtet gnirehtet.apk; do
  [ -f "$TOOLS/$t" ] || die "tools/$t is missing from $TOOLS (see tools/README.md)"
done

echo "==> PyInstaller: quest-tether and qtetherd"
rm -rf "$BUILD/pyinstaller"
cd "$ROOT"
pyinstaller --noconfirm --clean \
  --distpath "$BUILD/pyinstaller/dist" --workpath "$BUILD/pyinstaller/work" \
  "$ROOT/build-linux.spec"
for b in quest-tether qtetherd; do
  [ -f "$BUILD/pyinstaller/dist/$b" ] || die "PyInstaller did not make $b"
done

echo "==> Decky plugin zip"
ZIP="$BUILD/$NAME-decky.zip"
rm -f "$ZIP"
rm -rf "$BUILD/decky"
if command -v pnpm >/dev/null 2>&1; then
  command -v zip >/dev/null 2>&1 || die "zip is not on PATH (needed for the Decky zip)"
  (cd "$DECKY" && pnpm install --frozen-lockfile && pnpm run build)
  [ -f "$DECKY/dist/index.js" ] || die "pnpm run build did not write decky/dist/index.js"
  # The zip holds one folder: plugin.json, main.py and dist/ (decky/README.md).
  mkdir -p "$BUILD/decky/$NAME/dist"
  cp "$DECKY/plugin.json" "$DECKY/main.py" "$BUILD/decky/$NAME/"
  cp -r "$DECKY/dist/." "$BUILD/decky/$NAME/dist/"
  (cd "$BUILD/decky" && zip -qr "$ZIP" "$NAME")
  echo "Decky zip: $ZIP"
else
  echo "pnpm is not on PATH: skipping the Decky zip. The package goes out without the Decky panel;"
  echo "build it later as decky/README.md says."
fi

echo "==> Assembling $NAME/"
rm -rf "$BUILD/stage"
mkdir -p "$STAGE/tools"
cp "$BUILD/pyinstaller/dist/quest-tether" "$BUILD/pyinstaller/dist/qtetherd" "$STAGE/"
# Only the native relay (no gnirehtet.jar): the Linux tools set (#16 decides any change).
for t in adb gnirehtet gnirehtet.apk; do
  cp "$TOOLS/$t" "$STAGE/tools/$t"
done
cp "$HERE/install.sh" "$HERE/uninstall.sh" "$HERE/quest-tether.service" \
  "$HERE/51-quest-tether.rules" "$HERE/README.md" "$STAGE/"
if [ -f "$ZIP" ]; then
  cp "$ZIP" "$STAGE/"
fi
chmod 755 "$STAGE/quest-tether" "$STAGE/qtetherd" "$STAGE/install.sh" "$STAGE/uninstall.sh" \
  "$STAGE/tools/adb" "$STAGE/tools/gnirehtet"

echo "==> Tarball"
mkdir -p "$DIST"
rm -f "$TARBALL"
tar -C "$BUILD/stage" --owner=0 --group=0 --numeric-owner -czf "$TARBALL" "$NAME"
echo "Wrote $TARBALL"
