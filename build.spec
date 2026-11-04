# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Quest Tether
# Run: pyinstaller build.spec

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

a = Analysis(
    ['main.py'],
    pathex=[_SPEC_DIR],
    binaries=[],
    datas=[
        # Include customtkinter theme files
        (os.path.join(os.path.dirname(customtkinter.__file__)), 'customtkinter'),
        # tools/ is NOT bundled — lives next to the exe at deploy location.
        # This keeps adb.exe at a fixed path so Windows Firewall rules persist.
        # Include assets
        ('assets', 'assets'),
    ],
    hiddenimports=['customtkinter', 'pystray', 'pystray._win32', 'PIL', 'bug_drop_config'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='QuestTether',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if os.path.exists('assets/icon.ico') else None,
)
