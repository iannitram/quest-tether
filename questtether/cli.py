"""
Quest Tether — command-line interface.

Headless control of the reverse tether. Useful on SteamOS for first-run
diagnostics (`doctor`), and as the engine behind the GUI / Game Mode frontends.

    quest-tether doctor      # check adb, gnirehtet, udev, and device readiness
    quest-tether status      # one-shot status of adb + any connected Quest
    quest-tether connect     # bring the tether up and hold it (Ctrl+C to stop)
    quest-tether install     # push the gnirehtet client APK to the Quest
    quest-tether gui         # launch the desktop GUI (Desktop Mode)
"""

import argparse
import glob
import os
import signal
import sys
import time
from datetime import timedelta

from . import __version__
from . import platform_support as ps
from .adb import ADBManager
from .tether import TetherManager, TetherState


# --- Formatting helpers ---

def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    if b < 1024 ** 3:
        return f"{b / (1024 ** 2):.1f} MB"
    return f"{b / (1024 ** 3):.2f} GB"


def format_rate(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 ** 2:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / (1024 ** 2):.1f} MB/s"


def format_uptime(seconds: float) -> str:
    td = timedelta(seconds=int(seconds))
    hours, remainder = divmod(td.seconds + td.days * 86400, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


_OK = "✓"
_NO = "✗"
_WARN = "!"


def _mark(ok):
    return _OK if ok else _NO


# --- Commands ---

def cmd_doctor(args) -> int:
    adb = ADBManager()
    tether = TetherManager(adb)

    print("Quest Tether doctor")
    print(f"  version   : {__version__}")
    print(f"  platform  : {sys.platform} ({'Windows' if ps.IS_WINDOWS else 'Linux' if ps.IS_LINUX else 'other'})")

    adb_ok = adb.is_adb_available()
    print(f"  [{_mark(adb_ok)}] adb        : {adb.adb_path}")

    relay_ok = tether.is_relay_available()
    print(f"  [{_mark(relay_ok)}] gnirehtet  : {tether.relay_engine}")
    print(f"  [{_mark(bool(tether._apk_path))}] client apk : {tether._apk_path or 'MISSING'}")

    # udev rule (Linux only)
    if ps.IS_LINUX:
        rule_files = []
        for d in ("/etc/udev/rules.d", "/usr/lib/udev/rules.d", "/run/udev/rules.d"):
            for f in glob.glob(os.path.join(d, "*.rules")):
                try:
                    with open(f, "r", errors="ignore") as fh:
                        if "2833" in fh.read():
                            rule_files.append(f)
                except OSError:
                    pass
        udev_ok = bool(rule_files)
        detail = rule_files[0] if rule_files else "no rule referencing Meta vendor 2833 (run scripts/setup-udev.sh)"
        print(f"  [{_mark(udev_ok)}] udev rule  : {detail}")

    # Devices
    devices = adb.get_devices()
    quest = adb.find_quest()
    print(f"  devices    : {len(devices)} connected")
    for d in devices:
        tag = "  <- Quest" if quest and d["serial"] == quest["serial"] else ""
        print(f"               {d['serial']:<20} {d['status']:<14} {d['model']}{tag}")

    print()
    if not adb_ok:
        print(f"  {_NO} adb is not runnable. Check tools/adb.")
        return 1
    if not relay_ok:
        print(f"  {_NO} gnirehtet relay engine unavailable.")
        return 1
    if not quest:
        print(f"  {_WARN} No Quest detected. Plug it in via USB and enable USB debugging.")
        return 2
    if quest["status"] == "unauthorized":
        print(f"  {_WARN} Quest is UNAUTHORIZED — put on the headset and accept the ADB prompt.")
        return 2
    if quest["status"] != "device":
        print(f"  {_WARN} Quest status is '{quest['status']}' (needs 'device'). "
              f"On Linux this usually means a missing udev rule — run scripts/setup-udev.sh.")
        return 2
    print(f"  {_OK} Ready to tether: {quest['model']} ({quest['serial']})")
    return 0


def cmd_status(args) -> int:
    adb = ADBManager()
    if not adb.is_adb_available():
        print("adb: not available")
        return 1
    quest = adb.find_quest()
    if not quest:
        print("No Quest connected.")
        return 2
    print(f"{quest['model']} ({quest['serial']}) — status: {quest['status']}")
    if quest["status"] == "device":
        details = adb.get_device_details(quest["serial"])
        if details.get("battery_level"):
            print(f"  battery : {details['battery_level']} {details.get('battery_status', '')}".rstrip())
        print(f"  tun0    : {'up (' + details['vpn_ip'] + ')' if details.get('vpn_ip') else 'down'}")
    return 0


def cmd_install(args) -> int:
    adb = ADBManager()
    tether = TetherManager(adb)
    quest = adb.find_quest()
    if not quest or quest["status"] != "device":
        print("No authorized Quest connected.")
        return 1
    cmd = ([tether._java_path, "-jar", tether._relay_path] if tether._relay_kind == "jar"
           else [tether._relay_path]) + ["install", quest["serial"]]
    print(f"Installing gnirehtet client on {quest['serial']}...")
    result = ps.run(cmd, timeout=120, env=tether._relay_env())
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    return result.returncode


def cmd_connect(args) -> int:
    adb = ADBManager()
    tether = TetherManager(adb)
    if args.verbose:
        tether.relay_output = None  # let gnirehtet log to this terminal

    tether.set_callbacks(on_log=lambda m: print(f"  · {m}", flush=True))

    if not adb.is_adb_available():
        print("adb not available — see `quest-tether doctor`.")
        return 1
    if not tether.is_relay_available():
        print("gnirehtet relay engine not available — see `quest-tether doctor`.")
        return 1

    quest = adb.find_quest()
    if not quest:
        print("No Quest found. Plug it in via USB.")
        return 2
    if quest["status"] == "unauthorized":
        print("Quest is unauthorized — accept the ADB prompt in the headset, then retry.")
        return 2
    if quest["status"] != "device":
        print(f"Quest status '{quest['status']}' (needs 'device'). Try `quest-tether doctor`.")
        return 2

    serial = quest["serial"]
    tether.auto_reconnect = not args.no_reconnect

    interrupted = {"flag": False}

    def _on_sigint(signum, frame):
        if not interrupted["flag"]:
            interrupted["flag"] = True
            print("\nStopping — restoring the Quest's Wi-Fi...", flush=True)
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _on_sigint)

    try:
        ok = tether.connect(serial)
        if not ok:
            print(f"Failed to connect: {tether.stats.last_error}")
            return 1

        print(f"\nTethered: {tether.stats.device_model} ({serial}). Press Ctrl+C to stop.\n")
        while tether._running and tether.is_relay_running():
            s = tether.stats
            line = (f"\r  up {format_uptime(time.time() - (tether._start_time or time.time()))}"
                    f"  ↓ {format_rate(s.rx_rate_bps):>10}"
                    f"  ↑ {format_rate(s.tx_rate_bps):>10}"
                    f"  total ↓{format_bytes(s.rx_bytes)} ↑{format_bytes(s.tx_bytes)}"
                    f"  reconn {s.reconnect_count}   ")
            sys.stdout.write(line)
            sys.stdout.flush()
            time.sleep(1.0)
        print()
        if tether.stats.state == TetherState.ERROR:
            print(f"Tether error: {tether.stats.last_error}")
            return 1
    except KeyboardInterrupt:
        pass
    finally:
        tether.disconnect()
    return 0


def cmd_gui(args) -> int:
    from . import gui
    return gui.main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quest-tether", description="Reverse-tether a Meta Quest over USB.")
    p.add_argument("--version", action="version", version=f"quest-tether {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="check adb, gnirehtet, udev, and device readiness")
    sub.add_parser("status", help="one-shot status of adb + any connected Quest")
    sub.add_parser("install", help="push the gnirehtet client APK to the Quest")
    sub.add_parser("gui", help="launch the desktop GUI (Desktop Mode)")

    c = sub.add_parser("connect", help="bring the tether up and hold it (Ctrl+C to stop)")
    c.add_argument("-v", "--verbose", action="store_true", help="show gnirehtet's own relay logs")
    c.add_argument("--no-reconnect", action="store_true", help="do not auto-reconnect if the relay dies")

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "doctor"
    return {
        "doctor": cmd_doctor,
        "status": cmd_status,
        "install": cmd_install,
        "connect": cmd_connect,
        "gui": cmd_gui,
    }[cmd](args)


if __name__ == "__main__":
    sys.exit(main())
