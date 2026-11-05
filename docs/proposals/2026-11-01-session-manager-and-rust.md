# Quest Tether — proposal

Date: 2026-11-01

Status: product proposals for discussion. Rust migration requested by the owner; the detailed architecture and expanded feature scope below are recommendations, not amendments to the approved spec yet.

Review basis: GitHub main at `a72f7d5`, including SPEC.md, docs/code-map.md, desktop implementation, service and Decky scaffolding, recorded findings, and open issues. This was a source review, not a running-app or headset validation session. The code map contains older status statements; implementation and current issues take precedence.

## Direction

Turn Quest Tether into a wired VR session manager. Make the journey from plugging in the headset to working or playing dependable and understandable. Automatic recovery and battery temperature monitoring already belong to the existing plan; the three proposals below extend it.

## 1. Start my session

Replace Connect as the central experience with saved activities: Work, Play, and Custom. Each activity prepares the tether, checks required streaming software, verifies host reachability, and guides the user into the appropriate headset app.

Show a clear sequence: Headset detected → Cable ready → Host reachable → Ready to work.

First use provides instructions at the point of need, including accepting USB debugging in the headset. Later sessions remember the headset and chosen activity. Ending a session restores network settings that Quest Tether changed and explains any incomplete restoration.

Third-party software continues to provide streaming. Integrations handle preparation and launch assistance only where supported. This expands the current reverse-tethering-only scope and needs approval before implementation.

Acceptance target: after initial setup, a returning user reaches a working session through one host action, with required headset actions clearly identified.

## 2. Connection map with evidence

Replace the statistics grid as the primary screen with a live connection diagram: Computer → USB → Quest → Streaming app.

Each segment has independent evidence and state. Selecting a failed segment reveals its cause and an appropriate recovery action. Distinguish USB detected, tunnel established, traffic verified, and stream route verified. Claim the last state only when an integration provides sufficient evidence: tunnel traffic alone cannot prove that a particular streaming app avoided an internet relay. Otherwise show Stream route unknown.

Add a session timeline for sleep, recovery, throughput drops, and temperature changes. Use it to explain intermittent failures and provide useful support context.

Visual direction: quiet charcoal surfaces, a prominent connection path, readable labels, and one primary action. Keep logs in an expandable diagnostic drawer. Use text and symbols alongside state colors. Final styling should follow the project's shared design system when established.

Acceptance target: deliberately break each connection stage; the interface identifies the failed stage without showing a misleading overall green status.

## 3. Session controls inside the headset

Create a lightweight companion panel accessible through the Quest browser over the tether. Show connection health, battery temperature, session duration, and recovery progress. Offer check connection, change activity, and end session.

Begin with a browser panel and assess a dedicated headset app only if browser access interrupts the experience too much. Require pairing and limit access to the intended local connection.

The host engine handles cable loss and recovery independently. The companion cannot repair a connection it can no longer reach. Define an explicit disconnected state and recovery instructions.

Acceptance target: a user can inspect health and end a session while wearing the headset, with a clear recovery path when the panel loses contact.

## Rust migration

Rust is the requested direction for the application engine. Recommended architecture:

| Part | Direction |
| --- | --- |
| Connection engine | Rust state machine, device monitoring, recovery, telemetry, and process supervision |
| Background owner | One Rust process on both Windows and Linux; every interface controls this process |
| Desktop | Tauri interface over the Rust engine |
| Decky | Thin TypeScript interface and minimal Python bridge |
| Headset panel | Local web interface using the same versioned control API |

This is a hybrid UI architecture: Rust owns behavior; web technologies render the desktop interface. Verify Tauri's platform webview dependencies and packaging on SteamOS before committing to the desktop framework. A Rust engine rewrite does not imply rewriting the external Android client or eliminating Decky's bridge.

Keep the working Windows Java relay during the initial migration. The repository records that the Rust gnirehtet relay starts without carrying traffic on Windows; the root cause remains unresolved in issue #9. Validate replacement of that relay separately from migration of Quest Tether's supervisor. Continue using the existing external tools behind explicit adapters.

### Delivery order

1. Rust engine parity: preserve the approved behavior, establish the control API, and prove single ownership on Windows and Linux.
2. Connection map and evidence-backed states.
3. Session launcher and supported streaming integrations.
4. Headset companion controls.

Before replacing the current engine, verify USB unplug/replug, headset sleep/wake, clean disconnect with network restoration, and multi-hour stability on both supported platforms. Automated state-machine and parsing checks complement these hardware checks. macOS remains outside the approved scope.

Before satellite implementation, reconcile the Python-specific open issues with the Rust architecture so work is not implemented twice. Update the spec and code map after the expanded product direction is approved.

## References

- [Approved project spec](https://github.com/iannitram/quest-tether/blob/a72f7d5/SPEC.md)
- [Recorded relay findings](https://github.com/iannitram/quest-tether/blob/a72f7d5/docs/notes/resolved-questions.md)
- [Windows Rust relay investigation](https://github.com/iannitram/quest-tether/issues/9)
- [Tauri architecture](https://v2.tauri.app/concept/architecture/)
- [Tauri prerequisites](https://tauri.app/start/prerequisites/)
- [gnirehtet upstream](https://github.com/Genymobile/gnirehtet)
