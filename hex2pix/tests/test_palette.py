"""Tests for palette presets and GPL files."""

import unittest

from hex2pix.model import HexError
from hex2pix.palette import (PALETTES, format_gpl, parse_gpl,
                             preset_colors)

GPL = """GIMP Palette
Name: Test Ramp
Columns: 4
#
  0   0   0\tBlack
255 128   0\tOrange
# a comment
255 255 255 White
"""


class PresetTest(unittest.TestCase):
    def test_presets_are_rgba(self):
        for name in ("PICO-8", "DB16", "Grayscale"):
            colors = preset_colors(name)
            self.assertEqual(len(colors), 16, name)
            for color in colors:
                self.assertEqual(len(color), 4)
                self.assertEqual(color[3], 255)

    def test_pico8_and_db16_are_hex_lists(self):
        self.assertEqual(len(PALETTES["PICO-8"]), 16)
        self.assertTrue(all(len(value) == 6 for value in PALETTES["DB16"]))

    def test_grayscale_is_a_ramp(self):
        values = [color[0] for color in preset_colors("Grayscale")]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 0)
        self.assertEqual(values[-1], 255)


class GplTest(unittest.TestCase):
    def test_parse_reads_name_columns_and_colors(self):
        name, colors, columns = parse_gpl(GPL)
        self.assertEqual(name, "Test Ramp")
        self.assertEqual(columns, 4)
        self.assertEqual(colors, [(0, 0, 0, 255), (255, 128, 0, 255),
                                  (255, 255, 255, 255)])

    def test_parse_rejects_empty_palettes(self):
        with self.assertRaises(HexError):
            parse_gpl("GIMP Palette\nName: Empty\n#\n")

    def test_parse_skips_junk_lines(self):
        text = "GIMP Palette\nnot a color\n12 34\n999 0 0\n1 2 3\n"
        _name, colors, _columns = parse_gpl(text)
        self.assertEqual(colors, [(1, 2, 3, 255)])

    def test_format_has_header_and_hex_comments(self):
        text = format_gpl("Ramp", [(1, 2, 3, 255)])
        self.assertTrue(text.startswith("GIMP Palette"))
        self.assertIn("Name: Ramp", text)
        self.assertIn("#010203", text)

    def test_round_trip(self):
        colors = preset_colors("PICO-8")
        text = format_gpl("PICO-8", colors)
        name, parsed, columns = parse_gpl(text)
        self.assertEqual(name, "PICO-8")
        self.assertEqual(columns, 8)
        self.assertEqual(parsed, colors)


if __name__ == "__main__":
    unittest.main()
