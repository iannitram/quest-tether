# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Linux package (#21). packaging/linux/build.sh runs it.
#
# Two one-file binaries, both console programs (no window of their own):
#   quest-tether  the CLI from cli.py (#74); its `gui` command opens the window
#   qtetherd      the service from qtetherd.py (the user unit runs it, #37)
# tools/ is NOT bundled: it ships beside the binaries (see tools/README.md).
# build.spec (Windows) is separate; keep the two in step by hand.

import os
import customtkinter

_SPEC_DIR = globals().get("SPECPATH") or os.getcwd()
_CONFIG_PATH = os.path.join(_SPEC_DIR, "bug_drop_config.py")
_CONFIG_BASE = os.environ.get("CRAFT_BUG_DROP_BASE")
_CONFIG_TOKEN = os.environ.get("CRAFT_BUG_DROP_TOKEN")
with open(_CONFIG_PATH, "w", encoding="utf-8") as _config:
    _config.write('"""Build-time File Drop destination: BASE and TOKEN."""\n')
    _config.write(f"BASE = {_CONFIG_BASE!r}\n")
    _config.write(f"TOKEN = {_CONFIG_TOKEN!r}\n")
if not (_CONFIG_BASE and _CONFIG_TOKEN):
    print("warning: CRAFT_BUG_DROP_BASE / CRAFT_BUG_DROP_TOKEN not set; "
          "this build will not send crash reports.")

block_cipher = None

_DATAS = [
    # customtkinter theme files, and the app's assets
    (os.path.dirname(customtkinter.__file__), 'customtkinter'),
    ('assets', 'assets'),
]
_HIDDEN = [
    'customtkinter', 'pystray', 'pystray._xorg', 'pystray._appindicator',
    'PIL', 'bug_drop_config',
]

# quest-tether: the CLI. Its `gui` command imports main, so the window's modules come with it.
cli_a = Analysis(
    ['cli.py'],
    pathex=[_SPEC_DIR],
    binaries=[],
    datas=_DATAS,
    hiddenimports=_HIDDEN + ['main'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data, cipher=block_cipher)
cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    cli_a.binaries,
    cli_a.zipfiles,
    cli_a.datas,
    [],
    name='quest-tether',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# qtetherd: the service. It does not import the window.
svc_a = Analysis(
    ['qtetherd.py'],
    pathex=[_SPEC_DIR],
    binaries=[],
    datas=[],
    hiddenimports=['bug_drop_config'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['customtkinter', 'tkinter', 'PIL', 'pystray'],
    noarchive=False,
    cipher=block_cipher,
)
svc_pyz = PYZ(svc_a.pure, svc_a.zipped_data, cipher=block_cipher)
svc_exe = EXE(
    svc_pyz,
    svc_a.scripts,
    svc_a.binaries,
    svc_a.zipfiles,
    svc_a.datas,
    [],
    name='qtetherd',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
