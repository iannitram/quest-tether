# Quest Tether: setup guide

A Windows GUI around gnirehtet reverse tethering for Meta Quest headsets. No headset reboot is needed.

## What it does

It shares the PC's internet connection with a Quest 2/3/Pro over USB, so the headset gets full internet without Wi-Fi.

- Detects the Quest when it is plugged in.
- One-click connect and disconnect.
- Auto-reconnects after USB hiccups or headset sleep, without a headset reboot.
- Live dashboard: throughput, uptime, battery, reconnect count.
- Scrollable log for debugging.

## Prerequisites

1. Developer Mode enabled on the Quest (Settings > System > Developer).
2. USB Debugging enabled (Settings > System > Developer > USB Debug).
3. A USB-C data cable between the Quest and the PC.
4. A Java runtime (JDK or JRE), needed for the gnirehtet relay.

## Run from source

### 1. Get the dependencies

Put these in `tools/`:

| File | Where to get it |
|------|-----------------|
| `adb.exe`, `AdbWinApi.dll`, `AdbWinUsbApi.dll` | [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools): download, extract, copy the three files |
| `gnirehtet.jar` | [gnirehtet releases](https://github.com/Genymobile/gnirehtet/releases): the **Java** release ZIP |
| `gnirehtet.apk` | the same release ZIP |

Use the Java release of gnirehtet, not the Rust binary. The Rust relay (`gnirehtet.exe`) starts but carries no traffic on my Windows machine; the Java relay works immediately (see issue #9). `adb.exe` itself is not stored in the repo (see issue #11); the DLLs, jar and apk are.

`tools/` should contain `adb.exe`, `AdbWinApi.dll`, `AdbWinUsbApi.dll`, `gnirehtet.jar`, `gnirehtet.apk`.

### 2. Install Python dependencies

```
pip install -r requirements.txt
```

### 3. Run

```
python main.py
```

### 4. First-time device setup

1. Plug in the Quest via USB.
2. Put on the headset. An "Allow USB Debugging?" prompt appears; check "Always allow" and tap OK.
3. Take the headset off. The app should show the Quest as detected.
4. Click **Connect**.
5. Put the headset on again. A VPN connection request from gnirehtet appears; tap OK.
6. The Quest now has internet through the PC.

Steps 2 and 5 are not needed again unless the headset is factory reset.

## Build a standalone EXE

```
pip install pyinstaller customtkinter
pyinstaller build.spec --clean --noconfirm
```

The output is `dist/QuestTether.exe`; `build.bat` does the same. Copy the `tools/` folder next to the EXE afterwards, or the app cannot find the ADB and gnirehtet binaries. (Issue #10 tracks packaging this properly.)

## How it works

1. Relay server: the Java relay (`java -jar gnirehtet.jar relay`) runs on the PC and listens on port 31416.
2. ADB reverse tunnel: maps the Quest's `localabstract:gnirehtet` socket to the PC's port 31416.
3. VPN client: the gnirehtet APK on the Quest registers as a VPN, intercepts all traffic, routes it through the tunnel to the relay, and the relay sends it out over the PC's network.

The "no reboot" part: when the connection drops, the app stops the VPN client, tears down the tunnel, re-establishes both, and restarts the client, all through ADB commands. (The app later moved to wrapping `gnirehtet run` as one managed process; see `phase-1-core-development.md`.)

## Troubleshooting

- **"ADB: Not Found":** put `adb.exe` in `tools/`, or install Platform Tools and add it to PATH.
- **"Device found but unauthorized":** put on the headset and accept the USB debugging prompt.
- **Quest not detected:** check that Developer Mode and USB Debugging are on; try a different cable (a data cable, not charge-only).
- **"Failed to start relay server":** most likely an orphaned relay from a previous session holds port 31416. The app kills stale relays on startup; if it persists, kill `java.exe` in Task Manager and retry.
- **Connected but no internet:** check that the PC itself has internet; the relay forwards through the PC's active connection.
- **Keeps reconnecting:** some USB cables are flaky. Use a short, good USB-C cable, and check the Quest's auto-sleep setting (Settings > System > Power > Auto Sleep).
