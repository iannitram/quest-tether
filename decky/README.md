# Quest Tether Decky plugin

The Quick Access Menu panel for SteamOS Game Mode. It shows the tether state and
the headset battery temperature as a green/yellow/red dot with its value, and has
one Connect / Disconnect button. It refreshes every few seconds.

The plugin is a thin frontend: `main.py` forwards `status`, `connect` and
`disconnect` to `qtetherd` on `127.0.0.1:31417`; the service owns the tether.

## Build

Install the toolchain and build with the Decky plugin template's config:

```sh
cd decky
pnpm install
pnpm run build
```

`rollup -c` writes `dist/index.js`. To load it in Decky, put `plugin.json`,
`main.py` and `dist/` in a folder, zip that folder, and install the zip from
Decky's developer settings.

The backend needs a running `qtetherd` user service. When the service is not
running the panel says so instead of showing stale state.
