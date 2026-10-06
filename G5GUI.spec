# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Qemu-system-ppc64 G5 Openbios GUI.
#
# macOS, with the python.org framework Python (it has a working tkinter):
#
#   /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
#       -m PyInstaller --noconfirm G5GUI.spec
#
# The macOS bundle is arm64 unless QEMUGUI_TARGET_ARCH says otherwise
# (x86_64 or universal2).
#
# Put the result ("dist/Qemu-system-ppc64 G5 Openbios GUI.app" on macOS, the single
# "dist/Qemu-system-ppc64 G5 Openbios GUI.exe" on Windows) into the folder that holds
# qemu-system-ppc64, openbios-qemu.elf and pc-bios/. Machines/ is made
# beside the application, never inside the bundle.

import os
import sys

NAME = 'Qemu-system-ppc64 G5 Openbios GUI'
TARGET_ARCH = os.environ.get('QEMUGUI_TARGET_ARCH', 'arm64') if sys.platform == 'darwin' else None

a = Analysis(
    ['g5_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['tkinter', 'tkinter.ttk', 'tkinter.filedialog',
                   'tkinter.messagebox', 'tkinter.simpledialog',
                   'pyftpdlib', 'pyftpdlib.authorizers', 'pyftpdlib.handlers',
                   'pyftpdlib.servers'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == 'win32':
    # One self-contained .exe; it unpacks itself to a temp folder at start.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        target_arch=TARGET_ARCH,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=NAME,
    )
    app = BUNDLE(
        coll,
        name=NAME + '.app',
        icon=None,
        bundle_identifier='org.cat7.qemu-gui-g5',
        info_plist={
            'CFBundleName': NAME,
            'CFBundleDisplayName': NAME,
            'CFBundleShortVersionString': '1.0',
            'CFBundleVersion': '1.0',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
            'LSApplicationCategoryType': 'public.app-category.utilities',
            'NSMicrophoneUsageDescription': 'The emulated machine uses the microphone as its audio input.',
        },
    )
