"""Tests for the custom brush tip and stamping the Pattern grid as a tip."""

import unittest

from hex2pix import app as app_module
from hex2pix import shapes
from hex2pix.effects import blur_area, shuffle_area, spray_area
from hex2pix.model import Bitmap, Document, selection_rect_mask
from hex2pix.shapes import (brush_offsets, custom_brush, custom_brush_from_text,
                            custom_brush_text, custom_tip_rows,
                            paint_pattern_dab, paint_pattern_stroke,
                            set_custom_brush, stamp_pattern)

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
CLEAR = (0, 0, 0, 0)


class _ValueVar(object):
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Settings(object):
    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value


class _TipHarness(app_module.hex2pixApp):
    def __init__(self, document):
        self.doc = document
        self.settings = _Settings()
        self.brush_shape_var = _ValueVar("Square")
        self.export_info = _ValueVar("")

    def report_error(self, message):
        raise AssertionError(message)


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

    def test_a_rectangular_tip_keeps_its_aspect(self):
        set_custom_brush(["11111", "11111", "11111"])
        offsets = brush_offsets(9, "custom")
        self.assertEqual({x for x, _y in offsets}, set(range(-4, 5)))
        self.assertEqual({y for _x, y in offsets}, set(range(-2, 3)))

    def test_a_bitmap_copies_into_a_custom_tip(self):
        bitmap = Bitmap(3, 2)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(2, 1, BLUE)
        self.assertEqual(custom_tip_rows(bitmap), [[1, 0, 0], [0, 0, 1]])

    def test_transparent_pixels_make_empty_cells(self):
        self.assertEqual(custom_tip_rows(Bitmap(2, 2)), [[0, 0], [0, 0]])

    def test_large_artwork_is_sampled_down_without_cropping(self):
        bitmap = Bitmap(40, 8)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(39, 7, BLUE)
        rows = custom_tip_rows(bitmap, 16)
        self.assertEqual((len(rows[0]), len(rows)), (16, 3))
        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[-1][-1], 1)

    def test_the_selection_action_stores_and_selects_the_tip(self):
        document = Document(6, 4)
        document.bitmap.set_rgba(1, 1, RED)
        document.bitmap.set_rgba(4, 2, BLUE)
        document.selection.apply_mask(
            selection_rect_mask(6, 4, 1, 1, 5, 3), "replace")
        app = _TipHarness(document)
        app.copy_selection_to_custom_tip()
        self.assertEqual(custom_brush(),
                         ((1, 0, 0, 0, 0),
                          (0, 0, 0, 1, 0),
                          (0, 0, 0, 0, 0)))
        self.assertEqual(app.brush_shape_var.get(), "Custom")
        self.assertEqual(app.settings.values["custom_brush"],
                         custom_brush_text())

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

    def test_pattern_dab_keeps_the_brush_shape(self):
        bitmap = Bitmap(7, 7)
        painted = paint_pattern_dab(bitmap, None, 3, 3, 3, [[RED]],
                                    "circle", "", BLUE)
        self.assertEqual(painted, len(brush_offsets(3, "circle")))
        self.assertEqual(bitmap.rgba(3, 3), RED)
        self.assertEqual(bitmap.rgba(2, 2), CLEAR)

    def test_pattern_dab_uses_pattern_colours(self):
        bitmap = Bitmap(5, 5)
        cells = [[RED, BLUE], [BLUE, RED]]
        painted = paint_pattern_dab(bitmap, None, 2, 2, 3, cells,
                                    "square", "", BLUE)
        self.assertEqual(painted, 9)
        self.assertEqual(bitmap.rgba(1, 1), cells[1][1])
        self.assertEqual(bitmap.rgba(2, 1), cells[1][0])
        self.assertNotEqual(bitmap.rgba(1, 1), bitmap.rgba(2, 1))

    def test_pattern_none_cells_fall_back_without_cutting_holes(self):
        bitmap = Bitmap(5, 5)
        cells = [[None, BLUE], [BLUE, BLUE]]
        painted = paint_pattern_dab(bitmap, None, 2, 2, 3, cells,
                                    "square", "", RED)
        self.assertEqual(painted, 9)
        self.assertEqual(bitmap.rgba(2, 2), RED)

    def test_pattern_stroke_keeps_the_brush_footprint(self):
        bitmap = Bitmap(7, 7)
        expected = set()
        for x in range(1, 6):
            for dx, dy in brush_offsets(3, "circle"):
                expected.add((x + dx, 3 + dy))
        painted = paint_pattern_stroke(bitmap, None, 1, 3, 5, 3, 3,
                                       [[RED]], "circle", "", RED)
        self.assertEqual(painted, len(expected))
        self.assertEqual(bitmap.rgba(0, 2), CLEAR)


if __name__ == "__main__":
    unittest.main()
