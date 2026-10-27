# Phase 2: testing and validation (in progress)

Objective: validate the app end to end with a Quest 2: auto-reconnect, sustained use, disconnect/reconnect reliability.

## State

The core connection flow was verified over five live sessions. Seventeen bugs were found and fixed (see `bugs.md`); one is still open (issue #1). Cold-start connection works automatically, throughput is 16.7 MB/s. Auto-reconnect (issue #2) and the stability soak (issue #3) are not tested yet. Status: beta-testing.

## Test phases

- **Environment setup: passed.** Python 3.11, CustomTkinter 5.2.2, Java 25, `tools/` complete.
- **Device detection: passed.** Quest 2 detected within seconds; no USB debugging prompt was needed.
- **First connection: passed after three attempts.**
  - Attempt 1 failed: relay started and the VPN got an IP, but the connectivity-check loop failed (bug 1, ICMP). After a Quest reboot, an orphaned Java process was left behind (bug 2).
  - Attempt 2 partial: bugs 1 and 2 fixed; tunnel up and TCP check passed, but the VPN never started on the Quest (no `tun0`). The check passed over Wi-Fi (bug 4). This led to bugs 5 (wrong tunnel type) and 6 (implicit intent).
  - Attempt 3 succeeded: bugs 5 and 6 fixed via ADB during the session; `tun0` up, traffic flowing. The health monitor then triggered a false reconnect after about 6 minutes (bug 7).
- **Clean cold start: passed (session 3).** First run with all patched code came up fully automatically. The bug 7 fix held (the throughput guard prevented a false reconnect). Bug 7b was found and fixed.
- **Disconnect: passed (sessions 3-4).** `tun0` torn down, Wi-Fi restored, routing and DNS working after the bug 9 fix.
- **Auto-reconnect: not tested.** Needs USB unplug/replug and headset sleep/wake (issue #2).
- **Stability soak: not tested.** Needs a multi-hour session to check for drift, memory growth and reconnect failures (issue #3).

## Work log (2026-04-10)

- Session 2: first live test. Environment and detection passed; connection failed, found bugs 1, 2, 5, 6; fixed 1 and 2; traffic confirmed after a manual ADB fix for 5 and 6.
- Session 3: clean cold start confirmed; bugs 7 and 7b found and fixed; disconnect verified (bug 9 fix); speed issue diagnosed and resolved.
- Session 4: code audit and comparison against stock gnirehtet; bugs 10 (pipe freeze) and 12 (ADB PATH) found and fixed; architecture change to wrapping `gnirehtet run`; 16.7 MB/s confirmed.
- Session 5: bugs 11, 13, 14, 15, 16, 17 found and fixed during EXE packaging and UI polish; full flow verified by hand.

## Decision

I keep the status at beta-testing: the core works and the major bugs are fixed, but auto-reconnect and stability are untested, so it is not "complete" until the soak test passes.
