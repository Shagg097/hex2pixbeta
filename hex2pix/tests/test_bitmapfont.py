"""Tests for the 5x7 pixel font and stamping text into a bitmap."""

import unittest

from hex2pix.bitmapfont import (GLYPH_HEIGHT, GLYPH_WIDTH, GLYPHS,
                                glyph_rows, text_points, text_size)
from hex2pix.model import Bitmap
from hex2pix.shapes import stamp_text

RED = (255, 0, 0, 255)


class GlyphTest(unittest.TestCase):
    def test_every_glyph_is_five_by_seven(self):
        for char, rows in GLYPHS.items():
            self.assertEqual(len(rows), GLYPH_HEIGHT, char)
            for row in rows:
                self.assertEqual(len(row), GLYPH_WIDTH, char)
                self.assertTrue(set(row) <= set("01"), char)

    def test_cases_and_digits_exist(self):
        for char in ("A", "Z", "a", "z", "0", "9", "!", "?"):
            self.assertIn(char, GLYPHS)

    def test_unknown_character_falls_back_to_a_question_mark(self):
        self.assertEqual(glyph_rows("\u2603"), GLYPHS["?"])


class TextGeometryTest(unittest.TestCase):
    def test_glyph_stays_inside_its_box(self):
        for char in ("A", "I", "g", "8", "?"):
            width, height = text_size(char, 1, 1)
            self.assertGreaterEqual(width, 1)
            self.assertLessEqual(width, GLYPH_WIDTH)
            self.assertEqual(height, GLYPH_HEIGHT)

    def test_scale_multiplies_the_ink(self):
        one = text_points("A", 1, 0)
        two = text_points("A", 2, 0)
        self.assertEqual(len(two), 4 * len(one))
        self.assertEqual(text_size("A", 2, 0),
                         (2 * text_size("A", 1, 0)[0],
                          2 * text_size("A", 1, 0)[1]))

    def test_spacing_widens_the_line_by_whole_columns(self):
        tight = text_size("AA", 1, 0)[0]
        loose = text_size("AA", 1, 2)[0]
        self.assertEqual(loose - tight, 2)

    def test_scaled_spacing_scales_too(self):
        tight = text_size("AA", 3, 0)[0]
        loose = text_size("AA", 3, 2)[0]
        self.assertEqual(loose - tight, 6)

    def test_space_inks_nothing(self):
        self.assertEqual(text_points("   "), [])
        self.assertEqual(text_size(" ", 3, 1), (0, 0))

    def test_bad_scale_and_spacing_are_clamped(self):
        self.assertEqual(text_points("A", 0, -4), text_points("A", 1, 0))

    def test_solid_glyph_metrics(self):
        # The box starts at the origin, so the two blank columns before the
        # bar are part of the measured width.
        self.assertEqual(text_size("|", 1, 0), (3, GLYPH_HEIGHT))


class StampTest(unittest.TestCase):
    def test_stamp_paints_at_the_click_point(self):
        bitmap = Bitmap(16, 12)
        painted = stamp_text(bitmap, None, 1, 2, "A", RED, 1, 1)
        self.assertGreater(painted, 0)
        # 'A' starts one column into its glyph box, so the ink begins at x=2.
        self.assertEqual(bitmap.rgba(2, 2), RED)
        self.assertEqual(bitmap.rgba(1, 2), (0, 0, 0, 0))

    def test_stamp_leaves_pixels_outside_the_text(self):
        bitmap = Bitmap(24, 12)
        stamp_text(bitmap, None, 0, 0, "A", RED, 1, 1)
        self.assertEqual(bitmap.rgba(20, 10), (0, 0, 0, 0))

    def test_stamp_respects_the_selection_mask(self):
        bitmap = Bitmap(16, 12)
        mask = bytearray(16 * 12)
        self.assertEqual(stamp_text(bitmap, mask, 1, 2, "A", RED), 0)

    def test_mask_allows_only_the_selected_pixels(self):
        bitmap = Bitmap(16, 12)
        mask = bytearray(16 * 12)
        mask[2 * 16 + 2] = 255
        painted = stamp_text(bitmap, mask, 1, 2, "A", RED)
        self.assertEqual(painted, 1)
        self.assertEqual(bitmap.rgba(2, 2), RED)

    def test_mirror_doubles_the_stamp(self):
        plain = Bitmap(24, 10)
        mirrored = Bitmap(24, 10)
        one = stamp_text(plain, None, 2, 1, "|", RED)
        two = stamp_text(mirrored, None, 2, 1, "|", RED, 1, 1, "x")
        self.assertEqual(two, 2 * one)
        self.assertEqual(mirrored.rgba(19, 1), RED)

    def test_scaled_stamp_is_solid(self):
        bitmap = Bitmap(32, 32)
        stamp_text(bitmap, None, 0, 0, "|", RED, 3, 1)
        # The column glyph starts at x=2, so the first block is x 6..8.
        for y in range(3):
            for x in range(6, 9):
                self.assertEqual(bitmap.rgba(x, y), RED,
                                 "%d,%d" % (x, y))
        self.assertEqual(bitmap.rgba(9, 0), (0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
