# Code map

What each part owns, who calls whom, and how a Windows-only app becomes Windows + Linux + a Decky plugin (SPEC M6-M9). Describes commit: `6907b8a` (2026-10-28). Checked against `main` on 2026-10-28: the skeleton is still all that is new; the only pull request merged so far (#22) moved CI to our own runners (replaced by GitHub-hosted runners in #25) and changed no application code. Spec: [../SPEC.md](../SPEC.md). Background and measured facts: [notes/](notes/).

## Layout

| Path | Owns | State | Musts |
|---|---|---|---|
| `adb_manager.py` | `ADBManager`: find adb, list devices, find the Quest, device details, start/stop the gnirehtet client on the headset, VPN up/down, `tun0` stats, Wi-Fi toggle, USB monitor loop. | present (Windows paths inside) | M1, M2, M5 |
| `tether_manager.py` | `TetherManager` + `TetherState` + `TetherStats`: the relay process (`gnirehtet run`), connect/disconnect, health monitor and heal, auto-reconnect, stats, the headset Wi-Fi policy. The tether core. | present (OS calls go through `qt_platform.current()`) | M1-M5 |
| `heal.py` | **New.** `Healer`: the pure heal ladder and flap breaker lifted out of the health loop. `now` is passed in, nothing sleeps, and every reading returns `None`, `RestartClient`, `RestartAdbThenClient`, `FullReconnect` or `GiveUp`. | present | M2 |
| `relay_log.py` | Pure parser: relay "Client #N connected/disconnected" lines and the connected-client set (#62). | new | M1 |
| `net_sync.py` | Copy the PC's Wi-Fi network to the headset: PC SSID, key and security from the platform layer, then `cmd wifi` on the headset. | present (platform-backed) | M5, M7 |
| `main.py` | The desktop window (CustomTkinter), dashboard, log panel, tray (pystray + Pillow), single-instance guard (through the platform layer), `--minimized`. | present | M4, M6 |
| `qt_platform/` | **New.** The platform layer: everything that differs by OS behind one interface (`base.py`: the `Platform` class), with `windows.py` (`WindowsPlatform`) and `linux.py` (`LinuxPlatform`); `current()` in `__init__.py` picks one (Windows, Linux, else `RuntimeError`). Callers in `adb_manager.py`, `tether_manager.py`, `net_sync.py` and `main.py` use it. | present on both OSes | M6, M7 |
| `qtetherd.py` | **New.** Linux user service that owns the one tether (core in-process) and answers JSON requests on `127.0.0.1:31417` (status, connect, disconnect; the relay's own port is 31416). Clients: the desktop window on Linux, the Decky plugin. | stub | M7, M8 |
| `decky/` | **New.** The Decky Loader plugin: `plugin.json`, a Python backend (`main.py`) that is a client of `qtetherd`, and the Quick Access panel (`src/index.tsx`) with the `decky-plugin-template` build config (`package.json`, `rollup.config.js`, `tsconfig.json`). | panel built; backend stub | M8 |
| `build.spec`, `build.bat` | PyInstaller one-file EXE (Windows). Linux packaging joins it (#10 and the Linux package issue). | present | M9 |
| `tools/` | Third-party binaries at run time (adb, relay, headset APK). Supplied by the package, never committed (#11). Four are still tracked: `AdbWinApi.dll`, `AdbWinUsbApi.dll`, `gnirehtet.jar`, `gnirehtet.apk`. | to clean (#11) | M9 |
| `tests/` | **New.** Unit tests that need no headset (platform selection, temperature zones, parsing). `test_platform.py` (platform selection) is there; the rest arrive with their issues (#4, #17, #19, ...). CI runs them with `python -m unittest discover -s tests`. | present (1 file) | all |
| `am_result.py` | **New.** Pure parser: the first `Error` line of an `am start` reply (#63). | new | - |
| `heal_policy.py` | **New.** Pure awake-gated liveness policy: wakefulness parse, restart backoff, `Liveness` transitions (#65). | new | - |

## The platform layer (`qt_platform/base.py`)

| Function | Windows (`windows.py`) | Linux (`linux.py`) |
|---|---|---|
| `data_dir()` | `%LOCALAPPDATA%\QuestTether` (today's `LOG_DIR`) | `$XDG_STATE_HOME/quest-tether` (else `~/.local/state/quest-tether`) |
| `tool_path(name, base_dirs)` | `adb.exe` in `tools/` next to the EXE, else `where` | `adb` in `tools/` next to the binary, else `which` |
| `no_window_flags()` | `subprocess.CREATE_NO_WINDOW` | `0` |
| `relay_command(tools, serial)` | `java -jar gnirehtet.jar run <serial>` (Java relay, #9) | `gnirehtet run <serial>` (native relay; Java fallback if #9 says so) |
| `stale_relay_pids(port)` / `kill(pid)` / `kill_tree(pid)` | `netstat -ano` + PowerShell command line + `taskkill` (`kill_tree` adds `/T`) | `ss -ltnp` (or `/proc/net/tcp` + `/proc/<pid>/cmdline`) + `os.kill` |
| `pc_wifi()` -> `(ssid, key, security)` | `netsh wlan show interfaces` / `show profile key=clear`; `security` is `wpa2` or `wpa3` | `nmcli` on the active Wi-Fi connection: `802-11-wireless.ssid`, `802-11-wireless-security.psk`, `802-11-wireless-security.key-mgmt` (`wpa-psk` -> `wpa2`, `sae` -> `wpa3`) |
| `single_instance(name)` | named mutex `Local\QuestTetherSingleInstance` (today's code) | a lock file in `data_dir()` (`fcntl.flock`) |
| `autostart()` / `set_autostart(on)` | `HKCU\...\Run` value with `--minimized` | `~/.config/autostart/quest-tether.desktop` with `--minimized` |

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
- **CI:** `.github/workflows/check.yml` byte-compiles everything (`compileall`) and runs `tests/` on Windows and Linux, on GitHub-hosted runners (`windows-latest`, `ubuntu-latest`); push to `main` and every pull request. No Mac job, by design.

## Open-source pieces

- gnirehtet (Genymobile, Apache-2.0): the relay and headset APK; Java relay on Windows, native relay on Linux.
- Android platform-tools (`adb`): per-OS binary from Google's download, in the package.
- CustomTkinter, pystray, Pillow (already used).
- Decky Loader's plugin template (`decky-plugin-template`, `@decky/ui`, `@decky/api`) for the plugin's frontend build.

## Dev stages to 1.0

Where it stands (2026-10-28): Dev Loop, nothing past the skeleton built. Open issues #1-#21 (#12 is the goals-and-rules note, #5 is after-1.0); no pull request is open, and the one merged (#22) is CI only.

1. Clean the repo and package (#11, then #10); CI on Windows and Linux (CI done). Not started.
2. Fix the honest Connected state (#1) and add battery temperature (#4). Not started.
3. The platform layer: Windows implementation from today's code (#13), Linux implementation (#14), callers switched (#15). Done: both implementations and every caller (`adb_manager.py`, `tether_manager.py`, `net_sync.py`, `main.py`).
4. Linux relay and USB access (#16, #17); `qtetherd` (#18); the Decky plugin (#19, #20); the Linux package (#21). The #20 panel and its build config are in; the rest are stubs.
5. Device checks on both OSes: reconnect (#2, with #6's answer), soak (#3), the questions (#7, #8, #9). Need the headset; not started.
