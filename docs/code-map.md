# Code map

What each part owns, who calls whom, and how a Windows-only app becomes Windows + Linux + a Decky plugin (SPEC M6-M9). Read from the tree on 2026-10-26 (checked again that night against `main`: no pull request merged yet, so the skeleton is all that is new). Spec: [../SPEC.md](../SPEC.md). Background and measured facts: [notes/](notes/).

## Layout

| Path | Owns | State | Musts |
|---|---|---|---|
| `adb_manager.py` | `ADBManager`: find adb, list devices, find the Quest, device details, start/stop the gnirehtet client on the headset, VPN up/down, `tun0` stats, Wi-Fi toggle, USB monitor loop. | present (Windows paths inside) | M1, M2, M5 |
| `tether_manager.py` | `TetherManager` + `TetherState` + `TetherStats`: the relay process (`gnirehtet run`), connect/disconnect, health monitor and heal, auto-reconnect, stats, the headset Wi-Fi policy. The tether core. | present (Windows calls inside: `netstat`, `taskkill`, PowerShell, `CREATE_NO_WINDOW`, `%LOCALAPPDATA%`) | M1-M5 |
| `net_sync.py` | Copy the PC's Wi-Fi network to the headset: PC SSID and key from `netsh wlan`, then `cmd wifi` on the headset. | present (Windows only) | M5, M7 |
| `main.py` | The desktop window (CustomTkinter), dashboard, log panel, tray (pystray + Pillow), single-instance guard (Windows named mutex), `--minimized`. | present | M4, M6 |
| `qt_platform/` | **New.** The platform layer: everything that differs by OS behind one interface (`base.py`), with `windows.py` and `linux.py`; `current()` picks one. | stubs | M6, M7 |
| `qtetherd.py` | **New.** Linux user service that owns the one tether (core in-process) and answers JSON requests on `127.0.0.1` (status, connect, disconnect). Clients: the desktop window on Linux, the Decky plugin. | stub | M7, M8 |
| `decky/` | **New.** The Decky Loader plugin: `plugin.json`, a Python backend (`main.py`) that is a client of `qtetherd`, and the Quick Access panel (`src/index.tsx`). | stubs | M8 |
| `build.spec`, `build.bat` | PyInstaller one-file EXE (Windows). Linux packaging joins it (#10 and the Linux package issue). | present | M9 |
| `tools/` | Third-party binaries at run time (adb, relay, headset APK). Supplied by the package, never committed (#11). Four are still tracked: `AdbWinApi.dll`, `AdbWinUsbApi.dll`, `gnirehtet.jar`, `gnirehtet.apk`. | to clean (#11) | M9 |
| `tests/` | **New.** Unit tests that need no headset (platform selection, temperature zones, parsing). `test_platform.py` (platform selection) is there; the rest arrive with their issues (#4, #17, #19, ...). | present (1 file) | all |

## The platform layer (`qt_platform/base.py`)

| Function | Windows (`windows.py`) | Linux (`linux.py`) |
|---|---|---|
| `data_dir()` | `%LOCALAPPDATA%\QuestTether` (today's `LOG_DIR`) | `$XDG_STATE_HOME/quest-tether` (else `~/.local/state/quest-tether`) |
| `tool_path(name, base_dirs)` | `adb.exe` in `tools/` next to the EXE, else `where` | `adb` in `tools/` next to the binary, else `which` |
| `no_window_flags()` | `subprocess.CREATE_NO_WINDOW` | `0` |
| `relay_command(tools, serial)` | `java -jar gnirehtet.jar run <serial>` (Java relay, #9) | `gnirehtet run <serial>` (native relay; Java fallback if #9 says so) |
| `stale_relay_pids(port)` / `kill(pid)` | `netstat -ano` + PowerShell command line + `taskkill` (today's code) | `ss -ltnp` (or `/proc/net/tcp` + `/proc/<pid>/cmdline`) + `os.kill` |
| `pc_wifi()` -> `(ssid, key)` | `netsh wlan show interfaces` / `show profile key=clear` (today's `net_sync`) | `nmcli -t -f active,ssid dev wifi` / `nmcli -s -g 802-11-wireless-security.psk connection show <ssid>` |
| `single_instance(name)` | named mutex `Local\QuestTetherSingleInstance` (today's code) | a lock file in `data_dir()` (`fcntl.flock`) |
| `autostart_get/set(on)` | `HKCU\...\Run` value with `--minimized` | `~/.config/autostart/quest-tether.desktop` with `--minimized` |

Callers stop testing `os.name` and call `qt_platform.current()`.

## Who calls whom

```mermaid
flowchart LR
  subgraph Windows
    WinUI[main.py window + tray] --> CoreW[TetherManager / ADBManager]
  end
  subgraph Linux
    LinUI[main.py window + tray] -->|JSON on 127.0.0.1| D[qtetherd.py]
    Decky[decky/ Quick Access panel] --> DB[decky/main.py backend] -->|JSON on 127.0.0.1| D
    D --> CoreL[TetherManager / ADBManager]
  end
  CoreW --> P[qt_platform.current()]
  CoreL --> P
  CoreW --> Sync[net_sync.py] --> P
  CoreL --> Sync
  P --> Tools[(tools/: adb, relay, APK)]
```

- On Windows nothing changes in shape: the window runs the core in-process.
- On Linux the service owns the relay, so the desktop window and Game Mode can't start two relays fighting over port 31416 (the bug the Windows mutex fixed). It runs as a systemd user unit, started at login.
- The Decky backend never runs adb or the relay itself; it only forwards status / connect / disconnect.

## Mac, PC and Linux

- **Windows:** today's app. Keeps the Java relay (Rust relay carries no traffic there, #9).
- **Linux (desktop and SteamOS):** the same window and tray (Tk and pystray work on Linux; pystray needs an AppIndicator on KDE/SteamOS), native relay, NetworkManager for Wi-Fi sync, USB access through a udev rule for the headset's vendor id. The Linux package installs the rule at install time with one visible sudo prompt and removes it on uninstall (the owner's ruling, 2026-10-26: "Install with the package"); the app never asks for root after that.
- **SteamOS Game Mode:** the Decky plugin only (no window).
- **Mac:** not supported (SPEC Won'ts).
- **CI:** byte-compile everything and run `tests/` on Windows and Linux.

## Open-source pieces

- gnirehtet (Genymobile, Apache-2.0): the relay and headset APK; Java relay on Windows, native relay on Linux.
- Android platform-tools (`adb`): per-OS binary from Google's download, in the package.
- CustomTkinter, pystray, Pillow (already used).
- Decky Loader's plugin template (`decky-plugin-template`, `@decky/ui`, `@decky/api`) for the plugin's frontend build.

## Dev stages to 1.0

Where it stands (2026-10-26): Dev Loop, nothing past the skeleton built. Open issues #1-#21 (#12 is the goals-and-rules note, #5 is after-1.0); no pull request merged or open.

1. Clean the repo and package (#11, then #10); CI on Windows and Linux (CI done). Not started.
2. Fix the honest Connected state (#1) and add battery temperature (#4). Not started.
3. The platform layer: Windows implementation from today's code (#13), Linux implementation (#14), callers switched (#15). Skeleton only (`qt_platform/` stubs).
4. Linux relay and USB access (#16, #17); `qtetherd` (#18); the Decky plugin (#19, #20); the Linux package (#21). Stubs only.
5. Device checks on both OSes: reconnect (#2, with #6's answer), soak (#3), the questions (#7, #8, #9). Need the headset; not started.
