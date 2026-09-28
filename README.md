# Qemu-system-ppc64 G5 GUI

A portable launcher for the Power Mac G5 (PowerMac7,3) machine of
`qemu-system-ppc64`, branch `powermac73` of github.com/cat7/qemu, with the
OpenBIOS of branch `powermac73` of github.com/cat7/openbios. The program
must sit in the same folder as `qemu-system-ppc64`, `openbios-qemu.elf`
and `pc-bios/` (and `qemu-img` for making new disks). The graphics card
ROMs (`*.rom`) are picked from that folder.

## Requirements

- To run from source: Python 3.11+ with tkinter, plus `pyftpdlib` for the
  shared folder (`python -m pip install pyftpdlib`).
- To build a bundle: PyInstaller, with `pyftpdlib` installed in the same
  Python.

## Run from source

    python g5_gui.py

## Shared folder

Each machine can share one host folder over FTP while it runs. With the
default (slirp) network the Mac reaches it at `ftp://10.0.2.2/` (`:2121`
when port 21 is taken); with vmnet choose "All interfaces", set a
password, and use the host's own address.

## Build on macOS

With the python.org framework Python (Homebrew's Python has no tkinter):

    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
        -m PyInstaller --noconfirm G5GUI.spec

Result: `dist/Qemu-system-ppc64 G5 GUI.app`, arm64. Set
`QEMUGUI_TARGET_ARCH=universal2` (or `x86_64`) for another architecture.

## Build on Windows

    py -m PyInstaller --noconfirm G5GUI.spec

Result: a single windowed executable, `dist/Qemu-system-ppc64 G5 GUI.exe`.
Put it alongside `qemu-system-ppc64.exe`.

## Tests

    python -m unittest discover -s tests
