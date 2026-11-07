"""`quest-tether mcp`: a Model Context Protocol server over stdio (#75).

Claude Code and other agents start it with `quest-tether mcp` and drive the
same command layer as the CLI (cli_commands.py). The transport is JSON-RPC 2.0,
one message per line on stdin and stdout, and nothing else may reach stdout:
serve() sends stray prints to stderr.

The SDK choice: a small stdlib loop, not the `mcp` package. This server needs
initialize, ping, tools/list and tools/call only, and the SDK would add pydantic
and anyio, which makes the PyInstaller build harder.

Read tools run when called. Write tools change the tether or the headset, and
each one refuses unless the caller passes "confirm": true.
"""

import contextlib
import json
import signal
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import cli_commands
import cli_format
import crash_report
from tether_manager import ACTIVE_CONNECT_STATES, TetherState

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "quest-tether"
INSTRUCTIONS = (
    "Quest Tether controls the Quest reverse tether on this PC. Read tools "
    "report state. Write tools (connect, disconnect, wifi_sync, install_apk, "
    "settings_set, crash_queue_flush, service) do nothing unless the call "
    "passes confirm: true."
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

SETTING_KEYS = ["auto_reconnect", "wifi_mode", "crash_reports", "autostart"]
SERVICE_ACTIONS = ["start", "stop"]
LIVE_STATES = ACTIVE_CONNECT_STATES + (TetherState.RECONNECTING,)
DEFAULT_LOG_LINES = 50
MAX_LOG_LINES = 1000


class RpcError(Exception):
    """A protocol-level error: the JSON-RPC error object goes back to the caller."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: Dict[str, dict]
    fn: Callable[[Any, dict], dict]
    write: bool = False
    required: Tuple[str, ...] = ()

    def schema(self) -> dict:
        properties = dict(self.properties)
        required = list(self.required)
        if self.write:
            properties["confirm"] = {
                "type": "boolean",
                "description": "Must be true. Without it this tool refuses and changes nothing.",
            }
            required.append("confirm")
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema(),
            "annotations": {"readOnlyHint": not self.write},
        }


# ---- tool functions: (backend, validated arguments) -> cli_commands dict ----

def _status(backend, args):
    return cli_commands.status(backend)


def _doctor(backend, args):
    return cli_commands.doctor(backend)


def _logs(backend, args):
    return cli_commands.logs(
        backend,
        relay=bool(args.get("relay", False)),
        n=args.get("n", DEFAULT_LOG_LINES),
    )


def _crash_queue(backend, args):
    return cli_commands.crash_queue(backend, flush=False)


def _crash_queue_flush(backend, args):
    return cli_commands.crash_queue(backend, flush=True)


def _settings_get(backend, args):
    return cli_commands.settings_get(backend)


def _settings_set(backend, args):
    return cli_commands.settings_set(backend, args["key"], args["value"])


def _service_status(backend, args):
    return cli_commands.service(backend, "status")


def _service(backend, args):
    return cli_commands.service(backend, args["action"])


def _connect(backend, args):
    return cli_commands.connect(backend)


def _disconnect(backend, args):
    return cli_commands.disconnect(backend)


def _wifi_sync(backend, args):
    return cli_commands.wifi_sync(backend)


def _install_apk(backend, args):
    return cli_commands.install_apk(backend)


TOOL_LIST: List[Tool] = [
    Tool(
        "status",
        "Tether state, throughput, uptime, battery level and temperature, reconnect "
        "count and headset serial. Read only.",
        {},
        _status,
    ),
    Tool(
        "doctor",
        "Check the tools, adb, the relay, the udev rule (Linux), the headset and the "
        "service. Read only.",
        {},
        _doctor,
    ),
    Tool(
        "logs",
        "The last lines of the app log, or of relay.log when relay is true. Read only.",
        {
            "relay": {"type": "boolean", "description": "read relay.log instead of the app log"},
            "n": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_LOG_LINES,
                "description": "number of lines, default %d" % DEFAULT_LOG_LINES,
            },
        },
        _logs,
    ),
    Tool(
        "crash_queue",
        "Crash reports waiting to be sent. Read only.",
        {},
        _crash_queue,
    ),
    Tool(
        "settings_get",
        "Every persisted setting: auto_reconnect, wifi_mode, crash_reports, autostart. "
        "Read only.",
        {},
        _settings_get,
    ),
    Tool(
        "service_status",
        "Whether the Linux tether service (qtetherd) is running. Read only.",
        {},
        _service_status,
    ),
    Tool(
        "connect",
        "Bring the tether up. With the service running it stays up; without it, the "
        "tether lasts only while this MCP server runs. Needs confirm.",
        {},
        _connect,
        write=True,
    ),
    Tool(
        "disconnect",
        "Bring the tether down. Needs confirm.",
        {},
        _disconnect,
        write=True,
    ),
    Tool(
        "wifi_sync",
        "Copy the PC's Wi-Fi network to the headset. Needs confirm.",
        {},
        _wifi_sync,
        write=True,
    ),
    Tool(
        "install_apk",
        "Push the gnirehtet client APK to the headset. Needs confirm.",
        {},
        _install_apk,
        write=True,
    ),
    Tool(
        "crash_queue_flush",
        "Retry sending the queued crash reports. Needs confirm.",
        {},
        _crash_queue_flush,
        write=True,
    ),
    Tool(
        "settings_set",
        "Change one persisted setting: on/off (true/false) for auto_reconnect, "
        "crash_reports and autostart; off, sync or leave for wifi_mode. Needs confirm.",
        {
            "key": {"type": "string", "enum": SETTING_KEYS},
            "value": {
                "type": ["boolean", "string"],
                "description": "true/false or on/off, or off/sync/leave for wifi_mode",
            },
        },
        _settings_set,
        write=True,
        required=("key", "value"),
    ),
    Tool(
        "service",
        "Start or stop the Linux tether service (qtetherd). Needs confirm.",
        {"action": {"type": "string", "enum": SERVICE_ACTIONS}},
        _service,
        write=True,
        required=("action",),
    ),
]

TOOLS: Dict[str, Tool] = {tool.name: tool for tool in TOOL_LIST}


# ---- argument checks ----

def _type_ok(types, value) -> bool:
    for kind in types:
        if kind == "boolean" and isinstance(value, bool):
            return True
        if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if kind == "string" and isinstance(value, str):
            return True
    return False


def _check_arguments(tool: Tool, args: dict) -> None:
    """Raise RpcError(INVALID_PARAMS) unless args match the tool's input schema."""
    schema = tool.schema()
    for name in schema["required"]:
        if name not in args:
            raise RpcError(INVALID_PARAMS, "%s needs the argument %s" % (tool.name, name))
    for name, value in args.items():
        spec = schema["properties"].get(name)
        if spec is None:
            raise RpcError(INVALID_PARAMS, "%s has no argument %s" % (tool.name, name))
        types = spec.get("type", [])
        if isinstance(types, str):
            types = [types]
        if not _type_ok(types, value):
            raise RpcError(INVALID_PARAMS, "%s must be %s" % (name, " or ".join(types)))
        if "enum" in spec and value not in spec["enum"]:
            raise RpcError(
                INVALID_PARAMS, "%s must be one of %s" % (name, ", ".join(spec["enum"]))
            )
        if "minimum" in spec and value < spec["minimum"]:
            raise RpcError(INVALID_PARAMS, "%s must be at least %s" % (name, spec["minimum"]))
        if "maximum" in spec and value > spec["maximum"]:
            raise RpcError(INVALID_PARAMS, "%s must be at most %s" % (name, spec["maximum"]))


# ---- results ----

def _result(payload: dict, is_error: bool) -> dict:
    """A tools/call result: the command's dict as one JSON text block."""
    text = json.dumps(payload, sort_keys=True, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def call_tool(name, arguments, backend_factory: Optional[Callable[[], Any]] = None) -> dict:
    """Run one tool and return its tools/call result.

    Protocol problems (unknown tool, bad arguments) raise RpcError. A refused
    write, a failed command and a raised exception come back as isError results,
    so the caller can see the message. backend_factory is called once per call,
    and a write without confirm true never calls it.
    """
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        raise RpcError(INVALID_PARAMS, "unknown tool: %s" % (name,))
    args = {} if arguments is None else arguments
    if not isinstance(args, dict):
        raise RpcError(INVALID_PARAMS, "tool arguments must be an object")
    if tool.write and args.get("confirm") is not True:
        return _result(
            {
                "ok": False,
                "error": "refused",
                "detail": "%s changes the tether or the headset; call it again with "
                "confirm set to true" % tool.name,
            },
            is_error=True,
        )
    _check_arguments(tool, args)
    factory = backend_factory or cli_commands.make_backend
    try:
        backend = factory()
        result = tool.fn(backend, args)
    except Exception as exc:
        return _result({"ok": False, "error": str(exc) or exc.__class__.__name__}, True)
    if not isinstance(result, dict):
        result = {"ok": False, "error": "bad reply from %s" % tool.name}
    return _result(result, is_error=cli_format.exit_code(result) != 0)


# ---- JSON-RPC ----

def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _initialize() -> dict:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": crash_report.APP_VERSION},
        "instructions": INSTRUCTIONS,
    }


def _dispatch(method: str, params, backend_factory):
    if method == "initialize":
        return _initialize()
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [tool.describe() for tool in TOOL_LIST]}
    if method == "tools/call":
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "tools/call needs params")
        name = params.get("name")
        if not isinstance(name, str):
            raise RpcError(INVALID_PARAMS, "tools/call needs a tool name")
        return call_tool(name, params.get("arguments"), backend_factory)
    if method.startswith("notifications/"):
        return None
    raise RpcError(METHOD_NOT_FOUND, "unknown method: %s" % method)


def handle_message(message, backend_factory=None) -> Optional[dict]:
    """Answer one JSON-RPC message; None when no reply is due (a notification)."""
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "expected one JSON object per message")
    is_request = "id" in message
    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(request_id, INVALID_REQUEST, "missing method") if is_request else None
    try:
        result = _dispatch(method, message.get("params"), backend_factory)
    except RpcError as exc:
        return _error(request_id, exc.code, exc.message) if is_request else None
    except Exception as exc:
        message_text = str(exc) or exc.__class__.__name__
        return _error(request_id, INTERNAL_ERROR, message_text) if is_request else None
    if not is_request:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class _KeepInProcess:
    """Asks the factory on every call, but keeps the first in-process backend.

    The service is probed again each time, so a service that starts or stops
    during the session is noticed. An in-process backend keeps its tether in
    memory, so the same backend must serve the next call: otherwise a connect
    would be forgotten by the next status.
    """

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._local = None

    def __call__(self):
        backend = self._factory()
        if getattr(backend, "mode", None) == "service":
            return backend
        if self._local is None:
            self._local = backend
        return self._local

    def close(self) -> None:
        """Bring down a tether this process started, so no relay outlives the server.

        Only an in-process tether that is still live is touched: a relay that
        another process or the service owns is none of this server's business.
        """
        local, self._local = self._local, None
        tether = getattr(local, "_tether", None)
        if tether is None:
            return
        try:
            state = getattr(getattr(tether, "stats", None), "state", None)
            live = tether.is_relay_running() or state in LIVE_STATES
            if live:
                local.disconnect()
        except Exception:
            pass


def _stop_on_signal(signum, frame):
    raise SystemExit(0)


def _install_signal_handlers() -> Callable[[], None]:
    """Turn SIGTERM, SIGHUP and SIGINT into a clean exit; return the undo."""
    saved = []
    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            saved.append((number, signal.signal(number, _stop_on_signal)))
        except (ValueError, OSError):
            pass  # not the main thread: the caller owns the signals

    def restore():
        for number, handler in saved:
            try:
                signal.signal(number, handler)
            except (ValueError, OSError, TypeError):
                pass

    return restore


def _line_reply(line: str, backend_factory) -> Optional[dict]:
    try:
        message = json.loads(line)
    except ValueError:
        return _error(None, PARSE_ERROR, "parse error")
    if isinstance(message, list):
        return _error(None, INVALID_REQUEST, "batches are not supported")
    return handle_message(message, backend_factory)


def serve(stdin=None, stdout=None, backend_factory=None) -> int:
    """Read JSON-RPC lines from stdin and answer on stdout until stdin closes.

    On the way out (stdin closed, SIGTERM, SIGHUP, Ctrl+C or a closed stdout) a
    tether this process started in-process is brought down, so the relay does
    not outlive the server.
    """
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    picker = _KeepInProcess(backend_factory or cli_commands.make_backend)
    restore = _install_signal_handlers() if stdin is None else (lambda: None)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            for raw in source:
                line = raw.strip()
                if not line:
                    continue
                reply = _line_reply(line, picker)
                if reply is not None:
                    try:
                        sink.write(json.dumps(reply, default=str) + "\n")
                        sink.flush()
                    except (BrokenPipeError, ValueError):
                        break  # the client is gone
    finally:
        restore()
        picker.close()
    return 0
