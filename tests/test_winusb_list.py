"""winusb-switch output format.

Run:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "winusb"))

import winusb_list as wl  # noqa: E402

LIST = r'''
{"id":"046d:0990","instance":"USB\\VID_046D&PID_0990\\8C5C6B32","description":"USB Composite Device","product":"Camera","class":"USB","class_guid":"{36fc9e60-c465-11cf-8056-444553540000}","service":"usbccgp","inf":"usb.inf","driver":"USB Composite Device","composite":true,"winusb":false,"problem":0,"functions":[{"class":"Camera","service":"usbvideo"},{"class":"MEDIA","service":"usbaudio"}],"refuse":""}
{"id":"0e8d:1887","instance":"USB\\VID_0E8D&PID_1887\\5&1A2B3C&0&3","description":"WinUsb Device","product":"MT1887","class":"USBDevice","class_guid":"{88bae032-5a81-49f0-bc3d-a4ff138216d6}","service":"WinUSB","inf":"winusb.inf","driver":"WinUsb Device","composite":false,"winusb":true,"problem":0,"functions":[],"refuse":""}

{"id":"046d:c52b","instance":"USB\\VID_046D&PID_C52B\\6&1&0&2","description":"USB Composite Device","product":"USB Receiver","class":"USB","class_guid":"{36fc9e60-c465-11cf-8056-444553540000}","service":"usbccgp","inf":"usb.inf","driver":"USB Composite Device","composite":true,"winusb":false,"problem":0,"functions":[{"class":"HIDClass","service":"HidUsb"}],"refuse":"keyboard or mouse"}
'''


class ListFormat(unittest.TestCase):
    def test_parse(self):
        devs = wl.parse_list(LIST)
        self.assertEqual([d.id for d in devs],
                         ["046d:0990", "0e8d:1887", "046d:c52b"])
        cam, dvd, rx = devs
        self.assertTrue(cam.composite)
        self.assertFalse(cam.winusb)
        self.assertEqual(cam.functions[0]["service"], "usbvideo")
        self.assertTrue(cam.switchable)
        self.assertEqual(cam.label, "Camera")
        self.assertTrue(dvd.winusb)
        self.assertEqual(dvd.inf, "winusb.inf")
        self.assertFalse(rx.switchable)
        self.assertEqual(rx.refuse, "keyboard or mouse")

    def test_bad_lines(self):
        with self.assertRaises(ValueError):
            wl.parse_list('{"id":"46d:990","instance":"x"}')
        with self.assertRaises(ValueError):
            wl.parse_list('{"id":"046d:0990"}')
        with self.assertRaises(ValueError):
            wl.parse_list('[1,2]')
        with self.assertRaises(ValueError):
            wl.parse_list('{"op":"list","ok":false,"error":"cannot enumerate"}')

    def test_result(self):
        r = wl.parse_result('{"op":"bind","id":"046d:0990","ok":true,'
                            '"method":"class-guid","service":"WinUSB",'
                            '"reboot":false}\n')
        self.assertTrue(r["ok"])
        r = wl.parse_result('{"op":"unbind","id":"046d:0990","ok":false,'
                            '"refuse":"not on WinUSB"}')
        self.assertEqual(r["refuse"], "not on WinUSB")
        r = wl.parse_result('{"op":"unbind","id":"046d:0990","ok":false,'
                            '"refuse":"in use","veto_type":5,'
                            '"veto":"outstanding open",'
                            '"veto_name":"USB\\\\VID_046D&PID_0990\\\\1"}')
        self.assertEqual(r["veto"], "outstanding open")
        r = wl.parse_result('{"op":"unbind","ok":true,"reboot":true,'
                            '"status":"pending_reboot"}')
        self.assertEqual(r["status"], "pending_reboot")
        with self.assertRaises(ValueError):
            wl.parse_result('{"ok":true}')
        with self.assertRaises(ValueError):
            wl.parse_result('{"op":"bind","ok":true}\n{"op":"bind","ok":true}')


if __name__ == "__main__":
    unittest.main()
