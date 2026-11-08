"""Tests for the Aseprite-style drawing helpers."""

import unittest

from hex2pix.model import Bitmap, HexError
from hex2pix.shapes import (brush_offsets, gradient_fill, mirror_points,
                            paint_dab, paint_shape, paint_stroke,
                            shape_points)

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
CLEAR = (0, 0, 0, 0)


class BrushTest(unittest.TestCase):
    def test_square_offsets(self):
        self.assertEqual(len(brush_offsets(3)), 9)
        self.assertEqual(brush_offsets(1), [(0, 0)])

    def test_circle_offsets_drop_corners(self):
        self.assertEqual(len(brush_offsets(3, "circle")), 5)
        self.assertNotIn((-1, -1), brush_offsets(3, "circle"))

    def test_mirror_points(self):
        self.assertEqual(mirror_points(1, 2, 8, 8, ""), [(1, 2)])
        self.assertEqual(mirror_points(1, 2, 8, 8, "x"), [(1, 2), (6, 2)])
        self.assertEqual(mirror_points(1, 2, 8, 8, "y"), [(1, 2), (1, 5)])
        self.assertEqual(len(mirror_points(1, 2, 8, 8, "xy")), 4)

    def test_mirrored_dab(self):
        bitmap = Bitmap(8, 8)
        painted = paint_dab(bitmap, None, 1, 2, 1, RED, "square", "x")
        self.assertEqual(painted, 2)
        self.assertEqual(bitmap.rgba(1, 2), RED)
        self.assertEqual(bitmap.rgba(6, 2), RED)

    def test_circle_dab_with_vertical_mirror(self):
        bitmap = Bitmap(5, 5)
        painted = paint_dab(bitmap, None, 2, 1, 3, BLUE, "circle", "y")
        self.assertEqual(painted, 9)
        self.assertEqual(bitmap.rgba(1, 1), BLUE)
        self.assertEqual(bitmap.rgba(1, 3), BLUE)
        self.assertEqual(bitmap.rgba(0, 0), CLEAR)

    def test_brush_cannot_leave_bounds(self):
        bitmap = Bitmap(4, 4)
        paint_dab(bitmap, None, 0, 0, 3, RED)
        self.assertEqual(bitmap.rgba(0, 0), RED)

    def test_mask_is_respected(self):
        bitmap = Bitmap(4, 1)
        mask = bytearray(4)
        mask[2] = 255
        painted = paint_dab(bitmap, mask, 1, 0, 3, RED)
        self.assertEqual(painted, 1)
        self.assertEqual(bitmap.rgba(1, 0), CLEAR)
        self.assertEqual(bitmap.rgba(2, 0), RED)


class ShapeTest(unittest.TestCase):
    def test_rect_outline_and_fill(self):
        outline = shape_points("rect", 0, 0, 2, 2)
        self.assertEqual(len(outline), 8)
        self.assertNotIn((1, 1), outline)
        filled = shape_points("rect", 0, 0, 2, 2, True)
        self.assertEqual(len(filled), 9)

    def test_line_is_inclusive(self):
        self.assertEqual(shape_points("line", 0, 0, 3, 0),
                         [(0, 0), (1, 0), (2, 0), (3, 0)])

    def test_ellipse_fill_covers_centre(self):
        points = shape_points("ellipse", 0, 0, 4, 4, True)
        self.assertIn((2, 2), points)
        self.assertTrue(all(0 <= x <= 4 and 0 <= y <= 4 for x, y in points))

    def test_ellipse_outline_is_inside_fill(self):
        outline = set(shape_points("ellipse", 0, 0, 4, 4))
        filled = set(shape_points("ellipse", 0, 0, 4, 4, True))
        self.assertTrue(outline.issubset(filled))

    def test_line_tool_paints_a_stroke(self):
        bitmap = Bitmap(8, 4)
        painted = paint_shape(bitmap, None, "line", 0, 0, 3, 0,
                              1, RED, False, "square")
        self.assertEqual(painted, 4)
        self.assertEqual(bitmap.rgba(2, 0), RED)
        self.assertEqual(bitmap.rgba(2, 1), CLEAR)

    def test_shape_tool_mirrors_endpoints(self):
        bitmap = Bitmap(8, 4)
        paint_shape(bitmap, None, "line", 0, 0, 3, 0,
                    1, RED, False, "square", "x")
        self.assertEqual(bitmap.rgba(7, 0), RED)
        self.assertEqual(bitmap.rgba(4, 0), RED)

    def test_stroke_uses_brush_size(self):
        bitmap = Bitmap(8, 8)
        painted = paint_stroke(bitmap, None, 1, 1, 3, 1, 3, BLUE)
        self.assertEqual(painted, 15)
        self.assertEqual(bitmap.rgba(2, 0), BLUE)


class GradientTest(unittest.TestCase):
    def test_endpoints_and_midpoint(self):
        bitmap = Bitmap(8, 1)
        gradient_fill(bitmap, None, 0, 0, 7, 0, RED, BLUE)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(7, 0), BLUE)
        middle = bitmap.rgba(3, 0)
        self.assertGreater(middle[0], middle[2])

    def test_mask_limits_the_gradient(self):
        bitmap = Bitmap(4, 1)
        mask = bytearray(4)
        mask[1] = 255
        painted = gradient_fill(bitmap, mask, 0, 0, 3, 0, RED, BLUE)
        self.assertEqual(painted, 1)
        self.assertEqual(bitmap.rgba(0, 0), CLEAR)
        self.assertNotEqual(bitmap.rgba(1, 0), CLEAR)

    def test_zero_length_direction_is_an_error(self):
        bitmap = Bitmap(4, 4)
        with self.assertRaises(HexError):
            gradient_fill(bitmap, None, 2, 2, 2, 2, RED, BLUE)


if __name__ == "__main__":
    unittest.main()
