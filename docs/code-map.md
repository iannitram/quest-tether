# Code map

What each part owns, who calls whom, and how a Windows-only app becomes Windows + Linux + a Decky plugin (SPEC M6-M9), with a command line on top. Describes commit: `f86f36e` (2026-11-05). Checked against `main` on 2026-11-07: every module in the repo root, `qt_platform/` and `decky/` is listed below; 647 unit tests pass on Linux with the MCP server (#75). Spec: [../SPEC.md](../SPEC.md). Background and measured facts: [notes/](notes/).

## Layout

"Wired" means the running app (window, `qtetherd`, CLI) calls it; "not wired yet" means it is merged and tested but nothing calls it, and the issue named wires it in.

| Path | Owns | State | Musts |
|---|---|---|---|
| `adb_manager.py` | `ADBManager`: find adb, list devices, find the Quest, device details, start/stop the gnirehtet client on the headset (an `Error` line in the `am start` reply, read by `am_result`, means the intent was refused, #63; the headset is woken with `keyevent 224` before the start and stop intents, #64), VPN up/down, the VPN agent's state from `dumpsys` (diagnostic, #64), `tun0` stats, Wi-Fi toggle, USB monitor loop. | wired | M1, M2, M5 |
| `tether_manager.py` | `TetherManager` + `TetherState` + `TetherStats` + `temperature_zone`: the relay process, connect/disconnect, health monitor (heal decisions from `heal.Healer`), auto-reconnect (`reconnect_count` counts outages, not attempts, #79), split relay mode (#66: `relay -p` alone, a port wait, a port held by another program is refused and never killed, then the client start; `QT_RELAY_MODE=run` keeps `gnirehtet run` for A/B), stats, battery level and temperature on one `dumpsys battery` poll (#4), Connected only once `tun0` is up (#1), the headset Wi-Fi policy. Disconnect stops the VPN on the headset first and waits up to 6 s for `tun0` to go (force-stop on timeout), then Wi-Fi, reverse tunnels and the relay, in that order, with the relay killed even on failure (#64). `force_kill` (quit) sends a bounded STOP first. The tether core. | wired (OS calls go through `qt_platform.current()`) | M1-M5 |
| `heal.py` | `Healer`: the pure heal ladder plus two breakers, the flap breaker and the reconnect cap (#46). `now` is passed in, nothing sleeps; each `observe()` returns a `Heal`: `NONE`, `RESTART_CLIENT`, `RESTART_ADB_THEN_CLIENT`, `FULL_RECONNECT` or `GIVE_UP`, and `trip_reason` (`"flap"` or `"reconnects"`) says which breaker tripped. | wired (`tether_manager`) | M2 |
| `heal_policy.py` | Pure awake-gated liveness policy: wakefulness parse, restart backoff, `Liveness` transitions (#65). | not wired yet (#65) | M2 |
| `relay_log.py` | Pure parser: relay "Client #N connected/disconnected" lines and the connected-client set (#62). | not wired yet (#62) | M1 |
| `vpn_state.py` | Pure parser: the gnirehtet VPN agent's state from `dumpsys connectivity`. `adb_manager.vpn_agent_state` reads it; the disconnect log uses it when `tun0` outlives the STOP (#64). The poll itself checks `tun0`. | wired (`adb_manager`, diagnostic only) | M5 |
| `am_result.py` | Pure parser: the first `Error` line of an `am start` reply (#63). | wired (`adb_manager`) | M1 |
| `net_sync.py` | Copy the PC's Wi-Fi network to the headset: SSID, key and security from `qt_platform`, then `cmd wifi` on the headset. | wired | M5, M7 |
| `first_run.py` | Pure: which tool (adb, relay, APK, Java on Windows) is missing, by name (#40). | CLI `doctor` only; the window's first-run check still has its own "ADB: Not Found" text (#10) | M9 |
| `crash_scrub.py`, `crash_report.py`, `crash_queue.py`, `crash_hook.py` | Crash reporting (#30, #41-#45): scrub private details, build the report and zip, rate limit (5 a day) and offline queue (10), one sink for uncaught exceptions on any thread and in Tk, background upload to the bug drop; on/off setting. The drop address is written into `bug_drop_config.py` at build time from `CRAFT_BUG_DROP_BASE` / `CRAFT_BUG_DROP_TOKEN` (never committed). | wired (`main.py`, `qtetherd.py`, CLI `crash-queue`) | - |
| `soak_report.py` | Pure parts: counts from an app log (connects, drops, reconnects, refusals, disconnects, unrecovered drops, first and last timestamp), a throughput accumulator (peak and integer average, empty gives 0), `mem_used_kb` from `/proc/meminfo`, the `ps` parser, the orphan count (relay processes not under a running app), and the record and report builders. The sampler (`run_sampler`, #3) polls `qtetherd` status and `ps` every interval and writes JSON lines; `report` summarizes them; Ctrl+C stops it and keeps the samples taken so far. Windows has no process lister yet, so its RSS and orphan figures read 0. Command line: `python3 soak_report.py sample --minutes 15 --out <file>` and `python3 soak_report.py report <file> [--log <app log>]`. Not in the `quest-tether` CLI. | sampler and report built (#3); the app does not call it; the 15-minute run is device work | M3 |
| `main.py` | The desktop window (CustomTkinter), dashboard with the temperature colour, log panel, tray (pystray + Pillow), single-instance guard (through the platform layer), `--minimized`, crash hook install. On Linux it is a `qtetherd` client when the service answers; with no service it runs the core in-process. Its start-up probe is deliberately not `make_backend`'s rule: any failed probe, a slow service included, runs in-process, because the window needs a status reply to draw (owner's call, #74). Windows always runs in-process. | wired | M4, M6 |
| `qtetherd.py` | Linux user service that owns the one tether and answers JSON lines on `127.0.0.1:31417` (`status`, `connect`, `disconnect`, `wifi-sync`, `logs`, `settings-get`, `settings-set`; the relay's own port is 31416). `serve()` builds the one `ADBManager` + `TetherManager`; a second copy on the port prints one line and exits 1. `request()` is the client call. Clients: the Linux window, the Decky backend, the CLI. | wired | M7, M8 |
| `cli.py` | The `quest-tether` argparse entry point (`python cli.py`; on Linux the packaged binary is named `quest-tether` and `install.sh` links it at `~/.local/bin/quest-tether`; the build that makes the binary is #21; `gui` opens the window; `mcp` starts the MCP server over stdio, #75): one subcommand per feature, `--json` on every command except `mcp`, exit codes 0/1/2 (#74, #88). | Linux link in `install.sh` (#74); binary build open (#21), device check open, Windows EXE open (#74) | - |
| `cli_commands.py` | The command layer: functions returning dicts, one per CLI command. `make_backend` picks qtetherd (`ServiceBackend`) when it answers, else the in-process `TetherManager` (`LocalBackend`). Only a refused, reset, silent or timed-out probe falls back; any other socket error or a non-JSON answer keeps the service path and reports the error (#74). Plus doctor, install-apk, crash-queue, service and gui. The MCP server (#75) reuses it. | merged | - |
| `cli_format.py` | Pure text for the CLI: rates, uptime, status block, exit codes (#81). | wired (`cli.py`) | - |
| `mcp_server.py` | The MCP server (#75): `quest-tether mcp`, a local stdio server with a stdlib JSON-RPC 2.0 loop (protocol 2025-06-18: `initialize`, `ping`, `tools/list`, `tools/call`; no `mcp` package). Its tools mirror `cli_commands`: read tools `status`, `doctor`, `logs`, `crash_queue`, `settings_get`, `service_status`; write tools `connect`, `disconnect`, `wifi_sync`, `install_apk`, `crash_queue_flush`, `settings_set`, `service` (start or stop), each refused unless `confirm` is true. Asks `make_backend` on every call; keeps one in-process backend so a `connect` survives into the next call, and brings that tether down when the server exits (stdin closed, SIGTERM, SIGHUP, Ctrl+C, closed stdout). `gui` is not exposed. stdout carries only protocol lines. | wired (`cli.py` `mcp`); the device checks (`claude mcp list`, a status call from Claude Code) are open (#75) | - |
| `qt_platform/` | The platform layer: everything that differs by OS behind one interface (`base.py`: `Platform`), with `windows.py` (`WindowsPlatform`) and `linux.py` (`LinuxPlatform`), plus Linux pure helpers `linux_parse.py` (nmcli, `ss`, `/proc` cmdline) and `linux_paths.py` (XDG paths, relay commands, autostart entry). `current()` in `__init__.py` picks one (Windows, Linux, else `RuntimeError`). Every caller uses it (#13, #14 children #31-#34, #15). | wired on both OSes | M6, M7 |
| `decky/` | The Decky Loader plugin: `plugin.json`, the backend `main.py` (forwards `status`, `connect`, `disconnect` to `qtetherd`; `service-down` when it gets no answer, #19) and the Quick Access panel `src/index.tsx` (state, temperature dot, Connect / Disconnect, #20) with the `decky-plugin-template` build (`package.json`, `rollup.config.js`, `tsconfig.json`). | built; Game Mode check open (M8) | M8 |
| `packaging/linux/` | `install.sh` / `uninstall.sh` (one visible sudo prompt, only for the udev rule, #39; install links `~/.local/bin/quest-tether` to the packaged CLI and uninstall removes that link only when it points there, #74; with `claude` on PATH, install registers the MCP server at user scope with its full path and uninstall removes it, both quietly skipped without `claude`, #75), `quest-tether.service` (systemd user unit running `qtetherd`, #37), `51-quest-tether.rules` (udev, vendor `2833`, #17). | scripts in; nothing builds the binaries or the tar.gz they install yet (#21) | M7, M9 |
| `build.spec`, `build.bat` | PyInstaller one-file `QuestTether.exe` from `main.py` (Windows only); tools sit beside the EXE, not inside it. No CLI binary and no Linux target yet (#10, #21, #74). | Windows only | M9 |
| `tools/` | Third-party binaries at run time (adb, relay, headset APK), supplied by the package, never committed (#11). Only `tools/README.md` is tracked. | done (#11) | M9 |
| `tests/` | Unit tests that need no headset (44 `test_*.py` files plus 3 text fixtures in `tests/fixtures/`: platform, parsers, heal, connect flow, qtetherd, CLI, the MCP server over stdio, crash reporting, Decky backend, install scripts, temperature zones). CI runs `python -m unittest discover -s tests`. | present | all |
| `docs/proposals/` | The 2026-11-01 session-manager and Rust/Tauri proposal, for discussion only; not part of the plan (#90 asks the owner when). | parked | - |

## The platform layer (`qt_platform/base.py`)

| Function | Windows (`windows.py`) | Linux (`linux.py`) |
|---|---|---|
| `data_dir()` | `%LOCALAPPDATA%\QuestTether` (today's `LOG_DIR`) | `$XDG_STATE_HOME/quest-tether` (else `~/.local/state/quest-tether`) |
| `tool_path(name, base_dirs)` | `<name>.exe`, then `<name>`, in each caller-given dir (for adb: `tools/` next to the EXE when frozen, `tools/` and `tools/platform-tools` next to the script), else `where` | an executable `<name>` in the same dirs, else `which` |
| `relay_candidates()` | `[("gnirehtet.jar", True)]` (needs Java) | `[("gnirehtet", False)]` (native) |
| `no_window_flags()` | `subprocess.CREATE_NO_WINDOW` | `0` |
| `relay_command(tools, serial)` | `java -jar gnirehtet.jar run <serial>` (Java relay, #9) | `<tools>/gnirehtet run <serial>` (native relay only; no Java fallback yet, #16) |
| `relay_server_command(tools, port)` | `java -jar gnirehtet.jar relay -p <port>` (split mode, #66; no serial, the client is started by adb) | `<tools>/gnirehtet relay -p <port>` (split mode, #66) |
| `port_holders(port)` | every listener on the port as `(pid, is_gnirehtet)`: `netstat -ano` + PowerShell command line; an unreadable command line counts as not gnirehtet (#67) | `ss -ltnp` + `/proc/<pid>/cmdline`, same rule. Used by connect to refuse a port held by another program (#66) |
| `stale_relay_pids(port)` / `kill(pid)` / `kill_tree(pid)` | `netstat -ano` + PowerShell command line + `taskkill` (`kill_tree` adds `/T`); an unreadable command line is never treated as gnirehtet (#67) | `ss -ltnp` (no PIDs if `ss` fails), each kept only if `/proc/<pid>/cmdline` mentions gnirehtet; `kill` checks the command line again and leaves a non-gnirehtet PID alone, else `os.kill(pid, 9)`; `kill_tree` is a plain `os.kill(pid, 9)` on that one PID (no child walk); an unreadable command line is never treated as gnirehtet (#67) |
| `pc_ssid()` | `netsh wlan show interfaces` | `nmcli` active Wi-Fi connection's `802-11-wireless.ssid` (the connection name may differ, #59) |
| `pc_wifi()` -> `(ssid, key, security)` | `netsh wlan show interfaces` / `show profile key=clear`; `security` is `wpa2` or `wpa3` | `nmcli` on the active Wi-Fi connection: `802-11-wireless.ssid`, `802-11-wireless-security.psk`, `802-11-wireless-security.key-mgmt` (`wpa-psk` -> `wpa2`, `sae` -> `wpa3`) |
| `single_instance(name)` | named mutex `Local\QuestTetherSingleInstance` (today's code) | a lock file in `data_dir()` (`fcntl.flock`) |
| `uses_tether_service()` | `False` (the window runs the core) | `True` (the window is a `qtetherd` client) |
| `autostart()` / `set_autostart(on)` | `HKCU\...\Run` value with `--minimized` | `~/.config/autostart/quest-tether.desktop` with `--minimized` |
| `service(action)` | `{"ok": False, "error": "no service on Windows"}` | `status`, `start`, `stop` only: `systemctl --user start|stop quest-tether.service`, then `is-active` to set `running` (the unit that runs `qtetherd`); any other action returns an error |

Every caller uses `qt_platform.current()` for OS behaviour; the one exception is `crash_report.platform_name()`, which reads `sys.platform` itself to name the report file. Each platform also has a `name` (`windows`, `linux`), which `doctor` uses.

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
  CLI -->|no service| CoreL
  MCP[mcp_server.py: quest-tether mcp, stdio] --> CLI
  CoreW --> P[qt_platform.current()]
  CoreL --> P
  CoreW --> Sync[net_sync.py] --> P
  CoreL --> Sync
  CoreW --> Heal[heal.py]
  CoreL --> Heal
  P --> Tools[(tools/: adb, relay, APK)]
```

- On Windows nothing changes in shape: the window runs the core in-process.
- On Linux the service owns the relay, so the desktop window and Game Mode can't start two relays fighting over port 31416 (the bug the Windows mutex fixed). It runs as a systemd user unit, started at login. If `request({"cmd": "status"})` gets no answer when the window starts, the window runs the old in-process core. Once running, it takes the core over only when the port is refused (service gone, `_reprobe_service` in `main.py`); a slow or busy service leaves it a client.
- The Decky backend never runs adb or the relay itself; it only forwards status / connect / disconnect.
- The CLI talks to the same service for status, connect, disconnect, wifi-sync, logs and settings when it answers; otherwise (Windows, or no service) it runs `TetherManager` in-process (the owner's ruling, 2026-11-04). The MCP server (#75) sits on the same `cli_commands` layer, so Claude Code gets the same answers as the CLI.
- Crash reports: `crash_hook` catches uncaught exceptions in the window and the service and hands them to `crash_report`, which scrubs, limits, queues and uploads in the background.

## Mac, PC and Linux

- **Windows:** today's app. Keeps the Java relay (Rust relay carries no traffic there, #9).
- **Linux (desktop and SteamOS):** the same window and tray (Tk and pystray work on Linux; pystray needs an AppIndicator on KDE/SteamOS), native relay, NetworkManager for Wi-Fi sync, USB access through a udev rule for the headset's vendor id. The Linux package installs the rule at install time with one visible sudo prompt and removes it on uninstall (the owner's ruling, 2026-10-26: "Install with the package"); the app never asks for root after that.
- **SteamOS Game Mode:** the Decky plugin only (no window).
- **Mac:** not supported (SPEC Won'ts).
- **CI:** `.github/workflows/check.yml` byte-compiles everything (`compileall`) and runs `tests/` on Windows and Linux, on GitHub-hosted runners (`windows-latest`, `ubuntu-latest`); push to `main` and every pull request. No Mac job, by design.

## Open-source pieces

- gnirehtet (Genymobile, Apache-2.0): the relay and headset APK; Java relay on Windows, native relay on Linux.
- gnirehtet-gui (Apache-2.0): the `am start` `Error:` detection pattern in `am_result.py` and `adb_manager.py` (#63, credited in `NOTICE`), and the relay "Client #N connected/disconnected" parser in `relay_log.py` (#62, credited in a source comment and in `NOTICE` by #92).
- Android platform-tools (`adb`): per-OS binary from Google's download, in the package.
- CustomTkinter, pystray, Pillow (already used).
- flat-to-3d (the owner's repo): the heal ladder and flap breaker in `heal.py` (#46, ported from flat-to-3d#164).
- quest-vd-wired (Apache-2.0): the awake-gated liveness policy in `heal_policy.py` (#65, credited in a source comment) and the stop-VPN-first Disconnect order, the wake before the client intents and the bounded VPN-closed wait (#64, credited in source comments and in `NOTICE`).
- `soak_report.py` (#3) is our own Python: the log counter and the sampler. Its throughput accumulator and memory figure follow the shape of flat-to-3d's soak summary (`inject/src/stats.rs`, flat-to-3d#158 and #177), written fresh; no code was copied.
- Decky Loader's plugin template (`decky-plugin-template`, `@decky/ui`, `@decky/api`) for the plugin's frontend build.

## Dev stages to 1.0

Where it stands (2026-11-07): Dev Loop. The Linux platform, the service, the Decky plugin, crash reporting and the CLI are merged; the reuse helpers are merged but three are not wired in; packaging and the long device checks are what is left. Device-tested on a Quest 2 over USB (Linux, 2026-11-04): connect, status and disconnect through the service, auto-connect, duplicate connect refused, replug back about 3 s after plug-in, sleep/wake holds (#2, #6 closed). No pull request is open.

1. Repo and package. Done: tool binaries out of git (#11), CI on Windows and Linux (#25), first-run tool check as a pure function (#40), Linux install/uninstall scripts and service unit (#37, #39). Open: Windows release package and the window's first-run message (#10), the Linux build and tar.gz (#21), the Windows `quest-tether` EXE (#74; the Linux command ships through `install.sh`).
2. Honest Connected state (#1) and battery temperature (#4). Done.
3. The platform layer (#13, #14 via #31-#34, #15). Done; #14 stays open only as the parent.
4. Linux service and Game Mode. Done: `qtetherd` (#35-#38; #18 is the parent), Decky backend and panel (#19, #20), udev rule and guide (#17). Open: native relay proven on SteamOS without Java (#16), the Game Mode check of the panel (M8).
5. Reconnect and liveness. Done: heal ladder (#46), refused client starts (#63), fail-closed stale-relay check (#67), one reconnect per outage (#79). Open: through the service, an unplug runs the headset-side disconnect against a missing headset, and the replug is a fresh connect not counted as a reconnect (#86); wire in the merged helpers: relay-log client signal (#62), awake-gated liveness (#65); separate relay with a port probe (#66, built and unit-tested; the headset check is open).
6. Command line and agents (scope added by the owner, 2026-11-04). Done: CLI merged (#88). Open: try it on the headset and package it (#74). The MCP server (#75) is built and unit-tested; `claude mcp list` and a status call from Claude Code are device checks still open.
7. Device checks before 1.0. Open: the multi-hour soak on both OSes (#3; the sampler and report are in `soak_report.py`, the runs are device work), the Windows questions (needs a Windows PC with the headset): USB audio chain (#7), audio output and display layout revert (#8), Rust relay on Windows (#9).

8. Design pass (R04 in my pipeline), the last stage before 1.0: the window and the Decky panel are checked against the shared design system and the differences filed as `design-pass` issues. The CLI and the service have no UI and skip it.

Order (owner, 2026-11-07): Linux 1.0 first. The Windows parts (#7, #8, #9, #10 and the Windows halves of #3 and #74) wait until after the Linux release.

After 1.0: charge control (#5), peer-survey ideas (#68). Tracker: #12 (goals and rules).
