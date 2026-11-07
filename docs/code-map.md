# Code map

What each part owns, who calls whom, and how a Windows-only app becomes Windows + Linux + a Decky plugin (SPEC M6-M9), with a command line on top. Describes commit: `f86f36e` (2026-11-05). Checked against `main` on 2026-11-07: every module in the repo root, `qt_platform/` and `decky/` is listed below; 479 unit tests pass on Linux. Spec: [../SPEC.md](../SPEC.md). Background and measured facts: [notes/](notes/).

## Layout

"Wired" means the running app (window, `qtetherd`, CLI) calls it; "not wired yet" means it is merged and tested but nothing calls it, and the issue named wires it in.

| Path | Owns | State | Musts |
|---|---|---|---|
| `adb_manager.py` | `ADBManager`: find adb, list devices, find the Quest, device details, start/stop the gnirehtet client on the headset (an `Error` line in the `am start` reply, read by `am_result`, means the intent was refused, #63), VPN up/down, `tun0` stats, Wi-Fi toggle, USB monitor loop. | wired | M1, M2, M5 |
| `tether_manager.py` | `TetherManager` + `TetherState` + `TetherStats` + `temperature_zone`: the relay process, connect/disconnect, health monitor (heal decisions from `heal.Healer`), auto-reconnect (`reconnect_count` counts outages, not attempts, #79), stats, battery level and temperature on one `dumpsys battery` poll (#4), Connected only once `tun0` is up (#1), the headset Wi-Fi policy. The tether core. | wired (OS calls go through `qt_platform.current()`) | M1-M5 |
| `heal.py` | `Healer`: the pure heal ladder and flap breaker (#46). `now` is passed in, nothing sleeps; every reading returns `None`, `RestartClient`, `RestartAdbThenClient`, `FullReconnect` or `GiveUp`. | wired (`tether_manager`) | M2 |
| `heal_policy.py` | Pure awake-gated liveness policy: wakefulness parse, restart backoff, `Liveness` transitions (#65). | not wired yet (#65) | M2 |
| `relay_log.py` | Pure parser: relay "Client #N connected/disconnected" lines and the connected-client set (#62). | not wired yet (#62) | M1 |
| `vpn_state.py` | Pure parser: the gnirehtet VPN agent's state from `dumpsys connectivity`, for a verified "VPN closed" (#64). | not wired yet (#64) | M5 |
| `am_result.py` | Pure parser: the first `Error` line of an `am start` reply (#63). | wired (`adb_manager`) | M1 |
| `net_sync.py` | Copy the PC's Wi-Fi network to the headset: SSID, key and security from `qt_platform`, then `cmd wifi` on the headset. | wired | M5, M7 |
| `first_run.py` | Pure: which tool (adb, relay, APK, Java on Windows) is missing, by name (#40). | CLI `doctor` only; the window's first-run check still has its own "ADB: Not Found" text (#10) | M9 |
| `crash_scrub.py`, `crash_report.py`, `crash_queue.py`, `crash_hook.py` | Crash reporting (#30, #41-#45): scrub private details, build the report and zip, rate limit (5 a day) and offline queue (10), one sink for uncaught exceptions on any thread and in Tk, background upload to the bug drop; on/off setting. The drop address is written into `bug_drop_config.py` at build time from `CRAFT_BUG_DROP_BASE` / `CRAFT_BUG_DROP_TOKEN` (never committed). | wired (`main.py`, `qtetherd.py`, CLI `crash-queue`) | - |
| `soak_report.py` | Pure: summary of a soak log (start, end, peak and average throughput, reconnects, memory, orphans) for #3. | used by hand for #3; no caller in the app | M3 |
| `main.py` | The desktop window (CustomTkinter), dashboard with the temperature colour, log panel, tray (pystray + Pillow), single-instance guard (through the platform layer), `--minimized`, crash hook install. On Linux it is a `qtetherd` client when the service answers; with no service it runs the core in-process. Windows always runs in-process. | wired | M4, M6 |
| `qtetherd.py` | Linux user service that owns the one tether and answers JSON lines on `127.0.0.1:31417` (`status`, `connect`, `disconnect`, `wifi-sync`, `logs`, `settings-get`, `settings-set`; the relay's own port is 31416). `serve()` builds the one `ADBManager` + `TetherManager`; a second copy on the port prints one line and exits 1. `request()` is the client call. Clients: the Linux window, the Decky backend, the CLI. | wired | M7, M8 |
| `cli.py` | The `quest-tether` argparse entry point (`python cli.py`; the packaged binary is not built yet, #74): one subcommand per feature, `--json` on every command, exit codes 0/1/2 (#74, #88). | merged; device check and packaging open (#74) | - |
| `cli_commands.py` | The command layer: functions returning dicts, one per CLI command. `make_backend` picks qtetherd (`ServiceBackend`) when it answers, else the in-process `TetherManager` (`LocalBackend`); plus doctor, install-apk, crash-queue, service and gui. The MCP server (#75) will reuse it. | merged | - |
| `cli_format.py` | Pure text for the CLI: rates, uptime, status block, exit codes (#81). | wired (`cli.py`) | - |
| `qt_platform/` | The platform layer: everything that differs by OS behind one interface (`base.py`: `Platform`), with `windows.py` (`WindowsPlatform`) and `linux.py` (`LinuxPlatform`), plus Linux pure helpers `linux_parse.py` (nmcli, `ss`, `/proc` cmdline) and `linux_paths.py` (XDG paths, relay command, autostart entry). `current()` in `__init__.py` picks one (Windows, Linux, else `RuntimeError`). Every caller uses it (#13, #14 children #31-#34, #15). | wired on both OSes | M6, M7 |
| `decky/` | The Decky Loader plugin: `plugin.json`, the backend `main.py` (forwards `status`, `connect`, `disconnect` to `qtetherd`; `service-down` when it gets no answer, #19) and the Quick Access panel `src/index.tsx` (state, temperature dot, Connect / Disconnect, #20) with the `decky-plugin-template` build (`package.json`, `rollup.config.js`, `tsconfig.json`). | built; Game Mode check open (M8) | M8 |
| `packaging/linux/` | `install.sh` / `uninstall.sh` (one visible sudo prompt, only for the udev rule, #39), `quest-tether.service` (systemd user unit running `qtetherd`, #37), `51-quest-tether.rules` (udev, vendor `2833`, #17). | scripts in; nothing builds the binaries or the tar.gz they install yet (#21) | M7, M9 |
| `build.spec`, `build.bat` | PyInstaller one-file `QuestTether.exe` from `main.py` (Windows only); tools sit beside the EXE, not inside it. No CLI binary and no Linux target yet (#10, #21, #74). | Windows only | M9 |
| `tools/` | Third-party binaries at run time (adb, relay, headset APK), supplied by the package, never committed (#11). Only `tools/README.md` is tracked. | done (#11) | M9 |
| `tests/` | Unit tests that need no headset (45 files: platform, parsers, heal, connect flow, qtetherd, CLI, crash reporting, Decky backend, install scripts, temperature zones). CI runs `python -m unittest discover -s tests`. | present | all |
| `docs/proposals/` | The 2026-11-01 session-manager and Rust/Tauri proposal, for discussion only; not part of the plan (#90 asks the owner when). | parked | - |

## The platform layer (`qt_platform/base.py`)

| Function | Windows (`windows.py`) | Linux (`linux.py`) |
|---|---|---|
| `data_dir()` | `%LOCALAPPDATA%\QuestTether` (today's `LOG_DIR`) | `$XDG_STATE_HOME/quest-tether` (else `~/.local/state/quest-tether`) |
| `tool_path(name, base_dirs)` | `adb.exe` in `tools/` next to the EXE, else `where` | `adb` in `tools/` next to the binary, else `which` |
| `relay_candidates()` | `[("gnirehtet.jar", True)]` (needs Java) | `[("gnirehtet", False)]` (native) |
| `no_window_flags()` | `subprocess.CREATE_NO_WINDOW` | `0` |
| `relay_command(tools, serial)` | `java -jar gnirehtet.jar run <serial>` (Java relay, #9) | `gnirehtet run <serial>` (native relay; Java fallback if #9 says so) |
| `stale_relay_pids(port)` / `kill(pid)` / `kill_tree(pid)` | `netstat -ano` + PowerShell command line + `taskkill` (`kill_tree` adds `/T`); an unreadable command line is never treated as gnirehtet (#67) | `ss -ltnp` (or `/proc/net/tcp` + `/proc/<pid>/cmdline`) + `os.kill`; an unreadable command line is never treated as gnirehtet (#67) |
| `pc_ssid()` | `netsh wlan show interfaces` | `nmcli` active Wi-Fi connection's `802-11-wireless.ssid` (the connection name may differ, #59) |
| `pc_wifi()` -> `(ssid, key, security)` | `netsh wlan show interfaces` / `show profile key=clear`; `security` is `wpa2` or `wpa3` | `nmcli` on the active Wi-Fi connection: `802-11-wireless.ssid`, `802-11-wireless-security.psk`, `802-11-wireless-security.key-mgmt` (`wpa-psk` -> `wpa2`, `sae` -> `wpa3`) |
| `single_instance(name)` | named mutex `Local\QuestTetherSingleInstance` (today's code) | a lock file in `data_dir()` (`fcntl.flock`) |
| `uses_tether_service()` | `False` (the window runs the core) | `True` (the window is a `qtetherd` client) |
| `autostart()` / `set_autostart(on)` | `HKCU\...\Run` value with `--minimized` | `~/.config/autostart/quest-tether.desktop` with `--minimized` |
| `service(action)` | `{"ok": False, "error": "no service on Windows"}` | `systemctl --user <action> quest-tether.service` (the unit that runs `qtetherd`) |

Every caller uses `qt_platform.current()`; no caller tests `os.name`. Each platform also has a `name` (`windows`, `linux`) used by crash reports and `doctor`.

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
  CLI[cli.py + cli_commands.py] -->|service answers| D
  CLI -->|no service| CoreW
  CoreW --> P[qt_platform.current()]
  CoreL --> P
  CoreW --> Sync[net_sync.py] --> P
  CoreL --> Sync
  CoreW --> Heal[heal.py]
  CoreL --> Heal
  P --> Tools[(tools/: adb, relay, APK)]
```

- On Windows nothing changes in shape: the window runs the core in-process.
- On Linux the service owns the relay, so the desktop window and Game Mode can't start two relays fighting over port 31416 (the bug the Windows mutex fixed). It runs as a systemd user unit, started at login. If `request({"cmd": "status"})` gets no answer, the window falls back to the old in-process core.
- The Decky backend never runs adb or the relay itself; it only forwards status / connect / disconnect.
- The CLI talks to the same service for status, connect, disconnect, wifi-sync, logs and settings when it answers; otherwise (Windows, or no service) it runs `TetherManager` in-process (the owner's ruling, 2026-11-04). The MCP server (#75) will sit on the same `cli_commands` layer.
- Crash reports: `crash_hook` catches uncaught exceptions in the window and the service and hands them to `crash_report`, which scrubs, limits, queues and uploads in the background.

## Mac, PC and Linux

- **Windows:** today's app. Keeps the Java relay (Rust relay carries no traffic there, #9).
- **Linux (desktop and SteamOS):** the same window and tray (Tk and pystray work on Linux; pystray needs an AppIndicator on KDE/SteamOS), native relay, NetworkManager for Wi-Fi sync, USB access through a udev rule for the headset's vendor id. The Linux package installs the rule at install time with one visible sudo prompt and removes it on uninstall (the owner's ruling, 2026-10-26: "Install with the package"); the app never asks for root after that.
- **SteamOS Game Mode:** the Decky plugin only (no window).
- **Mac:** not supported (SPEC Won'ts).
- **CI:** `.github/workflows/check.yml` byte-compiles everything (`compileall`) and runs `tests/` on Windows and Linux, on GitHub-hosted runners (`windows-latest`, `ubuntu-latest`); push to `main` and every pull request. No Mac job, by design.

## Open-source pieces

- gnirehtet (Genymobile, Apache-2.0): the relay and headset APK; Java relay on Windows, native relay on Linux.
- gnirehtet-gui (Apache-2.0): the `am start` `Error:` detection pattern reused by `am_result.py` and `adb_manager.py` (#63); attribution in `NOTICE`.
- Android platform-tools (`adb`): per-OS binary from Google's download, in the package.
- CustomTkinter, pystray, Pillow (already used).
- quest-vd-wired and flat-to-3d: shapes reused for the heal ladder (#46), liveness policy (#65), stop-VPN-first check (#64) and the soak summary (#3).
- Decky Loader's plugin template (`decky-plugin-template`, `@decky/ui`, `@decky/api`) for the plugin's frontend build.

## Dev stages to 1.0

Where it stands (2026-11-07): Dev Loop. The Linux platform, the service, the Decky plugin, crash reporting and the CLI are merged; the reuse helpers are merged but three are not wired in; packaging and the long device checks are what is left. Device-tested on a Quest 2 over USB (Linux, 2026-11-04): connect, status and disconnect through the service, auto-connect, duplicate connect refused, replug back about 3 s after plug-in, sleep/wake holds (#2, #6 closed). No pull request is open.

1. Repo and package. Done: tool binaries out of git (#11), CI on Windows and Linux (#25), first-run tool check as a pure function (#40), Linux install/uninstall scripts and service unit (#37, #39). Open: Windows release package and the window's first-run message (#10), the Linux build and tar.gz (#21), shipping the `quest-tether` command in both (#74).
2. Honest Connected state (#1) and battery temperature (#4). Done.
3. The platform layer (#13, #14 via #31-#34, #15). Done; #14 stays open only as the parent.
4. Linux service and Game Mode. Done: `qtetherd` (#35-#38; #18 is the parent), Decky backend and panel (#19, #20), udev rule and guide (#17). Open: native relay proven on SteamOS without Java (#16), the Game Mode check of the panel (M8).
5. Reconnect and liveness. Done: heal ladder (#46), refused client starts (#63), fail-closed stale-relay check (#67), one reconnect per outage (#79). Open: an unplug runs the headset-side disconnect against a missing headset (#86); wire in the merged helpers: relay-log client signal (#62), stop the VPN first and verify it closed (#64), awake-gated liveness (#65); separate relay with a port probe (#66).
6. Command line and agents (scope added by the owner, 2026-11-04). Done: CLI merged (#88). Open: try it on the headset and package it (#74), the MCP server on the same command layer (#75).
7. Device checks before 1.0. Open: the multi-hour soak on both OSes (#3; summary tool merged in #89), the Windows questions (needs a Windows PC with the headset): USB audio chain (#7), audio output and display layout revert (#8), Rust relay on Windows (#9).

After 1.0: charge control (#5), peer-survey ideas (#68). Trackers: #12 (goals and rules), #90 (status and next steps).
