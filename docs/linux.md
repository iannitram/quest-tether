# Linux Guide

Information on running Quest Tether on Linux (desktop distributions and SteamOS).

## USB access (udev rule)

To reach the Quest headset over USB without root privileges, Linux systems require a udev rule that grants access to USB devices with vendor ID `2833`.

### Package Installation

The Linux package installs the rule automatically at install time with one visible sudo prompt shown to the player, and removes it on uninstall:
- On install, the rule is placed in `/etc/udev/rules.d/51-quest-tether.rules` and udev rules are reloaded.
- On uninstall, it deletes `/etc/udev/rules.d/51-quest-tether.rules` and reloads the rules.

Once installed, the app never asks for root permissions.

### Manual Installation (Running from Source)

When running from source, you can install the udev rule manually:

1. Copy the rule file to `/etc/udev/rules.d/`:
   ```bash
   sudo cp packaging/linux/51-quest-tether.rules /etc/udev/rules.d/
   ```

2. Reload and trigger udev rules:
   ```bash
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```

3. Unplug and replug the headset.

On SteamOS, `/etc` survives system updates but modifying it requires `sudo`.

### Manual Removal

To remove the rule manually:

```bash
sudo rm /etc/udev/rules.d/51-quest-tether.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## Build the package (#21)

`packaging/linux/build.sh` makes the Linux package. Run it from the repo root on a Linux machine:

```sh
pip install pyinstaller          # the build tool
packaging/linux/build.sh
```

It needs `tools/adb`, `tools/gnirehtet` and `tools/gnirehtet.apk` in the repo's `tools/` folder (see `tools/README.md`); it stops and names any that is missing. It runs PyInstaller on `build-linux.spec` to make `quest-tether` (the CLI; `quest-tether gui` opens the window) and `qtetherd` (the service). If `pnpm` is on PATH it also builds the Decky plugin and zips it; without `pnpm` the package goes out without the Decky panel.

The output is `dist/quest-tether-linux.tar.gz`. It unpacks to one `quest-tether/` folder with the binaries, `tools/`, `install.sh`, `uninstall.sh`, the service unit, the udev rule and a short `README.md`. The player runs `install.sh` from that folder (see the package's `README.md`).

`install.sh` does not load the Decky zip into Decky. It prints where the zip is, and the player installs it from Decky's developer settings. This keeps the install to one sudo prompt.

The unit tests (`tests/test_linux_build_script.py`) run the script with fake `pyinstaller`, `pnpm` and `zip` on PATH and check the tarball's layout. They do not run PyInstaller.
