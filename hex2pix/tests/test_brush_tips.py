"""Tests for the custom brush tip and stamping the Pattern grid as a tip."""

import unittest

from hex2pix import shapes
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
