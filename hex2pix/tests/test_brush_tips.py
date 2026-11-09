"""Tests for the custom brush tip and stamping the Pattern grid as a tip."""

import unittest

from hex2pix import shapes
from hex2pix.effects import blur_area, shuffle_area, spray_area
from hex2pix.model import Bitmap
from hex2pix.shapes import (brush_offsets, custom_brush, custom_brush_from_text,
                            custom_brush_text, set_custom_brush,
                            stamp_pattern)

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)


class CustomBrushTest(unittest.TestCase):
    def tearDown(self):
        shapes.set_custom_brush(None)

    def test_the_default_tip_is_a_plus(self):
        offsets = set(brush_offsets(9, "custom"))
        self.assertIn((0, 0), offsets)
        self.assertIn((0, -4), offsets)
        self.assertIn((4, 0), offsets)
        self.assertNotIn((1, 1), offsets)

    def test_a_saved_tip_replaces_the_default(self):
        set_custom_brush(["10", "01"])
        self.assertEqual(brush_offsets(2, "custom"), [(-1, -1), (0, 0)])

    def test_the_tip_scales_to_the_brush_size(self):
        set_custom_brush(["10", "01"])
        self.assertEqual(set(brush_offsets(1, "custom")), {(0, 0)})
        self.assertEqual(
            set(brush_offsets(4, "custom")),
            {(-2, -2), (-1, -2), (-2, -1), (-1, -1),
             (0, 0), (1, 0), (0, 1), (1, 1)})

    def test_a_thin_tip_survives_a_small_size(self):
        # Point sampling used to miss this tip below 9 cells and fall back to
        # a square, which made Blur and Shuffle ignore the custom shape.
        set_custom_brush(["000", "010", "000"])
        self.assertEqual(brush_offsets(1, "custom"), [(0, 0)])
        four = set(brush_offsets(4, "custom"))
        self.assertTrue(four)
        self.assertNotEqual(four, set(brush_offsets(4, "square")))

    def test_blur_and_shuffle_take_the_custom_footprint(self):
        set_custom_brush(["100", "010", "001"])
        bitmap = Bitmap(7, 7)
        bitmap.fill(RED)
        self.assertEqual(blur_area(bitmap, None, 3, 3, 3, 100, "custom"), 3)
        bitmap.fill(RED)
        self.assertEqual(shuffle_area(bitmap, None, 3, 3, 3, "shuffle", 100,
                                      "custom"), 3)

    def test_spray_takes_the_custom_footprint(self):
        set_custom_brush(["100", "010", "001"])
        bitmap = Bitmap(7, 7)
        self.assertEqual(spray_area(bitmap, None, 3, 3, 3, 100, RED,
                                    "custom"), 3)

    def test_spray_feather_follows_a_hollow_tip(self):
        # Every ring pixel used to sit on the circular feather rim, so Blur
        # faded the whole tip away instead of softening its edge.
        ring = [(1 if x in (0, 8) or y in (0, 8) else 0)
                for y in range(9) for x in range(9)]
        set_custom_brush([ring[y * 9:(y + 1) * 9] for y in range(9)])
        bitmap = Bitmap(11, 11)
        spray_area(bitmap, None, 5, 5, 4, 100, RED, "custom", blur=100)
        painted = [bitmap.rgba(x, y) for y in range(11) for x in range(11)
                   if bitmap.rgba(x, y)[3]]
        self.assertTrue(painted)
        self.assertGreater(min(color[3] for color in painted), 0)

    def test_an_empty_tip_falls_back_to_a_square(self):
        set_custom_brush(["00", "00"])
        self.assertEqual(brush_offsets(2, "custom"), brush_offsets(2, "square"))

    def test_the_tip_round_trips_through_settings_text(self):
        rows = [[1, 0, 1], [0, 1, 0]]
        set_custom_brush(rows)
        text = custom_brush_text()
        set_custom_brush(None)
        custom_brush_from_text(text)
        self.assertEqual(custom_brush(), ((1, 0, 1), (0, 1, 0)))

    def test_ragged_rows_are_padded(self):
        set_custom_brush([[1], [1, 1, 1]])
        self.assertEqual(custom_brush(), ((1, 0, 0), (1, 1, 1)))

    def test_empty_text_keeps_the_default(self):
        self.assertIsNone(custom_brush_from_text(""))
        self.assertEqual(custom_brush(),
                         tuple(tuple(1 if cell == "1" else 0 for cell in row)
                               for row in shapes.DEFAULT_CUSTOM_BRUSH))


class PatternTipTest(unittest.TestCase):
    def test_the_grid_is_stamped_around_the_cursor(self):
        bitmap = Bitmap(6, 6)
        cells = [[RED, None], [None, BLUE]]
        self.assertEqual(stamp_pattern(bitmap, None, 2, 2, cells), 2)
        self.assertEqual(bitmap.rgba(1, 1), RED)
        self.assertEqual(bitmap.rgba(2, 2), BLUE)
        self.assertEqual(bitmap.rgba(3, 3), (0, 0, 0, 0))

    def test_none_cells_leave_the_artwork_alone(self):
        bitmap = Bitmap(4, 4)
        bitmap.fill(BLUE)
        self.assertEqual(stamp_pattern(bitmap, None, 1, 1,
                                       [[None, None], [None, None]]), 0)
        self.assertEqual(bitmap.rgba(0, 0), BLUE)

    def test_stamping_respects_the_mask(self):
        bitmap = Bitmap(2, 1)
        mask = bytearray(2)
        mask[0] = 1
        self.assertEqual(stamp_pattern(bitmap, mask, 1, 0, [[RED, RED]]), 1)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(1, 0), (0, 0, 0, 0))

    def test_stamping_is_mirrored(self):
        bitmap = Bitmap(4, 1)
        stamp_pattern(bitmap, None, 0, 0, [[RED]], "x")
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(3, 0), RED)

    def test_stamping_off_the_canvas_is_clipped(self):
        bitmap = Bitmap(2, 2)
        self.assertEqual(stamp_pattern(bitmap, None, 0, 0, [[RED, RED, RED]]),
                         2)


if __name__ == "__main__":
    unittest.main()
