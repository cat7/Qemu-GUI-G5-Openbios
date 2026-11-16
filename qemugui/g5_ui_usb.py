"""USB devices (Windows): which host devices QEMU may use. A setting of this
computer, not of a machine; each machine then picks what it passes through.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from . import usbhost
from . import winusb
from .g5_ui_dialogs import refresh_native_style
from .g5_ui_fonts import heading_font

GREY = "gray"
WIDTH = 720

INTRO = ("QEMU can use a USB device of this computer only after it is given to QEMU. "
         "Give to QEMU takes the device away from Windows; Give back to Windows returns "
         "it. Windows asks for permission each time. A device given to QEMU stays with "
         "QEMU, also after a restart, until it is given back. Each machine then chooses, "
         "on its USB devices tab, which of these devices it passes through. High-speed "
         "devices go on the USB 2.0 bus, others on the second USB 1.1 "
         "bus.")


class UsbDevicesDialog(tk.Toplevel):
    """One row per plugged device: its name, whether Windows or QEMU has it,
    and the two buttons. ``on_change()`` runs after each switch."""

    def __init__(self, parent, on_change=None):
        super().__init__(parent)
        refresh_native_style(self)
        self.on_change = on_change
        self.busy = False
        self.title("USB devices")
        self.transient(parent)
        self.resizable(True, True)
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        ttk.Label(f, text="USB devices", font=heading_font(self)).grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(f, text=INTRO, foreground=GREY, wraplength=WIDTH - 40,
                  justify="left").grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.list = ttk.Frame(f)
        self.list.grid(row=2, column=0, sticky="nsew")
        self.list.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        self.status = ttk.Label(f, text="", wraplength=WIDTH - 40, justify="left")
        self.status.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        bar = ttk.Frame(f)
        bar.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Refresh", command=self.fill).pack(side="right", padx=4)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.buttons: dict[str, tuple[ttk.Button, ttk.Button]] = {}
        self.rows: dict[str, ttk.Label] = {}
        self.warning_label: ttk.Label | None = None
        self.fill()
        self.update_idletasks()
        self.geometry(f"{WIDTH}x{min(self.winfo_reqheight(), self.winfo_screenheight() - 160)}")

    def fill(self):
        for w in self.list.winfo_children():
            w.destroy()
        self.buttons = {}
        self.rows = {}
        devices, problem = winusb.list_devices()
        plugged: dict[str, winusb.WinDevice] = {}
        for d in devices:
            plugged.setdefault(d.id, d)
        # Devices that can never be given stay out, unless one is QEMU's
        hidden = [i for i, d in plugged.items() if d.refuse and not d.winusb]
        shown = [i for i in plugged if i not in hidden]
        general, per_device = winusb.explain_warnings(devices)
        r = 0
        if problem:
            ttk.Label(self.list, text=problem, foreground=GREY).grid(row=r, column=0,
                                                                     columnspan=3, sticky="w")
            r += 1
        elif not shown:
            ttk.Label(self.list, text="No USB device that QEMU can use is plugged in.",
                      foreground=GREY).grid(row=r, column=0, sticky="w")
            r += 1
        for dev_id in shown:
            d = plugged[dev_id]
            text = f"{d.display}  ({dev_id}, {d.speed or '?'} speed): {d.state}"
            if d.refuse:
                text += f"; cannot be given to QEMU ({d.refuse})"
            row = ttk.Label(self.list, text=text)
            row.grid(row=r, column=0, sticky="w", pady=(4, 0))
            give = ttk.Button(self.list, text="Give to QEMU",
                              command=lambda i=dev_id: self.switch("bind", i))
            back = ttk.Button(self.list, text="Give back to Windows",
                              command=lambda i=dev_id: self.switch("unbind", i))
            give.grid(row=r, column=1, padx=2, pady=(4, 0))
            back.grid(row=r, column=2, padx=2, pady=(4, 0))
            if d.refuse or d.winusb:
                give.state(["disabled"])
            if not d.winusb:
                back.state(["disabled"])
            self.buttons[dev_id] = (give, back)
            self.rows[dev_id] = row
            r += 1
            for note in per_device.get(dev_id, []):
                ttk.Label(self.list, text="    " + note, foreground=GREY,
                          wraplength=WIDTH - 80, justify="left").grid(
                    row=r, column=0, columnspan=3, sticky="w")
                r += 1
        if hidden:
            ttk.Label(self.list, text=usbhost.hidden_note(len(hidden)), foreground=GREY).grid(
                row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))
            r += 1
        self.warning_label = ttk.Label(self.list, text="\n".join(general), foreground="red",
                                       wraplength=WIDTH - 40, justify="left")
        self.warning_label.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def switch(self, op: str, dev_id: str, wait: bool = False):
        """Run winusb-switch elevated for one device; *wait* runs it in line
        (tests), otherwise on a thread so the window stays alive."""
        if self.busy:
            return
        self.busy = True
        self.status.config(text="Waiting for you to answer Windows' permission prompt…")
        for give, back in self.buttons.values():
            give.state(["disabled"])
            back.state(["disabled"])
        if wait:
            self._switched(winusb.run_elevated(op, dev_id))
            return
        box: dict = {}
        worker = threading.Thread(
            target=lambda: box.setdefault("res", winusb.run_elevated(op, dev_id)), daemon=True)
        worker.start()

        def poll():
            try:
                if worker.is_alive():
                    self.after(200, poll)
                    return
                self._switched(box.get("res") or
                               {"op": op, "id": dev_id, "ok": False, "error": "no result"})
            except tk.TclError:
                pass        # closed meanwhile
        self.after(200, poll)

    def _switched(self, res: dict):
        self.busy = False
        self.fill()
        self.status.config(text=winusb.outcome_text(res))
        if callable(self.on_change):
            self.on_change()
