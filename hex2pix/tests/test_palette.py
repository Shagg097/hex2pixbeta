"""Tests for palette presets, palette files, building and quantization."""

import unittest

from hex2pix.model import Bitmap, HexError
from hex2pix.palette import (MAX_PALETTE_COLORS, PALETTES, cube_256,
                             format_gpl, format_hex_palette, format_jasc_pal,
                             format_palette, make_palette, nearest_color,
                             parse_gpl, parse_hex_palette, parse_jasc_pal,
                             parse_palette, preset_colors, quantize_bitmap)

GPL = """GIMP Palette
Name: Test Ramp
Columns: 4
#
  0   0   0\tBlack
255 128   0\tOrange
# a comment
255 255 255 White
"""

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


class PresetTest(unittest.TestCase):
    def test_small_presets_are_rgba(self):
        for name in ("PICO-8", "DB16", "Grayscale"):
            colors = preset_colors(name)
            self.assertEqual(len(colors), 16, name)
            for color in colors:
                self.assertEqual(len(color), 4)
                self.assertEqual(color[3], 255)

    def test_256_cube_has_256_unique_colours(self):
        colors = cube_256()
        self.assertEqual(len(colors), 256)
        self.assertEqual(len(set(colors)), 256)
        self.assertIn(BLACK, colors)
        self.assertIn(WHITE, colors)
        self.assertEqual(len(PALETTES["256 Cube"]), 256)

    def test_pico8_and_db16_are_hex_lists(self):
        self.assertEqual(len(PALETTES["PICO-8"]), 16)
        self.assertTrue(all(len(value) == 6 for value in PALETTES["DB16"]))

    def test_grayscale_is_a_ramp(self):
        values = [color[0] for color in preset_colors("Grayscale")]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 0)
        self.assertEqual(values[-1], 255)


class BuildTest(unittest.TestCase):
    def test_black_and_white_fills(self):
        _name, colors = make_palette("Black", 8, "black")
        self.assertEqual(len(colors), 8)
        self.assertTrue(all(color == BLACK for color in colors))
        _name, colors = make_palette("White", 4, "white")
        self.assertTrue(all(color == WHITE for color in colors))

    def test_foreground_fill_keeps_rgba(self):
        _name, colors = make_palette("Mine", 3, "foreground", (1, 2, 3))
        self.assertEqual(colors, [(1, 2, 3, 255)] * 3)

    def test_grayscale_fill_spans_black_to_white(self):
        _name, colors = make_palette("Ramp", 5, "grayscale")
        self.assertEqual([color[0] for color in colors],
                         [0, 64, 128, 191, 255])

    def test_count_is_clamped_and_name_defaults(self):
        name, colors = make_palette("", 0, "black")
        self.assertEqual(name, "Untitled palette")
        self.assertEqual(len(colors), 1)
        _name, colors = make_palette("Big", MAX_PALETTE_COLORS + 50, "black")
        self.assertEqual(len(colors), MAX_PALETTE_COLORS)


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


class HexPaletteTest(unittest.TestCase):
    def test_parse_accepts_hex_forms_and_names(self):
        text = ("# Name: Sunset\n"
                "# a comment\n"
                "FF0000\n"
                "#00FF00\n"
                "0: 0000FF\n"
                "11223344\n")
        name, colors = parse_hex_palette(text)
        self.assertEqual(name, "Sunset")
        self.assertEqual(colors, [RED, (0, 255, 0, 255), BLUE,
                                  (0x11, 0x22, 0x33, 0x44)])

    def test_parse_ignores_decimal_gpl_lines(self):
        name, colors = parse_hex_palette("255 0 0\nFF0000\n")
        self.assertEqual(name, "Imported palette")
        self.assertEqual(colors, [RED])

    def test_parse_rejects_empty_and_oversized_palettes(self):
        with self.assertRaises(HexError):
            parse_hex_palette("# nothing here\n")
        with self.assertRaises(HexError):
            parse_hex_palette("\n".join(["123456"] * 257))

    def test_format_round_trip_keeps_name_and_colours(self):
        colors = preset_colors("PICO-8")
        text = format_hex_palette("PICO-8", colors)
        name, parsed = parse_hex_palette(text)
        self.assertEqual(name, "PICO-8")
        self.assertEqual(parsed, colors)


class JascTest(unittest.TestCase):
    TEXT = "JASC-PAL\n0100\n3\n0 0 0\n255 0 0\n0 0 255\n"

    def test_parse(self):
        _name, colors = parse_jasc_pal(self.TEXT)
        self.assertEqual(colors, [BLACK, RED, BLUE])

    def test_round_trip(self):
        text = format_jasc_pal([BLACK, RED, BLUE])
        _name, colors = parse_jasc_pal(text)
        self.assertEqual(colors, [BLACK, RED, BLUE])

    def test_rejects_other_files(self):
        with self.assertRaises(HexError):
            parse_jasc_pal("GIMP Palette\n")


class DispatchTest(unittest.TestCase):
    def test_extension_and_header_detection(self):
        gpl = format_gpl("Ramp", [RED])
        self.assertEqual(parse_palette(gpl, "x.gpl")[0], "Ramp")
        jasc = format_jasc_pal([BLUE])
        self.assertEqual(parse_palette(jasc, "x.pal")[1], [BLUE])
        hexes = format_hex_palette("Mine", [RED])
        self.assertEqual(parse_palette(hexes, "x.256")[1], [RED])

    def test_format_by_extension(self):
        self.assertTrue(format_palette("x.gpl", "R", [RED])
                        .startswith("GIMP Palette"))
        self.assertTrue(format_palette("x.pal", "R", [RED])
                        .startswith("JASC-PAL"))
        self.assertIn("FF0000", format_palette("x.256", "R", [RED]))


class QuantizeTest(unittest.TestCase):
    def test_nearest_colour(self):
        palette = [BLACK, RED, BLUE]
        self.assertEqual(nearest_color((250, 10, 10, 255), palette), RED)
        self.assertEqual(nearest_color((10, 10, 250, 255), palette), BLUE)
        self.assertEqual(nearest_color((20, 20, 20, 255), palette), BLACK)

    def test_nearest_needs_a_palette(self):
        with self.assertRaises(HexError):
            nearest_color(RED, [])

    def test_map_without_dither(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, (200, 10, 10, 255))
        bitmap.set_rgba(1, 0, (10, 10, 200, 255))
        painted = quantize_bitmap(bitmap, [RED, BLUE])
        self.assertEqual(painted, 2)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(1, 0), BLUE)

    def test_transparent_pixels_and_mask_are_skipped(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, (200, 10, 10, 255))
        bitmap.set_rgba(1, 0, CLEAR)
        mask = bytearray([255, 0])
        self.assertEqual(quantize_bitmap(bitmap, [BLUE], mask=mask), 1)
        self.assertEqual(bitmap.rgba(0, 0), BLUE)
        self.assertEqual(bitmap.rgba(1, 0), CLEAR)

    def test_alpha_is_preserved(self):
        bitmap = Bitmap(1, 1)
        bitmap.set_rgba(0, 0, (200, 10, 10, 128))
        quantize_bitmap(bitmap, [RED])
        self.assertEqual(bitmap.rgba(0, 0), (255, 0, 0, 128))

    def test_dither_mixes_palette_entries_on_a_ramp(self):
        bitmap = Bitmap(16, 1)
        for x in range(16):
            level = int(round(x * 255 / 15.0))
            bitmap.set_rgba(x, 0, (level, level, level, 255))
        painted = quantize_bitmap(bitmap, [BLACK, WHITE], dither=True)
        self.assertEqual(painted, 16)
        seen = {bitmap.rgba(x, 0) for x in range(16)}
        self.assertEqual(seen, {BLACK, WHITE})
        self.assertGreater(sum(1 for x in range(16)
                               if bitmap.rgba(x, 0) == BLACK), 0)
        self.assertGreater(sum(1 for x in range(16)
                               if bitmap.rgba(x, 0) == WHITE), 0)


if __name__ == "__main__":
    unittest.main()
