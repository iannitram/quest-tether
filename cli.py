"""`quest-tether` command-line entry point (#74).

    python cli.py status --json
    python cli.py connect --hold
    python cli.py settings set crash_reports off
    python cli.py service status

Every command prints human-readable text by default and one JSON object with
--json. Exit codes come from cli_format.exit_code: 0 ok, 1 failure, 2 bad usage.
"""

import argparse
import json
import sys
import time

import cli_commands
import cli_format


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="print one JSON object instead of text"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quest-tether",
        description="Control the Quest reverse tether from the command line.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser(
        "status", help="state, traffic, battery, reconnects, serial"
    )
    _add_json(status)

    connect = sub.add_parser("connect", help="bring the tether up")
    connect.add_argument(
        "--hold",
        action="store_true",
        help="without a service, print status until Ctrl+C",
    )
    _add_json(connect)

    disconnect = sub.add_parser("disconnect", help="bring the tether down")
    _add_json(disconnect)

    doctor = sub.add_parser("doctor", help="check tools, udev, device and service")
    _add_json(doctor)

    install = sub.add_parser("install-apk", help="push the gnirehtet client APK")
    _add_json(install)

    wifi = sub.add_parser("wifi-sync", help="copy the PC's Wi-Fi to the headset")
    _add_json(wifi)

    logs = sub.add_parser("logs", help="tail the app log or relay.log")
    logs.add_argument("--relay", action="store_true", help="read relay.log instead")
    logs.add_argument("-n", type=int, default=50, help="number of lines (default 50)")
    _add_json(logs)

    crash = sub.add_parser("crash-queue", help="show or retry queued crash reports")
    crash.add_argument("--flush", action="store_true", help="retry queued reports")
    _add_json(crash)

    settings = sub.add_parser("settings", help="read or change persisted settings")
    settings_sub = settings.add_subparsers(dest="settings_action", required=True)
    settings_get = settings_sub.add_parser("get", help="show every setting")
    _add_json(settings_get)
    settings_set = settings_sub.add_parser("set", help="change one setting")
    settings_set.add_argument(
        "key", help="auto_reconnect, wifi_mode, crash_reports, autostart"
    )
    settings_set.add_argument("value", help="on/off, or off/sync/leave for wifi_mode")
    _add_json(settings_set)

    service = sub.add_parser("service", help="status, start or stop the tether service")
    service_sub = service.add_subparsers(dest="service_action", required=True)
    for action in ("status", "start", "stop"):
        action_parser = service_sub.add_parser(action)
        _add_json(action_parser)

    gui = sub.add_parser("gui", help="start the desktop window")
    _add_json(gui)

    return parser


def _finish(result, args, render=None) -> int:
    """Print one result (JSON or text) and return its exit code."""
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        if render is not None:
            text = render(result)
            if text:
                print(text)
        if (
            isinstance(result, dict)
            and result.get("error")
            and result.get("ok") is False
        ):
            message = str(result["error"])
            detail = result.get("detail")
            if detail:
                message += ": " + str(detail)
            print("Error: " + message, file=sys.stderr)
    return cli_format.exit_code(result)


def _render_status(result) -> str:
    text = cli_format.format_status(result)
    serial = result.get("device_serial")
    if serial:
        text += "\nSerial:     %s" % serial
    return text


def _render_connect(result) -> str:
    if result.get("ok") is False or result.get("error"):
        return ""
    if result.get("already"):
        return "Already %s." % result["already"]
    return "Connected."


def _render_disconnect(result) -> str:
    if result.get("ok") is False or result.get("error"):
        return ""
    return "Disconnected."


def _render_wifi_sync(result) -> str:
    if result.get("ok") is False or result.get("error"):
        return ""
    return "Wi-Fi sync: %s" % (result.get("result") or "done")


def _render_doctor(result) -> str:
    lines = [
        "Quest Tether doctor",
        "Platform: %s" % (result.get("platform") or "unknown"),
    ]
    for check in result.get("checks") or []:
        mark = "ok" if check.get("ok") else "FAIL"
        lines.append(
            "  [%s] %-8s %s"
            % (mark, check.get("name") or "", check.get("detail") or "")
        )
    return "\n".join(lines)


def _render_install(result) -> str:
    lines = []
    output = (result.get("output") or "").rstrip()
    if output:
        lines.append(output)
    if result.get("ok") is not False and not result.get("error"):
        lines.append(
            "Installed the gnirehtet client on %s."
            % (result.get("serial") or "the headset")
        )
    return "\n".join(lines)


def _render_setting_value(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _render_settings(result) -> str:
    settings = result.get("settings") or {}
    if not settings:
        return ""
    return "\n".join(
        "%s: %s" % (key, _render_setting_value(settings[key]))
        for key in sorted(settings)
    )


def _render_logs(result) -> str:
    return "\n".join(result.get("lines") or [])


def _render_crash_queue(result) -> str:
    if result.get("ok") is False or result.get("error"):
        return ""
    queued = result.get("queued") or []
    lines = ["Queued crash reports:"]
    if queued:
        lines.extend("  " + name for name in queued)
    else:
        lines.append("  none")
    if "sent" in result:
        lines.append("Retried %d report(s)." % (result.get("sent") or 0))
    return "\n".join(lines)


def _render_service(result) -> str:
    if result.get("error") and not result.get("detail"):
        return ""
    running = result.get("running")
    detail = result.get("detail") or "unknown"
    if running is True:
        return "Service running: %s" % detail
    if running is False:
        return "Service not running: %s" % detail
    return "Service: %s" % detail


def _hold(backend, stream) -> int:
    """Print status once a second until Ctrl+C, then disconnect (in-process only)."""
    print("Holding the tether; press Ctrl+C to stop.", file=stream)
    try:
        while True:
            reply = backend.status()
            if isinstance(reply, dict) and reply.get("state"):
                line = "%s | down %s | up %s | uptime %s" % (
                    reply.get("state"),
                    cli_format.format_rate(reply.get("rx_bps")),
                    cli_format.format_rate(reply.get("tx_bps")),
                    cli_format.format_uptime(reply.get("uptime_s")),
                )
            else:
                line = "status unavailable"
            stream.write("\r" + line + "   ")
            stream.flush()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print(file=stream)
    result = backend.disconnect()
    code = cli_format.exit_code(result)
    if code:
        print(
            "Error disconnecting: %s" % (result.get("error") or "failed"),
            file=sys.stderr,
        )
    return code


def _run(args) -> int:
    command = args.command

    if command == "gui":
        return _finish(cli_commands.gui(), args)

    backend = cli_commands.make_backend()

    if command == "status":
        return _finish(cli_commands.status(backend), args, _render_status)
    if command == "connect":
        result = cli_commands.connect(backend)
        code = _finish(result, args, _render_connect)
        if args.hold and code == 0:
            if backend.mode == "service":
                if not args.json:
                    print("The service keeps the tether up; nothing to hold.")
                return 0
            return _hold(backend, sys.stderr if args.json else sys.stdout)
        return code
    if command == "disconnect":
        return _finish(cli_commands.disconnect(backend), args, _render_disconnect)
    if command == "doctor":
        return _finish(cli_commands.doctor(backend), args, _render_doctor)
    if command == "install-apk":
        return _finish(cli_commands.install_apk(backend), args, _render_install)
    if command == "wifi-sync":
        return _finish(cli_commands.wifi_sync(backend), args, _render_wifi_sync)
    if command == "logs":
        return _finish(
            cli_commands.logs(backend, relay=args.relay, n=args.n),
            args,
            _render_logs,
        )
    if command == "crash-queue":
        return _finish(
            cli_commands.crash_queue(backend, flush=args.flush),
            args,
            _render_crash_queue,
        )
    if command == "settings":
        if args.settings_action == "get":
            result = cli_commands.settings_get(backend)
        else:
            result = cli_commands.settings_set(backend, args.key, args.value)
        return _finish(result, args, _render_settings)
    if command == "service":
        return _finish(
            cli_commands.service(backend, args.service_action), args, _render_service
        )
    return 2


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return _run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        result = {"ok": False, "error": str(exc) or exc.__class__.__name__}
        if getattr(args, "json", False):
            print(json.dumps(result, sort_keys=True))
        else:
            print("Error: " + result["error"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
