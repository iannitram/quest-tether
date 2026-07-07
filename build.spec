# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Quest Tether
# Run: pyinstaller build.spec

import os
import customtkinter

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Include customtkinter theme files
        (os.path.join(os.path.dirname(customtkinter.__file__)), 'customtkinter'),
        # tools/ is NOT bundled — lives next to the exe at deploy location.
        # This keeps adb.exe at a fixed path so Windows Firewall rules persist.
        # Include assets
        ('assets', 'assets'),
    ],
    hiddenimports=['customtkinter', 'pystray', 'pystray._win32', 'PIL'],
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
