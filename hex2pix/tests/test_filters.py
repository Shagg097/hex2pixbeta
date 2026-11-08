"""Tests for the bare-necessity art filters."""

import unittest

from hex2pix.filters import (brightness, contrast, edges, grayscale, hue_shift,
                             luminance, noise, posterize, saturate, sepia,
                             threshold)
from hex2pix.model import Bitmap

RED = (255, 0, 0, 255)
GREY = (128, 128, 128, 255)
DARK = (100, 100, 100, 255)


def one(color):
    bitmap = Bitmap(1, 1)
    bitmap.set_rgba(0, 0, color)
    return bitmap


class BrightnessTest(unittest.TestCase):
    def test_positive_lightens(self):
        bitmap = one(DARK)
        self.assertEqual(brightness(bitmap, None, 20), 1)
        self.assertEqual(bitmap.rgba(0, 0), (151, 151, 151, 255))

    def test_clamps_at_white(self):
        bitmap = one((250, 250, 250, 255))
        brightness(bitmap, None, 100)
        self.assertEqual(bitmap.rgba(0, 0), (255, 255, 255, 255))

    def test_negative_reaches_black(self):
        bitmap = one(DARK)
        brightness(bitmap, None, -100)
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 255))

    def test_alpha_is_kept(self):
        bitmap = one((10, 20, 30, 77))
        brightness(bitmap, None, 10)
        self.assertEqual(bitmap.rgba(0, 0)[3], 77)


class ContrastTest(unittest.TestCase):
    def test_positive_spreads_from_mid_grey(self):
        bitmap = one(DARK)
        contrast(bitmap, None, 100)
        self.assertEqual(bitmap.rgba(0, 0), (72, 72, 72, 255))

    def test_negative_flattens_to_mid_grey(self):
        bitmap = one(RED)
        contrast(bitmap, None, -100)
        self.assertEqual(bitmap.rgba(0, 0), (128, 128, 128, 255))

    def test_extremes_are_clamped(self):
        bitmap = one(DARK)
        contrast(bitmap, None, 200)
        self.assertEqual(bitmap.rgba(0, 0), (72, 72, 72, 255))


class GrayscaleTest(unittest.TestCase):
    def test_red_becomes_its_luminance(self):
        bitmap = one(RED)
        self.assertEqual(grayscale(bitmap, None), 1)
        self.assertEqual(bitmap.rgba(0, 0), (76, 76, 76, 255))

    def test_alpha_is_kept(self):
        bitmap = one((10, 20, 30, 90))
        grayscale(bitmap, None)
        self.assertEqual(bitmap.rgba(0, 0)[3], 90)

    def test_luminance_helper(self):
        self.assertEqual(luminance((0, 0, 0)), 0)
        self.assertEqual(luminance((255, 255, 255)), 255)


class PosterizeTest(unittest.TestCase):
    def test_two_levels_become_black_and_white(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, (100, 100, 100, 255))
        bitmap.set_rgba(1, 0, (200, 200, 200, 255))
        posterize(bitmap, None, 2)
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 255))
        self.assertEqual(bitmap.rgba(1, 0), (255, 255, 255, 255))

    def test_four_levels_snap_to_quarters(self):
        bitmap = one((100, 100, 100, 255))
        posterize(bitmap, None, 4)
        self.assertEqual(bitmap.rgba(0, 0), (85, 85, 85, 255))

    def test_levels_are_clamped_to_thirty_two(self):
        high = one((200, 200, 200, 255))
        clamped = one((200, 200, 200, 255))
        posterize(high, None, 99)
        posterize(clamped, None, 32)
        self.assertEqual(high.rgba(0, 0), clamped.rgba(0, 0))


class ThresholdTest(unittest.TestCase):
    def test_grey_above_the_cut_goes_white(self):
        bitmap = one(GREY)
        threshold(bitmap, None, 128)
        self.assertEqual(bitmap.rgba(0, 0), (255, 255, 255, 255))

    def test_dark_below_the_cut_goes_black(self):
        bitmap = one((100, 100, 100, 255))
        threshold(bitmap, None, 128)
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 255))

    def test_zero_cut_is_all_white(self):
        bitmap = one(DARK)
        threshold(bitmap, None, 0)
        self.assertEqual(bitmap.rgba(0, 0), (255, 255, 255, 255))


class SaturationTest(unittest.TestCase):
    def test_negative_drains_to_grey(self):
        bitmap = one(RED)
        self.assertEqual(saturate(bitmap, None, -100), 1)
        self.assertEqual(bitmap.rgba(0, 0), (76, 76, 76, 255))

    def test_positive_pushes_channels_apart(self):
        bitmap = one((200, 150, 150, 255))
        saturate(bitmap, None, 100)
        self.assertEqual(bitmap.rgba(0, 0), (235, 135, 135, 255))

    def test_grey_is_unchanged(self):
        bitmap = one(GREY)
        saturate(bitmap, None, 100)
        self.assertEqual(bitmap.rgba(0, 0), GREY)


class HueShiftTest(unittest.TestCase):
    def test_red_turns_green(self):
        bitmap = one(RED)
        self.assertEqual(hue_shift(bitmap, None, 120), 1)
        self.assertEqual(bitmap.rgba(0, 0), (0, 255, 0, 255))

    def test_full_turn_is_identity(self):
        bitmap = one(RED)
        hue_shift(bitmap, None, 360)
        self.assertEqual(bitmap.rgba(0, 0), RED)

    def test_alpha_is_kept(self):
        bitmap = one((255, 0, 0, 40))
        hue_shift(bitmap, None, 90)
        self.assertEqual(bitmap.rgba(0, 0)[3], 40)


class NoiseTest(unittest.TestCase):
    def test_zero_amount_changes_nothing(self):
        bitmap = one(RED)
        self.assertEqual(noise(bitmap, None, 0), 1)
        self.assertEqual(bitmap.rgba(0, 0), RED)

    def test_grain_is_deterministic(self):
        first = Bitmap(8, 8)
        second = Bitmap(8, 8)
        for bitmap in (first, second):
            for y in range(8):
                for x in range(8):
                    bitmap.set_rgba(x, y, GREY)
        noise(first, None, 40)
        noise(second, None, 40)
        self.assertEqual(first.data, second.data)

    def test_grain_varies_and_stays_within_range(self):
        bitmap = Bitmap(8, 8)
        for y in range(8):
            for x in range(8):
                bitmap.set_rgba(x, y, GREY)
        noise(bitmap, None, 40)
        values = set(bitmap.rgba(x, y) for y in range(8) for x in range(8))
        self.assertGreater(len(values), 1)
        for value in values:
            self.assertLessEqual(abs(value[0] - 128), 102)
            self.assertEqual(value[3], 255)


class SepiaTest(unittest.TestCase):
    def test_red_maps_to_warm_brown(self):
        bitmap = one(RED)
        self.assertEqual(sepia(bitmap, None), 1)
        self.assertEqual(bitmap.rgba(0, 0), (100, 89, 69, 255))

    def test_black_stays_black(self):
        bitmap = one((0, 0, 0, 255))
        sepia(bitmap, None)
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 255))

    def test_alpha_is_kept(self):
        bitmap = one((255, 0, 0, 60))
        sepia(bitmap, None)
        self.assertEqual(bitmap.rgba(0, 0)[3], 60)


class EdgesTest(unittest.TestCase):
    def make(self):
        bitmap = Bitmap(3, 3)
        for y in range(3):
            for x in range(3):
                level = 255 if x == 2 else 0
                bitmap.set_rgba(x, y, (level, level, level, 255))
        return bitmap

    def test_flat_area_stays_black(self):
        bitmap = self.make()
        edges(bitmap, None)
        self.assertEqual(bitmap.rgba(0, 1), (0, 0, 0, 255))

    def test_boundary_becomes_grey(self):
        bitmap = self.make()
        edges(bitmap, None)
        self.assertEqual(bitmap.rgba(1, 1), (128, 128, 128, 255))

    def test_alpha_is_kept(self):
        bitmap = self.make()
        edges(bitmap, None)
        for y in range(3):
            for x in range(3):
                self.assertEqual(bitmap.rgba(x, y)[3], 255)


class MaskTest(unittest.TestCase):
    def test_only_selected_pixels_change(self):
        bitmap = Bitmap(3, 1)
        for x in range(3):
            bitmap.set_rgba(x, 0, RED)
        mask = bytearray(3)
        mask[1] = 255
        self.assertEqual(grayscale(bitmap, mask), 1)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(1, 0), (76, 76, 76, 255))

    def test_empty_mask_changes_nothing(self):
        bitmap = one(RED)
        self.assertEqual(threshold(bitmap, bytearray(1), 128), 0)
        self.assertEqual(bitmap.rgba(0, 0), RED)

    def test_edges_respects_the_mask(self):
        bitmap = EdgesTest().make()
        mask = bytearray(9)
        mask[4] = 255
        self.assertEqual(edges(bitmap, mask), 1)
        self.assertEqual(bitmap.rgba(0, 1), (0, 0, 0, 255))
        self.assertEqual(bitmap.rgba(2, 1), (255, 255, 255, 255))


if __name__ == "__main__":
    unittest.main()
