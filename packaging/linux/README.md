# Quest Tether for Linux

This package runs Quest Tether on Linux, SteamOS included. It holds:

- `quest-tether`: the command line. `quest-tether gui` opens the window.
- `qtetherd`: the service that owns the USB tether. `install.sh` runs it as a systemd user service.
- `tools/`: `adb`, the native `gnirehtet` relay and `gnirehtet.apk`. The program looks for them here, next to the binaries.
- `quest-tether-decky.zip`: the Decky Quick Access panel. It is in the package only when the package was built with pnpm.
- `quest-tether.service` and `51-quest-tether.rules`: the user service and the USB rule.
- `install.sh` and `uninstall.sh`.

## Install

1. Unpack the package: `tar -xzf quest-tether-linux.tar.gz`
2. Run the installer: `cd quest-tether` and `./install.sh`
3. The installer asks for your administrator password once. It is for the USB rule, which lets the headset work without root. Nothing else needs root.
4. Unplug the headset and plug it in again.

The installer puts the files in `~/.local/share/quest-tether`, links `quest-tether` into `~/.local/bin`, and starts the service. If `claude` is on your PATH, it also registers the MCP server with Claude Code.

## Decky panel

`install.sh` does not load the Decky zip. Install `quest-tether-decky.zip` from Decky's developer settings. The installer prints where the zip is.

## Uninstall

Run `./uninstall.sh` from the package folder you unpacked. It stops the service and removes the files, the command link and the Claude Code registration. It asks for your administrator password once, to remove the USB rule.

## Build

Developers build this package with `packaging/linux/build.sh` in the source repo. See `docs/linux.md`.
