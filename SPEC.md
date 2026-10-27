# Spec

**1.0 means:** on a Windows PC and on a Linux machine (SteamOS included, in Desktop Mode and from a Decky plugin in Game Mode), I plug in the Quest, the tether comes up and says honestly whether traffic is on the tunnel, it reconnects by itself after an unplug or a headset sleep, it holds for hours, the dashboard shows the headset's battery temperature in green, yellow or red, and I install it through the launcher with no tool binaries in git.

**Approved by the owner:** 2026-10-26, the owner, in the Project Manager session's question tool: "Same + battery temp + Linux + decky plugin" (typed answer; "Same" = the reliable-tether 1.0: #1, #2, #3, #10, #11, with #6-#9 investigated as part of it; plus battery temperature #4, Linux support and a Decky plugin; charge control #5 after 1.0).

A project is Spec'd only when the line above has a date and a link. Change a Must or a Won't after approval and the approval line goes back to "not yet" until the owner approves again.

## Musts

Checks that need the headset name `needs-owner-device` in their issue: CI cannot plug in a Quest.

| # | Must | Done looks like | Check |
|---|---|---|---|
| M1 | WHEN the Quest is plugged in and I press Connect (or auto-connect is on), THE APP SHALL bring up the tunnel and show Connected only once traffic is on `tun0`, never while the headset is merely on Wi-Fi. | With Wi-Fi on and the tunnel not started, the state is not Connected; once `tun0` carries traffic, it is. | #1's check, on Windows and Linux. |
| M2 | WHEN the USB cable is unplugged and replugged, or the headset sleeps and wakes, THE APP SHALL restore the tunnel without a headset reboot or a click, and quitting from the tray SHALL leave no relay behind. | Time to recover, reconnect count on the dashboard, no manual action. | #2's three cases, on Windows and Linux (needs-owner-device), using #6's answer. |
| M3 | WHILE connected for a multi-hour session of real use, THE APP SHALL keep throughput steady, its own and the relay's memory flat, and raise no spurious reconnects. | A soak log: start/end, peak and average throughput, reconnect count, memory at start and end, no orphaned processes after. | #3's soak, on Windows and Linux (needs-owner-device). |
| M4 | THE dashboard SHALL show throughput, uptime, battery level, reconnect count, and the headset battery temperature coloured green (below 38 C), yellow (38-42 C) or red (above 42 C), read on the existing battery poll. | The temperature and its colour on the dashboard; still one `dumpsys battery` call per poll. | #4's check. |
| M5 | WHEN I press Disconnect, THE APP SHALL stop the VPN on the headset first, wait for its Wi-Fi to come back, then stop the relay. | The headset is back on Wi-Fi with working DNS; no relay process left. | Disconnect on Windows and Linux. |
| M6 | ON the desktop (Windows, and Linux including SteamOS Desktop Mode), THE APP SHALL run as a window with a tray icon (red idle, green connected; Show / Disconnect / Quit), and can start minimized at login. | The tray icon and menu on both OSes; login start on both. | Start, minimize, log in again, on each OS. |
| M7 | ON Linux, THE APP SHALL use Linux tools: the Linux `adb`, the Linux relay, Wi-Fi sync through NetworkManager instead of `netsh`, and USB access to the headset without running as root. The Linux package installs the udev rule for this itself, with one sudo prompt the player sees, and its uninstall removes the rule (the owner's ruling, 2026-10-26, question tool: "Install with the package"). | The same connect/disconnect/reconnect flow as on Windows. | M1, M2, M5 on a SteamOS machine (needs-owner-device). |
| M8 | IN SteamOS Game Mode, a Decky plugin in the Quick Access Menu SHALL show the tether state and the battery temperature colour, and offer Connect / Disconnect, driving the same tether as the desktop app. | The panel in Game Mode changes to Connected when the tether is up, and Disconnect from it works. | Connect and disconnect from the plugin in Game Mode (needs-owner-device). |
| M9 | THE APP SHALL ship through the launcher for Windows and Linux (the Decky plugin with it), with `adb`, the relay and the headset APK supplied by the package, never stored in git, and SHALL say on first run which tool is missing. | No tool binary in the repo tree; a package per OS; a missing tool is named on screen. | #10 and #11's checks; the launcher installs it on each OS. |
| M10 | The open questions SHALL be answered and written down before 1.0: what sleep does to the headset VPN (#6), whether the tether disturbs a USB audio chain (#7), why Windows audio output and display layout revert on connect (#8), why the Rust relay carries no traffic on Windows (#9). | Each question issue closed with the measured answer. | The issues. |

## Won'ts

- Charge control while tethered (#5): software can't do it; a data-only adapter or firmware limit is a hardware matter, after 1.0.
- macOS: the app is Windows and Linux only.
- A sync engine or anything beyond reverse tethering (no Wi-Fi hotspot, no Virtual Desktop control).
- Root on the headset.

## Problem

The Quest has no wired internet. Tethering it to the PC over USB means running gnirehtet and adb by hand, and the link drops when the cable moves or the headset sleeps. In a warm room the headset throttles without warning.

## Solution

One app that brings the tether up by itself, heals it after unplugs and sleeps, shows the temperature before throttling hits, and runs the same way on the Windows PC, the Linux desktop and in SteamOS Game Mode.

## User stories

1. As the owner, I want the headset online over the cable without typing commands, so that I use it like any wired device.
2. As the owner, I want the tether to come back by itself after I unplug or the headset sleeps, so that I don't babysit it.
3. As the owner, I want to see the battery temperature colour, so that I lower bitrates before lag starts.
4. As the owner on a SteamOS handheld, I want Connect in the Quick Access Menu, so that I never leave Game Mode.

## Implementation decisions

- One tether core (device detection, relay process, health monitor, reconnect, stats), shared by every front end; everything that differs by OS sits behind one platform layer (`docs/code-map.md`).
- Windows keeps the Java relay (#9's measured result). Linux uses the native `gnirehtet` relay binary, the upstream's home platform, so SteamOS needs no Java runtime; if it fails there too, Linux falls back to the Java relay (#9 records which).
- On Linux one small user service owns the tether, and both the desktop window and the Decky plugin are clients of it, so the two can never start two relays. On Windows the window runs the core in-process, as today.
- Tool binaries come with the per-OS package from the launcher.

## Testing decisions

- Pure logic (state machine decisions, stats parsing, temperature zones, platform selection) gets unit tests that run in CI on Windows and Linux without a headset.
- Everything with a headset is a repeatable manual check in its issue, marked needs-owner-device.

## Further notes

- Measured so far (Windows, Quest 2): 16.7 MB/s through the Java relay, matching stock gnirehtet; clean cold start and disconnect verified (`docs/notes/phase-2-testing-validation.md`).
