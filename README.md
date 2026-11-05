# Quest Tether

Windows desktop app that tethers a Meta Quest headset to the PC (reverse tethering over USB with gnirehtet, plus keeping the headset on the PC's Wi-Fi).

Private repository. This repo holds the code and the project notes (setup guide, bug log, phase plans, resolved questions) under `docs/notes/`; work is tracked in this repo's issues.

The branch `backup/other-copy-2026-10-20` keeps the older copy of three files (`adb_manager.py`, `main.py`, `tether_manager.py`) that differed between the two places the code lived. `main` has the newer version of each.

`tools/adb.exe` (Google's Android platform-tools, 7 MB) is not stored in the repo. Download platform-tools from Google and put `adb.exe` in `tools/` before building; all tools come with the package and `tools/README.md` lists them.

## Command line

Run `python cli.py <command>` (the packaged binary is `quest-tether`). Every
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
