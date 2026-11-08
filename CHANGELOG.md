# Changelog

Newest first. One line per change of stage, release or decision that the next session needs.

- 2026-11-07 Service settings persist (#117): auto-reconnect and the headset Wi-Fi mode are saved to `settings.json` in the config folder, loaded at service start (a valid `QT_WIFI_MODE` wins for Wi-Fi, then the file, then the defaults), and the window shows them at start. Linux only; the Windows in-process path is unchanged.
- 2026-11-07 The Linux package builds from the repo (#21): `packaging/linux/build.sh` makes `quest-tether` and `qtetherd` (`build-linux.spec`), the Decky zip when pnpm is present, and `dist/quest-tether-linux.tar.gz`. `install.sh` prints where the Decky zip is and does not load it (one sudo prompt, for the USB rule).
- 2026-11-07 Code map and roadmap brought up to date with main (`f86f36e`): every module listed, stage list rewritten from what is merged and what is open (#76).
- 2026-11-04 The owner's ruling: a `quest-tether` CLI (and an MCP server on the same command layer) drives every feature, through `qtetherd` when it answers, else in-process (#74, #75).
- 2026-10-26 The owner's ruling: the Linux package installs the udev rule itself, with one visible sudo prompt, and its uninstall removes it (SPEC M7, #17, #21).
- 2026-10-26 Spec approved by the owner: Windows + Linux + a Decky plugin, battery temperature (SPEC.md); Roadmap done (docs/code-map.md, platform-layer skeleton, CI, issues #13-#21); stage Dev Loop.
- 2026-10-26 Moved from Parked to Spec on the owner's word; needs a spec conversation.
- 2026-10-26 Normalized to pipeline v2 at Parked (paused).
- 2026-10-26 Project notes moved into the repo under docs/notes.
- 2026-10-20 Code imported into this repo.
