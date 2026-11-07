# Quest Tether

Desktop app for Windows and Linux (SteamOS included, with a Decky plugin for Game Mode) that tethers a Meta Quest headset to the PC: reverse tethering over USB with gnirehtet, plus keeping the headset on the PC's Wi-Fi. A `quest-tether` command line drives the same features.

This repo holds the code and the project notes (setup guide, bug log, phase plans, resolved questions) under `docs/notes/`; work is tracked in this repo's issues.

The branch `backup/other-copy-2026-10-20` keeps the older copy of three files (`adb_manager.py`, `main.py`, `tether_manager.py`) that differed between the two places the code lived. `main` has the newer version of each.

No tool binaries (adb, the relay, the headset APK) are stored in the repo. `tools/README.md` lists each one per OS and where to download it; put them in `tools/` before running or building. Linux setup: the udev rule is in `docs/linux.md`; `packaging/linux/install.sh` installs the `qtetherd` user service (`quest-tether.service`). Code map and roadmap: `docs/code-map.md`.

## Command line

Run `python cli.py <command>`. On Linux, `packaging/linux/install.sh` installs the packaged `quest-tether` and links it at `~/.local/bin/quest-tether`; `quest-tether gui` opens the window. Every
command prints text by default, or one JSON object with `--json`; exit codes are
0 on success, 1 on failure and 2 on bad usage. When `qtetherd` answers on
127.0.0.1:31417 the command goes through the service; otherwise it runs the
tether in-process.

| Command | Does |
|---|---|
| `status` | State, throughput, uptime, battery level and temperature zone, reconnect count, serial. |
| `connect [--hold]` | Bring the tether up; `--hold` prints status until Ctrl+C when no service is running. |
| `disconnect` | Bring the tether down. |
| `doctor` | Check adb, gnirehtet, the APK, Java on Windows, the udev rule on Linux, the device and the service. |
| `install-apk` | Push the gnirehtet client APK to the headset. |
| `wifi-sync` | Copy the PC's Wi-Fi network to the headset. |
| `settings get` | Show every persisted setting (auto reconnect, Wi-Fi mode, crash reports, autostart). |
| `settings set <key> <value>` | Change one setting. |
| `logs [--relay] [-n N]` | Tail the app log, or `relay.log` with `--relay`. |
| `crash-queue [--flush]` | Show queued crash reports, or retry them with `--flush`. |
| `service status\|start\|stop` | Manage the Linux systemd user service. |
| `gui` | Start the desktop window. |
| `mcp` | Run the MCP server over stdio for Claude Code (see below). Takes no `--json`. |

## Claude Code (MCP server)

`quest-tether mcp` starts a Model Context Protocol server on stdin and stdout. It opens no network port. It drives the same commands as the CLI, through the same service or in-process path.

- Read tools run at once: `status`, `doctor`, `logs`, `crash_queue`, `settings_get`, `service_status`.
- Write tools change the tether or the headset: `connect`, `disconnect`, `wifi_sync`, `install_apk`, `crash_queue_flush`, `settings_set`, `service`. Each one refuses unless the call passes `confirm: true`.
- Without the service, a `connect` lasts only as long as the MCP server runs, because the tether lives in that process. The server brings it down when it exits (stdin closed or a stop signal).

On Linux, `packaging/linux/install.sh` registers the server with Claude Code for your user when `claude` is on PATH, using the full path to the installed `quest-tether`. Check it with `claude mcp list`. `uninstall.sh` removes the registration. Without `claude`, install skips this step quietly.

On Windows, once the console `quest-tether.exe` is packaged (#74), register it by hand, using the folder where it sits:

    claude mcp add --scope user quest-tether -- "<folder>\quest-tether.exe" mcp

The server is a small stdlib loop, not the `mcp` Python package. It needs only four protocol methods, and the package would add pydantic and anyio, which makes the PyInstaller build harder.
