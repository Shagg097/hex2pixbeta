"""Tests for ordered, error-diffusion, pattern and gradient dithering."""

import unittest

from hex2pix.dither import (DITHER_LABELS, DITHER_MODES,
                            apply_gradient_dither, bayer_matrix,
                            bayer_threshold, dither_bitmap, luminance,
                            pattern_dither, pattern_threshold)
from hex2pix.model import SKIP, Bitmap, Block, HexError

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


def gradient_bitmap(width=16, height=1):
    bitmap = Bitmap(width, height)
    for y in range(height):
        for x in range(width):
            level = int(round(255 * x / float(max(1, width - 1))))
            bitmap.set_rgba(x, y, (level, level, level, 255))
    return bitmap


class BayerTest(unittest.TestCase):
    def test_base_matrix(self):
        self.assertEqual(bayer_matrix(2), ((0, 2), (3, 1)))

    def test_four_by_four_first_row(self):
        self.assertEqual(bayer_matrix(4)[0], (0, 8, 2, 10))

    def test_matrices_use_every_number_once(self):
        for size in (2, 4, 8):
            values = sorted(value for row in bayer_matrix(size)
                            for value in row)
            self.assertEqual(values, list(range(size * size)))

    def test_thresholds_are_fractions(self):
        for x in range(4):
            for y in range(4):
                threshold = bayer_threshold(x, y, 4)
                self.assertGreater(threshold, 0.0)
                self.assertLess(threshold, 1.0)

    def test_luminance(self):
        self.assertEqual(luminance(BLACK), 0)
        self.assertEqual(luminance(WHITE), 255)
        self.assertEqual(luminance(RED), 76)


class DitherTest(unittest.TestCase):
    def test_only_the_two_colours_are_written(self):
        bitmap = gradient_bitmap(16, 1)
        painted = dither_bitmap(bitmap, None, WHITE, BLACK, "bayer4")
        self.assertEqual(painted, 16)
        for x in range(16):
            self.assertIn(bitmap.rgba(x, 0), (WHITE, BLACK))

    def test_dark_end_and_light_end(self):
        bitmap = gradient_bitmap(16, 1)
        dither_bitmap(bitmap, None, WHITE, BLACK, "bayer4")
        self.assertEqual(bitmap.rgba(0, 0), BLACK)
        self.assertEqual(bitmap.rgba(15, 0), WHITE)

    def test_mask_limits_the_dither(self):
        bitmap = gradient_bitmap(16, 1)
        mask = bytearray(16)
        mask[4] = 255
        self.assertEqual(dither_bitmap(bitmap, mask, WHITE, BLACK, "bayer4"),
                         1)

    def test_off_mode_does_nothing(self):
        bitmap = gradient_bitmap(8, 1)
        before = [bitmap.rgba(x, 0) for x in range(8)]
        self.assertEqual(dither_bitmap(bitmap, None, WHITE, BLACK, "off"), 0)
        self.assertEqual([bitmap.rgba(x, 0) for x in range(8)], before)

    def test_floyd_spreads_mid_grey(self):
        bitmap = gradient_bitmap(32, 1)
        painted = dither_bitmap(bitmap, None, WHITE, BLACK, "floyd")
        self.assertEqual(painted, 32)
        light = sum(1 for x in range(32) if bitmap.rgba(x, 0) == WHITE)
        self.assertGreater(light, 4)
        self.assertLess(light, 28)


class PatternDitherTest(unittest.TestCase):
    def test_bright_cells_become_light_pixels(self):
        bitmap = Bitmap(4, 1)
        block = Block(1, 1, [[WHITE]])
        self.assertEqual(pattern_dither(bitmap, None, WHITE, BLACK, block), 4)
        for x in range(4):
            self.assertEqual(bitmap.rgba(x, 0), WHITE)

    def test_dark_cells_become_dark_pixels(self):
        bitmap = Bitmap(4, 1)
        block = Block(1, 1, [[BLACK]])
        pattern_dither(bitmap, None, WHITE, BLACK, block)
        for x in range(4):
            self.assertEqual(bitmap.rgba(x, 0), BLACK)

    def test_skip_cells_leave_pixels_alone(self):
        bitmap = Bitmap(2, 1)
        block = Block(2, 1, [[SKIP, WHITE]])
        painted = pattern_dither(bitmap, None, WHITE, BLACK, block)
        self.assertEqual(painted, 1)
        self.assertEqual(bitmap.rgba(0, 0), CLEAR)
        self.assertEqual(bitmap.rgba(1, 0), WHITE)

    def test_scale_repeats_the_pattern(self):
        bitmap = Bitmap(4, 1)
        block = Block(2, 1, [[WHITE, BLACK]])
        pattern_dither(bitmap, None, WHITE, BLACK, block, 2)
        self.assertEqual([bitmap.rgba(x, 0) for x in range(4)],
                         [WHITE, WHITE, BLACK, BLACK])

    def test_pattern_threshold_handles_missing_block(self):
        self.assertEqual(pattern_threshold(None, 0, 0), 0.5)


class ModeTest(unittest.TestCase):
    def test_noise_uses_both_colours(self):
        bitmap = Bitmap(16, 16)
        for y in range(16):
            for x in range(16):
                bitmap.set_rgba(x, y, (128, 128, 128, 255))
        self.assertEqual(dither_bitmap(bitmap, None, WHITE, BLACK, "noise"),
                         256)
        pixels = set(bitmap.rgba(x, y) for y in range(16) for x in range(16))
        self.assertEqual(pixels, set([WHITE, BLACK]))

    def test_lines_band_every_row(self):
        bitmap = Bitmap(8, 8)
        for y in range(8):
            for x in range(8):
                bitmap.set_rgba(x, y, (128, 128, 128, 255))
        dither_bitmap(bitmap, None, WHITE, BLACK, "lines")
        self.assertEqual(set(bitmap.rgba(x, 0) for x in range(8)), set([WHITE]))
        self.assertEqual(set(bitmap.rgba(x, 1) for x in range(8)), set([BLACK]))

    def test_lines_scale_widens_the_bands(self):
        bitmap = Bitmap(8, 8)
        for y in range(8):
            for x in range(8):
                bitmap.set_rgba(x, y, (128, 128, 128, 255))
        dither_bitmap(bitmap, None, WHITE, BLACK, "lines", scale=2)
        self.assertEqual(set(bitmap.rgba(x, 0) for x in range(8)), set([WHITE]))
        self.assertEqual(set(bitmap.rgba(x, 1) for x in range(8)), set([WHITE]))
        self.assertEqual(set(bitmap.rgba(x, 2) for x in range(8)), set([BLACK]))


class OptionsTest(unittest.TestCase):
    def test_bias_darkens_the_cut(self):
        neutral = gradient_bitmap(16, 1)
        biased = gradient_bitmap(16, 1)
        dither_bitmap(neutral, None, WHITE, BLACK, "bayer4")
        dither_bitmap(biased, None, WHITE, BLACK, "bayer4", bias=100)
        neutral_light = sum(1 for x in range(16)
                            if neutral.rgba(x, 0) == WHITE)
        biased_light = sum(1 for x in range(16) if biased.rgba(x, 0) == WHITE)
        self.assertLess(biased_light, neutral_light)

    def test_negative_bias_lightens_the_cut(self):
        neutral = gradient_bitmap(16, 1)
        biased = gradient_bitmap(16, 1)
        dither_bitmap(neutral, None, WHITE, BLACK, "bayer4")
        dither_bitmap(biased, None, WHITE, BLACK, "bayer4", bias=-100)
        neutral_light = sum(1 for x in range(16)
                            if neutral.rgba(x, 0) == WHITE)
        biased_light = sum(1 for x in range(16) if biased.rgba(x, 0) == WHITE)
        self.assertGreater(biased_light, neutral_light)

    def test_strength_zero_keeps_every_pixel(self):
        bitmap = gradient_bitmap(8, 1)
        before = [bitmap.rgba(x, 0) for x in range(8)]
        self.assertEqual(dither_bitmap(bitmap, None, WHITE, BLACK, "bayer4",
                                       strength=0), 8)
        self.assertEqual([bitmap.rgba(x, 0) for x in range(8)], before)

    def test_strength_half_mixes(self):
        bitmap = Bitmap(2, 2)
        for y in range(2):
            for x in range(2):
                bitmap.set_rgba(x, y, (128, 128, 128, 255))
        dither_bitmap(bitmap, None, WHITE, BLACK, "bayer4", strength=50)
        self.assertEqual(bitmap.rgba(1, 0), (64, 64, 64, 255))

    def test_serpentine_only_writes_the_two_colours(self):
        bitmap = gradient_bitmap(16, 4)
        painted = dither_bitmap(bitmap, None, WHITE, BLACK, "floyd",
                                serpentine=True)
        self.assertEqual(painted, 64)
        colours = set(bitmap.rgba(x, y) for y in range(4) for x in range(16))
        self.assertTrue(colours <= set([WHITE, BLACK]))

    def test_serpentine_changes_the_pattern(self):
        plain = Bitmap(10, 10)
        wavy = Bitmap(10, 10)
        for bitmap in (plain, wavy):
            for y in range(10):
                for x in range(10):
                    bitmap.set_rgba(x, y, (153, 153, 153, 255))
        dither_bitmap(plain, None, WHITE, BLACK, "floyd")
        dither_bitmap(wavy, None, WHITE, BLACK, "floyd", serpentine=True)
        plain_rows = [[plain.rgba(x, y) for x in range(10)] for y in range(10)]
        wavy_rows = [[wavy.rgba(x, y) for x in range(10)] for y in range(10)]
        self.assertNotEqual(plain_rows, wavy_rows)
        for rows in (plain_rows, wavy_rows):
            for row in rows:
                for pixel in row:
                    self.assertIn(pixel, (WHITE, BLACK))

    def test_gradient_strength_blends_towards_the_ramp(self):
        bitmap = Bitmap(8, 1)
        apply_gradient_dither(bitmap, None, 0, 0, 7, 0, RED, BLUE, "bayer4",
                              strength=50)
        colours = set(bitmap.rgba(x, 0) for x in range(8))
        self.assertNotIn(RED, colours)
        self.assertNotIn(BLUE, colours)

    def test_gradient_bias_shifts_the_cut(self):
        neutral = Bitmap(8, 1)
        biased = Bitmap(8, 1)
        apply_gradient_dither(neutral, None, 0, 0, 7, 0, RED, BLUE, "bayer4")
        apply_gradient_dither(biased, None, 0, 0, 7, 0, RED, BLUE, "bayer4",
                              bias=-100)
        neutral_start = sum(1 for x in range(8)
                            if neutral.rgba(x, 0) == RED)
        biased_start = sum(1 for x in range(8) if biased.rgba(x, 0) == RED)
        self.assertGreater(biased_start, neutral_start)


class GradientDitherTest(unittest.TestCase):
    def test_gradient_uses_only_the_two_colours(self):
        bitmap = Bitmap(16, 1)
        painted = apply_gradient_dither(bitmap, None, 0, 0, 15, 0, RED, BLUE,
                                        "bayer4")
        self.assertEqual(painted, 16)
        for x in range(16):
            self.assertIn(bitmap.rgba(x, 0), (RED, BLUE))
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(15, 0), BLUE)

    def test_gradient_mask_limits_the_fill(self):
        bitmap = Bitmap(8, 1)
        mask = bytearray(8)
        mask[2] = 255
        self.assertEqual(apply_gradient_dither(bitmap, mask, 0, 0, 7, 0, RED,
                                               BLUE, "floyd"), 1)

    def test_gradient_pattern_block(self):
        bitmap = Bitmap(4, 1)
        block = Block(1, 1, [[WHITE]])
        apply_gradient_dither(bitmap, None, 0, 0, 3, 0, RED, BLUE, "pattern",
                              block)
        for x in range(4):
            self.assertEqual(bitmap.rgba(x, 0), RED)

    def test_zero_length_gradient_is_an_error(self):
        with self.assertRaises(HexError):
            apply_gradient_dither(Bitmap(4, 4), None, 2, 2, 2, 2, RED, BLUE)

    def test_labels_and_modes_agree(self):
        self.assertEqual(set(DITHER_LABELS.values()), set(DITHER_MODES))


if __name__ == "__main__":
    unittest.main()
