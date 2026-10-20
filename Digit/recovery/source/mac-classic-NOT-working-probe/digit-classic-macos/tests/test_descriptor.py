# SPDX-License-Identifier: MIT
"""Independent descriptor parser and C ABI wire-vector checks (no radio access)."""
import ctypes
import os
from pathlib import Path
import random
import shlex
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DescriptorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        is_macos = sys.platform == "darwin"
        library = Path(cls.temp.name) / ("libdigit_hid.dylib" if is_macos else "libdigit_hid.so")
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-dynamiclib" if is_macos else "-shared", "-fPIC",
                    "-I" + str(ROOT / "include"), str(ROOT / "src/digit_hid.c"),
                    "-o", str(library)]
        subprocess.run(command, check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.digit_keyboard_report.argtypes = [ctypes.c_uint8,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint8)]
        cls.lib.digit_keyboard_report.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_descriptor_defines_one_keyboard_input_and_led_output(self):
        size = ctypes.c_size_t.in_dll(self.lib, "digit_keyboard_descriptor_length").value
        data = bytes((ctypes.c_uint8 * size).in_dll(self.lib, "digit_keyboard_descriptor"))
        pos = 0
        report_size = report_count = report_id = 0
        collections = 0
        bits = {}
        application_usage = None
        usage_page = usage = None
        while pos < len(data):
            prefix = data[pos]; pos += 1
            self.assertNotEqual(prefix, 0xFE, "Unexpected long descriptor item")
            width = (0, 1, 2, 4)[prefix & 3]
            self.assertLessEqual(pos + width, len(data))
            value = int.from_bytes(data[pos:pos + width], "little"); pos += width
            item_type = (prefix >> 2) & 3
            tag = prefix >> 4
            if item_type == 1:
                if tag == 0: usage_page = value
                if tag == 7: report_size = value
                if tag == 8: report_id = value
                if tag == 9: report_count = value
            elif item_type == 2 and tag == 0:
                usage = value
            elif item_type == 0:
                if tag == 10:
                    collections += 1
                    if value == 1: application_usage = (usage_page, usage)
                if tag == 12: collections -= 1
                if tag in (8, 9, 11):
                    bits[(report_id, tag)] = bits.get((report_id, tag), 0) + report_size * report_count
        self.assertEqual(collections, 0)
        self.assertEqual(application_usage, (1, 6))
        self.assertEqual(bits, {(1, 8): 64, (1, 9): 8})

    def test_all_modifier_bytes_and_neutral(self):
        for modifiers in range(256):
            out = (ctypes.c_uint8 * 9)()
            self.assertEqual(self.lib.digit_keyboard_report(modifiers, None, 0, out), 0)
            self.assertEqual(bytes(out), bytes([1, modifiers, 0, 0, 0, 0, 0, 0, 0]))

    def test_seeded_random_report_vectors(self):
        rng = random.Random(20261006)
        for _ in range(1000):
            keys = rng.sample(range(4, 0x66), rng.randrange(7))
            modifiers = rng.randrange(256)
            usages = (ctypes.c_uint8 * len(keys))(*keys)
            out = (ctypes.c_uint8 * 9)()
            self.assertEqual(self.lib.digit_keyboard_report(modifiers, usages, len(keys), out), 0)
            expected = bytes([1, modifiers, 0] + keys + [0] * (6 - len(keys)))
            self.assertEqual(bytes(out), expected)

if __name__ == "__main__":
    unittest.main()
