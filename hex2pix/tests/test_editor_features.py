"""Tests for smear, the softened shuffle, and the unsaved marker."""

import random
import unittest

from hex2pix.app import MIN_ZOOM, min_zoom_for
from hex2pix.effects import (blur_stroke, smear_area, shuffle_area,
                             shuffle_stroke)
from hex2pix.model import Bitmap, Document

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)


class SmearTest(unittest.TestCase):
    def test_smear_pulls_colour_from_behind_the_brush(self):
        bitmap = Bitmap(4, 1)
        for x, color in enumerate((RED, RED, BLUE, BLUE)):
            bitmap.set_rgba(x, 0, color)
        self.assertEqual(smear_area(bitmap, None, 2, 0, 1, 100, "square", "",
                                    1, 0), 1)
        self.assertEqual(bitmap.rgba(2, 0), RED)

    def test_stationary_brush_does_nothing(self):
        bitmap = Bitmap(3, 3)
        bitmap.fill(BLUE)
        self.assertEqual(smear_area(bitmap, None, 1, 1, 3, 100), 0)
        self.assertEqual(bitmap.rgba(1, 1), BLUE)

    def test_half_amount_lands_between_the_two_colours(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(1, 0, BLUE)
        smear_area(bitmap, None, 1, 0, 1, 50, "square", "", 1, 0)
        red, _green, blue, alpha = bitmap.rgba(1, 0)
        self.assertGreater(red, 100)
        self.assertGreater(blue, 100)
        self.assertEqual(alpha, 255)

    def test_smear_respects_the_mask(self):
        bitmap = Bitmap(3, 1)
        bitmap.fill(RED)
        bitmap.set_rgba(2, 0, BLUE)
        mask = bytearray(3)
        self.assertEqual(smear_area(bitmap, mask, 2, 0, 1, 100, "square", "",
                                    1, 0), 0)
        self.assertEqual(bitmap.rgba(2, 0), BLUE)

    def test_smear_reads_the_snapshot_when_given_one(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, BLUE)
        bitmap.set_rgba(1, 0, BLUE)
        source = Bitmap(2, 1)
        source.set_rgba(0, 0, RED)
        smear_area(bitmap, None, 1, 0, 1, 100, "square", "", 1, 0, source)
        self.assertEqual(bitmap.rgba(1, 0), RED)


class SmearStrokeTest(unittest.TestCase):
    def test_a_smearing_drag_carries_colour_along_the_stroke(self):
        bitmap = Bitmap(6, 1)
        for x in range(6):
            bitmap.set_rgba(x, 0, BLUE if x >= 3 else RED)
        # Dragging left with no blur strength isolates the smear: the blue on
        # the right is pulled into every pixel the brush passes over.
        written = blur_stroke(bitmap, None, 5, 0, 0, 0, 1, 0, "square", "",
                              None, 100)
        # Six pixels, but the first dab has no direction to drag in.
        self.assertEqual(written, 5)
        for x in range(6):
            self.assertEqual(bitmap.rgba(x, 0), BLUE, x)

    def test_a_drag_without_smear_leaves_the_colours_put(self):
        bitmap = Bitmap(6, 1)
        for x in range(6):
            bitmap.set_rgba(x, 0, BLUE if x >= 3 else RED)
        blur_stroke(bitmap, None, 5, 0, 0, 0, 1, 0, "square", "", None, 0)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(5, 0), BLUE)


class ShuffleStrokeTest(unittest.TestCase):
    def _stripes(self):
        bitmap = Bitmap(9, 3)
        for x in range(9):
            color = BLUE if x % 2 else RED
            for y in range(3):
                bitmap.set_rgba(x, y, color)
        return bitmap

    def test_the_blur_slider_reaches_the_stroke(self):
        bitmap = self._stripes()
        shuffle_stroke(bitmap, None, 1, 1, 7, 1, 3, "shuffle", 50, "square",
                      "", random.Random(2), blur=100)
        blended = [bitmap.rgba(x, 1) for x in range(9)
                   if bitmap.rgba(x, 1) not in (RED, BLUE)]
        self.assertTrue(blended)

    def test_a_zero_blur_stroke_only_reorders_the_colours(self):
        bitmap = self._stripes()
        shuffle_stroke(bitmap, None, 1, 1, 7, 1, 3, "shuffle", 50, "square",
                      "", random.Random(2))
        for x in range(9):
            self.assertIn(bitmap.rgba(x, 1), (RED, BLUE))


class ShuffleBlurTest(unittest.TestCase):
    def test_zero_blur_keeps_the_shuffled_colours(self):
        bitmap = Bitmap(5, 5)
        bitmap.fill(RED)
        bitmap.set_rgba(2, 2, BLUE)
        shuffle_area(bitmap, None, 2, 2, 3, "shuffle", 50, "square", "",
                    random.Random(1))
        self.assertEqual(bitmap.rgba(1, 1), RED)

    def test_full_blur_softens_every_mixed_cell(self):
        bitmap = Bitmap(5, 5)
        bitmap.fill(RED)
        bitmap.set_rgba(2, 2, BLUE)
        shuffle_area(bitmap, None, 2, 2, 3, "shuffle", 50, "square", "",
                    random.Random(1), blur=100)
        for y in range(1, 4):
            for x in range(1, 4):
                self.assertEqual(bitmap.rgba(x, y), (227, 0, 28, 255),
                                 (x, y))

    def test_blur_leaves_untouched_pixels_alone(self):
        bitmap = Bitmap(5, 5)
        bitmap.fill(RED)
        shuffle_area(bitmap, None, 2, 2, 3, "shuffle", 50, "square", "",
                    random.Random(1), blur=100)
        self.assertEqual(bitmap.rgba(0, 0), RED)


class DocumentDirtyTest(unittest.TestCase):
    def test_a_new_document_is_clean(self):
        self.assertFalse(Document(4, 4).dirty)

    def test_a_commit_marks_the_document_unsaved(self):
        document = Document(4, 4)
        before = document.begin()
        document.bitmap.set_rgba(0, 0, RED)
        document.commit(before)
        self.assertTrue(document.dirty)

    def test_undo_of_a_saved_document_marks_it_unsaved_again(self):
        document = Document(4, 4)
        before = document.begin()
        document.bitmap.set_rgba(0, 0, RED)
        document.commit(before)
        document.dirty = False
        self.assertTrue(document.undo_last())
        self.assertTrue(document.dirty)


class ZoomOutLimitTest(unittest.TestCase):
    def test_the_floor_shrinks_with_the_longer_side(self):
        self.assertAlmostEqual(min_zoom_for(16, 16), 1.0 / (16 * 50000))
        self.assertAlmostEqual(min_zoom_for(4096, 100), 1.0 / (4096 * 50000))

    def test_a_bigger_artwork_may_zoom_out_further(self):
        self.assertLess(min_zoom_for(4000, 4000), min_zoom_for(400, 400))

    def test_either_side_can_set_the_limit(self):
        self.assertEqual(min_zoom_for(8, 64), min_zoom_for(64, 8))

    def test_the_floor_stays_positive(self):
        self.assertGreaterEqual(min_zoom_for(1, 1), MIN_ZOOM)
        self.assertGreaterEqual(min_zoom_for(12000000, 1), MIN_ZOOM)


if __name__ == "__main__":
    unittest.main()
