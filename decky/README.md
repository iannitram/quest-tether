# Quest Tether — Decky plugin

A [Decky Loader](https://decky.xyz/) plugin that controls the Quest Tether
reverse tether from **Game Mode**, via a Quick Access Menu panel — so you can
toggle the tether and watch throughput without leaving your game or an active
Steam Link stream.

It's a thin frontend over the [`questtether`](../questtether) engine (the same
one the desktop GUI uses); this plugin does not reimplement any tether logic.

## What it shows

- **Status** — Not Connected / Starting / Connected / Reconnecting / Error
- **Tether** toggle — connect / disconnect
- **Auto-connect** toggle — tether automatically when the Quest is plugged in
- Live **Down / Up / Uptime / Total / Device / Reconnects** while connected

## Requirements

- **Decky Loader** installed on the Deck.
- The `questtether` repo checked out at `~/dev/quest-tether` (or set
  `QUESTTETHER_REPO`). The plugin imports the engine and bundled `tools/` from
  there.
- The USB rule installed once via [`../scripts/setup-udev.sh`](../scripts/setup-udev.sh).

The backend runs as the `deck` user (no `root` flag needed — the `uaccess` udev
rule grants USB access by uid), and reuses the deck user's authorized adb key.

## Install

```bash
./install.sh      # pnpm build → copy to ~/homebrew/plugins/Quest Tether → restart plugin_loader
```

Then in Game Mode: **Quick Access (•••) → Decky (plug icon) → Quest Tether**.

## Develop

```bash
pnpm install
pnpm run build          # bundles src/index.tsx → dist/index.js
./install.sh            # reinstall + reload
tail -f ~/homebrew/logs/"Quest Tether"/*.log   # backend logs
```

- **Backend** (`main.py`): `class Plugin` on Decky's asyncio loop. Blocking
  engine calls run in a thread executor; an ADB monitor thread drives
  auto-connect; a 1s loop emits `tether_status` to the frontend.
- **Frontend** (`src/index.tsx`): `@decky/ui` + `@decky/api`; `callable()`
  bindings must match the backend method names/args, and the rendered fields
  must match the `_status()` payload.
- Build config (`rollup.config.js`, `tsconfig.json`, `package.json`) is the
  stock Decky template toolchain (`@decky/rollup`).

## Known issue

The backend auto-connects whenever Decky is running (including in Desktop Mode).
The desktop GUI also auto-connects — running both at once makes them fight over
gnirehtet's port 31416. Use one at a time; a single-owner lock is planned.
