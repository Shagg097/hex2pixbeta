"""Tests for the Aseprite-style drawing helpers."""

import unittest

from hex2pix.model import Bitmap, Block, HexError
from hex2pix.shapes import (SHAPE_KINDS, brush_offsets, curve_points,
                            gradient_fill, mirror_points, paint_curve,
                            paint_dab, paint_shape, paint_stroke, shape_color,
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


class PresetShapeTests(unittest.TestCase):
    def test_every_preset_kind_is_known(self):
        for kind in ("triangle", "diamond", "star", "arrow"):
            self.assertIn(kind, SHAPE_KINDS)

    def test_triangle_fill_is_wide_at_the_base(self):
        filled = set(shape_points("triangle", 0, 0, 4, 4, True))
        self.assertIn((2, 3), filled)
        self.assertNotIn((0, 0), filled)
        self.assertTrue(all(0 <= x <= 4 and 0 <= y <= 4 for x, y in filled))

    def test_diamond_is_symmetric(self):
        outline = shape_points("diamond", 0, 0, 8, 8)
        corners = set(outline)
        self.assertTrue(corners)
        for x, y in outline:
            self.assertIn((8 - x, y), corners)
            self.assertIn((x, 8 - y), corners)

    def test_star_fill_covers_its_centre_and_box(self):
        outline = set(shape_points("star", 0, 0, 12, 12))
        filled = set(shape_points("star", 0, 0, 12, 12, True))
        self.assertTrue(outline)
        self.assertIn((6, 6), filled)
        self.assertTrue(all(0 <= x <= 12 and 0 <= y <= 12
                            for x, y in filled))
        self.assertLessEqual(min(y for _, y in filled), 2)
        self.assertGreaterEqual(max(y for _, y in filled), 10)

    def test_arrow_points_right(self):
        filled = set(shape_points("arrow", 0, 0, 12, 8, True))
        self.assertIn((11, 4), filled)
        self.assertIn((1, 4), filled)
        self.assertNotIn((0, 0), filled)

    def test_preset_shapes_clip_to_the_canvas(self):
        points = shape_points("star", -20, -20, 20, 20, True, bounds=(6, 6))
        self.assertTrue(points)
        self.assertTrue(all(0 <= x < 6 and 0 <= y < 6 for x, y in points))

    def test_background_toggle_swaps_the_paint_colour(self):
        self.assertEqual(shape_color(RED, BLUE, True), BLUE)
        self.assertEqual(shape_color(RED, BLUE, False), RED)
        self.assertEqual(shape_color(RED, BLUE), RED)


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

    def test_dither_uses_only_the_two_colours(self):
        bitmap = Bitmap(16, 1)
        painted = gradient_fill(bitmap, None, 0, 0, 15, 0, RED, BLUE,
                                "bayer4")
        self.assertEqual(painted, 16)
        colours = [bitmap.rgba(x, 0) for x in range(16)]
        self.assertIn(RED, colours)
        self.assertIn(BLUE, colours)
        self.assertTrue(set(colours) <= set([RED, BLUE]))

    def test_gradient_dither_honours_strength(self):
        bitmap = Bitmap(8, 1)
        gradient_fill(bitmap, None, 0, 0, 7, 0, RED, BLUE, "bayer4",
                      strength=50)
        colours = set(bitmap.rgba(x, 0) for x in range(8))
        self.assertNotIn(RED, colours)
        self.assertNotIn(BLUE, colours)

    def test_pattern_dither_uses_the_block(self):
        bitmap = Bitmap(4, 1)
        block = Block(1, 1, [[(255, 255, 255, 255)]])
        gradient_fill(bitmap, None, 0, 0, 3, 0, RED, BLUE, "pattern", block)
        self.assertEqual([bitmap.rgba(x, 0) for x in range(4)], [RED] * 4)

    def test_radial_gradient_follows_the_distance(self):
        bitmap = Bitmap(9, 9)
        gradient_fill(bitmap, None, 4, 4, 8, 4, RED, BLUE, radial=True)
        self.assertEqual(bitmap.rgba(4, 4), RED)
        self.assertEqual(bitmap.rgba(8, 4), BLUE)
        self.assertEqual(bitmap.rgba(4, 8), BLUE)
        self.assertEqual(bitmap.rgba(0, 4), BLUE)

    def test_palette_gradient_walks_every_colour(self):
        bitmap = Bitmap(5, 1)
        stops = [RED, (0, 255, 0, 255), BLUE]
        gradient_fill(bitmap, None, 0, 0, 4, 0, RED, BLUE, colors=stops)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(2, 0), (0, 255, 0, 255))
        self.assertEqual(bitmap.rgba(4, 0), BLUE)

    def test_spread_repeats_and_reflects_the_ramp(self):
        repeat = Bitmap(9, 1)
        gradient_fill(repeat, None, 0, 0, 3, 0, RED, BLUE, spread="repeat")
        self.assertEqual(repeat.rgba(3, 0), RED)
        self.assertEqual(repeat.rgba(6, 0), RED)
        reflect = Bitmap(9, 1)
        gradient_fill(reflect, None, 0, 0, 3, 0, RED, BLUE, spread="reflect")
        self.assertEqual(reflect.rgba(3, 0), BLUE)
        self.assertEqual(reflect.rgba(6, 0), RED)
        clamp = Bitmap(9, 1)
        gradient_fill(clamp, None, 0, 0, 3, 0, RED, BLUE)
        self.assertEqual(clamp.rgba(6, 0), BLUE)

    def test_zero_length_direction_is_an_error(self):
        bitmap = Bitmap(4, 4)
        with self.assertRaises(HexError):
            gradient_fill(bitmap, None, 2, 2, 2, 2, RED, BLUE)


class CurveTest(unittest.TestCase):
    def test_zero_bend_is_a_straight_line(self):
        points = curve_points(0, 0, 6, 0, 0)
        self.assertEqual(points[0], (0, 0))
        self.assertEqual(points[-1], (6, 0))
        self.assertTrue(all(y == 0 for _x, y in points))

    def test_bend_arcs_away_from_the_chord(self):
        points = curve_points(0, 0, 8, 0, 50)
        self.assertEqual(points[0], (0, 0))
        self.assertEqual(points[-1], (8, 0))
        # A 50% bend on an 8 px chord peaks 2 px out.
        self.assertEqual(max(y for _x, y in points), 2)

    def test_negative_bend_goes_the_other_way(self):
        up = curve_points(0, 4, 8, 4, 50)
        down = curve_points(0, 4, 8, 4, -50)
        self.assertGreater(max(y for _x, y in up), 4)
        self.assertLess(min(y for _x, y in down), 4)

    def test_degenerate_chord_is_a_single_point(self):
        self.assertEqual(curve_points(3, 3, 3, 3, 50), [(3, 3)])

    def test_curve_paints_its_apex(self):
        bitmap = Bitmap(16, 16)
        painted = paint_curve(bitmap, None, 0, 4, 8, 4, 1, RED, 50)
        self.assertGreater(painted, 0)
        # The middle of a 50% bend on an 8 px chord sits 2 px below the ends.
        self.assertEqual(bitmap.rgba(4, 6), RED)

    def test_two_vectors_make_a_wave(self):
        points = curve_points(0, 4, 12, 4, 30, 2, "alternate")
        self.assertEqual(points[0], (0, 4))
        self.assertEqual(points[-1], (12, 4))
        ys = [y for _x, y in points]
        self.assertLess(min(ys), 4)
        self.assertGreater(max(ys), 4)

    def test_direction_picks_the_side(self):
        left = curve_points(0, 4, 8, 4, 30, 1, "left")
        right = curve_points(0, 4, 8, 4, 30, 1, "right")
        self.assertGreater(max(y for _x, y in left), 4)
        self.assertLess(min(y for _x, y in right), 4)

    def test_three_vectors_ripple(self):
        ys = [y for _x, y in curve_points(0, 10, 18, 10, 20, 3, "alternate")]
        self.assertLess(min(ys), 10)
        self.assertGreater(max(ys), 10)

    def test_vectors_are_clamped(self):
        self.assertEqual(curve_points(0, 0, 8, 0, 30, 0, "left"),
                         curve_points(0, 0, 8, 0, 30, 1, "left"))
        self.assertEqual(len(curve_points(0, 0, 8, 0, 30, 99, "alternate")),
                         len(curve_points(0, 0, 8, 0, 30, 8, "alternate")))

    def test_unknown_direction_falls_back_to_left(self):
        self.assertEqual(curve_points(0, 4, 8, 4, 30, 1, "sideways"),
                         curve_points(0, 4, 8, 4, 30, 1, "left"))

    def test_wave_paints_both_sides(self):
        bitmap = Bitmap(24, 20)
        paint_curve(bitmap, None, 2, 10, 22, 10, 1, RED, 30, "square", "", 2,
                    "alternate")
        above = any(bitmap.rgba(x, y) == RED
                    for x in range(24) for y in range(0, 10))
        below = any(bitmap.rgba(x, y) == RED
                    for x in range(24) for y in range(11, 20))
        self.assertTrue(above)
        self.assertTrue(below)

    def test_direction_right_flips_the_arc(self):
        left = Bitmap(16, 16)
        right = Bitmap(16, 16)
        paint_curve(left, None, 0, 8, 12, 8, 1, RED, 40, "square", "", 1,
                    "left")
        paint_curve(right, None, 0, 8, 12, 8, 1, RED, 40, "square", "", 1,
                    "right")
        self.assertEqual(left.rgba(6, 10), RED)
        self.assertEqual(right.rgba(6, 6), RED)

    def test_curve_mirrors_endpoints(self):
        bitmap = Bitmap(16, 16)
        paint_curve(bitmap, None, 1, 4, 9, 4, 1, RED, 40, "square", "x")
        self.assertEqual(bitmap.rgba(14, 4), RED)
        self.assertEqual(bitmap.rgba(6, 4), RED)


class OffCanvasTest(unittest.TestCase):
    """Shapes keep their geometry when a drag runs past the canvas edge."""

    def test_rect_is_clipped_to_the_canvas(self):
        points = shape_points("rect", -5, -5, 2, 2, True, bounds=(4, 4))
        self.assertEqual(sorted(points),
                         sorted((x, y) for y in range(3) for x in range(3)))

    def test_ellipse_points_stay_inside_the_bounds(self):
        points = shape_points("ellipse", -6, -6, 6, 6, False, bounds=(8, 8))
        self.assertTrue(points)
        self.assertTrue(all(0 <= x < 8 and 0 <= y < 8 for x, y in points))

    def test_ellipse_past_the_edge_keeps_its_curvature(self):
        bitmap = Bitmap(8, 8)
        paint_shape(bitmap, None, "ellipse", -100, -100, 100, 100, 1, RED,
                    True)
        filled = sum(1 for y in range(8) for x in range(8)
                     if bitmap.rgba(x, y) == RED)
        self.assertEqual(filled, 64)

    def test_line_past_the_edge_crosses_the_canvas(self):
        bitmap = Bitmap(8, 8)
        paint_shape(bitmap, None, "line", -8, -8, 8, 8, 1, RED)
        self.assertEqual(bitmap.rgba(4, 4), RED)
        self.assertEqual(bitmap.rgba(0, 0), RED)


if __name__ == "__main__":
    unittest.main()
