# Quest Tether

Reverse-tether a Meta Quest headset through a host computer over USB, so a
desktop/game stream to the headset travels over the **USB cable instead of
Wi-Fi** — crystal clear, low latency, even where the Wi-Fi is terrible.

- **Windows** host → streams via Microsoft **Remote Desktop**
- **SteamOS / Steam Deck** host → streams via Valve **Steam Link**

The app itself does **not** stream anything. It uses
[gnirehtet](https://github.com/Genymobile/gnirehtet) to give the Quest a VPN
(`tun0`) whose gateway is the host, so **all** the Quest's traffic routes back
through the host over the ADB/USB link. The streaming client (Remote Desktop /
Steam Link) then connects to the host over that fast local path.

## Why

Built for working "flat" in VR from a van with unreliable Wi-Fi: the headset
gets its network from the host's USB port, and the screen stream rides the same
cable. On a Steam Deck this was measured streaming **Direct over USB at
~28 Mbit/s** (not bounced through Valve's internet relay).

---

## Repository layout

```
questtether/            Cross-platform engine (pure stdlib, no external deps)
  platform_support.py     OS abstraction (process groups, tool discovery, ...)
  adb.py                  ADBManager — device detect + adb commands
  tether.py               TetherManager — drives `gnirehtet run`, health, reconnect
  cli.py                  `quest-tether` CLI (doctor/status/connect/install/gui)
  gui.py                  Native tkinter desktop GUI (Desktop Mode)
tools/                  Bundled binaries
  adb, gnirehtet          Linux (native gnirehtet Rust build — no Java needed)
  adb.exe, gnirehtet.jar  Windows
  gnirehtet.apk           Quest client (same on both)
scripts/
  setup-udev.sh           Install the USB access rule (SteamOS)
  install-desktop.sh      Install the KDE .desktop launcher
decky/                  Decky Loader plugin (Game Mode control) — see decky/README.md
quest-tether            Launcher: `./quest-tether <command>`
main.py, *_manager.py, build.*   Original Windows build (customtkinter + PyInstaller)
```

---

## SteamOS / Steam Deck setup

SteamOS has no Java, no adb, and a read-only root — everything needed is bundled
in `tools/` and installs under `$HOME`, so nothing fights the immutable OS.

### 1. Prepare the Quest (one time)

Enable **Developer Mode** in the Meta Horizon phone app (the headset must be on
the latest OS and on Wi-Fi, or the app errors "install the latest OS"). Then in
the headset: **Settings → Developer** and turn on **USB Connection Dialog** and
**MTP Notification**. Plug in via USB and accept **"Allow USB debugging"** (tick
"Always allow from this computer"). *(On Meta Horizon OS 2 the version reads
`2.x`, not `v8x`.)*

### 2. Grant USB access

```bash
./scripts/setup-udev.sh      # writes /etc/udev/rules.d/51-quest-tether.rules (uaccess),
                             # registers it to survive SteamOS updates, reloads udev
./quest-tether doctor        # should end with "Ready to tether"
```

### 3a. Desktop Mode — GUI

```bash
./scripts/install-desktop.sh   # adds "Quest Tether" to the KDE app menu (Network)
./quest-tether gui             # or launch directly
```

Auto-detects the Quest, auto-connects, shows live stats. Closing the window
disconnects gracefully and restores the Quest's Wi-Fi.

### 3b. Game Mode — Decky plugin

Requires [Decky Loader](https://decky.xyz/). See [`decky/README.md`](decky/README.md).

```bash
cd decky && ./install.sh       # build + install + restart the Decky loader
```

Then in Game Mode: **Quick Access (•••) → Decky → Quest Tether**.

### CLI

```bash
./quest-tether doctor     # check adb, gnirehtet, udev, device readiness
./quest-tether status     # one-shot device status
./quest-tether connect    # bring the tether up and hold it (Ctrl+C to stop)
./quest-tether connect -v # also show gnirehtet's relay logs
./quest-tether install    # push the gnirehtet client APK to the Quest
./quest-tether gui        # launch the desktop GUI
```

---

## Using Steam Link (SteamOS)

The Deck is both the tether host **and** the Steam Remote Play host — the same
role the Windows PC played for Remote Desktop.

1. On the Deck: **Steam → Settings → Remote Play → enable**, and enable
   **"Allow Direct Connection (IP Sharing)"** under Advanced Host Options.
2. Start the tether (GUI, Decky panel, or `quest-tether connect`).
3. On the Quest: open **Steam Link**, pick the Deck, **Start** (pair by PIN if
   it isn't listed — broadcast discovery does not cross the tether).

**Notes / limits**
- Steam Link streams the **Steam session (Game Mode / Big Picture)**, not the
  raw KDE desktop — that's a Steam Remote Play limitation. For full flat-desktop
  streaming over the same tether, **Sunshine + Moonlight** is the alternative.
- Confirm the stream is **Direct**, not **Relay**: the tether's Down rate should
  jump to a few MB/s. If it stays near zero, Steam bounced it to the SDR relay.

---

## How it works

`gnirehtet run <serial>` (one native process) installs the Quest client, sets up
the `adb reverse` tunnel, starts the relay on the host (port **31416**), and
starts the on-device VPN. `TetherManager` supervises that process — health
checks, auto-reconnect, throughput from the Quest's `tun0` counters — and on
disconnect it tears the VPN down and toggles the Quest's Wi-Fi to restore DNS.

Both the desktop GUI and the Decky backend are thin frontends over this one
engine, so behavior is identical in both modes.

> **Known issue:** the Decky backend (always running) and the desktop GUI both
> auto-connect; running both at once makes them fight over port 31416. Use one at
> a time (Desktop GUI *or* Game-Mode Decky). A single-owner lock is planned.

---

## Windows build

The original host. `build.bat` runs PyInstaller (`build.spec`) to produce
`QuestTether.exe`; drop the `tools/` folder next to it. See `main.py`,
`adb_manager.py`, `tether_manager.py`.

---

## Requirements

- **SteamOS:** nothing extra to install — Python 3 + tkinter ship with the OS,
  and adb/gnirehtet are bundled in `tools/`. Decky plugin build needs
  `node`/`pnpm` (present on the Deck).
- **Windows:** Python 3.10+, `pip install -r requirements.txt`.
