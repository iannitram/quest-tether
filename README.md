# Quest Tether

Windows desktop app that tethers a Meta Quest headset to the PC (reverse tethering over USB with gnirehtet, plus keeping the headset on the PC's Wi-Fi).

Private repository. This repo holds the code; the project's notes, bug lists and phase plans still live in the maintainer's vault for now, and work is tracked in this repo's issues.

The branch `backup/other-copy-2026-10-20` keeps the older copy of three files (`adb_manager.py`, `main.py`, `tether_manager.py`) that differed between the two places the code lived. `main` has the newer version of each.

`tools/adb.exe` (Google's Android platform-tools, 7 MB) is not stored in the repo. Download platform-tools from Google and put `adb.exe` in `tools/` before building; the two small `AdbWin*.dll` files beside it are in the repo.
