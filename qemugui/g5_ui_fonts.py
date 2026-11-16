"""Fonts shared by the windows."""

from __future__ import annotations

import tkinter.font as tkfont


_fonts: dict = {}


def heading_font(widget) -> tkfont.Font:
    """Section headings: bold, two points under the body text, at least 8.
    One per Tk root, kept alive here (Tk drops a font with its object)."""
    root = widget.winfo_toplevel()._root()
    f = _fonts.get(str(root))
    if f is None or f._owner is not root:
        base = tkfont.nametofont("TkDefaultFont", root=root).actual()
        size = abs(int(base.get("size") or 12))
        f = tkfont.Font(root=root, family=base.get("family"), size=max(8, size - 2),
                        weight="bold")
        f._owner = root
        _fonts[str(root)] = f
    return f
