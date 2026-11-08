"""Tests for the blur and jumble pixel effects."""

import random
import unittest

from hex2pix.effects import (CONTOUR_SIDES, JUMBLE_MODES, blur_area,
                             blur_stroke, content_mask, contour_mask,
                             grow_mask, jumble_area, shrink_mask, spray_area,
                             spray_stroke)
from hex2pix.model import Bitmap
from hex2pix.shapes import paint_mask

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
GREEN = (0, 255, 0, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


class BlurTest(unittest.TestCase):
    def test_full_strength_takes_the_neighbourhood_average(self):
        bitmap = Bitmap(3, 3)
        bitmap.fill(RED)
        bitmap.set_rgba(1, 1, BLUE)
        blur_area(bitmap, None, 1, 1, 1, 100)
        self.assertEqual(bitmap.rgba(1, 1), (227, 0, 28, 255))

    def test_strength_zero_leaves_the_pixels_alone(self):
        bitmap = Bitmap(3, 3)
        bitmap.fill(RED)
        bitmap.set_rgba(1, 1, BLUE)
        self.assertEqual(blur_area(bitmap, None, 1, 1, 1, 0), 0)
        self.assertEqual(bitmap.rgba(1, 1), BLUE)

    def test_half_strength_lands_between_the_two_colours(self):
        bitmap = Bitmap(3, 3)
        bitmap.fill(BLUE)
        bitmap.set_rgba(1, 1, RED)
        blur_area(bitmap, None, 1, 1, 1, 50)
        red, green, blue, alpha = bitmap.rgba(1, 1)
        self.assertGreater(red, 10)
        self.assertLess(red, 255)
        self.assertGreater(blue, 0)
        self.assertEqual(alpha, 255)

    def test_mask_limits_the_pixels_written(self):
        bitmap = Bitmap(3, 3)
        bitmap.fill(RED)
        bitmap.set_rgba(1, 1, BLUE)
        mask = bytearray(9)
        self.assertEqual(blur_area(bitmap, mask, 1, 1, 1, 100), 0)
        self.assertEqual(bitmap.rgba(1, 1), BLUE)

    def test_the_snapshot_stops_repeated_dabs_compounding(self):
        once = Bitmap(5, 5)
        twice = Bitmap(5, 5)
        for bitmap in (once, twice):
            bitmap.fill(RED)
            bitmap.set_rgba(2, 2, BLUE)
        source = once.copy()
        blur_area(once, None, 2, 2, 1, 100, source=source)
        blur_area(twice, None, 2, 2, 1, 100, source=twice.copy())
        blur_area(twice, None, 2, 2, 1, 100, source=source)
        self.assertEqual(once.rgba(2, 2), twice.rgba(2, 2))

    def test_stroke_softens_a_row(self):
        bitmap = Bitmap(9, 1)
        for x in range(0, 9, 2):
            bitmap.set_rgba(x, 0, WHITE)
        painted = blur_stroke(bitmap, None, 0, 0, 8, 0, 1, 100)
        self.assertGreater(painted, 0)
        self.assertNotEqual(bitmap.rgba(1, 0), CLEAR)

    def test_mirrored_blur_writes_both_sides(self):
        bitmap = Bitmap(9, 1)
        bitmap.fill(RED)
        bitmap.set_rgba(0, 0, WHITE)
        blur_area(bitmap, None, 0, 0, 1, 100, "square", "x")
        self.assertNotEqual(bitmap.rgba(0, 0), WHITE)
        self.assertNotEqual(bitmap.rgba(8, 0), WHITE)


class JumbleTest(unittest.TestCase):
    def four_colors(self):
        bitmap = Bitmap(2, 2)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(1, 0, GREEN)
        bitmap.set_rgba(0, 1, BLUE)
        bitmap.set_rgba(1, 1, WHITE)
        return bitmap

    def colors(self, bitmap):
        return sorted(bitmap.rgba(x, y) for y in range(bitmap.height)
                      for x in range(bitmap.width))

    def test_shuffle_keeps_every_colour(self):
        bitmap = self.four_colors()
        before = self.colors(bitmap)
        self.assertEqual(jumble_area(bitmap, None, 0, 0, 3, "shuffle", 50,
                                     rng=random.Random(7)), 4)
        self.assertEqual(self.colors(bitmap), before)

    def test_shuffle_is_deterministic_for_a_seed(self):
        first = self.four_colors()
        second = self.four_colors()
        jumble_area(first, None, 0, 0, 3, "shuffle", 50,
                    rng=random.Random(3))
        jumble_area(second, None, 0, 0, 3, "shuffle", 50,
                    rng=random.Random(3))
        self.assertEqual(self.colors(first), self.colors(second))
        self.assertEqual([first.rgba(x, y) for y in range(2) for x in range(2)],
                         [second.rgba(x, y) for y in range(2) for x in range(2)])

    def test_scatter_only_uses_colours_from_the_area(self):
        bitmap = self.four_colors()
        allowed = set(self.colors(bitmap))
        jumble_area(bitmap, None, 0, 0, 3, "scatter", 50,
                    rng=random.Random(11))
        self.assertTrue(set(self.colors(bitmap)) <= allowed)

    def test_noise_keeps_alpha_and_stays_in_range(self):
        bitmap = Bitmap(3, 3)
        bitmap.fill((100, 100, 100, 128))
        jumble_area(bitmap, None, 1, 1, 3, "noise", 20,
                    rng=random.Random(5))
        for y in range(3):
            for x in range(3):
                red, green, blue, alpha = bitmap.rgba(x, y)
                self.assertEqual(alpha, 128)
                for channel in (red, green, blue):
                    self.assertGreaterEqual(channel, 49)
                    self.assertLessEqual(channel, 151)

    def test_noise_with_zero_amount_changes_nothing(self):
        bitmap = self.four_colors()
        before = self.colors(bitmap)
        jumble_area(bitmap, None, 0, 0, 3, "noise", 0,
                    rng=random.Random(1))
        self.assertEqual(self.colors(bitmap), before)

    def test_single_pixel_area_does_nothing(self):
        bitmap = Bitmap(1, 1)
        bitmap.set_rgba(0, 0, RED)
        self.assertEqual(jumble_area(bitmap, None, 0, 0, 1, "shuffle"), 0)

    def test_mask_limits_the_area(self):
        bitmap = self.four_colors()
        mask = bytearray(4)
        mask[0] = 255
        self.assertEqual(jumble_area(bitmap, mask, 0, 0, 3, "shuffle"), 0)
        self.assertEqual(bitmap.rgba(0, 0), RED)

    def test_unknown_mode_falls_back_to_shuffle(self):
        bitmap = self.four_colors()
        before = self.colors(bitmap)
        jumble_area(bitmap, None, 0, 0, 3, "sparkle", 50,
                    rng=random.Random(2))
        self.assertEqual(self.colors(bitmap), before)
        self.assertIn("shuffle", JUMBLE_MODES)


class SprayTest(unittest.TestCase):
    def test_full_flow_fills_the_footprint(self):
        bitmap = Bitmap(9, 9)
        painted = spray_area(bitmap, None, 4, 4, 3, 100, RED, "square",
                             rng=random.Random(1))
        self.assertEqual(painted, 9)
        self.assertEqual(bitmap.rgba(4, 4), RED)

    def test_zero_flow_paints_nothing(self):
        bitmap = Bitmap(9, 9)
        self.assertEqual(spray_area(bitmap, None, 4, 4, 3, 0, RED,
                                    rng=random.Random(1)), 0)

    def test_flow_is_a_percentage_of_the_footprint(self):
        bitmap = Bitmap(20, 20)
        painted = spray_area(bitmap, None, 10, 10, 16, 50, RED, "square",
                             rng=random.Random(4))
        self.assertGreater(painted, 20)
        self.assertLess(painted, 256)

    def test_spray_respects_the_mask(self):
        bitmap = Bitmap(9, 9)
        mask = bytearray(81)
        self.assertEqual(spray_area(bitmap, mask, 4, 4, 3, 100, RED,
                                    rng=random.Random(1)), 0)

    def test_spray_uses_the_color_it_is_given(self):
        bitmap = Bitmap(9, 9)
        spray_area(bitmap, None, 4, 4, 3, 100, BLUE, "square",
                   rng=random.Random(1))
        self.assertEqual(bitmap.rgba(4, 4), BLUE)

    def test_spray_stroke_covers_the_path(self):
        bitmap = Bitmap(21, 3)
        painted = spray_stroke(bitmap, None, 0, 1, 20, 1, 1, 100, GREEN,
                               "square", rng=random.Random(1))
        self.assertEqual(painted, 21)
        self.assertEqual(bitmap.rgba(10, 1), GREEN)


def block():
    solid = bytearray(25)
    for y in (1, 2, 3):
        for x in (1, 2, 3):
            solid[y * 5 + x] = 255
    return solid


class ContourTest(unittest.TestCase):
    def test_outside_ring_around_a_block(self):
        ring = contour_mask(block(), 5, 5, 1, "outside", 4)
        self.assertEqual(sum(1 for value in ring if value), 12)
        self.assertEqual(ring[0], 0)
        self.assertEqual(ring[1 * 5 + 0], 255)

    def test_eight_connected_ring_fills_the_corners(self):
        ring = contour_mask(block(), 5, 5, 1, "outside", 8)
        self.assertEqual(sum(1 for value in ring if value), 16)

    def test_inside_ring_skips_the_centre(self):
        ring = contour_mask(block(), 5, 5, 1, "inside", 4)
        self.assertEqual(sum(1 for value in ring if value), 8)
        self.assertEqual(ring[2 * 5 + 2], 0)

    def test_width_widens_the_ring(self):
        ring = contour_mask(block(), 5, 5, 2, "outside", 4)
        self.assertEqual(sum(1 for value in ring if value), 16)

    def test_content_mask_uses_alpha(self):
        bitmap = Bitmap(3, 1)
        bitmap.set_rgba(1, 0, RED)
        self.assertEqual(list(content_mask(bitmap)), [0, 255, 0])

    def test_content_mask_prefers_the_region(self):
        bitmap = Bitmap(3, 1)
        self.assertEqual(list(content_mask(bitmap, bytearray([1, 0, 1]))),
                         [255, 0, 255])

    def test_paint_mask_respects_a_selection(self):
        bitmap = Bitmap(3, 1)
        region = bytes([255, 255, 255])
        mask = bytearray(3)
        mask[1] = 255
        self.assertEqual(paint_mask(bitmap, mask, region, BLUE), 1)
        self.assertEqual(bitmap.rgba(1, 0), BLUE)
        self.assertEqual(bitmap.rgba(0, 0), CLEAR)

    def test_painting_a_contour_writes_the_ring(self):
        bitmap = Bitmap(5, 5)
        for y in (1, 2, 3):
            for x in (1, 2, 3):
                bitmap.set_rgba(x, y, RED)
        ring = contour_mask(content_mask(bitmap), 5, 5, 1, "outside", 4)
        self.assertEqual(paint_mask(bitmap, None, ring, BLUE), 12)
        self.assertEqual(bitmap.rgba(0, 1), BLUE)
        self.assertEqual(bitmap.rgba(2, 2), RED)

    def test_grow_and_shrink_round_trip(self):
        grown = grow_mask(block(), 5, 5, 1, 4)
        self.assertEqual(sum(1 for value in grown if value), 21)
        shrunk = shrink_mask(grown, 5, 5, 1, 4)
        self.assertEqual(sum(1 for value in shrunk if value), 9)

    def test_side_names(self):
        self.assertEqual(CONTOUR_SIDES, ("outside", "inside"))


if __name__ == "__main__":
    unittest.main()
