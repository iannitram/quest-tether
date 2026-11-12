# Client contract

Quest Tether is a standalone app. Other apps, including Flat to 3D, are read-only
clients of this status interface; Quest Tether never depends on them.

## Calling status

Run `quest-tether status --json` (from source: `python cli.py status --json`).
Stdout contains one JSON object followed by a newline. When `qtetherd` answers,
the CLI reads the service's live status; otherwise it creates an in-process
manager. That fallback describes this new manager, so it cannot observe a
tether owned by a separate Windows desktop window or a previous CLI process.
Reading status does not connect or disconnect the headset.

Alternatively, when the service is running, open a TCP connection to
`127.0.0.1:31417`, send this UTF-8 JSON line (including its final newline), and
read one UTF-8 JSON reply line:

```json
{"cmd":"status"}
```

This is a JSON-lines TCP protocol, not HTTP. The relay uses a different port,
31416. If the service is absent, the TCP connection fails; there is no status
reply. A command failure may instead return `{"ok":false,"error":"..."}`;
that is an error reply, not the status object below.

## Status fields

Contract version: **1**. Every status object has all of these fields. JSON
`number` allows an integer or fractional value; only the fields marked nullable
use `null`. Unknown string readings use an empty string instead. Readings are
snapshots from the manager's most recent polls and may retain earlier values.

| Field | JSON type | Meaning |
|---|---|---|
| `version` | integer | Status contract version, currently 1; not the app release version. |
| `adb_path` | string or null | The manager's selected adb executable path, also passed to gnirehtet as `ADB`. Null when no path was found (the manager only has the unresolved fallback command `adb`). Reuses existing discovery; does not probe the executable again. |
| `state` | string | One of the six values below. |
| `rx_bps` | number | Receive rate from the headset's `tun0` counters, in bytes per second; 0 before a rate is available or when `tun0` is missing. |
| `tx_bps` | number | Transmit rate from the headset's `tun0` counters, in bytes per second; 0 before a rate is available or when `tun0` is missing. |
| `uptime_s` | number | Elapsed seconds since this tether session started; initially 0. |
| `battery_level` | string | Headset battery level as reported, normally with `%` (for example `81%`); empty when unavailable. |
| `reconnects` | integer | Reconnect outage count for this session, not retry attempts; initially 0. |
| `relay_clients` | integer or null | Connected clients reported by the relay this spawn. Null until its first client line; 0 means it has reported no connected clients. |
| `temp_c` | number or null | Headset battery temperature in degrees Celsius; null when no valid reading is available. |
| `zone` | string | `green` below 38 C, `yellow` from 38 through 42 C, `red` above 42 C, or `unknown` when `temp_c` is null. |
| `device_serial` | string | Selected headset's adb serial; empty before a headset is selected. |
| `device_model` | string | Headset model from adb; empty when unavailable. |
| `vpn_ip` | string | Headset's `tun0` IPv4 address; empty when unavailable. |
| `battery_status` | string | Battery status text, normally `Unknown`, `Charging`, `Discharging`, `Not Charging`, or `Full`; an unrecognised adb status code is passed through as text. Empty when unavailable. |
| `error` | string | Last tether error message; empty when none is recorded. May remain set while reconnecting. |

## State values

| Value | Meaning |
|---|---|
| `idle` | No tether session is running. |
| `starting` | Bringing up the relay and headset tunnel; traffic is not yet confirmed. |
| `connected` | The headset tunnel is up (`tun0`, plus a relay client when the relay reports clients). |
| `reconnecting` | Recovering the tether after an outage. |
| `stopping` | Disconnect is in progress. |
| `error` | The tether could not start or recover; see `error` for the reason. |

## CLI exit codes

- 0: success (including an idle status with no recorded error).
- 1: failure, including a status with a non-empty `error` string. Its JSON status
  object is still printed and can be read even when the exit code is 1.
- 2: bad usage. Argument parsing errors go to stderr and may have no JSON reply.

## Compatibility

New fields may be added without a version bump; clients must ignore unknown
fields. Existing fields and state values must not be renamed or removed without
an integer `version` bump. Incompatible changes to field types or meanings also
require a bump. Clients should check `version` before interpreting a status
object. The contract test compares the field and state tables above with the
handler and CLI output, so additions must be documented too.
