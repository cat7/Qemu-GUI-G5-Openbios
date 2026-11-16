"""Device names from the USB ID table."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from qemugui import usbnames as n  # noqa: E402


def win(*pairs):
    return n.windows_kinds([{"class": c, "service": s} for c, s in pairs])


class Names(unittest.TestCase):
    def test_table_is_there(self):
        self.assertEqual(n.vendor_name("046d:0990"), "Logitech")
        self.assertEqual(n.vendor_name("0951:1642"), "Kingston")
        self.assertEqual(n.product_name("046d:0990"), "QuickCam Pro 9000")

    def test_a_nameless_device_gets_maker_model_and_kind(self):
        self.assertEqual(n.describe("046d:0990", "USB Composite Device",
                                    win(("Camera", "usbvideo"), ("MEDIA", "usbaudio"))),
                         "Logitech QuickCam Pro 9000 (camera, audio)")

    def test_own_model_name_gets_its_maker(self):
        self.assertEqual(n.describe("0951:1642", "DT 101 G2", win(("DiskDrive", "disk"))),
                         "Kingston DT 101 G2 (disk)")
        self.assertEqual(n.describe("046d:0a37", "Logitech USB Headset H540",
                                    win(("MEDIA", "usbaudio"), ("HIDClass", "HidUsb"))),
                         "Logitech USB Headset H540 (audio)")

    def test_a_string_that_names_nothing_gives_way(self):
        self.assertEqual(n.describe("048d:5702", "ITE Device"),
                         "Integrated Technology Express RGB LED Controller")
        self.assertEqual(n.describe("046d:c31c", "USB Keyboard",
                                    win(("HIDClass", "HidUsb"))), "Logitech Keyboard K120")

    def test_our_winusb_package_name_is_not_a_name(self):
        self.assertEqual(n.describe("0e8d:1887", "USB device 0e8d:1887 for QEMU (WinUSB)"),
                         "MediaTek Slim Portable DVD Writer")

    def test_unknown(self):
        self.assertEqual(n.describe("fffe:fffe", ""), "Unknown USB device")

    def test_mac_interface_classes(self):
        self.assertEqual(n.iface_kinds((0x0e, 0x0e, 0x01, 0x01)), ["camera", "audio"])
        self.assertEqual(n.iface_kinds((0x03,)), ["controls"])


if __name__ == "__main__":
    unittest.main()
