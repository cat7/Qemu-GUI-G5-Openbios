"""Readable names for host USB devices: maker and model from the USB ID
Repository (usb_ids.gz beside this file) where the device's own string is
missing or says nothing, and what kind of device it is.

No Tk in here.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent / "usb_ids.gz"

_vendors: dict[str, str] | None = None
_products: dict[str, str] = {}

# Strings Windows or a device give that name no model
GENERIC = {"usb composite device", "usb input device", "winusb device", "usb device",
           "usb mass storage device", "generic usb hub", "usb2.0 hub", "usb hub",
           "unknown usb device", "hid-compliant device"}
_OUR_PACKAGE_RE = re.compile(r"^usb device [0-9a-f]{4}:[0-9a-f]{4} for qemu", re.I)
_FILLER = {"usb", "device", "composite", "generic", "input", "2.0", "3.0", "hid"}

_SUFFIX_RE = re.compile(
    r"[,.]?\s+(inc|inc\.|corp|corp\.|corporation|co\.?,?\s*ltd\.?|co\.|ltd\.?|limited|gmbh|"
    r"ag|s\.a\.|b\.v\.|llc|plc|technology|technologies|electronics|international|"
    r"computer|semiconductor|microelectronics|industrial)$", re.I)

# Windows device classes and services of a device's functions
WIN_KINDS = {"camera": "camera", "image": "camera or scanner", "usbvideo": "camera",
             "media": "audio", "usbaudio": "audio", "diskdrive": "disk", "usbstor": "disk",
             "cdrom": "optical drive", "mouse": "mouse", "mouhid": "mouse",
             "keyboard": "keyboard", "kbdhid": "keyboard", "hidclass": "controls",
             "ports": "serial port", "net": "network", "bluetooth": "Bluetooth",
             "printer": "printer", "usbprint": "printer", "wpd": "phone or media player",
             "smartcardreader": "card reader", "biometric": "fingerprint reader"}

# USB interface classes (macOS)
IFACE_KINDS = {0x01: "audio", 0x02: "modem or network", 0x03: "controls", 0x06: "camera or scanner",
               0x07: "printer", 0x08: "disk", 0x0a: "", 0x0b: "card reader", 0x0e: "camera",
               0xe0: "Bluetooth"}


def _load() -> dict[str, str]:
    global _vendors
    if _vendors is not None:
        return _vendors
    _vendors = {}
    try:
        with gzip.open(DATA, "rt", encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                key, _tab, name = line.rstrip("\n").partition("\t")
                if ":" in key:
                    _products[key] = name
                else:
                    _vendors[key] = name
    except OSError:
        pass
    return _vendors


def vendor_name(dev_id: str) -> str:
    """The maker, without Inc., Corp. and the like; "" if unknown."""
    name = _load().get(dev_id[:4].lower(), "")
    prev = None
    while name and name != prev:
        prev = name
        name = _SUFFIX_RE.sub("", name).strip(" ,.")
    return name


def product_name(dev_id: str) -> str:
    _load()
    return _products.get(dev_id.lower(), "")


def _says_nothing(s: str) -> bool:
    s = s.strip()
    if not s or s.lower() in GENERIC or _OUR_PACKAGE_RE.match(s):
        return True
    words = [w for w in re.split(r"\s+", s.lower()) if w not in _FILLER]
    return len(words) <= 1 and not any(c.isdigit() for c in s)


def describe(dev_id: str, own: str = "", kinds=()) -> str:
    """Maker, model and kind, e.g. "Logitech QuickCam Pro 9000 (camera, audio)".
    *own* is the device's or Windows' name for it; *kinds* words like "disk"."""
    vendor = vendor_name(dev_id)
    known = product_name(dev_id)
    own = (own or "").strip()
    if _says_nothing(own):
        model = known or ("" if not own or _OUR_PACKAGE_RE.match(own) or
                          own.lower() in GENERIC else own)
    else:
        model = own
    if model and vendor and vendor.split()[0].lower() not in model.lower():
        name = f"{vendor} {model}"
    elif model:
        name = model
    elif vendor:
        name = f"{vendor} device"
    else:
        name = "Unknown USB device"
    words = []
    low = name.lower()
    for k in kinds:
        if k == "controls" and any(w in low for w in ("keyboard", "mouse", "controller",
                                                       "headset", "gamepad", "joystick")):
            continue
        if k and k not in words and k.lower() not in low:
            words.append(k)
    return f"{name} ({', '.join(words)})" if words else name


def windows_kinds(functions, cls: str = "", service: str = "") -> list[str]:
    """Kinds from winusb-switch's class/service of the device and its functions."""
    out = []
    pairs = [(f.get("class", ""), f.get("service", "")) for f in functions or []
             if isinstance(f, dict)]
    if not pairs:
        pairs = [(cls, service)]
    for c, s in pairs:
        k = WIN_KINDS.get(str(s).lower()) or WIN_KINDS.get(str(c).lower())
        if k and k not in out:
            out.append(k)
    # "controls" next to a keyboard, mouse or audio adds nothing
    if "controls" in out and len(out) > 1:
        out.remove("controls")
    return out


def iface_kinds(classes) -> list[str]:
    out = []
    for c in classes or ():
        k = IFACE_KINDS.get(c, "")
        if k and k not in out:
            out.append(k)
    if "controls" in out and len(out) > 1:
        out.remove("controls")
    return out
