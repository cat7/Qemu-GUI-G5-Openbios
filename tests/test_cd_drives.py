"""CD images (cue, dmg), host optical drives and CD audio out."""
from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qemugui import g5_command as command  # noqa: E402
from qemugui import g5_model as model  # noqa: E402
from qemugui import optical, paths  # noqa: E402
from qemugui.g5_model import Machine, Drive  # noqa: E402

QD = "/Applications/q"
MD = "/m"
NAME = "drive:HL-DT-ST DVDRAM GP57EB40"


def machine(*drives) -> Machine:
    """Positions in order: ATA-100 master, ATA-100 slave, SATA A, SATA B."""
    m = Machine(name="C", display="cocoa")
    m.drives = list(drives) + [None] * (4 - len(drives))
    return m


def cd(file, fmt="raw") -> Drive:
    return Drive("cdrom", file, fmt)


def drives(argv):
    return [argv[i + 1] for i, t in enumerate(argv) if t == "-drive"]


def devices(argv):
    return [argv[i + 1] for i, t in enumerate(argv) if t == "-device"]


def globals_(argv):
    return [argv[i + 1] for i, t in enumerate(argv) if t == "-global"]


def audiodevs(argv):
    return [argv[i + 1] for i, t in enumerate(argv) if t == "-audiodev"]


class Formats(unittest.TestCase):
    def test_dmg_and_cue_by_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            for name, fmt in (("a.dmg", "dmg"), ("a.CUE", "cue")):
                (Path(d) / name).write_bytes(b"x" * 600)
                self.assertEqual(paths.detect_format(str(Path(d) / name)), fmt)

    def test_stored_raw_dmg_is_corrected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.dmg").write_bytes(b"x" * 600)
            self.assertEqual(paths.drive_format("raw", "a.dmg", d), "dmg")

    def test_new_disks_stay_raw_or_qcow2(self):
        self.assertEqual(model.FORMATS, ("raw", "qcow2"))
        self.assertEqual(model.DRIVE_FORMATS, ("raw", "qcow2", "dmg", "cue"))


class CdImages(unittest.TestCase):
    def test_cue_cd_with_audio(self):
        m = machine(None, cd("/c/disc.cue", "cue"))
        argv = command.build_argv(m, QD, MD, "darwin")
        self.assertEqual(drives(argv), ["file=/c/disc.cue,format=cue,media=cdrom,index=2"])
        self.assertEqual(audiodevs(argv), ["coreaudio,id=snd0", "coreaudio,id=cdaudio"])
        self.assertIn("ide-cd.audiodev=cdaudio", globals_(argv))

    def test_cue_detected_from_a_raw_record(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x.cue").write_text("FILE")
            argv = command.build_argv(machine(None, cd("x.cue", "raw")), QD, d, "darwin")
            self.assertIn("format=cue", drives(argv)[0])

    def test_dmg_cd_is_readonly(self):
        argv = command.build_argv(machine(cd("/c/x.dmg", "dmg")), QD, MD, "darwin")
        self.assertEqual(drives(argv),
                         ["file=/c/x.dmg,format=dmg,media=cdrom,index=2,readonly=on"])

    def test_dmg_hard_disks_are_snapshots(self):
        m = machine(Drive("disk", "/c/x.dmg", "dmg"), None, Drive("disk", "/c/y.dmg", "dmg"))
        self.assertEqual(drives(command.build_argv(m, QD, MD, "darwin")),
                         ["file=/c/x.dmg,format=dmg,media=disk,index=0,snapshot=on",
                          "file=/c/y.dmg,format=dmg,if=none,id=sata0,snapshot=on"])

    def test_raw_hard_disk_unchanged(self):
        argv = command.build_argv(machine(Drive("disk", "/c/x.img")), QD, MD, "darwin")
        self.assertEqual(drives(argv), ["file=/c/x.img,format=raw,media=disk,index=0"])

    def test_no_cd_no_cd_audiodev(self):
        argv = command.build_argv(machine(Drive("disk", "/a.img")), QD, MD, "darwin")
        self.assertEqual(audiodevs(argv), ["coreaudio,id=snd0"])
        self.assertEqual(globals_(argv), ["macio-newworld.audiodev=snd0"])


class CdAudio(unittest.TestCase):
    def test_reuses_the_usb_audiodev(self):
        m = machine(None, cd("/a.iso"))
        m.usb_audio = True
        argv = command.build_argv(m, QD, MD, "darwin")
        self.assertEqual(audiodevs(argv), ["coreaudio,id=snd0", "coreaudio,id=usb"])
        self.assertIn("ide-cd.audiodev=usb", globals_(argv))

    def test_own_backend_when_usb_audio_comes_from_extra_args(self):
        m = machine(None, cd("/a.iso"))
        m.usb_audio = True
        m.extra_args = "-device usb-audio,audiodev=mine"
        argv = command.build_argv(m, QD, MD, "darwin")
        self.assertEqual(audiodevs(argv), ["coreaudio,id=snd0", "coreaudio,id=cdaudio"])
        self.assertIn("ide-cd.audiodev=cdaudio", globals_(argv))

    def test_windows_backend(self):
        argv = command.build_argv(machine(None, cd("/a.iso")), "C:\\q", "C:\\m", "win32")
        self.assertIn("dsound,id=cdaudio", audiodevs(argv))

    def test_off_means_no_audiodev_property(self):
        m = machine(None, cd("/a.iso"))
        m.cd_audio = False
        argv = command.build_argv(m, QD, MD, "darwin")
        self.assertEqual(audiodevs(argv), ["coreaudio,id=snd0"])
        self.assertNotIn("ide-cd.audiodev", " ".join(globals_(argv)))

    def test_default_on_and_old_records_load_on(self):
        self.assertTrue(Machine().cd_audio)
        self.assertTrue(Machine.from_dict({"name": "x"}).cd_audio)
        self.assertFalse(Machine.from_dict({"name": "x", "cd_audio": False}).cd_audio)
        self.assertFalse(Machine.from_json(Machine(cd_audio=False).to_json()).cd_audio)


class HostDrive(unittest.TestCase):
    NAME = NAME

    def test_detection(self):
        for f in (self.NAME, "/dev/disk5", "/dev/cdrom", "D:", "d:\\", "\\\\.\\E:"):
            self.assertTrue(paths.is_host_drive(f), f)
        for f in ("/a/disk5.iso", "disk5", "C:\\x.iso", "", "drive:"):
            self.assertFalse(paths.is_host_drive(f), f)

    def test_macos_argv_names_the_drive(self):
        argv = command.build_argv(machine(None, cd(self.NAME)), QD, MD, "darwin")
        self.assertEqual(drives(argv), ["driver=host_cdrom,drive=HL-DT-ST DVDRAM GP57EB40,"
                                        "media=cdrom,index=2"])
        self.assertIn("ide-cd.audiodev=cdaudio", globals_(argv))

    def test_drive_name_commas_escaped(self):
        argv = command.build_argv(machine(cd("drive:ACME CD,X")), QD, MD, "darwin")
        self.assertIn("drive=ACME CD,,X,", drives(argv)[0])

    def test_old_disc_record_follows_the_first_drive(self):
        argv = command.build_argv(machine(cd("/dev/disk5")), QD, MD, "darwin")
        self.assertEqual(drives(argv),
                         ["driver=host_cdrom,filename=/dev/cdrom,media=cdrom,index=2"])

    def test_windows_argv(self):
        for f in ("d:", "D:\\", "\\\\.\\D:"):
            argv = command.build_argv(machine(cd(f)), "C:\\q", "C:\\m", "win32")
            self.assertEqual(drives(argv),
                             ["driver=host_cdrom,drive=D:,media=cdrom,index=2"], f)

    def test_records_from_the_other_host(self):
        mac = machine(cd(self.NAME))
        self.assertIn("filename=/dev/cdrom",
                      drives(command.build_argv(mac, "C:\\q", "C:\\m", "win32"))[0])
        win = machine(cd("D:"))
        self.assertIn("filename=/dev/cdrom", drives(command.build_argv(win, QD, MD, "darwin"))[0])

    def test_host_drive_needs_no_sudo(self):
        m = machine(cd(self.NAME))
        self.assertFalse(command.needs_sudo(m, "darwin"))
        self.assertFalse(command.needs_sudo(m, "win32"))

    def test_usb_passthrough_still_needs_sudo_with_a_host_drive(self):
        m = machine(cd(self.NAME))
        m.usb_host_devices = [model.UsbHostDevice("046d:0825", "Webcam")]
        self.assertTrue(command.needs_sudo(m, "darwin"))

    def test_launcher_runs_without_sudo_or_unmounting(self):
        for f in (self.NAME, "/dev/disk5"):
            text = command.launcher_text(machine(cd(f)), QD, MD, "darwin")
            self.assertNotIn("diskutil", text)
            self.assertFalse(any(ln.startswith("sudo ") for ln in text.splitlines()))

    def test_bat_has_no_diskutil(self):
        self.assertNotIn("diskutil", command.launcher_text(machine(cd("D:")), "C:\\q",
                                                           "C:\\m", "win32"))

    def test_not_checked_as_an_image_file(self):
        for f in (self.NAME, "/dev/disk5"):
            m = machine(cd(f))
            self.assertEqual(m.image_paths(), [])
            _errors, warnings = model.validate(m, QD, "darwin", machine_dir=MD)
            self.assertFalse(any("GP57EB40" in w or "disk5" in w for w in warnings), f)

    def test_record_round_trips(self):
        back = Machine.from_json(machine(cd(self.NAME)).to_json())
        self.assertEqual(back.drives[0].file, self.NAME)

    def test_a_hard_disk_entry_is_not_a_host_drive(self):
        self.assertEqual(model.host_drives(machine(Drive("disk", "/dev/disk5"))), [])
        self.assertEqual(model.host_drives(machine(cd(self.NAME))), [self.NAME])


def ioreg_node(vendor, product, media=None):
    node = {"IOObjectClass": "IODVDServices",
            "Device Characteristics": {"Vendor Name": vendor, "Product Name": product},
            "IORegistryEntryChildren": [{"IOObjectClass": "SCSITaskUserClientIniter"}]}
    if media:
        cls, bsd = media
        node["IORegistryEntryChildren"].append(
            {"IOObjectClass": "IODVDBlockStorageDriver", "IORegistryEntryChildren": [
                {"IOObjectClass": cls, "BSD Name": bsd, "Whole": True,
                 "IORegistryEntryChildren": [
                     {"IOObjectClass": "IOMedia", "BSD Name": bsd + "s1", "Whole": False}]}]})
    return node


class Listing(unittest.TestCase):
    def plist(self, d):
        return plistlib.dumps(d)

    def test_drive_with_and_without_disc(self):
        data = self.plist([ioreg_node("HL-DT-ST ", " DVDRAM  GP57EB40", ("IOCDMedia", "disk5")),
                           ioreg_node("MATSHITA", "DVD-R UJ-85J")])
        self.assertEqual(optical.parse_ioreg(data),
                         [("HL-DT-ST DVDRAM GP57EB40", "disk5", "CD"),
                          ("MATSHITA DVD-R UJ-85J", "", "")])

    def test_dvd_media_kind(self):
        data = self.plist([ioreg_node("A", "B", ("IODVDMedia", "disk9"))])
        self.assertEqual(optical.parse_ioreg(data), [("A B", "disk9", "DVD")])

    def test_junk_and_nameless(self):
        self.assertEqual(optical.parse_ioreg(b"junk"), [])
        self.assertEqual(optical.parse_ioreg(self.plist([{"IOObjectClass": "X"}, 3])), [])
        self.assertEqual(optical.parse_ioreg(self.plist([ioreg_node("", "")])), [])

    def test_disc_title(self):
        self.assertEqual(optical.disc_title(self.plist({"VolumeName": "Chess "})), "Chess")
        self.assertEqual(optical.disc_title(self.plist({"MediaName": "x"})), "")
        self.assertEqual(optical.disc_title(b"junk"), "")

    def test_drive_text(self):
        d = optical.OpticalDrive("drive:A B", "A B", "")
        self.assertEqual(d.text, "A B  -  no disc")
        self.assertEqual(optical.OpticalDrive("D:", "D:", "Chess").text, "D:  -  Chess")

    def test_name_matches_qemu_folding(self):
        self.assertEqual(optical.drive_name(" HL-DT-ST\t", "DVDRAM   GP57EB40 "),
                         "HL-DT-ST DVDRAM GP57EB40")


if __name__ == "__main__":
    unittest.main()
