"""The PowerMac7,3 (G5) machine record: dataclasses, JSON load/save, checks.

``machine.json`` schema 1 of the G5 family. The machine is QEMU's ``mac99``
with a 970FX CPU (the U3 memory controller, K2 I/O), started with its own
OpenBIOS (``-bios openbios-qemu.elf``).

Facts the record encodes (qemu ``G5-openbios``, ``hw/ppc/mac_newworld.c``):

* Drives. The K2 ATA-100 has two positions and holds the optical drives;
  the K2 SATA has two ports (buses ``sata.0``/``sata.1``) for hard disks.
  A legacy ``-drive ...,index=N`` goes to the ATA-100 when N is 0 or the
  drive is a CD, every other hard disk to SATA; the ATA-100 fills master
  then slave in index order. A hard disk on the ATA-100 master is emitted
  as index 0, CDs as index 2 (see ``ata_index``), so nothing lands on SATA
  unasked; SATA disks are attached by name (``-device ide-hd,bus=sata.N``).
  With index 2 left empty QEMU adds an empty CD drive there.
* Boot. OpenBIOS probes ATA-100 master, slave, then SATA A, B, and names
  the first hard disk ``hd`` and the first CD ``cd``; ``-boot c`` picks
  ``hd``, ``-boot d`` picks ``cd``, as long as the NVRAM's boot-device is
  still the default.
* NVRAM. QEMU keeps it in ``nvram.img`` (16 KB) in its working directory,
  the machine folder, creating it when absent; a file of another size is
  not used. ``-prom-env`` values are set in it at every start, over what it
  holds; a variable not given keeps its saved value. There is no PRAM file.
* Sound. The K2's I2S sound takes ``-global macio-newworld.audiodev=``;
  ``usb-audio`` needs a backend of its own.
* Graphics. With no card chosen the machine's own std VGA runs. The ATI
  cards sit in the AGP slot (``bus=pci.0,addr=0x10``) and need their
  Open Firmware ROM (``romfile``).
* Input. The machine always has a USB keyboard and mouse.
* Host USB devices. Passed through with QEMU's ``usb-host``, which needs
  QEMU to run as root (the launcher uses sudo, as for vmnet): high- and
  super-speed devices on the EHCI (``usb-bus.2``, high speed only), others
  on the empty second OHCI (``usb-bus.1``), by the speed saved with the
  device. macOS, and Windows, where QEMU runs unelevated and opens only a
  device that is on Windows' WinUSB driver (``winusb-switch.exe`` moves it
  there and back).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import paths
from .paths import (DISPLAYS, default_display, AUDIO_DEFAULT,
                    NETWORK_MODES, NETWORK_MODE_PLATFORM, NETWORK_MODES_WITH_IFNAME,
                    NETWORK_MODE_LABELS, network_mode_label, network_mode_by_label,
                    network_modes_for_host, network_labels_for_host, default_ifname,
                    ifname_label, default_audio_label, FORMATS, detect_format)

DEFAULT_MAC = "00:05:02:12:34:56"

SCHEMA = 1
NAME_RE = re.compile(r"^[A-Za-z0-9._ -]+$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
VNC_RE = re.compile(r"^([A-Za-z0-9.\-]*:)?\d+$")
RTC_BASE_RE = re.compile(r"^(utc|localtime|\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?)$")
RTC_BASE_CHOICES = ("", "localtime")

MACHINE_TYPE = "mac99,via=pmu"
CPU_TYPE = "970fx"
FIRMWARE_FILE = "openbios-qemu.elf"

# Drive positions, in OpenBIOS probe order.
DRIVE_SLOTS = ("ATA-100 master", "ATA-100 slave", "SATA port A", "SATA port B")
ATA_MASTER, ATA_SLAVE, SATA_A, SATA_B = range(4)
ATA_SLOTS = (ATA_MASTER, ATA_SLAVE)
SATA_SLOTS = (SATA_A, SATA_B)

DRIVE_KINDS = ("disk", "cdrom")


def slot_name(i: int) -> str:
    return DRIVE_SLOTS[i]


def is_sata(i: int) -> bool:
    return i in SATA_SLOTS


def slot_kinds(i: int) -> tuple[str, ...]:
    """What a position can hold: SATA hard disks only, the ATA-100 slave a
    CD only (a hard disk there would be sent to SATA)."""
    if is_sata(i):
        return ("disk",)
    if i == ATA_SLAVE:
        return ("cdrom",)
    return DRIVE_KINDS


def ata_index(m: Machine, i: int) -> int:
    """The legacy -drive index for ATA-100 position *i*. Index 2 is the only
    one besides 0 that stays on the ATA-100 for a CD, and using it for a
    lone CD keeps QEMU from adding an empty one."""
    if i == ATA_MASTER:
        master = m.drives[ATA_MASTER]
        slave = m.drives[ATA_SLAVE]
        if master and master.kind == "cdrom" and not (slave and slave.file):
            return 2
        return 0
    return 2


def resolved_boot_kind(m: Machine) -> str:
    """'cdrom' if ``boot_slot`` names a filled CD position, else 'disk'."""
    i = m.boot_slot
    if i is not None and 0 <= i < len(m.drives) and m.drives[i] and m.drives[i].file:
        return m.drives[i].kind if m.drives[i].kind in DRIVE_KINDS else "disk"
    return "disk"


def lowest_slot_of_kind(m: Machine, kind: str) -> int | None:
    """The position OpenBIOS names for *kind*: the first filled one."""
    for i, d in enumerate(m.drives):
        if d and d.file and d.kind == kind:
            return i
    return None


AUDIO_MODES = ("default", "sdl", "none")
RAM_CHOICES = (1024, 2048, 3072, 4096, 6144, 8192, 16384)
RAM_MIN, RAM_MAX = 256, 16384
RAM_DEFAULT = 2048
SMP_MIN, SMP_MAX = 1, 4

GPU_MODELS = ("radeon9800", "rv100")
GPU_LABELS = {"radeon9800": "ATI Radeon 9800", "rv100": "ATI Radeon 7000 (RV100)"}
GL_MODES = ("off", "on", "fast")
ASYNC_MODES = ("auto", "on", "off")
RASTER_MIN, RASTER_MAX = 0, 8          # 0 = automatic, 1 = one thread
ROM_SUFFIXES = (".rom",)

NVRAM_FILE = "nvram.img"
SAVED_SETTINGS_FILES = (NVRAM_FILE,)

OWNED_FILES = ("machine.json", "run.command", "run.bat", "last-run.log",
               NVRAM_FILE, ".DS_Store")

IMAGE_SUFFIXES = {".img", ".dsk", ".qcow2", ".iso", ".toast", ".cdr", ".dmg",
                  ".hfv", ".hfs", ".vmdk", ".raw"}


def looks_like_disk_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_SUFFIXES


def roms_in(folder: str | Path | None) -> list[str]:
    """The ROM files lying in *folder*, by name."""
    if not folder:
        return []
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return []
    return sorted((p.name for p in entries
                   if p.is_file() and p.suffix.lower() in ROM_SUFFIXES), key=str.lower)


@dataclass
class Drive:
    kind: str = "disk"
    file: str = ""
    format: str = "raw"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "file": self.file, "format": self.format}

    @classmethod
    def from_dict(cls, d: Any) -> "Drive | None":
        if not isinstance(d, dict):
            return None
        return cls(str(d.get("kind", "disk")), str(d.get("file", "")), str(d.get("format", "raw")))


@dataclass
class Gpu:
    """The graphics card in the AGP slot. ``None`` on the machine means the
    machine's own std VGA."""
    model: str = "radeon9800"
    romfile: str | None = None
    gl: str = "off"                 # radeon9800 only
    raster_threads: int = 0
    async_engine: str = "auto"
    agp: bool = True                # rv100 only

    def to_dict(self) -> dict:
        return {"model": self.model, "romfile": self.romfile, "gl": self.gl,
                "raster_threads": self.raster_threads, "async_engine": self.async_engine,
                "agp": self.agp}

    @classmethod
    def from_dict(cls, d: Any) -> "Gpu | None":
        if not isinstance(d, dict):
            return None
        rom = d.get("romfile")
        try:
            threads = int(d.get("raster_threads", 0) or 0)
        except (TypeError, ValueError):
            threads = 0
        return cls(str(d.get("model") or "radeon9800"), str(rom) if rom else None,
                   str(d.get("gl") or "off"), threads,
                   str(d.get("async_engine") or "auto"), bool(d.get("agp", True)))

    @property
    def label(self) -> str:
        return GPU_LABELS.get(self.model, self.model)


HOSTFWD_PROTOS = ("tcp", "udp")
HOSTFWD_ROWS = 4


@dataclass
class HostFwd:
    proto: str = "tcp"
    host_port: str = ""
    guest_port: str = ""

    def to_dict(self) -> dict:
        return {"proto": self.proto, "host_port": self.host_port, "guest_port": self.guest_port}

    @classmethod
    def from_dict(cls, d: Any) -> "HostFwd":
        if not isinstance(d, dict):
            return cls()
        return cls(str(d.get("proto") or "tcp"), str(d.get("host_port") or "").strip(),
                   str(d.get("guest_port") or "").strip())

    @property
    def empty(self) -> bool:
        return not (self.host_port or self.guest_port)


def _port_ok(text: str) -> bool:
    return text.isascii() and text.isdigit() and 1 <= int(text) <= 65535


@dataclass
class Network:
    mode: str = "user"
    mac: str = DEFAULT_MAC
    ifname: str = ""
    hostfwd: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {"mode": self.mode, "mac": self.mac}
        if self.mode in NETWORK_MODES_WITH_IFNAME or self.ifname:
            d["ifname"] = self.ifname
        rules = [r.to_dict() for r in self.hostfwd if not r.empty]
        if rules:
            d["hostfwd"] = rules
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "Network":
        if not isinstance(d, dict):
            return cls()
        rules = d.get("hostfwd")
        return cls(str(d.get("mode", "user")), str(d.get("mac", DEFAULT_MAC)),
                   str(d.get("ifname", "") or ""),
                   [HostFwd.from_dict(r) for r in rules] if isinstance(rules, list) else [])

    @property
    def needs_sudo(self) -> bool:
        return self.mode.startswith("vmnet-")

    @property
    def platform(self) -> str | None:
        return NETWORK_MODE_PLATFORM.get(self.mode)


@dataclass
class PromEnv:
    """Open Firmware variables, set in the NVRAM at every start (see the
    module docstring)."""
    auto_boot: bool = True
    boot_device: str = ""
    boot_args: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "PromEnv":
        if not isinstance(d, dict):
            return cls()
        return cls(bool(d.get("auto_boot", True)), str(d.get("boot_device", "")),
                   str(d.get("boot_args", "")))


USB_ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")


@dataclass
class UsbHostDevice:
    """A host USB device this machine takes, by vendor:product id (lower-case
    hex); the speed picks the bus."""
    id: str = ""
    name: str = ""
    speed: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "speed": self.speed}

    @classmethod
    def from_dict(cls, d: Any) -> "UsbHostDevice | None":
        if not isinstance(d, dict):
            return None
        return cls(str(d.get("id", "") or "").strip().lower(), str(d.get("name", "") or ""),
                   str(d.get("speed", "") or ""))


def usb_host_devices_from(value: Any) -> list[UsbHostDevice]:
    out: list[UsbHostDevice] = []
    for x in value if isinstance(value, list) else []:
        dev = UsbHostDevice.from_dict(x)
        if dev and dev.id and all(dev.id != o.id for o in out):
            out.append(dev)
    return out


SHARE_SCOPES = ("guest-only", "all-interfaces")
SHARE_DEFAULT_USER = "guest"


@dataclass
class Share:
    """One host folder, offered to the guest over FTP while it runs -- see
    g5_share.py."""
    folder: str = ""             # "" = no shared folder
    user: str = SHARE_DEFAULT_USER
    password: str = ""
    scope: str = "guest-only"    # guest-only | all-interfaces

    def to_dict(self) -> dict:
        return {"folder": self.folder, "user": self.user, "password": self.password,
                "scope": self.scope}

    @classmethod
    def from_dict(cls, d: Any) -> "Share":
        if not isinstance(d, dict):
            return cls()
        return cls(str(d.get("folder", "") or ""),
                   str(d.get("user", SHARE_DEFAULT_USER) or SHARE_DEFAULT_USER),
                   str(d.get("password", "") or ""),
                   str(d.get("scope", "guest-only") or "guest-only"))

    @property
    def enabled(self) -> bool:
        return bool(self.folder.strip())


@dataclass
class Machine:
    name: str = "New machine"
    ram_mb: int = RAM_DEFAULT
    smp: int = 1
    display: str = "cocoa"
    vnc: str = ""                  # "" = off; else a -vnc display spec, e.g. ":1"
    audio: str = "default"
    usb_audio: bool = False
    usb_tablet: bool = False
    gpu: Gpu | None = None
    network: Network = field(default_factory=Network)
    boot_slot: int | None = None   # index into drives, or None -- see resolved_boot_kind
    drives: list = field(default_factory=lambda: [None, None, None, None])
    prom_env: PromEnv = field(default_factory=PromEnv)
    share: Share = field(default_factory=Share)
    usb_host_devices: list = field(default_factory=list)   # [UsbHostDevice]
    rtc_base: str = ""
    extra_args: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "name": self.name,
            "ram_mb": self.ram_mb,
            "smp": self.smp,
            "display": self.display,
            "vnc": self.vnc,
            "audio": self.audio,
            "usb_audio": self.usb_audio,
            "usb_tablet": self.usb_tablet,
            "gpu": self.gpu.to_dict() if self.gpu else None,
            "network": self.network.to_dict(),
            "boot_slot": self.boot_slot,
            "drives": [d.to_dict() if d else None for d in self.drives],
            "prom_env": self.prom_env.to_dict(),
            "share": self.share.to_dict(),
            "usb_host_devices": [u.to_dict() for u in self.usb_host_devices],
            "rtc_base": self.rtc_base,
            "extra_args": self.extra_args,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Machine":
        drives = [Drive.from_dict(x) for x in list(d.get("drives") or [])][:len(DRIVE_SLOTS)]
        while len(drives) < len(DRIVE_SLOTS):
            drives.append(None)
        boot_slot = d.get("boot_slot")
        boot_slot = int(boot_slot) if isinstance(boot_slot, int) else None
        return cls(
            name=str(d.get("name", "New machine")),
            ram_mb=int(d.get("ram_mb", RAM_DEFAULT)),
            smp=int(d.get("smp", 1) or 1),
            display=str(d.get("display") or default_display(paths.HOST_PLATFORM)),
            vnc=str(d.get("vnc", "") or ""),
            audio=str(d.get("audio", "default")),
            usb_audio=bool(d.get("usb_audio", False)),
            usb_tablet=bool(d.get("usb_tablet", False)),
            gpu=Gpu.from_dict(d.get("gpu")),
            network=Network.from_dict(d.get("network")),
            boot_slot=boot_slot,
            drives=drives,
            prom_env=PromEnv.from_dict(d.get("prom_env")),
            share=Share.from_dict(d.get("share")),
            usb_host_devices=usb_host_devices_from(d.get("usb_host_devices")),
            rtc_base=str(d.get("rtc_base", "") or ""),
            extra_args=str(d.get("extra_args", "")),
            notes=str(d.get("notes", "")),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Machine":
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: Path) -> "Machine":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    def copy(self) -> "Machine":
        return Machine.from_dict(self.to_dict())

    def first_unfilled_disk_slot(self) -> int | None:
        """The first position a new hard disk can go in: SATA first."""
        for i in (SATA_A, SATA_B, ATA_MASTER):
            d = self.drives[i]
            if d is None or not d.file:
                return i
        return None

    def slot_status(self, i: int) -> str:
        d = self.drives[i]
        if d is None or not d.file:
            return "empty"
        return f"replace {Path(d.file).name}"

    def image_paths(self) -> list[str]:
        return [f for _label, f in _image_files(self) if f]


def new_machine(name: str) -> Machine:
    """A fresh record: one CPU, no drive, the machine's own graphics. No file
    field is ever filled in."""
    return Machine(name=name, display=default_display(paths.HOST_PLATFORM))


def file_fields(m: Machine) -> dict[str, str]:
    fields = {"gpu.romfile": (m.gpu.romfile or "") if m.gpu else ""}
    for i, d in enumerate(m.drives):
        fields[f"drives[{i}]"] = d.file if d else ""
    return fields


def start_blockers(m: Machine) -> list[str]:
    """The firmware ships with the emulator, so nothing has to be chosen
    before Start."""
    return []


# ---------------------------------------------------------------- checking

def validate(m: Machine, qemu_dir: str | None, platform: str = paths.HOST_PLATFORM,
             check_files: bool = True, machine_dir: str | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not m.name or not m.name.strip():
        errors.append("This machine has no name.")
    elif not NAME_RE.match(m.name) or m.name.strip() != m.name:
        errors.append("That name will not work.")
    if not (RAM_MIN <= m.ram_mb <= RAM_MAX):
        errors.append(f"Memory has to be between {RAM_MIN} and {RAM_MAX} MB.")
    if not (SMP_MIN <= m.smp <= SMP_MAX):
        errors.append(f"CPUs has to be between {SMP_MIN} and {SMP_MAX}.")
    if m.display == "cocoa" and platform != "darwin":
        warnings.append("'cocoa' only works on a Mac.")
    if m.vnc.strip() and not VNC_RE.match(m.vnc.strip()):
        errors.append("VNC display has to look like :1 or 127.0.0.1:1.")
    if m.rtc_base.strip() and not RTC_BASE_RE.match(m.rtc_base.strip()):
        errors.append("Date and time has to be localtime, utc, or look like "
                      "2005-04-29T10:30:00.")

    gpu = m.gpu
    if gpu is not None:
        if gpu.model not in GPU_MODELS:
            errors.append(f"'{gpu.model}' is not a graphics card.")
        if gpu.gl not in GL_MODES:
            errors.append(f"'{gpu.gl}' is not an OpenGL setting.")
        if gpu.async_engine not in ASYNC_MODES:
            errors.append(f"'{gpu.async_engine}' is not an engine thread setting.")
        if not (RASTER_MIN <= gpu.raster_threads <= RASTER_MAX):
            errors.append(f"Raster threads has to be between {RASTER_MIN} and {RASTER_MAX}.")
        if not gpu.romfile:
            warnings.append("The graphics card has no ROM file.")

    net = m.network
    if net.mode not in NETWORK_MODES:
        errors.append(f"'{net.mode}' is not a network setting.")
    else:
        if net.mode != "none" and not MAC_RE.match(net.mac):
            errors.append("The card address has to look like 00:05:02:12:34:56.")
        if net.mode in NETWORK_MODES_WITH_IFNAME and not net.ifname.strip():
            errors.append("No interface named.")
        low = False
        for r in net.hostfwd:
            if r.empty:
                continue
            if r.proto not in HOSTFWD_PROTOS:
                errors.append(f"'{r.proto}' is not a forwarding protocol.")
            if not (_port_ok(r.host_port) and _port_ok(r.guest_port)):
                errors.append("Port forwarding needs host and guest ports from 1 to 65535.")
            elif int(r.host_port) < 1024:
                low = True
        if low and net.mode == "user" and not paths.is_windows(platform) \
                and not (platform == "darwin" and m.usb_host_devices):
            warnings.append("Host ports below 1024 can need root; this machine does not "
                            "start with sudo (vmnet or USB devices do).")
        host = "win32" if paths.is_windows(platform) else platform
        if net.platform is not None and net.platform != host:
            warnings.append("This network setting only works on "
                            f"{'a Mac' if net.platform == 'darwin' else 'Windows'}.")

    if len(m.drives) != len(DRIVE_SLOTS):
        errors.append("There are not four drive positions.")
    if m.boot_slot is not None and not (0 <= m.boot_slot < len(m.drives)):
        errors.append("The marked boot drive is not a real drive position.")

    for i, d in enumerate(m.drives[:len(DRIVE_SLOTS)]):
        if d is None or not d.file:
            continue
        if d.kind not in DRIVE_KINDS:
            errors.append(f"{slot_name(i)} has no drive type.")
        elif d.kind not in slot_kinds(i):
            if is_sata(i):
                errors.append(f"{slot_name(i)} takes a hard disk; a CD goes on the ATA-100.")
            else:
                errors.append(f"{slot_name(i)} takes a CD; a second hard disk goes on SATA.")

    if m.boot_slot is not None and 0 <= m.boot_slot < len(m.drives):
        marked = m.drives[m.boot_slot]
        if marked is None or not marked.file:
            warnings.append(f"{slot_name(m.boot_slot)} is marked Boot but is empty.")
        elif marked.kind in DRIVE_KINDS:
            winner = lowest_slot_of_kind(m, marked.kind)
            if winner is not None and winner != m.boot_slot:
                kind_word = "CD" if marked.kind == "cdrom" else "hard disk"
                warnings.append(f"{slot_name(m.boot_slot)} is marked Boot, but "
                                f"{slot_name(winner)} (also a {kind_word}) will boot first.")

    share = m.share
    if share.scope not in SHARE_SCOPES:
        errors.append(f"'{share.scope}' is not a sharing setting.")
    if share.enabled:
        if not Path(share.folder.strip()).expanduser().is_dir():
            errors.append("The shared folder is not a folder that exists.")
        if not share.user.strip():
            errors.append("The shared folder has no user name.")
        if share.scope == "all-interfaces" and not share.password:
            errors.append("Sharing on all interfaces needs a password.")
        if share.scope == "guest-only" and net.mode != "user":
            warnings.append("Guest only sharing is only reachable with default (slirp).")

    for u in m.usb_host_devices:
        if not USB_ID_RE.match(u.id):
            errors.append(f"'{u.id}' is not a USB device id like 046d:0990.")
    if m.usb_host_devices and platform != "darwin" and not paths.is_windows(platform):
        warnings.append("Host USB devices only work on a Mac or on Windows.")

    if check_files:
        qd = qemu_dir or ""
        if qd and paths.has_qemu(qd, platform):
            fw = paths.join_path(qd, FIRMWARE_FILE, platform)
            if not Path(fw).is_file():
                warnings.append(f"The firmware is missing: {fw}")
            rel = gpu.romfile if gpu else None
            if rel and not Path(paths.join_path(qd, rel, platform)).is_file():
                warnings.append("The graphics card's ROM is missing: "
                                f"{paths.join_path(qd, rel, platform)}")
        for label, f in _image_files(m):
            if not f:
                continue
            p = Path(f).expanduser()
            if not p.is_absolute():
                if machine_dir is None:
                    continue
                p = Path(machine_dir) / p
            if not p.is_file():
                warnings.append(f"{label}: {f} is not there.")
    return errors, warnings


def _image_files(m: Machine):
    for i, d in enumerate(m.drives):
        if d:
            yield slot_name(i), d.file


def check_new_image_path(folder: Path | str, name: str, fmt: str) -> tuple[Path | None, str | None]:
    name = (name or "").strip()
    if not name:
        return None, "Give the disk a name."
    if "/" in name or "\\" in name or name in (".", ".."):
        return None, "The name cannot contain slashes."
    suffixes = (".img", ".qcow2", ".dsk")
    if not name.lower().endswith(suffixes):
        name += ".qcow2" if fmt == "qcow2" else ".img"
    target = Path(folder) / name
    if target.exists() or target.is_symlink():
        return None, f"There is already a file called {name}."
    return target, None


# ---------------------------------------------------------------- machines

@dataclass
class DeleteResult:
    folder: Path
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    folder_removed: bool = False

    @property
    def kept_images(self) -> list[str]:
        return [f for f in self.kept if looks_like_disk_image(f)]


class Library:
    """The Machines folder: one subfolder per machine, named after it.
    Nothing here ever deletes a disk image, and nothing ever picks a file."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root).expanduser() if root else paths.machines_dir()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, name: str) -> Path:
        return self.root / name

    def json_path(self, name: str) -> Path:
        return self.folder(name) / "machine.json"

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        out = []
        for p in sorted(self.root.iterdir(), key=lambda x: x.name.lower()):
            if p.is_dir() and (p / "machine.json").is_file():
                out.append(p.name)
        return out

    def load(self, name: str) -> Machine:
        m = Machine.load(self.json_path(name))
        m.name = name
        return m

    def load_all(self) -> list[Machine]:
        out = []
        for n in self.names():
            try:
                out.append(self.load(n))
            except (OSError, ValueError, KeyError, TypeError):
                out.append(Machine(name=n, notes="(unreadable)"))
        return out

    def save(self, m: Machine, old_name: str | None = None) -> Path:
        if not NAME_RE.match(m.name):
            raise ValueError("illegal machine name")
        self.ensure()
        if old_name and old_name != m.name and self.folder(old_name).is_dir():
            if self.folder(m.name).exists():
                raise FileExistsError(f"A machine called '{m.name}' already exists.")
            old_folder, new_folder = self.folder(old_name), self.folder(m.name)
            moved = [(old_folder, new_folder),
                     (old_folder.resolve(), new_folder.parent.resolve() / new_folder.name)]
            old_folder.rename(new_folder)
            _repoint_images(m, moved)
        self.folder(m.name).mkdir(parents=True, exist_ok=True)
        m.save(self.json_path(m.name))
        return self.folder(m.name)

    def exists(self, name: str) -> bool:
        return self.folder(name).exists()

    def has_record(self, name: str) -> bool:
        return self.json_path(name).is_file()

    def duplicate(self, name: str, new_name: str) -> Machine:
        if self.exists(new_name):
            raise FileExistsError(f"A machine called '{new_name}' already exists.")
        m = self.load(name)
        m.name = new_name
        self.save(m)
        for f in SAVED_SETTINGS_FILES:
            src = self.folder(name) / f
            dst = self.folder(new_name) / f
            if src.is_file() and not dst.exists():
                shutil.copy2(src, dst)
        return m

    def folder_contents(self, name: str) -> list[str]:
        d = self.folder(name)
        if not d.is_dir():
            return []
        return sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() or p.is_symlink())

    def delete_preview(self, name: str) -> tuple[list[str], list[str]]:
        contents = self.folder_contents(name)
        removed = [f for f in contents if f in OWNED_FILES]
        kept = [f for f in contents if f not in OWNED_FILES]
        return removed, kept

    def delete(self, name: str) -> DeleteResult:
        d = self.folder(name)
        result = DeleteResult(folder=d)
        if not d.is_dir() or d.resolve().parent != self.root.resolve():
            return result
        for f in OWNED_FILES:
            p = d / f
            if p.is_symlink() or p.is_file():
                try:
                    p.unlink()
                except OSError:
                    continue
                result.removed.append(f)
        result.kept = self.folder_contents(name)
        if not result.kept:
            for sub in sorted((p for p in d.rglob("*") if p.is_dir()),
                              key=lambda p: len(p.parts), reverse=True):
                try:
                    sub.rmdir()
                except OSError:
                    pass
            try:
                d.rmdir()
                result.folder_removed = True
            except OSError:
                pass
        return result

    def saved_settings_status(self, name: str) -> dict[str, int | None]:
        out: dict[str, int | None] = {}
        for f in SAVED_SETTINGS_FILES:
            p = self.folder(name) / f
            out[f] = p.stat().st_size if p.is_file() else None
        return out

    def clear_saved_settings(self, name: str) -> list[str]:
        removed = []
        for f in SAVED_SETTINGS_FILES:
            p = self.folder(name) / f
            if p.is_file():
                p.unlink()
                removed.append(f)
        return removed


def _repoint_images(m: Machine, moved: list[tuple[Path, Path]]) -> None:
    def fixed(p: str) -> str:
        if not p:
            return p
        for old_folder, new_folder in moved:
            try:
                rel = Path(p).relative_to(old_folder)
            except ValueError:
                continue
            return str(new_folder / rel)
        return p

    for d in m.drives:
        if d:
            d.file = fixed(d.file)
