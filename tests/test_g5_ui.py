"""Tk-backed tests for the G5 editor and main window.

These build a real (withdrawn) Tk window, so they are skipped where there is
no working tkinter.

Run:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from qemugui import g5_model as model  # noqa: E402
from qemugui import paths  # noqa: E402
from qemugui.g5_model import Machine, PromEnv, Gpu, Drive  # noqa: E402


def _tk_available():
    try:
        import tkinter
        r = tkinter.Tk(); r.withdraw(); r.destroy(); return True
    except Exception:
        return False


@unittest.skipUnless(_tk_available(), "no display")
class CheckboxPolarity(unittest.TestCase):
    """"Boot into Open Firmware" asks the opposite question from the field
    it writes."""

    def _editor(self, m: Machine):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, m, lib, "/q", on_save=lambda *a: None)
        ed.withdraw()
        return ed

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            r.destroy()
        self.td.cleanup()

    def test_unchecked_boot_into_ofw_means_auto_boot_true(self):
        m = Machine(name="t", prom_env=PromEnv(auto_boot=True))
        ed = self._editor(m)
        self.assertFalse(ed.boot_into_ofw_var.get())
        self.assertTrue(ed.collect().prom_env.auto_boot)

    def test_checked_boot_into_ofw_means_auto_boot_false(self):
        m = Machine(name="t", prom_env=PromEnv(auto_boot=False))
        ed = self._editor(m)
        self.assertTrue(ed.boot_into_ofw_var.get())
        self.assertFalse(ed.collect().prom_env.auto_boot)

    def test_toggling_the_boot_into_ofw_box_flips_the_saved_value(self):
        m = Machine(name="t", prom_env=PromEnv(auto_boot=True))
        ed = self._editor(m)
        ed.boot_into_ofw_var.set(True)
        self.assertFalse(ed.collect().prom_env.auto_boot)
        ed.boot_into_ofw_var.set(False)
        self.assertTrue(ed.collect().prom_env.auto_boot)


@unittest.skipUnless(_tk_available(), "no display")
class BootCheckbox(unittest.TestCase):
    """The Drives tab's per-row "Boot" checkbox, mutually exclusive across
    the four positions, which sets ``Machine.boot_slot``."""

    def _editor(self, m: Machine):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, m, lib, "/q", on_save=lambda *a: None)
        ed.withdraw()
        return ed

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            r.destroy()
        self.td.cleanup()

    def test_defaults_to_nothing_checked(self):
        m = Machine(name="t")
        ed = self._editor(m)
        self.assertFalse(any(row.boot.get() for row in ed.drive_rows))
        self.assertIsNone(ed.collect().boot_slot)

    def test_loads_the_marked_slot(self):
        m = Machine(name="t", boot_slot=1,
                    drives=[Drive("disk", "/a.img"), Drive("cdrom", "/c.iso"), None, None])
        ed = self._editor(m)
        self.assertEqual([row.boot.get() for row in ed.drive_rows], [False, True, False, False])
        self.assertEqual(ed.collect().boot_slot, 1)

    def test_checking_one_row_unchecks_the_others(self):
        m = Machine(name="t", drives=[Drive("disk", "/a.img"), None, Drive("disk", "/b.img"), None])
        ed = self._editor(m)
        ed.drive_rows[0].boot.set(True)
        ed.drive_rows[0]._boot_toggled()
        self.assertEqual(ed.collect().boot_slot, 0)
        ed.drive_rows[2].boot.set(True)
        ed.drive_rows[2]._boot_toggled()
        self.assertFalse(ed.drive_rows[0].boot.get())
        self.assertEqual(ed.collect().boot_slot, 2)

    def test_emptying_a_checked_slot_clears_boot(self):
        m = Machine(name="t", boot_slot=0, drives=[Drive("disk", "/a.img"), None, None, None])
        ed = self._editor(m)
        self.assertTrue(ed.drive_rows[0].boot.get())
        ed.drive_rows[0].kind.set("Empty")
        ed.drive_rows[0]._kind_changed()
        self.assertFalse(ed.drive_rows[0].boot.get())
        self.assertIsNone(ed.collect().boot_slot)


@unittest.skipUnless(_tk_available(), "no display")
class NewDiskButton(unittest.TestCase):
    """The Drives tab's "New disk…" button, offered only when qemu-img
    sits next to the program, and its wiring of CreateDiskDialog's result
    back into the editor."""

    def _editor(self, m: Machine):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, m, lib, "/q", on_save=lambda *a: None)
        ed.withdraw()
        return ed

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.install_dir = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            r.destroy()
        self.td.cleanup()
        self.install_dir.cleanup()
        paths.use_install_dir(None)

    def test_absent_without_qemu_img(self):
        paths.use_install_dir(self.install_dir.name)
        ed = self._editor(Machine(name="t"))
        self.assertIsNone(ed.new_disk_button)

    def test_present_with_qemu_img(self):
        (Path(self.install_dir.name) / paths.qemu_img_name()).write_text("")
        paths.use_install_dir(self.install_dir.name)
        ed = self._editor(Machine(name="t"))
        self.assertIsNotNone(ed.new_disk_button)

    def test_places_new_disk_in_the_chosen_position(self):
        import qemugui.g5_ui_machine as ui_machine
        ed = self._editor(Machine(name="t"))

        class FakeDialog:
            def __init__(self, *_a, **_k):
                self.result = ("/new/disk.img", "raw", model.SATA_B)

        orig, ui_machine.CreateDiskDialog = ui_machine.CreateDiskDialog, FakeDialog
        try:
            ed._new_disk()
        finally:
            ui_machine.CreateDiskDialog = orig
        self.assertEqual(ed.drive_rows[model.SATA_B].file.get(), "/new/disk.img")
        self.assertEqual(ed.drive_rows[model.SATA_B].kind.get(), "Hard disk")
        self.assertEqual(ed.collect().drives[model.SATA_B], Drive("disk", "/new/disk.img", "raw"))

    def test_cancelled_dialog_changes_nothing(self):
        import qemugui.g5_ui_machine as ui_machine
        ed = self._editor(Machine(name="t", drives=[Drive("disk", "/a.img"), None, None, None]))

        class FakeDialog:
            def __init__(self, *_a, **_k):
                self.result = None

        orig, ui_machine.CreateDiskDialog = ui_machine.CreateDiskDialog, FakeDialog
        try:
            ed._new_disk()
        finally:
            ui_machine.CreateDiskDialog = orig
        self.assertEqual(ed.drive_rows[0].file.get(), "/a.img")
        self.assertEqual(ed.collect().drives, [Drive("disk", "/a.img"), None, None, None])


@unittest.skipUnless(_tk_available(), "no display")
class ShareTab(unittest.TestCase):
    """The Shared folder tab: load/collect round trip for Machine.share."""

    def _editor(self, m: Machine):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, m, lib, "/q", on_save=lambda *a: None)
        ed.withdraw()
        return ed

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            r.destroy()
        self.td.cleanup()

    def test_defaults_to_no_shared_folder(self):
        from qemugui.g5_model import Share
        ed = self._editor(Machine(name="t"))
        self.assertEqual(ed.share_folder_var.get(), "")
        self.assertEqual(ed.share_scope.get(), "guest-only")
        self.assertEqual(ed.collect().share, Share())

    def test_loads_and_collects_a_share(self):
        from qemugui.g5_model import Share
        m = Machine(name="t", share=Share("/shared", "mac", "secret", "all-interfaces"))
        ed = self._editor(m)
        self.assertEqual(ed.share_folder_var.get(), "/shared")
        self.assertEqual(ed.share_user_var.get(), "mac")
        self.assertEqual(ed.share_password_var.get(), "secret")
        self.assertEqual(ed.share_scope.get(), "all-interfaces")
        self.assertEqual(ed.collect().share, m.share)


@unittest.skipUnless(_tk_available(), "no display")
class UsbAudioCheckbox(unittest.TestCase):

    def _editor(self, m: Machine):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, m, lib, "/q", on_save=lambda *a: None)
        ed.withdraw()
        return ed

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            r.destroy()
        self.td.cleanup()

    def test_off_by_default(self):
        ed = self._editor(Machine(name="t"))
        self.assertFalse(ed.usb_audio_var.get())
        self.assertFalse(ed.collect().usb_audio)

    def test_loads_and_collects(self):
        ed = self._editor(Machine(name="t", usb_audio=True))
        self.assertTrue(ed.usb_audio_var.get())
        self.assertTrue(ed.collect().usb_audio)
        ed.usb_audio_var.set(False)
        self.assertFalse(ed.collect().usb_audio)


@unittest.skipUnless(_tk_available(), "no display")
class PortForwardRows(UsbAudioCheckbox):

    def test_four_rows_load_and_collect_with_values_kept_when_disabled(self):
        rules = [model.HostFwd("udp", "5353", "53"), model.HostFwd("tcp", "8080", "80")]
        ed = self._editor(Machine(name="t", network=model.Network("user", model.DEFAULT_MAC, "", rules)))
        self.assertEqual(len(ed.fwd_rows), 4)
        self.assertEqual(str(ed.fwd_rows[0][3][1].cget("state")), "normal")
        ed.net_mode.set(model.network_mode_label("none"))
        ed._net_mode_changed()
        self.assertEqual(str(ed.fwd_rows[0][3][1].cget("state")), "disabled")
        got = [r.to_dict() for r in ed.collect().network.hostfwd if not r.empty]
        self.assertEqual(got, [r.to_dict() for r in rules])
        ed.net_mode.set(model.network_mode_label("user"))
        ed._net_mode_changed()
        self.assertEqual(str(ed.fwd_rows[3][3][2].cget("state")), "normal")


@unittest.skipUnless(_tk_available(), "no display")
class TheNetworkAndSoundTabFollowsTheHost(unittest.TestCase):
    """A new machine opened on either host shows slirp, the host's own
    interface label and the host's own sound backend."""

    def setUp(self):
        self.saved = paths.HOST_PLATFORM
        self.td = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        paths.HOST_PLATFORM = self.saved
        for r in self.roots:
            r.destroy()
        self.td.cleanup()

    def _tab(self, platform: str):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        paths.HOST_PLATFORM = platform
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, model.new_machine(""), lib, "/q",
                           on_save=lambda *a: None, is_new=True)
        ed.withdraw()
        return ed

    def test_windows(self):
        ed = self._tab("win32")
        self.assertEqual(ed.net_mode.get(), "default (slirp)")
        self.assertEqual(ed.net_mode_cb.get(), "default (slirp)")
        values = list(ed.net_mode_cb.cget("values"))
        self.assertEqual(values[0], "default (slirp)")
        self.assertIn("tap", values)
        self.assertFalse(any(v.startswith("vmnet") for v in values), values)
        self.assertEqual(str(ed.ifname_label.cget("text")), "Tap device name:")
        self.assertEqual(ed.audio_var.get(), "default")
        self.assertEqual(str(ed.audio_default_rb.cget("text")), "DirectSound")
        self.assertEqual(ed.collect().network.mode, "user")
        self.assertEqual(ed.collect().audio, "default")

    def test_mac(self):
        ed = self._tab("darwin")
        self.assertEqual(ed.net_mode.get(), "default (slirp)")
        values = list(ed.net_mode_cb.cget("values"))
        self.assertEqual(values[0], "default (slirp)")
        self.assertIn("vmnet-bridged", values)
        self.assertNotIn("tap", values)
        self.assertEqual(str(ed.ifname_label.cget("text")), "Vmnet host interface:")
        self.assertEqual(str(ed.audio_default_rb.cget("text")), "CoreAudio")
        self.assertEqual(ed.collect().network.mode, "user")


@unittest.skipUnless(_tk_available(), "no display")
class DateAndTime(unittest.TestCase):

    def _editor(self, m: Machine):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, m, lib, "/q", on_save=lambda *a: None)
        ed.withdraw()
        return ed

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            r.destroy()
        self.td.cleanup()

    def test_empty_by_default(self):
        ed = self._editor(Machine(name="t"))
        self.assertEqual(ed.rtc_base_var.get(), "")
        self.assertEqual(ed.collect().rtc_base, "")

    def test_loads_and_collects(self):
        ed = self._editor(Machine(name="t", rtc_base="localtime"))
        self.assertEqual(ed.rtc_base_var.get(), "localtime")
        ed.rtc_base_var.set(" 2005-04-29T10:30:00 ")
        self.assertEqual(ed.collect().rtc_base, "2005-04-29T10:30:00")

    def test_a_bad_value_is_reported_on_save(self):
        import qemugui.g5_ui_machine as ui_machine
        ed = self._editor(Machine(name="t"))
        ed.rtc_base_var.set("yesterday")
        shown = []
        orig = ui_machine.show_validation
        ui_machine.show_validation = lambda parent, errors, warnings: (shown.append(errors), False)[1]
        try:
            ed.save()
        finally:
            ui_machine.show_validation = orig
        self.assertTrue(any("Date and time" in e for e in shown[0]))


@unittest.skipUnless(_tk_available(), "no display")
class NoSystemChooserOnScreen(unittest.TestCase):
    """New machine asks for a name only."""

    def test_the_machine_tab_has_no_system_widget(self):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        with tempfile.TemporaryDirectory() as td:
            lib = model.Library(td)
            m = model.new_machine("t")
            root = tk.Tk(); root.withdraw()
            try:
                ed = MachineEditor(root, m, lib, "/q", on_save=lambda *a: None)
                ed.withdraw()
                self.assertFalse(hasattr(ed, "system_var"))
            finally:
                root.destroy()


@unittest.skipUnless(_tk_available(), "no display")
class GraphicsTab(unittest.TestCase):

    def _editor(self, m: Machine, qemu_dir: str = "/q"):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, m, lib, qemu_dir, on_save=lambda *a: None)
        ed.withdraw()
        return ed

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            r.destroy()
        self.td.cleanup()

    def test_no_card_by_default(self):
        ed = self._editor(Machine(name="t"))
        self.assertIsNone(ed.collect().gpu)
        self.assertTrue(ed.gl_cb.instate(["disabled"]))
        self.assertTrue(ed.agp_cb.instate(["disabled"]))
        self.assertTrue(ed.raster_sb.instate(["disabled"]))

    def test_radeon9800_loads_and_collects(self):
        gpu = Gpu("radeon9800", "r9800.rom", "fast", 4, "off")
        ed = self._editor(Machine(name="t", gpu=gpu))
        self.assertEqual(ed.gpu_model_var.get(), "radeon9800")
        self.assertTrue(ed.gl_cb.instate(["!disabled"]))
        self.assertTrue(ed.agp_cb.instate(["disabled"]))
        self.assertEqual(ed.collect().gpu, gpu)

    def test_switching_to_rv100(self):
        ed = self._editor(Machine(name="t", gpu=Gpu("radeon9800", "r.rom", "fast", 2)))
        ed.gpu_model_var.set("rv100")
        ed._gpu_changed()
        self.assertTrue(ed.gl_cb.instate(["disabled"]))
        self.assertTrue(ed.agp_cb.instate(["!disabled"]))
        ed.agp_var.set(False)
        self.assertEqual(ed.collect().gpu, Gpu("rv100", "r.rom", "fast", 2, "auto", False))
        ed.gpu_model_var.set("vga")
        ed._gpu_changed()
        self.assertIsNone(ed.collect().gpu)

    def test_bad_raster_threads_is_reported(self):
        ed = self._editor(Machine(name="t", gpu=Gpu("radeon9800", "r.rom")))
        ed.raster_var.set("many")
        errors, _ = model.validate(ed.collect(), None, "darwin", check_files=False)
        self.assertTrue(any("Raster threads" in e for e in errors), errors)

    def test_the_rom_list_is_the_install_folders_roms(self):
        with tempfile.TemporaryDirectory() as qd:
            for n in ("ati_oem_9800xt_123_agp_full.rom", "ati_radeon_7000_208.rom",
                      "openbios-qemu.elf"):
                (Path(qd) / n).write_text("")
            ed = self._editor(Machine(name="t"), qemu_dir=qd)
            self.assertEqual(list(ed.gpu_rom_cb.cget("values")),
                             ["ati_oem_9800xt_123_agp_full.rom", "ati_radeon_7000_208.rom"])
            self.assertEqual(ed.gpu_rom_var.get(), "")

    def test_a_chosen_rom_beside_the_emulator_is_kept_by_name(self):
        from qemugui.g5_ui_machine import rom_value
        with tempfile.TemporaryDirectory() as qd:
            self.assertEqual(rom_value(str(Path(qd) / "card.rom"), qd), "card.rom")
            self.assertEqual(rom_value("/elsewhere/card.rom", qd), "/elsewhere/card.rom")


@unittest.skipUnless(_tk_available(), "no display")
class DrivePositions(unittest.TestCase):

    def _editor(self, m: Machine):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        lib = model.Library(self.td.name)
        root = tk.Tk(); root.withdraw()
        self.roots.append(root)
        ed = MachineEditor(root, m, lib, "/q", on_save=lambda *a: None)
        ed.withdraw()
        return ed

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.roots = []

    def tearDown(self):
        for r in self.roots:
            r.destroy()
        self.td.cleanup()

    def test_each_position_offers_what_its_bus_holds(self):
        ed = self._editor(Machine(name="t"))
        got = [list(r.kind_cb.cget("values")) for r in ed.drive_rows]
        self.assertEqual(got, [["Empty", "Hard disk", "CD"], ["Empty", "CD"],
                               ["Empty", "Hard disk"], ["Empty", "Hard disk"]])

    def test_a_typed_file_takes_the_kind_its_position_allows(self):
        ed = self._editor(Machine(name="t"))
        ed.drive_rows[model.SATA_A].file.set("/images/install.iso")
        ed.drive_rows[model.ATA_SLAVE].file.set("/images/disk.img")
        ed.drive_rows[model.ATA_MASTER].file.set("/images/install.iso")
        drives = ed.collect().drives
        self.assertEqual(drives[model.SATA_A].kind, "disk")
        self.assertEqual(drives[model.ATA_SLAVE].kind, "cdrom")
        self.assertEqual(drives[model.ATA_MASTER].kind, "cdrom")

    def test_round_trip(self):
        drives = [Drive("cdrom", "/c.iso"), None, Drive("disk", "/a.qcow2", "qcow2"),
                  Drive("disk", "/b.img")]
        ed = self._editor(Machine(name="t", drives=drives, boot_slot=2))
        m = ed.collect()
        self.assertEqual((m.drives, m.boot_slot), (drives, 2))


@unittest.skipUnless(_tk_available(), "no display")
class UsbTabletCheckbox(unittest.TestCase):

    def test_off_by_default_and_collected(self):
        import tkinter as tk
        from qemugui.g5_ui_machine import MachineEditor
        with tempfile.TemporaryDirectory() as td:
            root = tk.Tk(); root.withdraw()
            try:
                ed = MachineEditor(root, model.new_machine("t"), model.Library(td), "/q",
                                   on_save=lambda *a: None)
                ed.withdraw()
                self.assertFalse(ed.collect().usb_tablet)
                ed.usb_tablet_var.set(True)
                self.assertTrue(ed.collect().usb_tablet)
            finally:
                root.destroy()


class _FakeRun:
    exit_code = None
    share = None

    def poll(self):
        return None

    def uptime(self):
        return 0.0


@unittest.skipUnless(_tk_available(), "no display")
class StartDispatch(unittest.TestCase):
    """On macOS every start goes through Terminal, sudo or not; on any other
    host the GUI runs the launcher itself and tracks the process."""

    def setUp(self):
        from qemugui import g5_ui_main as ui
        self.ui = ui
        self.td = tempfile.TemporaryDirectory()
        paths.use_install_dir(self.td.name)
        self.saved = (paths.HOST_PLATFORM, ui.start_in_terminal, ui.start_machine)
        self.calls: list[str] = []
        ui.start_in_terminal = lambda m, d: (self.calls.append("terminal"), (Path(d) / "run.command", None))[1]
        ui.start_machine = lambda m, d: (self.calls.append("direct"), _FakeRun())[1]
        self.roots = []

    def tearDown(self):
        paths.HOST_PLATFORM, self.ui.start_in_terminal, self.ui.start_machine = self.saved
        for r in self.roots:
            r.destroy()
        paths.use_install_dir(None)
        self.td.cleanup()

    def _window(self, platform: str):
        paths.HOST_PLATFORM = platform
        w = self.ui.MainWindow(paths.Settings(), settings_path=Path(self.td.name) / "settings.json")
        w.withdraw()
        self.roots.append(w)
        w.library.save(model.new_machine("t"))
        w.refresh_list(select="t")
        return w

    def test_macos_starts_in_terminal_without_sudo(self):
        w = self._window("darwin")
        self.assertIsNone(w.start_selected())
        self.assertEqual(self.calls, ["terminal"])
        self.assertNotIn("t", w.running)
        self.assertIn("t", w.terminal_started)
        self.assertTrue(w.run_status.cget("text").startswith(self.ui.TERMINAL_STATUS))

    def test_other_hosts_run_the_launcher_directly(self):
        w = self._window("linux")
        self.assertIsNotNone(w.start_selected())
        self.assertEqual(self.calls, ["direct"])
        self.assertIn("t", w.running)
        self.assertNotIn("t", w.terminal_started)
        self.assertTrue(w.run_status.cget("text").startswith("Running"))


@unittest.skipUnless(_tk_available(), "no display")
class WindowsSpawn(unittest.TestCase):
    """The console Windows hands out is the one QEMU should print into: the
    .bat runs in a new console with no redirection. Other hosts are untouched."""

    def setUp(self):
        from qemugui import g5_ui_main as ui
        self.ui = ui
        self.saved = (paths.HOST_PLATFORM, ui.subprocess.Popen)
        self.td = tempfile.TemporaryDirectory()
        paths.use_install_dir(self.td.name)
        self.calls = []

        class FakePopen:
            pid = 4321

            def __init__(_s, argv, **kw):
                self.calls.append((argv, kw))

            def poll(_s):
                return None

        ui.subprocess.Popen = FakePopen

    def tearDown(self):
        paths.HOST_PLATFORM, self.ui.subprocess.Popen = self.saved
        paths.use_install_dir(None)
        self.td.cleanup()

    def _start(self, platform: str):
        paths.HOST_PLATFORM = platform
        return self.ui.start_machine(model.new_machine("t"), Path(self.td.name) / "t")

    def test_windows_gets_a_new_console_and_no_redirection(self):
        run = self._start("win32")
        argv, kw = self.calls[0]
        self.assertTrue(str(argv[-1]).endswith("run.bat"), argv)
        self.assertEqual(kw["creationflags"], self.ui.CREATE_NEW_CONSOLE)
        self.assertNotIn("stdout", kw)
        self.assertNotIn("stderr", kw)
        self.assertIsNone(run.poll())
        self.assertTrue(run.log_path.read_text().startswith("# "))

    def test_other_hosts_still_redirect_into_the_log(self):
        run = self._start("linux")
        self.addCleanup(run._log_fh.close)
        argv, kw = self.calls[0]
        self.assertTrue(str(argv[0]).endswith("qemu-system-ppc64"), argv)
        self.assertNotIn("creationflags", kw)
        self.assertIsNotNone(kw["stdout"])


if __name__ == "__main__":
    unittest.main()
