# Bug log

Write-ups of the bugs found while testing (all found 2026-04-10). Bug 4 is the only one still open; it is tracked as issue #1.

| # | Title | Status |
|---|-------|--------|
| 1 | Connectivity check used ICMP ping | fixed |
| 2 | Orphaned relay blocks next launch | fixed |
| 3 | VPN up but no traffic | fixed (side effect of 1) |
| 4 | Connectivity check does not verify tunnel vs Wi-Fi | open (issue #1) |
| 5 | Wrong ADB reverse tunnel type | fixed |
| 6 | `am start` needs an explicit component name | fixed |
| 7 | Health monitor too aggressive | fixed |
| 7b | Health monitor ADB polling starves bandwidth | fixed |
| 8 | Graceful disconnect VPN teardown | fixed |
| 9 | Disconnect orphans the DNS resolver | fixed |
| 10 | Relay subprocess pipe blocks Java | fixed |
| 11 | Orphaned relay survives app quit | fixed |
| 12 | ADB not on PATH for the gnirehtet subprocess | fixed |
| 13 | `subprocess.run()` flashes console windows | fixed |
| 14 | Zombie restart after tray quit | fixed |
| 15 | Tray Disconnect always greyed out | fixed |
| 16 | Minimize button kept the app in the taskbar | fixed |
| 17 | PyInstaller `_MEI` temp dir warning on quit | fixed |

## 1. Connectivity check used ICMP ping

- Symptom: the health monitor looped "Reconnect attempt 1/10..." even when the VPN had an IP.
- Root cause: `check_device_connectivity()` used `ping 8.8.8.8`, but gnirehtet relays only TCP and UDP, so ping always fails through it.
- Fix: TCP-based checks: `toybox nc` to dns.google:443, fallback `wget --spider`, last resort `curl`. The same fix went into `check_wifi_connectivity()`.

## 2. Orphaned relay blocks next launch

- Symptom: "Failed to start relay server" on the second launch after closing the app.
- Root cause: the Java relay child survived app close and held port 31416.
- Fix, three layers: `_kill_stale_relay()` before every relay start; `stop_relay()` escalates terminate, kill, `taskkill /F`; an `atexit` handler cleans up on unexpected exit.

## 3. VPN up but no traffic

- Symptom: the first connection got VPN IP 10.0.0.2 but the Quest browser could not load pages.
- Root cause: a side effect of bug 1. The reconnect loop kept tearing down and restarting the VPN client, so traffic never stabilized.
- Fix: resolved by the bug 1 fix.

## 4. Connectivity check does not verify tunnel vs Wi-Fi (open)

- Symptom: the app shows "Connected" with "Device has internet connectivity through tunnel!" but throughput is 0 B and the VPN IP is a dash. The Quest is routing over Wi-Fi.
- Root cause: `check_device_connectivity()` runs generic TCP checks from the Quest shell. With Wi-Fi active the check passes even when `tun0` does not exist. It proves the Quest has internet, not that it has internet through the tunnel.
- Proposed fix: verify `tun0` exists and carries traffic, or that the VPN interface is the active route. Quick version: add a `tun0` existence check before the TCP test.
- Priority: low. After the bug 5 and 6 fixes the tunnel comes up reliably, so the false positive is less likely, but it is still a correctness issue.

## 5. Wrong ADB reverse tunnel type (the root cause of the failed first connections)

- Symptom: the VPN never activates on the Quest; no `tun0`, no traffic, yet the app reports "Connected".
- Root cause: the app used `adb reverse tcp:31416 tcp:31416`. Stock gnirehtet uses `adb reverse localabstract:gnirehtet tcp:31416`, and the VPN client connects to the abstract socket `gnirehtet`, so the tunnel mapped the wrong thing.
- Fix: `setup_reverse_tunnel()` and `remove_reverse_tunnel()` now use `localabstract:gnirehtet`. Needed together with bug 6.

## 6. `am start` needs an explicit component name

- Symptom: even with the right tunnel, the VPN does not start; the gnirehtet process runs but `tun0` never appears.
- Root cause: after `am force-stop` or a reboot, Android marks the app `stopped=true`, and implicit intents do not resolve to a stopped app; they are dropped silently. Stock gnirehtet uses `-n com.genymobile.gnirehtet/.GnirehtetActivity`.
- Fix: `start_gnirehtet_client()` and `stop_gnirehtet_client()` use explicit component targeting. Needed together with bug 5.

## 7. Health monitor too aggressive

- Symptom: a working connection (132 KB throughput) was torn down after about 6 minutes; the log showed "Waiting for connectivity... (6/10)".
- Root cause: `_health_loop()` triggered `_attempt_reconnect()` on the first connectivity failure, with no grace period, throughput guard or failure threshold.
- Fix: require 3 consecutive failures; skip the check if rx/tx bytes are increasing; 10 s cooldown after connect. Health interval raised from 3 s to 5 s.

## 7b. Health monitor ADB polling starves USB bandwidth

- Symptom: the connection works but speed is terrible (about 2.5 KB/s); YouTube will not load.
- Root cause: `_update_stats()` ran 8+ ADB shell commands per 5 s cycle (six static `getprop` calls, `dumpsys battery`, `ip addr show tun0`), about 13 ADB commands per cycle in total. ADB is serial, so each command competes with tunnel data for USB bandwidth.
- Fix: cache static device details; refresh battery and VPN IP every 6th cycle (about 30 s); skip the connectivity check while throughput is active. One ADB call per cycle during active use instead of 13.

## 8. Graceful disconnect VPN teardown

- Symptom: after Disconnect, `tun0` stays up on the Quest with no relay behind it, and the Quest loses internet.
- Root cause: same as bug 6; the STOP intent was implicit, without `-n`.
- Fix: explicit component on the STOP intent. Verified: `tun0` torn down, the force-stop fallback worked, `adb reverse --remove-all` ran.

## 9. Disconnect orphans the DNS resolver

- Symptom: after disconnect the Quest has IP routing but no DNS; toggling Wi-Fi restores it.
- Root cause: gnirehtet was the registered DNS provider, and Android did not re-acquire DNS from DHCP after the VPN went away.
- Fix: a Wi-Fi toggle is part of the disconnect sequence. Verified: `tun0` gone, TCP to 8.8.8.8 and to a hostname both work.

## 10. Relay subprocess pipe blocks Java

- Symptom: `tun0` up with a VPN IP and the app says "Connected", but no tunnel traffic; the relay is alive but frozen.
- Root cause: the relay was started with `subprocess.Popen(stdout=PIPE, stderr=PIPE)` but the pipes were never read. The Windows pipe buffer (about 4 KB) fills when Java logs, the next write blocks the whole JVM including the NIO selector loop.
- Fix: `stdout=DEVNULL, stderr=DEVNULL`. The relay's output is discarded because the app has its own logging.

## 11. Orphaned relay survives app quit

- Symptom: after quitting via window close, the Java relay (131 MB) kept running and held port 31416.
- Root cause: (1) `_on_close()` ran a full 15 s disconnect and Windows killed the "not responding" Python mid-cleanup, while Java survived; (2) `_atexit_cleanup` referenced a dead attribute `_relay_process` instead of `_process`; (3) no process-tree kill, since `terminate()` kills only the parent.
- Fix: new `force_kill()` using `taskkill /F /T /PID`. Window close now minimizes to the tray, tray quit calls `force_kill()`, and the atexit attribute name is fixed.

## 12. ADB not on PATH for the gnirehtet subprocess

- Symptom: `gnirehtet run` could not find ADB when spawned by the app, though it worked from a terminal where ADB was on PATH.
- Root cause: ADB is bundled in `tools/`, not on the system PATH, and gnirehtet reads the `ADB` environment variable.
- Fix: set `ADB` to `self.adb.adb_path` and `GNIREHTET_APK` when spawning the process.

## 13. `subprocess.run()` flashes console windows

- Symptom: the packaged EXE spawned dozens of flickering console windows that stole focus; every ADB call, netstat and taskkill popped one.
- Root cause: only the main gnirehtet process had `CREATE_NO_WINDOW`; helper `subprocess.run()` calls used default flags.
- Fix: `_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0` in `adb_manager.py` and `tether_manager.py`, applied to all 12 `subprocess.run()` calls.

## 14. Zombie restart after tray quit

- Symptom: the app reopened itself about 15 s after quitting from the tray.
- Root cause: a shutdown race. `force_kill()` killed Java, but the still-running ADB monitor saw the device and auto-connect started a new gnirehtet before Python exited. The same race occurs when the user disconnects and then quits.
- Fix: a `_quitting` flag checked in every callback; auto-connect disabled before the kill; monitoring stopped before `force_kill()`; `_quit_app` runs `_kill_stale_process()` after `force_kill()` to sweep port 31416; `os._exit(0)` as the hard backstop.

## 15. Tray Disconnect always greyed out

- Symptom: Disconnect in the tray right-click menu was always disabled.
- Root cause: pystray's `enabled=lambda` did not evaluate correctly on Windows.
- Fix: removed the lambda; Disconnect is always clickable and does nothing when not connected.

## 16. Minimize button kept the app in the taskbar

- Symptom: only the X called `withdraw()`; minimize just iconified, leaving the app in the taskbar.
- Root cause: no event handler for minimize.
- Fix: an `<Unmap>` handler redirects minimize to `withdraw()`, so both X and minimize hide to the tray.

## 17. PyInstaller `_MEI` temp dir warning on quit

- Symptom: a modal warning about the temp directory on quit.
- Root cause: an orphan Java process held file locks on the extracted `adb.exe` and `gnirehtet.jar`, so the PyInstaller bootloader could not clean up.
- Fix: `force_kill()` waits for the process to die plus 0.5 s for Windows to release handles. Together with the bug 14 fix this removes the locked files.
