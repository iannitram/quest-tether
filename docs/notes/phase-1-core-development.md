# Phase 1: core development (complete)

Objective: a Windows GUI wrapping gnirehtet for Quest 2 USB reverse tethering, packaged as a standalone EXE with a system tray mode.

## State

Complete. The app ran from a fixed install folder with `tools/` beside it, started minimized at boot via a registry startup entry, and measured 16.7 MB/s, matching stock gnirehtet.

## What was built

- Python and CustomTkinter dark-themed GUI.
- Wraps `gnirehtet run` as one managed process (I abandoned the earlier decomposed relay + tunnel + client approach in session 4).
- Three source files: `adb_manager.py` (device detection and commands), `tether_manager.py` (managed gnirehtet process, reconnect state machine), `main.py` (GUI).
- Auto-connect on boot; auto-reconnect (10 attempts, 2 s delay).
- Graceful disconnect: stop the VPN on the Quest first, wait for Wi-Fi to restore, then tear down the relay.
- Tray mode with pystray and Pillow. Window close and minimize hide to the tray. The tray icon changes color (red idle, green connected). Right-click menu: Show / Disconnect / Quit. Tooltip: state, throughput, uptime.
- Custom icon (`assets/icon.ico`): a bold "Q" on a navy rounded rectangle, accent color changes with state.
- Standalone EXE with PyInstaller onefile, 18.5 MB.
- An atexit safety net for relay cleanup.

## Work log

- 2026-04-06: project created, V1 prototype.
- 2026-04-07: prototype verified, testing plan written.
- Session 4 (2026-04-10): architecture change to wrapping `gnirehtet run`; code audit removed all Rust relay references. Throughput measurement was rewritten from ADB polling to Windows `GetProcessIoCounters`, then reverted to ADB `cat /proc/net/dev` because that Win32 API does not see Winsock I/O.
- Session 5 (2026-04-10): tray mode, icon, EXE packaging, UI polish (680x720 down to 420x560), dead-code cleanup (230 lines removed).

## Decisions

- **Java relay over Rust.** The Rust relay starts but carries no traffic on Windows; Java works immediately. Java is permanent and Rust references were removed.
- **Wrap `gnirehtet run`, not a decomposed relay.** The decomposed version was 706 lines and fragile. Wrapping the stock command that works from a terminal is about 350 lines and matches stock behavior.
- **Tools are not bundled inside the EXE.** They live in a fixed folder beside it so Windows Firewall rules for `adb.exe` persist across updates; PyInstaller's temp extraction would change the path on every launch.
- **Throughput from ADB `cat /proc/net/dev`.** `GetProcessIoCounters` counts file I/O only, and Java NIO uses Winsock. `tun0` stats on a 5 s interval are the reliable source.
