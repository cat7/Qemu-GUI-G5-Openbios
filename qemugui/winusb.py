"""Host USB devices on Windows. QEMU's usb-host (libusb) can open a device
only while it is on Windows' own WinUSB driver. ``winusb-switch.exe``, next
to ``qemu-system-ppc64.exe``, moves one device there and back:

* ``list`` runs unelevated and prints one JSON object per device;
* ``bind`` / ``unbind`` need administrator rights, so they run through
  ShellExecuteEx "runas" (one UAC prompt) and write their result into a
  file (``--out``), since an elevated process's output cannot be read.

No Tk in here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from . import usbnames

HELPER = "winusb-switch.exe"

# Exit codes of winusb-switch
DONE, FAILED, USAGE, REFUSED, NOT_ADMIN, IN_USE = 0, 1, 2, 3, 4, 5
PENDING = 3010      # done once the device is replugged

ERROR_CANCELLED = 1223              # the UAC prompt was declined
CREATE_NO_WINDOW = 0x08000000

ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")

WINUSB_STATE = "given to QEMU"
WINDOWS_STATE = "used by Windows"
NOT_READY_NOTE = ("not passed through while Windows uses it: give it to QEMU with "
                  "USB devices… in the main window")


@dataclass
class WinDevice:
    id: str
    instance: str
    description: str = ""
    product: str = ""
    speed: str = ""
    cls: str = ""
    class_guid: str = ""
    service: str = ""
    inf: str = ""
    driver: str = ""
    provider: str = ""          # "winusb-switch": its own signed package
    composite: bool = False
    winusb: bool = False
    problem: int = 0
    functions: list = field(default_factory=list)
    refuse: str = ""

    @property
    def label(self) -> str:
        return self.product or self.description or f"USB device {self.id}"

    @property
    def display(self) -> str:
        """Maker, model and kind, for the lists."""
        return usbnames.describe(self.id, self.product or self.description,
                                 usbnames.windows_kinds(self.functions, self.cls, self.service))

    @property
    def switchable(self) -> bool:
        return not self.refuse

    @property
    def state(self) -> str:
        return WINUSB_STATE if self.winusb else WINDOWS_STATE


class DeviceList(list):
    """Devices from `list`, with its warnings: third-party filters or
    drivers (UsbDk, libusbK, libusb0) that can take devices away."""
    warnings: list = []


_DEV_FILTER_RE = re.compile(r"^([0-9a-f]{4}:[0-9a-f]{4}) (?:upper|lower) filter (\S+) installed$")
_CLASS_FILTER_RE = re.compile(r"^USB class (?:upper|lower) filter (\S+) installed$")
_PACKAGE_RE = re.compile(r"^([0-9a-f]{4}:[0-9a-f]{4}): driver package (\S+) installs (\S+)$")


def explain_warnings(devices) -> tuple[list[str], dict[str, list[str]]]:
    """The list's warnings in plain words: (for the whole computer, per
    device id). Device notes are kept only for listed devices."""
    general: list[str] = []
    per_device: dict[str, list[str]] = {}
    for w in getattr(devices, "warnings", []) or []:
        w = str(w)
        m = _DEV_FILTER_RE.match(w)
        if m:
            per_device.setdefault(m.group(1), []).append(
                f"Windows loads the extra driver {m.group(2)} with this device; "
                "giving it to QEMU may not work while that driver is there.")
            continue
        m = _PACKAGE_RE.match(w)
        if m:
            per_device.setdefault(m.group(1), []).append(
                f"A {m.group(3)} driver ({m.group(2)}) is installed for this device; "
                "Windows may pick it instead of its own driver when the device is given back.")
            continue
        m = _CLASS_FILTER_RE.match(w)
        if m:
            general.append(f"The driver {m.group(1)} sits under every USB device of this "
                           "computer; it can keep QEMU from opening a device given to it.")
        elif w.startswith("UsbDk"):
            general.append("UsbDk is installed (it comes with virt-viewer and similar tools); "
                           "a device given back to Windows may not get its Windows driver back.")
        else:
            general.append(w)
    return general, per_device


def warnings_text(devices) -> str:
    """The warnings for the whole computer, one per line; "" if none."""
    return "\n".join(explain_warnings(devices)[0])


def _obj(line: str) -> dict:
    o = json.loads(line)
    if not isinstance(o, dict):
        raise ValueError(f"not a JSON object: {line!r}")
    return o


def parse_list(text: str) -> list[WinDevice]:
    """`winusb-switch list` output -> devices (with .warnings). ValueError
    on a bad line."""
    out = DeviceList()
    out.warnings = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        o = _obj(line)
        if o.get("ok") is False:
            raise ValueError(o.get("error") or "list failed")
        if "id" not in o and isinstance(o.get("warnings"), list):
            out.warnings += [str(w) for w in o["warnings"]]
            continue
        dev_id = str(o.get("id", "")).lower()
        if not ID_RE.match(dev_id) or not o.get("instance"):
            raise ValueError(f"bad device line: {line!r}")
        funcs = o.get("functions") or []
        if not isinstance(funcs, list):
            raise ValueError(f"bad functions: {line!r}")
        out.append(WinDevice(
            id=dev_id,
            instance=str(o["instance"]),
            description=str(o.get("description", "")),
            product=str(o.get("product", "")),
            speed=str(o.get("speed", "")),
            cls=str(o.get("class", "")),
            class_guid=str(o.get("class_guid", "")),
            service=str(o.get("service", "")),
            inf=str(o.get("inf", "")),
            driver=str(o.get("driver", "")),
            provider=str(o.get("provider", "")),
            composite=bool(o.get("composite", False)),
            winusb=bool(o.get("winusb", False)),
            problem=int(o.get("problem", 0)),
            functions=[f for f in funcs if isinstance(f, dict)],
            refuse=str(o.get("refuse", "")),
        ))
    return out


def parse_result(text: str) -> dict:
    """`bind`/`unbind` output -> its one object."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise ValueError(f"expected one line, got {len(lines)}")
    o = _obj(lines[0])
    if o.get("op") not in ("bind", "unbind") or "ok" not in o:
        raise ValueError(f"bad result: {lines[0]!r}")
    return o


def helper_path(folder: Path | str | None = None) -> Path:
    """Beside qemu-system-ppc64.exe, which is the folder this GUI runs from."""
    return Path(folder or paths.install_dir()) / HELPER


def missing_message() -> str:
    return f"{HELPER} is not next to {paths.qemu_binary_name('win32')}."


def list_devices(folder: Path | str | None = None) -> tuple[list[WinDevice], str]:
    """(devices, "") or ([], why not). Unelevated; never changes a driver."""
    exe = helper_path(folder)
    if not exe.is_file():
        return [], missing_message()
    try:
        r = subprocess.run([str(exe), "list"], capture_output=True, timeout=60,
                           creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"{HELPER} list failed: {e}"
    try:
        return parse_list(r.stdout.decode("utf-8", "replace")), ""
    except ValueError as e:
        return [], f"{HELPER} list failed: {e}"


def owned_ids(folder: Path | str | None = None) -> tuple[set[str] | None, str]:
    """Ids of the devices QEMU owns now (on WinUSB), or (None, why not)."""
    devices, why = list_devices(folder)
    if why:
        return None, why
    return {d.id for d in devices if d.winusb}, ""


def passthrough_note(chosen: list[str], owned: set[str] | None, why: str,
                     names: dict | None = None) -> str:
    """What the launcher leaves out of the ticked devices, and why; "" if none.
    *names*: the saved name of each id."""
    if not chosen:
        return ""
    if owned is None:
        return f"No USB device is passed through: {why}"
    left = [i for i in chosen if i not in owned]
    if not left:
        return ""
    return ("Not passed through while Windows uses them (USB devices… > Give to QEMU): "
            + ", ".join(f"{usbnames.describe(i, (names or {}).get(i, ''))} ({i})"
                        for i in left))


def shell_execute_runas(exe: str, params: str) -> int | None:
    """Run *exe* elevated (one UAC prompt), hidden, and wait for it. Its exit
    code, or None when the prompt was declined."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    see_mask_nocloseprocess, see_mask_noasync, sw_hide = 0x40, 0x100, 0
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = see_mask_nocloseprocess | see_mask_noasync
    sei.lpVerb = "runas"
    sei.lpFile = exe
    sei.lpParameters = params
    sei.nShow = sw_hide
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            return None
        raise OSError(err, ctypes.FormatError(err))
    if not sei.hProcess:
        return None
    try:
        kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(sei.hProcess)


def run_elevated(op: str, dev_id: str, folder: Path | str | None = None) -> dict:
    """``bind``/``unbind`` one device, elevated. Always a result dict: the
    helper's own, or {"ok": False, "cancelled": True} / {"error": ...}; with
    "exit" set when the helper ran."""
    exe = helper_path(folder)
    if not exe.is_file():
        return {"op": op, "id": dev_id, "ok": False, "error": missing_message()}
    td = result_dir()
    try:
        out = td / "result.json"
        try:
            code = shell_execute_runas(str(exe), f'{op} {dev_id} --out "{out}"')
        except OSError as e:
            return {"op": op, "id": dev_id, "ok": False, "error": str(e)}
        if code is None:
            return {"op": op, "id": dev_id, "ok": False, "cancelled": True}
        res, why = _read_result(out)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    if res is None:
        res = _result_from_state(op, dev_id, folder, code, why)
    res["exit"] = code
    return res


def result_dir() -> Path:
    """A folder for the elevated helper's result. Not tempfile's: since
    Python 3.13 that one grants only SYSTEM, Administrators and the owner,
    and a file the elevated helper writes is owned by Administrators, which
    this unelevated program is then refused."""
    base = Path(tempfile.gettempdir())
    for _ in range(100):
        d = base / f"winusb-switch-{os.getpid()}-{os.urandom(4).hex()}"
        try:
            os.mkdir(d)         # default mode: the ACL is inherited from TEMP
            return d
        except FileExistsError:
            continue
    raise OSError("no folder for the winusb-switch result")


READ_TRIES, READ_PAUSE = 5, 0.1


def _read_result(out: Path, tries: int | None = None, pause: float | None = None) -> tuple[dict | None, str]:
    """The result file, retried a while: a virus scanner may still hold
    the file just written."""
    why = ""
    tries = tries or READ_TRIES
    pause = READ_PAUSE if pause is None else pause
    for i in range(tries):
        try:
            return parse_result(out.read_text(encoding="utf-8-sig")), ""
        except (OSError, ValueError) as e:
            why = f"{type(e).__name__}: {e}"
        if i + 1 < tries:
            time.sleep(pause)
    return None, why


def _result_from_state(op: str, dev_id: str, folder, code: int, why: str) -> dict:
    """No result file: what the device's driver now is decides."""
    try:
        devices, problem = list_devices(folder)
    except ValueError as e:         # off Windows: no creationflags
        devices, problem = [], str(e)
    d = next((x for x in devices if x.id == dev_id), None)
    if not problem and d is not None and d.winusb == (op == "bind"):
        return {"op": op, "id": dev_id, "ok": True, "status": "done", "unreported": why}
    return {"op": op, "id": dev_id, "ok": False,
            "error": f"{HELPER} left no report (exit code {code}; {why})"}


def outcome_text(res: dict) -> str:
    """One line for the editor, from a run_elevated result."""
    if res.get("cancelled"):
        return "Nothing changed: the Windows permission prompt was cancelled."
    if res.get("refuse") == "in use" or res.get("exit") == IN_USE:
        return ("Nothing changed: a program is using the device. Quit it (a running "
                "machine, the Camera app) and try again.")
    if res.get("ok"):
        if res.get("status") == "pending_reboot":
            return "Unplug the device and plug it in again to finish."
        if res.get("op") == "bind":
            return ("The device was already given to QEMU." if res.get("already")
                    else "Given to QEMU: machines can now pass it through.")
        return "Given back to Windows."
    if res.get("refuse"):
        return f"This device cannot be given to QEMU ({res['refuse']})."
    detail = res.get("detail") or {}
    msg = detail.get("message") if isinstance(detail, dict) else ""
    what = "give the device to QEMU" if res.get("op") == "bind" else "give the device back"
    return (f"Could not {what}: {res.get('error') or 'unknown error'}"
            + (f" ({msg})" if msg else "") + ".")
