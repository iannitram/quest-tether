# Phase 3: thermal management (blocked on hardware)

Objective: show the Quest's temperature and manage charging so long VR sessions in warm places do not thermally throttle.

## State

Blocked on a hardware solution. The ADB software route for disabling charging does not work: `dumpsys battery set ac 0` only changes what Android reports, not the charging IC, and there is no root on a Quest 2.

## Problem

The Quest 2 has no active cooling. In a warm space (77-81 F), USB charging plus Virtual Desktop decoding pushes battery temperature above 40 C and triggers Snapdragon XR2 throttling. At 44-47 C a decode backlog causes 1-2 s of input lag. This is a hardware limit, not a software bug.

## Feature 1: temperature display (issue #4)

Read `adb shell "dumpsys battery"`, field `temperature` (value / 10 = C), polled at the battery-level interval (about 2 minutes). Color zones:

- Green, below 38 C: comfortable, max bitrates fine.
- Yellow, 38-42 C: warm, monitor, consider lower Virtual Desktop bitrates.
- Red, above 42 C: throttle zone, lag imminent or active.

## Feature 2: charge disable (issue #5)

A way to stop USB charging while keeping data alive would be the highest-impact intervention.

- ADB approach failed (tested 2026-04-12): `dumpsys battery set ac 0` and `set usb 0` change reporting only; sysfs charger controls are permission-denied without root.
- Proposed solution: a USB-C data-only adapter that physically blocks the VBUS power pin (about $8). The Quest runs on battery and stays cooler; tether data is unaffected. Still to be sourced and verified.
- Alternative: Meta may have added a firmware charge limiter for Link in recent Quest updates; check Settings > Device > Battery while connected.

## Thermal map (measured 2026-04-12)

| Battery temp | Status | Virtual Desktop behavior |
|---|---|---|
| below 38 C | Comfortable | Full speed, max bitrates fine |
| 38-42 C | Warm | Probably fine, monitor |
| 42.5 C | Edge | Held at reduced bitrates (60/80 Mbps) |
| 44.5 C | Throttling | 1-2 s lag at max bitrates |
| 47.0 C | Cooked | Lag even at reduced bitrates |

Settings that worked in a warm space: Desktop 50-60 Mbps, VR 80 Mbps.

## Decision

Hardware over software: without root there is no programmatic control of the charging IC, so a physical data-only adapter is the only reliable charge control. The temperature display can still be done in software.
