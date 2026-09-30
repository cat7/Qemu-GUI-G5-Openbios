"""Reading winusb-switch output: one JSON object per line."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")

# Exit codes of winusb-switch
DONE, FAILED, USAGE, REFUSED, NOT_ADMIN, IN_USE = 0, 1, 2, 3, 4, 5
PENDING = 3010      # done once the device is replugged


@dataclass
class WinDevice:
    id: str
    instance: str
    description: str = ""
    product: str = ""
    cls: str = ""
    class_guid: str = ""
    service: str = ""
    inf: str = ""
    driver: str = ""
    composite: bool = False
    winusb: bool = False
    problem: int = 0
    functions: list = field(default_factory=list)
    refuse: str = ""

    @property
    def label(self) -> str:
        return self.product or self.description or f"USB device {self.id}"

    @property
    def switchable(self) -> bool:
        return not self.refuse


def _obj(line: str) -> dict:
    o = json.loads(line)
    if not isinstance(o, dict):
        raise ValueError(f"not a JSON object: {line!r}")
    return o


def parse_list(text: str) -> list[WinDevice]:
    """`winusb-switch list` stdout -> devices. ValueError on a bad line."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        o = _obj(line)
        if o.get("ok") is False:
            raise ValueError(o.get("error") or "list failed")
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
            cls=str(o.get("class", "")),
            class_guid=str(o.get("class_guid", "")),
            service=str(o.get("service", "")),
            inf=str(o.get("inf", "")),
            driver=str(o.get("driver", "")),
            composite=bool(o.get("composite", False)),
            winusb=bool(o.get("winusb", False)),
            problem=int(o.get("problem", 0)),
            functions=[f for f in funcs if isinstance(f, dict)],
            refuse=str(o.get("refuse", "")),
        ))
    return out


def parse_result(text: str) -> dict:
    """`bind`/`unbind` stdout -> its one object."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise ValueError(f"expected one line, got {len(lines)}")
    o = _obj(lines[0])
    if o.get("op") not in ("bind", "unbind") or "ok" not in o:
        raise ValueError(f"bad result: {lines[0]!r}")
    return o
