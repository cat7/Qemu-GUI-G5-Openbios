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

## Host USB devices (macOS)

A machine can use USB devices plugged into the Mac (a camera, a DVD
writer): tick them in the machine's settings, USB devices tab. QEMU can
take a device from macOS only as root, so a machine with devices ticked
starts with `sudo` and asks for your password in Terminal, as vmnet does.
High-speed devices go on the USB 2.0 bus, others on the second USB 1.1
bus; a device that is not plugged in at start is taken when it is plugged
in. Keyboards, mice and disks with a mounted volume are never offered (a
DVD drive with a mounted disc is fine). A device goes back to macOS when
the machine quits.

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

USB switch helper, from `winusb/` with a 64-bit mingw-w64 cross compiler:
`x86_64-w64-mingw32-gcc -O2 -Wall -municode -o winusb-switch.exe winusb-switch.c -lsetupapi -lcfgmgr32`

## Tests

    python -m unittest discover -s tests
