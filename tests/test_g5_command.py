"""Golden-output tests for qemugui.g5_command / qemugui.g5_model
(headless, no Tk).

Run:  python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from qemugui import g5_command as command  # noqa: E402
from qemugui import g5_model as model  # noqa: E402
from qemugui import paths  # noqa: E402
from qemugui.g5_model import Machine, Drive, Gpu, Network, PromEnv, HostFwd  # noqa: E402

FIXTURES = HERE / "fixtures"
QD = "/Applications/qemu-g5"
MD = "/Machines/Leopard"

BASE = [f"{QD}/qemu-system-ppc64",
        "-L", f"{QD}/pc-bios",
        "-M", "mac99,via=pmu",
        "-cpu", "970fx",
        "-bios", f"{QD}/openbios-qemu.elf"]
NIC = ["-nic", "user,model=sungem,mac=00:05:02:12:34:56"]
ONBOARD = ["-audiodev", "coreaudio,id=snd0", "-global", "macio-newworld.audiodev=snd0"]
USB_AUDIO = ["-audiodev", "coreaudio,id=usb", "-device", "usb-audio,audiodev=usb"]


def load_fixture(name: str) -> Machine:
    """CD audio out is off here: the golden lines have no ide-cd audiodev
    (see test_cd_drives)."""
    m = Machine.load(FIXTURES / name)
    m.cd_audio = False
    return m


def argv_of(m: Machine, platform: str = "darwin", qd: str = QD, md: str = MD) -> list[str]:
    return command.build_argv(m, qd, md, platform)


def plain(**kw) -> Machine:
    kw.setdefault("name", "t")
    kw.setdefault("display", "cocoa")
    return Machine(**kw)


def pairs(argv: list[str]) -> list[list[str]]:
    return command.group_options(argv)


def audiodevs(argv: list[str]) -> list[str]:
    return [g[1] for g in pairs(argv) if g[0] == "-audiodev"]


class GoldenCommandLines(unittest.TestCase):
    """Whole argv lists, compared exactly."""

    def test_radeon9800_gl_fast_raster_threads(self):
        self.assertEqual(argv_of(load_fixture("g5-r350.json")), BASE + [
            "-smp", "2", "-display", "cocoa", "-m", "4096", "-boot", "c",
            "-vga", "none"] + ONBOARD + USB_AUDIO + [
            "-device", "ati-radeon9800,bus=pci.0,addr=0x10,gl=fast,raster-threads=4,"
                       f"romfile={QD}/ati_oem_9800xt_123_agp_full.rom"] + NIC + [
            "-drive", "file=/images/MacOSX-10.5.iso,format=raw,media=cdrom,index=2",
            "-drive", "file=/images/leopard.qcow2,format=qcow2,if=none,id=sata0",
            "-device", "ide-hd,bus=sata.0,drive=sata0",
            "-prom-env", "auto-boot?=true"])

    def test_rv100(self):
        self.assertEqual(argv_of(load_fixture("g5-rv100.json")), BASE + [
            "-smp", "1", "-display", "cocoa", "-m", "2048", "-boot", "c",
            "-vga", "none"] + ONBOARD + [
            "-device", "ati-vga,model=rv100,bus=pci.0,addr=0x10,agp=on,raster-threads=1,"
                       f"romfile={QD}/ati_radeon_7000_208.rom"] + NIC + [
            "-drive", "file=/images/leopard.img,format=raw,media=disk,index=0",
            "-prom-env", "auto-boot?=true", "-prom-env", "boot-args=-v"])

    def test_ata_disk(self):
        m = plain(drives=[Drive("disk", "/hd/a.img"), None, None, None])
        self.assertEqual(argv_of(m), BASE + [
            "-smp", "1", "-display", "cocoa", "-m", "2048", "-boot", "c"] + ONBOARD + NIC + [
            "-drive", "file=/hd/a.img,format=raw,media=disk,index=0",
            "-prom-env", "auto-boot?=true"])

    def test_sata_disks(self):
        m = plain(drives=[None, None, Drive("disk", "/hd/a.qcow2", "qcow2"),
                          Drive("disk", "/hd/b.img")])
        self.assertEqual(argv_of(m), BASE + [
            "-smp", "1", "-display", "cocoa", "-m", "2048", "-boot", "c"] + ONBOARD + NIC + [
            "-drive", "file=/hd/a.qcow2,format=qcow2,if=none,id=sata0",
            "-device", "ide-hd,bus=sata.0,drive=sata0",
            "-drive", "file=/hd/b.img,format=raw,if=none,id=sata1",
            "-device", "ide-hd,bus=sata.1,drive=sata1",
            "-prom-env", "auto-boot?=true"])

    def test_cd_boot(self):
        m = plain(boot_slot=model.ATA_MASTER, cd_audio=False,
                  drives=[Drive("cdrom", "/iso/install.iso"), None,
                          Drive("disk", "/hd/a.img"), None])
        self.assertEqual(argv_of(m), BASE + [
            "-smp", "1", "-display", "cocoa", "-m", "2048", "-boot", "d"] + ONBOARD + NIC + [
            "-drive", "file=/iso/install.iso,format=raw,media=cdrom,index=2",
            "-drive", "file=/hd/a.img,format=raw,if=none,id=sata0",
            "-device", "ide-hd,bus=sata.0,drive=sata0",
            "-prom-env", "auto-boot?=true"])

    def test_onboard_and_usb_audio_have_their_own_backends(self):
        m = plain(usb_audio=True)
        self.assertEqual(argv_of(m), BASE + [
            "-smp", "1", "-display", "cocoa", "-m", "2048", "-boot", "c"] + ONBOARD + USB_AUDIO
            + NIC + ["-prom-env", "auto-boot?=true"])

    def test_vnc(self):
        m = plain(vnc=":5")
        self.assertEqual(argv_of(m), BASE + [
            "-smp", "1", "-display", "none", "-vnc", ":5", "-m", "2048", "-boot", "c"]
            + ONBOARD + NIC + ["-prom-env", "auto-boot?=true"])

    def test_windows(self):
        m = load_fixture("g5-r350.json")
        q, d = r"C:\qemu-g5", r"C:\Machines\Leopard"
        self.assertEqual(argv_of(m, "win32", q, d), [
            r"C:\qemu-g5\qemu-system-ppc64.exe",
            "-L", r"C:\qemu-g5\pc-bios",
            "-M", "mac99,via=pmu", "-cpu", "970fx",
            "-bios", r"C:\qemu-g5\openbios-qemu.elf",
            "-smp", "2", "-display", "cocoa", "-m", "4096", "-boot", "c",
            "-vga", "none",
            "-audiodev", "dsound,id=snd0", "-global", "macio-newworld.audiodev=snd0",
            "-audiodev", "dsound,id=usb", "-device", "usb-audio,audiodev=usb",
            "-device", "ati-radeon9800,bus=pci.0,addr=0x10,gl=fast,raster-threads=4,"
                       r"romfile=C:\qemu-g5\ati_oem_9800xt_123_agp_full.rom"] + NIC + [
            "-drive", r"file=C:\images\MacOSX-10.5.iso,format=raw,media=cdrom,index=2",
            "-drive", r"file=C:\images\leopard.qcow2,format=qcow2,if=none,id=sata0",
            "-device", "ide-hd,bus=sata.0,drive=sata0",
            "-prom-env", "auto-boot?=true"])

    def test_windows_bat_rendering(self):
        m = load_fixture("g5-r350.json")
        text = command.launcher_text(m, r"C:\qemu-g5", r"C:\Machines\Leopard", "win32")
        lines = text.split("\r\n")
        self.assertEqual(lines[0], "@echo off")
        self.assertIn(r"C:\qemu-g5\qemu-system-ppc64.exe ^", lines)
        self.assertIn('-M "mac99,via=pmu" ^', lines)
        self.assertIn('-audiodev "dsound,id=snd0" ^', lines)
        self.assertIn("-global macio-newworld.audiodev=snd0 ^", lines)
        self.assertIn('-device "ide-hd,bus=sata.0,drive=sata0" ^', lines)
        self.assertIn("-prom-env auto-boot?=true", lines)
        self.assertNotIn("sudo", text)
        self.assertNotIn("coreaudio", text)

    def test_macos_shell_rendering(self):
        text = command.launcher_text(load_fixture("g5-r350.json"), QD, MD, "darwin")
        lines = text.splitlines()
        self.assertEqual(lines[:3], ["#!/bin/bash",
                                     f"# Written by {paths.APP_NAME}. Do not edit.",
                                     'cd "$(dirname "$0")"'])
        body = [ln for ln in lines if ln.startswith("-") or ln.startswith("/")]
        for ln in body[:-1]:
            self.assertTrue(ln.endswith(" \\"), ln)
        self.assertEqual(body[-1], "-prom-env 'auto-boot?=true'")
        self.assertIn("-device ide-hd,bus=sata.0,drive=sata0 \\", lines)

    def test_macos_prom_env_values_quoted(self):
        m = load_fixture("g5-r350.json")
        m.prom_env.boot_args = "-v"
        lines = command.launcher_text(m, QD, MD, "darwin").splitlines()
        self.assertIn("-prom-env 'boot-args=-v'", lines)


class Graphics(unittest.TestCase):

    def gpu_arg(self, gpu: Gpu) -> str:
        argv = argv_of(plain(gpu=gpu))
        self.assertEqual(argv[argv.index("-vga") + 1], "none")
        return next(t for t in argv if t.startswith("ati-"))

    def test_no_card_means_the_machines_own_vga(self):
        argv = argv_of(plain())
        self.assertNotIn("-vga", argv)
        self.assertFalse(any(t.startswith("ati-") for t in argv))

    def test_radeon9800_defaults(self):
        self.assertEqual(self.gpu_arg(Gpu("radeon9800")),
                         "ati-radeon9800,bus=pci.0,addr=0x10,gl=off")

    def test_radeon9800_every_setting(self):
        self.assertEqual(self.gpu_arg(Gpu("radeon9800", "/roms/r,9800.rom", "on", 1, "off")),
                         "ati-radeon9800,bus=pci.0,addr=0x10,gl=on,raster-threads=1,"
                         "async-engine=off,romfile=/roms/r,,9800.rom")

    def test_rv100_agp_off_and_no_gl(self):
        self.assertEqual(self.gpu_arg(Gpu("rv100", None, "fast", 0, "on", False)),
                         "ati-vga,model=rv100,bus=pci.0,addr=0x10,agp=off,async-engine=on")

    def test_validation(self):
        errors, warnings = model.validate(plain(gpu=Gpu("radeon9800")), None, "darwin",
                                          check_files=False)
        self.assertEqual(errors, [])
        self.assertIn("The graphics card has no ROM file.", warnings)
        for bad in (Gpu("voodoo", "x.rom"), Gpu("radeon9800", "x.rom", "verify"),
                    Gpu("radeon9800", "x.rom", raster_threads=9),
                    Gpu("rv100", "x.rom", async_engine="maybe")):
            errors, _ = model.validate(plain(gpu=bad), None, "darwin", check_files=False)
            self.assertEqual(len(errors), 1, bad)

    def test_missing_rom_and_firmware_are_reported(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / paths.qemu_binary_name("darwin")).write_text("")
            m = plain(gpu=Gpu("radeon9800", "gone.rom"))
            _, warnings = model.validate(m, td, "darwin")
            self.assertTrue(any("ROM is missing" in w for w in warnings), warnings)
            self.assertTrue(any("firmware is missing" in w for w in warnings), warnings)
            (Path(td) / "gone.rom").write_text("")
            (Path(td) / model.FIRMWARE_FILE).write_text("")
            _, warnings = model.validate(m, td, "darwin")
            self.assertEqual(warnings, [])

    def test_roms_in_lists_rom_files_only(self):
        with tempfile.TemporaryDirectory() as td:
            for n in ("b.rom", "A.ROM", "openbios-qemu.elf", "notes.txt"):
                (Path(td) / n).write_text("")
            (Path(td) / "dir.rom").mkdir()
            self.assertEqual(model.roms_in(td), ["A.ROM", "b.rom"])
        self.assertEqual(model.roms_in(None), [])
        self.assertEqual(model.roms_in("/no/such/folder"), [])

    def test_bios_in_lists_firmware_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            for n in ("b.rom", "x.ELF", "OpenBIOS-old", "openbios-qemu.elf", "notes.txt"):
                (Path(td) / n).write_text("")
            (Path(td) / "dir.elf").mkdir()
            self.assertEqual(model.bios_in(td), ["OpenBIOS-old", "openbios-qemu.elf", "x.ELF"])
        self.assertEqual(model.bios_in(None), [])

    def test_bios_default_and_old_record(self):
        self.assertEqual(plain().bios, "openbios-qemu.elf")
        old = json.loads(plain().to_json())
        del old["bios"]
        m = Machine.from_dict(old)
        self.assertEqual(m.bios, "openbios-qemu.elf")
        self.assertEqual(argv_of(m)[:9], BASE)
        old["bios"] = ""
        self.assertEqual(Machine.from_dict(old).bios, "openbios-qemu.elf")

    def test_bios_custom_file(self):
        m = plain(bios="openbios-test.elf")
        self.assertEqual(argv_of(m)[7:9], ["-bios", f"{QD}/openbios-test.elf"])
        self.assertEqual(Machine.from_json(m.to_json()).bios, "openbios-test.elf")
        m = plain(bios="/elsewhere/fw.elf")
        self.assertEqual(argv_of(m)[7:9], ["-bios", "/elsewhere/fw.elf"])

    def test_bios_windows_paths(self):
        q = r"C:\qemu-g5"
        m = plain(bios="openbios-test.elf")
        argv = argv_of(m, "win32", q, r"C:\m")
        self.assertEqual(argv[argv.index("-bios") + 1], r"C:\qemu-g5\openbios-test.elf")
        m = plain(bios=r"D:\fw dir\fw.elf")
        argv = argv_of(m, "win32", q, r"C:\m")
        self.assertEqual(argv[argv.index("-bios") + 1], r"D:\fw dir\fw.elf")
        bat = command.render_bat(argv, 0)
        self.assertIn(r'"D:\fw dir\fw.elf"', bat)

    def test_missing_custom_bios_warns(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "qemu-system-ppc64").write_text("")
            (Path(td) / "openbios-qemu.elf").write_text("")
            m = plain(bios="other.elf")
            _, warnings = model.validate(m, td, "darwin")
            self.assertTrue(any("other.elf" in w for w in warnings), warnings)


class Drives(unittest.TestCase):

    def errors(self, drives) -> list[str]:
        return model.validate(plain(drives=drives), None, "darwin", check_files=False)[0]

    def test_positions_take_what_the_bus_holds(self):
        self.assertEqual(model.slot_kinds(model.ATA_MASTER), ("disk", "cdrom"))
        self.assertEqual(model.slot_kinds(model.ATA_SLAVE), ("cdrom",))
        self.assertEqual(model.slot_kinds(model.SATA_A), ("disk",))
        self.assertEqual(model.slot_kinds(model.SATA_B), ("disk",))

    def test_a_cd_on_sata_is_refused(self):
        self.assertTrue(any("SATA port A takes a hard disk" in e
                            for e in self.errors([None, None, Drive("cdrom", "/c.iso"), None])))

    def test_a_hard_disk_on_the_ata_slave_is_refused(self):
        self.assertTrue(any("ATA-100 slave takes a CD" in e
                            for e in self.errors([Drive("disk", "/a.img"),
                                                  Drive("disk", "/b.img"), None, None])))

    def test_ata_indexes_never_send_a_drive_to_sata(self):
        def drive_args(drives):
            return [t for t in argv_of(plain(drives=drives)) if t.startswith("file=")]
        self.assertEqual(drive_args([Drive("disk", "/a"), Drive("cdrom", "/c"), None, None]),
                         ["file=/a,format=raw,media=disk,index=0",
                          "file=/c,format=raw,media=cdrom,index=2"])
        self.assertEqual(drive_args([Drive("cdrom", "/c1"), Drive("cdrom", "/c2"), None, None]),
                         ["file=/c1,format=raw,media=cdrom,index=0",
                          "file=/c2,format=raw,media=cdrom,index=2"])
        self.assertEqual(drive_args([None, Drive("cdrom", "/c"), None, None]),
                         ["file=/c,format=raw,media=cdrom,index=2"])

    def test_boot_follows_the_marked_drive_kind(self):
        m = plain(drives=[Drive("cdrom", "/c.iso"), None, Drive("disk", "/a.img"), None])
        self.assertIn("c", argv_of(m)[argv_of(m).index("-boot") + 1])
        m.boot_slot = model.ATA_MASTER
        self.assertEqual(argv_of(m)[argv_of(m).index("-boot") + 1], "d")
        m.boot_slot = model.SATA_A
        self.assertEqual(argv_of(m)[argv_of(m).index("-boot") + 1], "c")

    def test_a_disk_behind_the_ata_disk_does_not_boot_first(self):
        m = plain(boot_slot=model.SATA_A,
                  drives=[Drive("disk", "/a.img"), None, Drive("disk", "/b.img"), None])
        _, warnings = model.validate(m, None, "darwin", check_files=False)
        self.assertTrue(any("ATA-100 master (also a hard disk) will boot first" in w
                            for w in warnings), warnings)
        m.boot_slot = model.ATA_MASTER
        self.assertEqual(model.validate(m, None, "darwin", check_files=False)[1], [])

    def test_marked_but_empty_warns(self):
        m = plain(boot_slot=model.SATA_B)
        self.assertTrue(any("is marked Boot but is empty" in w
                            for w in model.validate(m, None, "darwin", check_files=False)[1]))

    def test_new_disks_go_to_sata_first(self):
        m = plain()
        self.assertEqual(m.first_unfilled_disk_slot(), model.SATA_A)
        m.drives[model.SATA_A] = Drive("disk", "/a.img")
        self.assertEqual(m.first_unfilled_disk_slot(), model.SATA_B)
        m.drives[model.SATA_B] = Drive("disk", "/b.img")
        self.assertEqual(m.first_unfilled_disk_slot(), model.ATA_MASTER)


class Options(unittest.TestCase):

    def test_cpus_and_memory_limits(self):
        for smp, ok in ((1, True), (2, True), (4, True), (8, True), (16, True), (17, False), (0, False)):
            errors, _ = model.validate(plain(smp=smp), None, "darwin", check_files=False)
            self.assertEqual(errors == [], ok, smp)
        for ram, ok in ((2048, True), (8192, True), (16384, True), (16385, False), (128, False)):
            errors, _ = model.validate(plain(ram_mb=ram), None, "darwin", check_files=False)
            self.assertEqual(errors == [], ok, ram)

    def test_usb_tablet_is_off_by_default(self):
        self.assertFalse(model.new_machine("t").usb_tablet)
        self.assertNotIn("usb-tablet", argv_of(plain()))
        self.assertEqual(pairs(argv_of(plain(usb_tablet=True))).count(["-device", "usb-tablet"]), 1)

    def test_prom_env(self):
        m = plain(prom_env=PromEnv(False, "hd:,\\\\:tbxi", "-v"))
        argv = argv_of(m)
        self.assertEqual(argv[argv.index("-prom-env"):],
                         ["-prom-env", "auto-boot?=false",
                          "-prom-env", "boot-device=hd:,\\\\:tbxi",
                          "-prom-env", "boot-args=-v"])

    def test_no_nvram_option_is_ever_passed(self):
        argv = argv_of(load_fixture("g5-r350.json"))
        self.assertFalse(any("nvram" in t for t in argv), argv)

    def test_audio_none_and_sdl(self):
        for audio in ("none", "sdl"):
            m = plain(audio=audio, usb_audio=True)
            self.assertEqual(audiodevs(argv_of(m)), [f"{audio},id=snd0", f"{audio},id=usb"])

    def test_usb_audio_not_doubled_by_extra_args(self):
        m = plain(usb_audio=True, extra_args="-device usb-audio,audiodev=snd0")
        argv = argv_of(m)
        self.assertEqual(audiodevs(argv), ["coreaudio,id=snd0"])
        self.assertEqual(argv[-2:], ["-device", "usb-audio,audiodev=snd0"])

    def test_vnc_validation(self):
        for spec, ok in ((":1", True), ("127.0.0.1:3", True), ("one", False)):
            errors, _ = model.validate(plain(vnc=spec), None, "darwin", check_files=False)
            self.assertEqual(errors == [], ok, spec)

    def test_comma_in_path_is_escaped_for_qemu(self):
        m = plain(drives=[Drive("disk", "/a,b/c.img"), None, None, None])
        self.assertIn("file=/a,,b/c.img,format=raw,media=disk,index=0", argv_of(m))

    def test_extra_args_appended_verbatim(self):
        m = plain(extra_args="-qmp unix:/tmp/live.sock,server=on,wait=off")
        self.assertEqual(argv_of(m)[-2:], ["-qmp", "unix:/tmp/live.sock,server=on,wait=off"])


class ExtraArgsQuoting(unittest.TestCase):
    TEXT = "-prom-env 'boot-args=-v' -prom-env 'boot-args=-v -x' -name \"it's\" -x 'a?b'"
    WANT = ["-prom-env", "boot-args=-v", "-prom-env", "boot-args=-v -x", "-name", "it's",
            "-x", "a?b"]

    def machine(self) -> Machine:
        return plain(extra_args=self.TEXT)

    def test_argv_is_the_same_on_every_host(self):
        for platform in ("darwin", "win32", "linux"):
            self.assertEqual(argv_of(self.machine(), platform)[-len(self.WANT):], self.WANT)

    def test_bat_requotes_each_token(self):
        m = self.machine()
        argv = command.build_argv(m, r"C:\q", r"C:\m", "win32")
        lines = command.render_bat(argv, command.extra_count(m, "win32")).split("\r\n")
        self.assertIn('-prom-env "boot-args=-v" ^', lines)
        self.assertIn('-prom-env "boot-args=-v -x" ^', lines)
        self.assertIn("-name it's ^", lines)
        self.assertIn("-x a?b", lines)

    @unittest.skipIf(paths.is_windows(), "needs bash")
    def test_run_command_reproduces_the_argv_when_executed(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            qd = Path(td) / "q"
            qd.mkdir()
            fake = qd / paths.qemu_binary_name("darwin")
            fake.write_text('#!/bin/bash\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
            fake.chmod(0o755)
            m = load_fixture("g5-r350.json")
            m.extra_args = self.TEXT
            path, argv = command.write_launcher(m, str(qd), td, "darwin")
            got = subprocess.run([str(path)], capture_output=True, text=True, check=True)
            self.assertEqual(got.stdout.splitlines(), argv[1:])


class RtcBase(unittest.TestCase):

    def test_forms(self):
        self.assertNotIn("-rtc", argv_of(plain()))
        for value in ("localtime", "utc", "2005-04-29", "2005-04-29T10:30:00"):
            m = plain(rtc_base=value)
            self.assertEqual(model.validate(m, None, "darwin", check_files=False)[0], [])
            argv = argv_of(m)
            self.assertEqual(argv[argv.index("-rtc") + 1], f"base={value}")
        errors, _ = model.validate(plain(rtc_base="29/04/2005"), None, "darwin", check_files=False)
        self.assertTrue(any("Date and time" in e for e in errors))


class Networking(unittest.TestCase):

    def nic(self, mode, ifname="", platform="darwin"):
        argv = argv_of(plain(network=Network(mode, "00:05:02:12:34:56", ifname)), platform)
        return argv[argv.index("-nic") + 1]

    def test_modes(self):
        self.assertEqual(self.nic("none"), "none")
        self.assertEqual(self.nic("vmnet-bridged", "en0"),
                         "vmnet-bridged,ifname=en0,model=sungem,mac=00:05:02:12:34:56")
        self.assertEqual(self.nic("vmnet-shared"),
                         "vmnet-shared,model=sungem,mac=00:05:02:12:34:56")
        self.assertEqual(self.nic("tap", "TAP-Windows Adapter V9", "win32"),
                         "tap,ifname=TAP-Windows Adapter V9,model=sungem,mac=00:05:02:12:34:56")

    def test_vmnet_command_has_sudo_prefix_and_chown_tail(self):
        m = plain(network=Network("vmnet-bridged", "00:05:02:12:34:56", "en0"))
        lines = command.launcher_text(m, QD, MD, "darwin").splitlines()
        self.assertTrue(any(ln.startswith(f"sudo {QD}/qemu-system-ppc64") for ln in lines))
        self.assertTrue(lines[-1].startswith("sudo -n chown "), lines[-1])
        self.assertTrue(lines[-1].split("}\" ")[1].startswith("nvram.img"), lines[-1])
        self.assertNotIn("pram.img", lines[-1])

    def test_bat_never_has_sudo(self):
        m = plain(network=Network("tap", "00:05:02:12:34:56", "TAP-Windows Adapter V9"))
        self.assertNotIn("sudo", command.launcher_text(m, r"C:\q", r"C:\m", "win32"))

    def test_hosts_offer_their_own_modes(self):
        self.assertIn("tap", model.network_modes_for_host("win32"))
        self.assertNotIn("vmnet-bridged", model.network_modes_for_host("win32"))
        self.assertIn("vmnet-bridged", model.network_modes_for_host("darwin"))
        self.assertNotIn("tap", model.network_modes_for_host("darwin"))


class JsonRoundTrip(unittest.TestCase):

    def test_fixtures_round_trip(self):
        for f in sorted(FIXTURES.glob("g5-*.json")):
            m = Machine.load(f)
            self.assertEqual(m, Machine.from_json(m.to_json()), f.name)
            self.assertEqual(json.loads(m.to_json())["schema"], model.SCHEMA)

    def test_full_record_round_trip(self):
        m = Machine(name="Every field", ram_mb=8192, smp=2, display="sdl", vnc=":2",
                    audio="none", usb_audio=True, usb_tablet=True,
                    gpu=Gpu("rv100", "card.rom", "on", 3, "off", False),
                    network=Network("user", "00:11:22:33:44:55"), boot_slot=1,
                    drives=[Drive("disk", "/a.img", "qcow2"), Drive("cdrom", "/c.iso"),
                            Drive("disk", "/s.img"), None],
                    prom_env=PromEnv(False, "cd:,\\:tbxi", "-v"), rtc_base="localtime",
                    extra_args="-qmp none", notes="n\u00f6tes")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "machine.json"
            m.save(p)
            self.assertEqual(Machine.load(p), m)

    def test_missing_keys_take_defaults(self):
        m = Machine.from_dict({"name": "old"})
        self.assertEqual((m.ram_mb, m.smp, m.gpu, m.usb_tablet, m.drives),
                         (model.RAM_DEFAULT, 1, None, False, [None] * 4))
        g = Gpu.from_dict({"romfile": "x.rom"})
        self.assertEqual((g.model, g.gl, g.raster_threads, g.async_engine, g.agp),
                         ("radeon9800", "off", 0, "auto", True))


class NothingIsChosenForYou(unittest.TestCase):

    def test_a_new_machine_has_every_file_field_empty(self):
        m = model.new_machine("Fresh")
        for name, value in model.file_fields(m).items():
            self.assertEqual(value, "", f"{name} was filled in")
        self.assertIsNone(m.gpu)
        self.assertEqual(model.start_blockers(m), [])
        self.assertEqual((m.smp, m.ram_mb, m.boot_slot), (1, model.RAM_DEFAULT, None))


class NvramIsQemus(unittest.TestCase):
    """QEMU makes and checks the G5's 16 KB nvram.img itself; the GUI never
    writes one and Reset NVRAM removes only that file."""

    def test_write_launcher_leaves_the_nvram_alone(self):
        with tempfile.TemporaryDirectory() as td:
            command.write_launcher(load_fixture("g5-r350.json"), QD, td, "darwin")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()), ["run.command"])
            nvram = Path(td) / "nvram.img"
            nvram.write_bytes(b"\x5a" * 16384)
            command.write_launcher(load_fixture("g5-r350.json"), QD, td, "darwin")
            self.assertEqual(nvram.read_bytes(), b"\x5a" * 16384)

    def test_reset_removes_only_nvram(self):
        with tempfile.TemporaryDirectory() as td:
            lib = model.Library(td)
            lib.save(model.new_machine("G5"))
            folder = lib.folder("G5")
            for n in ("nvram.img", "pram.img", "disk.qcow2", "nvram.img.bak"):
                (folder / n).write_bytes(b"x")
            self.assertEqual(lib.saved_settings_status("G5"), {"nvram.img": 1})
            self.assertEqual(lib.clear_saved_settings("G5"), ["nvram.img"])
            self.assertEqual(sorted(p.name for p in folder.iterdir()),
                             ["disk.qcow2", "machine.json", "nvram.img.bak", "pram.img"])
            self.assertEqual(lib.saved_settings_status("G5"), {"nvram.img": None})
            self.assertEqual(lib.clear_saved_settings("G5"), [])


class LibraryOps(unittest.TestCase):

    def test_create_save_duplicate_delete(self):
        with tempfile.TemporaryDirectory() as td:
            lib = model.Library(td)
            lib.save(model.new_machine("Leopard"))
            (lib.folder("Leopard") / "nvram.img").write_bytes(b"\xff" * 16384)
            (lib.folder("Leopard") / "leopard.qcow2").write_bytes(b"QFI\xfb")
            lib.duplicate("Leopard", "Leopard copy")
            self.assertEqual((lib.folder("Leopard copy") / "nvram.img").stat().st_size, 16384)
            removed, kept = lib.delete_preview("Leopard")
            self.assertEqual(removed, ["machine.json", "nvram.img"])
            self.assertEqual(kept, ["leopard.qcow2"])
            result = lib.delete("Leopard")
            self.assertFalse(result.folder_removed)
            self.assertEqual(result.kept_images, ["leopard.qcow2"])
            lib.delete("Leopard copy")
            self.assertEqual(lib.names(), [])

    def test_rename_repoints_images_inside_the_folder(self):
        with tempfile.TemporaryDirectory() as td:
            lib = model.Library(td)
            m = model.new_machine("A")
            lib.save(m)
            m.drives[model.SATA_A] = Drive("disk", str(lib.folder("A") / "d.img"))
            lib.save(m)
            m.name = "B"
            lib.save(m, old_name="A")
            self.assertEqual(m.drives[model.SATA_A].file, str(lib.folder("B") / "d.img"))

    def test_write_launcher_is_executable_and_regenerated(self):
        with tempfile.TemporaryDirectory() as td:
            m = load_fixture("g5-r350.json")
            path, _ = command.write_launcher(m, QD, td, "darwin")
            self.assertEqual(path.name, "run.command")
            self.assertTrue(path.stat().st_mode & 0o111)
            m.ram_mb = 8192
            path, _ = command.write_launcher(m, QD, td, "darwin")
            self.assertIn("-m 8192", path.read_text())

    def test_windows_launcher_file(self):
        with tempfile.TemporaryDirectory() as td:
            path, argv = command.write_launcher(load_fixture("g5-r350.json"), r"C:\g5", td,
                                                "win32")
            self.assertEqual(path.name, "run.bat")
            self.assertEqual(argv[0], r"C:\g5\qemu-system-ppc64.exe")
            raw = path.read_bytes().decode("utf-8")
            self.assertIn(" ^\r\n", raw)
            self.assertNotIn(" \\\r\n", raw)
            self.assertIn("title Leopard R350\r\n", raw)
            self.assertTrue(raw.endswith("if errorlevel 1 pause\r\n"))


class Platform(unittest.TestCase):

    def setUp(self):
        self.saved = paths.HOST_PLATFORM

    def tearDown(self):
        paths.HOST_PLATFORM = self.saved

    def test_binary_names(self):
        self.assertEqual(paths.qemu_binary_name("darwin"), "qemu-system-ppc64")
        self.assertEqual(paths.qemu_binary_name("win32"), "qemu-system-ppc64.exe")
        self.assertEqual(paths.launcher_name("win32"), "run.bat")
        self.assertEqual(paths.launcher_name("darwin"), "run.command")

    def test_default_sound_and_display_per_host(self):
        self.assertEqual(model.default_audio_label("darwin"), "CoreAudio")
        self.assertEqual(model.default_audio_label("win32"), "DirectSound")
        self.assertEqual(model.ifname_label("win32"), "Tap device name:")
        for platform, display in (("darwin", "cocoa"), ("win32", "sdl"), ("linux", "sdl")):
            paths.HOST_PLATFORM = platform
            m = model.new_machine("Fresh")
            self.assertEqual((m.display, m.network.mode, m.audio), (display, "user", "default"))

    def test_cocoa_warns_off_a_mac(self):
        _, warnings = model.validate(plain(display="cocoa"), None, "win32", check_files=False)
        self.assertIn("'cocoa' only works on a Mac.", warnings)


class ImageFormatDetection(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.addCleanup(self.td.cleanup)

    def _image(self, name: str, head: bytes) -> Path:
        p = self.dir / name
        p.write_bytes(head + b"\0" * 64)
        return p

    def test_magic_and_name(self):
        self.assertEqual(model.detect_format(str(self._image("d.img", b"QFI\xfb"))), "qcow2")
        self.assertEqual(model.detect_format(str(self._image("d.qcow2", b"x"))), "qcow2")
        self.assertEqual(model.detect_format(str(self._image("p.img", b"\0"))), "raw")
        self.assertEqual(model.detect_format(str(self.dir / "none.qcow2")), "raw")

    def test_a_record_saved_as_raw_is_repaired_on_both_buses(self):
        self._image("ata.img", b"QFI\xfb")
        self._image("sata.img", b"QFI\xfb")
        m = plain(drives=[Drive("disk", "ata.img"), None, Drive("disk", "sata.img"), None])
        argv = command.build_argv(m, QD, str(self.dir), "darwin")
        got = [t for t in argv if t.startswith("file=")]
        self.assertEqual(len(got), 2)
        for tok in got:
            self.assertIn("format=qcow2", tok)

    def test_a_chosen_format_is_never_overridden(self):
        self._image("plain.img", b"\0")
        m = plain(drives=[None, None, Drive("disk", "plain.img", "qcow2"), None])
        argv = command.build_argv(m, QD, str(self.dir), "darwin")
        self.assertTrue(any("plain.img" in t and "format=qcow2" in t for t in argv), argv)


if __name__ == "__main__":
    unittest.main()


class PortForwarding(unittest.TestCase):
    MAC = "00:05:02:12:34:56"
    BASE = f"user,model=sungem,mac={MAC}"

    def nic(self, rules, mode="user", platform="darwin"):
        m = plain(network=Network(mode, self.MAC, "", rules))
        argv = argv_of(m, platform)
        return argv[argv.index("-nic") + 1]

    def test_no_rules_leaves_the_nic_alone(self):
        self.assertEqual(self.nic([]), self.BASE)
        self.assertEqual(self.nic([HostFwd(), HostFwd()]), self.BASE)

    def test_one_rule(self):
        self.assertEqual(self.nic([HostFwd("tcp", "8080", "80")]),
                         self.BASE + ",hostfwd=tcp::8080-:80")

    def test_four_rules_tcp_udp_skip_empty_rows(self):
        rules = [HostFwd("tcp", "8080", "80"), HostFwd(), HostFwd("udp", "5353", "53"),
                 HostFwd("tcp", "2222", "22"), HostFwd("udp", "69", "69")]
        self.assertEqual(self.nic(rules), self.BASE + ",hostfwd=tcp::8080-:80"
                         ",hostfwd=udp::5353-:53,hostfwd=tcp::2222-:22"
                         ",hostfwd=udp::69-:69")

    def test_ignored_when_the_network_is_not_user(self):
        self.assertEqual(self.nic([HostFwd("tcp", "8080", "80")], "vmnet-shared"),
                         f"vmnet-shared,model=sungem,mac={self.MAC}")
        self.assertEqual(self.nic([HostFwd("tcp", "8080", "80")], "none"), "none")

    def test_win32_bat_quotes_the_nic_token(self):
        m = plain(network=Network("user", self.MAC, "", [HostFwd("tcp", "8080", "80")]))
        argv = command.build_argv(m, r"C:\q", r"C:\m", "win32")
        self.assertEqual(argv[argv.index("-nic") + 1], self.BASE + ",hostfwd=tcp::8080-:80")
        bat = command.render_bat(argv, command.extra_count(m, "win32"))
        self.assertIn(self.BASE + ",hostfwd=tcp::8080-:80", bat)
        self.assertEqual(bat.count("hostfwd="), 1)

    def test_round_trip_and_old_records(self):
        net = Network("user", self.MAC, "", [HostFwd("udp", "5353", "53"),
                                             HostFwd("tcp", "2222", "22"), HostFwd()])
        d = json.loads(json.dumps(net.to_dict()))
        self.assertEqual(d["hostfwd"][0], {"proto": "udp", "host_port": "5353", "guest_port": "53"})
        self.assertEqual(len(d["hostfwd"]), 2)
        back = Network.from_dict(d)
        self.assertEqual([r.to_dict() for r in back.hostfwd], d["hostfwd"])
        self.assertEqual(Network.from_dict({"mode": "user", "mac": self.MAC}).hostfwd, [])
        self.assertNotIn("hostfwd", Network().to_dict())

    def test_old_record_with_guest_addr_loads_and_is_dropped(self):
        d = Network.from_dict({"mode": "user", "mac": self.MAC, "hostfwd": [
            {"proto": "tcp", "host_port": "2222", "guest_port": "22", "guest_addr": "10.0.2.20"}]})
        self.assertEqual(self.nic(d.hostfwd), self.BASE + ",hostfwd=tcp::2222-:22")
        self.assertEqual(d.to_dict()["hostfwd"],
                         [{"proto": "tcp", "host_port": "2222", "guest_port": "22"}])

    def check(self, rules, platform="darwin", **kw):
        m = plain(network=Network("user", self.MAC, "", rules), **kw)
        return model.validate(m, None, platform, check_files=False)

    def test_validation(self):
        for bad in (HostFwd("tcp", "0", "80"), HostFwd("tcp", "70000", "80"),
                    HostFwd("tcp", "abc", "80"), HostFwd("tcp", "80", ""),
                    HostFwd("tcp", "", "80"), HostFwd("icmp", "80", "80")):
            errors, _ = self.check([bad])
            self.assertTrue(errors, bad)
        errors, _ = self.check([HostFwd("tcp", "8080", "80"), HostFwd("udp", "65535", "1")])
        self.assertEqual(errors, [])

    def test_low_host_port_is_no_warning(self):
        for plat in ("darwin", "win32"):
            self.assertFalse(any("1024" in w for w in
                                 self.check([HostFwd("tcp", "80", "80")], plat)[1]))
