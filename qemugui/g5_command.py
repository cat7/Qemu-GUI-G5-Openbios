"""Build the QEMU argv for a G5 Machine and render it as run.command /
run.bat.

Pure: no Tk, no filesystem access beyond string handling. See
``qemugui/g5_model.py`` for the machine facts each choice rests on.
"""

from __future__ import annotations

from . import paths
from .paths import (qopt, split_extra_args, group_options, bat_quote, drive_format,
                    SUDO_KEEPALIVE)  # re-exported
from . import g5_model as model
from .g5_model import Machine

HEADER_NOTE = f"Written by {paths.APP_NAME}. Do not edit."

AUDIO_DEFAULT = paths.AUDIO_DEFAULT

PC_BIOS_DIR = "pc-bios"
GPU_SLOT = "bus=pci.0,addr=0x10"
ONBOARD_AUDIODEV = "snd0"
USB_AUDIODEV = "usb"


def _path(p: str, base: str, platform: str) -> str:
    return paths.join_path(base, p, platform)


def nic_option(net) -> str:
    """model=sungem is the K2 GMAC, made explicit."""
    if net.mode == "none":
        return "none"
    tail = f"model=sungem,mac={net.mac}"
    if net.mode == "user":
        return f"user,{tail}"
    if net.mode in ("vmnet-bridged", "tap"):
        return f"{net.mode},ifname={qopt(net.ifname)},{tail}"
    if net.mode in ("vmnet-shared", "vmnet-host"):
        return f"{net.mode},{tail}"
    raise ValueError(f"unknown network mode {net.mode!r}")


def needs_sudo(m: Machine, platform: str = paths.HOST_PLATFORM) -> bool:
    return paths.sudo_applies(m.network.needs_sudo, platform)


def prom_env_tokens(m: Machine) -> list[str]:
    pe = m.prom_env
    out = ["-prom-env", f"auto-boot?={'true' if pe.auto_boot else 'false'}"]
    if pe.boot_device.strip():
        out += ["-prom-env", f"boot-device={pe.boot_device.strip()}"]
    if pe.boot_args.strip():
        out += ["-prom-env", f"boot-args={pe.boot_args.strip()}"]
    return out


def gpu_option(gpu: model.Gpu, qemu_dir: str, platform: str) -> str:
    if gpu.model == "rv100":
        parts = ["ati-vga,model=rv100", GPU_SLOT, f"agp={'on' if gpu.agp else 'off'}"]
    else:
        parts = ["ati-radeon9800", GPU_SLOT, f"gl={gpu.gl}"]
    if gpu.raster_threads:
        parts.append(f"raster-threads={int(gpu.raster_threads)}")
    if gpu.async_engine != "auto":
        parts.append(f"async-engine={gpu.async_engine}")
    if gpu.romfile:
        parts.append(f"romfile={qopt(_path(gpu.romfile, qemu_dir, platform))}")
    return ",".join(parts)


def drive_tokens(m: Machine, machine_dir: str, platform: str) -> list[str]:
    out: list[str] = []
    for i, d in enumerate(m.drives):
        if d is None or not d.file:
            continue
        spec = (f"file={qopt(_path(d.file, machine_dir, platform))},"
                f"format={drive_format(d.format, d.file, machine_dir)}")
        if model.is_sata(i):
            port = model.SATA_SLOTS.index(i)
            out += ["-drive", f"{spec},if=none,id=sata{port}",
                    "-device", f"ide-hd,bus=sata.{port},drive=sata{port}"]
        else:
            media = "cdrom" if d.kind == "cdrom" else "disk"
            out += ["-drive", f"{spec},media={media},index={model.ata_index(m, i)}"]
    return out


def build_argv(m: Machine, qemu_dir: str, machine_dir: str,
               platform: str = paths.HOST_PLATFORM) -> list[str]:
    """The complete argv, first token = absolute path of the QEMU binary."""
    qd = qemu_dir
    argv: list[str] = [paths.join_path(qd, paths.qemu_binary_name(platform), platform)]

    argv += ["-L", _path(PC_BIOS_DIR, qd, platform)]
    argv += ["-M", model.MACHINE_TYPE, "-cpu", model.CPU_TYPE]
    argv += ["-bios", _path(model.FIRMWARE_FILE, qd, platform)]
    argv += ["-smp", str(int(m.smp))]
    if m.vnc.strip():
        argv += ["-display", "none", "-vnc", m.vnc.strip()]
    else:
        argv += ["-display", m.display]
    argv += ["-m", str(int(m.ram_mb))]
    argv += ["-boot", "d" if model.resolved_boot_kind(m) == "cdrom" else "c"]

    if m.gpu:
        argv += ["-vga", "none"]

    audio = paths.resolve_audio(m.audio, platform)
    argv += ["-audiodev", f"{audio},id={ONBOARD_AUDIODEV}",
             "-global", f"macio-newworld.audiodev={ONBOARD_AUDIODEV}"]
    extra = split_extra_args(m.extra_args, platform)
    if m.usb_audio and not any(t.split(",")[0] == "usb-audio" for t in extra):
        argv += ["-audiodev", f"{audio},id={USB_AUDIODEV}",
                 "-device", f"usb-audio,audiodev={USB_AUDIODEV}"]

    if m.gpu:
        argv += ["-device", gpu_option(m.gpu, qd, platform)]
    if m.usb_tablet:
        argv += ["-device", "usb-tablet"]

    argv += ["-nic", nic_option(m.network)]
    argv += drive_tokens(m, machine_dir, platform)
    argv += prom_env_tokens(m)
    if m.rtc_base.strip():
        argv += ["-rtc", f"base={m.rtc_base.strip()}"]
    argv += extra
    return argv


# QEMU writes nvram.img itself; a sudo run gets it back.
OWNED_SETTINGS_FILES = (model.NVRAM_FILE,)


def extra_count(m: Machine, platform: str = paths.HOST_PLATFORM) -> int:
    return len(split_extra_args(m.extra_args, platform))


def render_shell(argv: list[str], sudo: bool = False, extra: int = 0) -> str:
    return paths.render_shell(argv, HEADER_NOTE, OWNED_SETTINGS_FILES, sudo, extra)


def render_bat(argv: list[str], extra: int = 0, title: str = "") -> str:
    return paths.render_bat(argv, HEADER_NOTE, extra, title)


def render_launcher(argv: list[str], platform: str = paths.HOST_PLATFORM, sudo: bool = False,
                    extra: int = 0, title: str = "") -> str:
    return paths.render_launcher(argv, HEADER_NOTE, platform, sudo, OWNED_SETTINGS_FILES, extra,
                                 title)


def launcher_text(m: Machine, qemu_dir: str, machine_dir: str,
                  platform: str = paths.HOST_PLATFORM) -> str:
    return render_launcher(build_argv(m, qemu_dir, machine_dir, platform), platform,
                           needs_sudo(m, platform), extra_count(m, platform), m.name)


def write_launcher(m: Machine, qemu_dir: str, machine_dir: str,
                   platform: str = paths.HOST_PLATFORM):
    """Writes the launcher only; the NVRAM file is QEMU's to create."""
    from pathlib import Path
    import os
    import stat
    argv = build_argv(m, qemu_dir, machine_dir, platform)
    text = render_launcher(argv, platform, needs_sudo(m, platform), extra_count(m, platform),
                           m.name)
    path = Path(machine_dir) / paths.launcher_name(platform)
    path.write_text(text, encoding="utf-8", newline="")
    if not paths.is_windows(platform):
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path, argv
