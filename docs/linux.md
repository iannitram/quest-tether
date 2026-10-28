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
