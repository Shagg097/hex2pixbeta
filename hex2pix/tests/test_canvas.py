"""Tests for canvas properties: anchors, pad/crop, resampling, modes, undo."""

import unittest

from hex2pix.canvas import (ANCHOR_CODES, CanvasSpec, apply_spec_to_document,
                             convert_mode, flatten_alpha, offset_for,
                             physical_size, px_per_unit, resize_canvas,
                             scale_image)
from hex2pix.model import Bitmap, Document, HexError, paint_brush

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)


class SpecTests(unittest.TestCase):
    def test_defaults(self):
        spec = CanvasSpec(32, 24)
        self.assertEqual((spec.width, spec.height), (32, 24))
        self.assertEqual(spec.ppi, 72.0)
        self.assertEqual(spec.color_mode, "RGB")
        self.assertTrue(spec.alpha)
        self.assertEqual(spec.resize_mode, "canvas")

    def test_normalizes(self):
        spec = CanvasSpec(8, 8, 300, "grayscale", False, resize_mode="scale",
                          anchor="nope", resample="bilinear")
        self.assertEqual(spec.color_mode, "Grayscale")
        self.assertFalse(spec.alpha)
        self.assertEqual(spec.resize_mode, "scale")
        self.assertEqual(spec.anchor, "center")
        self.assertEqual(spec.resample, "bilinear")

    def test_units_and_physical_size(self):
        self.assertAlmostEqual(px_per_unit("in", 300), 300.0)
        self.assertAlmostEqual(px_per_unit("mm", 300), 300 / 25.4)
        mm_w, mm_h, in_w, in_h = physical_size(300, 150, 300)
        self.assertAlmostEqual(mm_w, 25.4)
        self.assertAlmostEqual(in_w, 1.0)
        self.assertAlmostEqual(in_h, 0.5)


class AnchorTests(unittest.TestCase):
    def test_grow(self):
        self.assertEqual(offset_for(2, 2, 4, 4, "nw"), (0, 0))
        self.assertEqual(offset_for(2, 2, 4, 4, "center"), (1, 1))
        self.assertEqual(offset_for(2, 2, 4, 4, "se"), (2, 2))
        self.assertEqual(offset_for(2, 2, 4, 4, "n"), (1, 0))
        self.assertEqual(offset_for(2, 2, 4, 4, "sw"), (0, 2))

    def test_shrink(self):
        self.assertEqual(offset_for(4, 4, 2, 2, "center"), (-1, -1))
        self.assertEqual(offset_for(4, 4, 2, 2, "nw"), (0, 0))
        self.assertEqual(offset_for(4, 4, 2, 2, "se"), (-2, -2))

    def test_all_anchors(self):
        for code in ANCHOR_CODES:
            dx, dy = offset_for(3, 3, 5, 5, code)
            self.assertTrue(-2 <= dx <= 2 and -2 <= dy <= 2)


class ResizeCanvasTests(unittest.TestCase):
    def test_pad_center(self):
        bitmap = Bitmap(2, 2)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(1, 1, BLUE)
        out = resize_canvas(bitmap, 4, 4, "center")
        self.assertEqual((out.width, out.height), (4, 4))
        self.assertEqual(out.rgba(1, 1), RED)
        self.assertEqual(out.rgba(2, 2), BLUE)
        self.assertEqual(out.rgba(0, 0), (0, 0, 0, 0))

    def test_pad_with_fill(self):
        out = resize_canvas(Bitmap(1, 1), 2, 2, "nw", fill=(255, 255, 255, 255))
        self.assertEqual(out.rgba(0, 0), (0, 0, 0, 0))
        self.assertEqual(out.rgba(1, 1), (255, 255, 255, 255))

    def test_crop_anchors(self):
        bitmap = Bitmap(4, 4)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(3, 3, BLUE)
        self.assertEqual(resize_canvas(bitmap, 2, 2, "se").rgba(1, 1), BLUE)
        self.assertEqual(resize_canvas(bitmap, 2, 2, "nw").rgba(0, 0), RED)

    def test_bad_size(self):
        with self.assertRaises(HexError):
            resize_canvas(Bitmap(2, 2), 0, 2)

    def test_grow_and_shrink_roundtrip(self):
        bitmap = Bitmap(2, 2)
        bitmap.set_rgba(0, 0, RED)
        padded = resize_canvas(bitmap, 6, 6, "center")
        back = resize_canvas(padded, 2, 2, "center")
        self.assertEqual(back.rgba(0, 0), RED)


class ScaleTests(unittest.TestCase):
    def test_nearest_upscale(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(1, 0, BLUE)
        out = scale_image(bitmap, 4, 2)
        self.assertEqual([out.rgba(x, 0)[:3] for x in range(4)],
                         [RED[:3], RED[:3], BLUE[:3], BLUE[:3]])
        self.assertEqual(out.rgba(0, 1)[:3], RED[:3])

    def test_nearest_downscale(self):
        bitmap = Bitmap(4, 1)
        for x, color in enumerate((RED, RED, BLUE, BLUE)):
            bitmap.set_rgba(x, 0, color)
        out = scale_image(bitmap, 2, 1)
        self.assertEqual([out.rgba(x, 0)[:3] for x in range(2)],
                         [RED[:3], BLUE[:3]])

    def test_bilinear_blends(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, (0, 0, 0, 255))
        bitmap.set_rgba(1, 0, (255, 255, 255, 255))
        out = scale_image(bitmap, 3, 1, "bilinear")
        middle = out.rgba(1, 0)
        self.assertTrue(100 <= middle[0] <= 155, middle)
        self.assertEqual(out.rgba(0, 0)[:3], (0, 0, 0))

    def test_bilinear_keeps_transparency_clean(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(1, 0, RED)
        out = scale_image(bitmap, 4, 1, "bilinear")
        self.assertEqual(out.rgba(0, 0), (0, 0, 0, 0))


class ModeTests(unittest.TestCase):
    def test_grayscale(self):
        bitmap = Bitmap(1, 1)
        bitmap.set_rgba(0, 0, RED)
        self.assertEqual(convert_mode(bitmap, "Grayscale"), 1)
        self.assertEqual(bitmap.rgba(0, 0)[:3], (54, 54, 54))
        self.assertEqual(convert_mode(bitmap, "RGB"), 0)
        self.assertEqual(bitmap.rgba(0, 0)[:3], (54, 54, 54))

    def test_flatten_alpha(self):
        bitmap = Bitmap(1, 1)
        bitmap.set_rgba(0, 0, (10, 20, 30, 128))
        flatten_alpha(bitmap, (255, 255, 255))
        self.assertEqual(bitmap.rgba(0, 0), (132, 137, 142, 255))
        bitmap.set_rgba(0, 0, (10, 20, 30, 0))
        flatten_alpha(bitmap, (0, 0, 0))
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 255))


class WorkingLimitTests(unittest.TestCase):
    def test_working_layout_keeps_packing_and_order(self):
        document = Document(1, 1)
        document.set_working_layout({"raw_channels": (5, 6, 5, 0),
                                     "raw_packing": "aligned",
                                     "raw_order": "BGR"}, quantize=False)
        self.assertEqual(document.working_widths, (5, 6, 5, 0))
        self.assertEqual(document.working_layout(),
                         {"raw_channels": (5, 6, 5, 0),
                          "raw_packing": "aligned",
                          "raw_order": "BGR"})

    def test_set_working_widths_reduces_existing_pixels(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (0x12, 0x34, 0x56, 0x78))
        document.set_working_widths((4, 4, 4, 0))
        self.assertEqual(document.bitmap.rgba(0, 0),
                         (0x10, 0x30, 0x50, 0x78))

    def test_commit_limits_direct_writes(self):
        document = Document(2, 1)
        document.set_working_widths((4, 4, 4, 0), quantize=False)
        before = document.begin()
        paint_brush(document.bitmap, None, 0, 0, 1,
                    (0xAB, 0xCD, 0xEF, 0x40))
        document.commit(before)
        self.assertEqual(document.bitmap.rgba(0, 0),
                         (0xA0, 0xC0, 0xE0, 0x40))

    def test_region_limit_leaves_other_pixels_alone(self):
        document = Document(3, 1)
        document.set_working_widths((4, 4, 4, 0), quantize=False)
        document.bitmap.set_rgba(0, 0, (0x12, 0x34, 0x56, 0x78))
        document.bitmap.set_rgba(2, 0, (0x12, 0x34, 0x56, 0x78))
        document.limit_pixels((0, 0, 1, 1))
        self.assertEqual(document.bitmap.rgba(0, 0),
                         (0x10, 0x30, 0x50, 0x78))
        self.assertEqual(document.bitmap.rgba(2, 0),
                         (0x12, 0x34, 0x56, 0x78))

    def test_alpha_channel_is_limited_only_when_it_has_bits(self):
        document = Document(1, 1)
        document.set_working_widths((4, 4, 4, 4), quantize=False)
        before = document.begin()
        document.bitmap.set_rgba(0, 0, (0x12, 0x34, 0x56, 0x7F))
        document.commit(before)
        self.assertEqual(document.bitmap.rgba(0, 0),
                         (0x10, 0x30, 0x50, 0x70))

    def test_undo_reapplies_the_limit(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (0x12, 0x34, 0x56, 0x78))
        document.set_working_widths((4, 4, 4, 0), quantize=False)
        before = document.begin()
        document.bitmap.set_rgba(0, 0, (0xFF, 0xFF, 0xFF, 0xFF))
        document.commit(before)
        self.assertTrue(document.undo_last())
        self.assertEqual(document.bitmap.rgba(0, 0),
                         (0x10, 0x30, 0x50, 0x78))


class SpecApplyTests(unittest.TestCase):
    def test_canvas_resize_keeps_pixels_and_metadata(self):
        document = Document(4, 4)
        document.bitmap.fill(RED)
        apply_spec_to_document(document, CanvasSpec(8, 8, 300, "RGB", True,
                                                    anchor="nw"))
        self.assertEqual((document.width, document.height), (8, 8))
        self.assertEqual(document.bitmap.rgba(3, 3), RED)
        self.assertEqual(document.bitmap.rgba(7, 7), (0, 0, 0, 0))
        self.assertEqual(document.ppi, 300.0)

    def test_scale_mode_resamples(self):
        document = Document(2, 2)
        document.bitmap.fill(RED)
        apply_spec_to_document(document, CanvasSpec(4, 4, resize_mode="scale"))
        self.assertEqual((document.width, document.height), (4, 4))
        self.assertEqual(document.bitmap.rgba(3, 3), RED)

    def test_alpha_off_flattens_over_matte(self):
        document = Document(2, 2)
        document.bitmap.set_rgba(0, 0, (0, 0, 0, 0))
        apply_spec_to_document(document, CanvasSpec(2, 2, alpha=False),
                               matte=(255, 255, 255))
        self.assertEqual(document.bitmap.rgba(0, 0), (255, 255, 255, 255))
        self.assertFalse(document.alpha)

    def test_gray_mode(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, BLUE)
        apply_spec_to_document(document, CanvasSpec(1, 1, color_mode="Grayscale"))
        self.assertEqual(document.color_mode, "Grayscale")
        self.assertEqual(document.bitmap.rgba(0, 0)[:3], (18, 18, 18))

    def test_no_change_makes_no_undo_entry(self):
        document = Document(4, 4)
        apply_spec_to_document(document, CanvasSpec(4, 4))
        self.assertFalse(document.undo.can_undo())


class DocumentStateTests(unittest.TestCase):
    def test_undo_restores_size_and_metadata(self):
        document = Document(2, 2)
        document.bitmap.fill(RED)
        before = document.begin()
        document.bitmap = resize_canvas(document.bitmap, 4, 4, "nw")
        document.ppi = 300.0
        document.color_mode = "Grayscale"
        self.assertTrue(document.commit(before))
        document.undo_last()
        self.assertEqual((document.width, document.height), (2, 2))
        self.assertEqual(document.ppi, 72.0)
        self.assertEqual(document.color_mode, "RGB")
        self.assertEqual(document.bitmap.rgba(0, 0), RED)
        document.redo_last()
        self.assertEqual((document.width, document.height), (4, 4))
        self.assertEqual(document.ppi, 300.0)
        self.assertEqual(document.color_mode, "Grayscale")

    def test_state_is_independent_copy(self):
        document = Document(2, 2)
        state = document.state()
        document.bitmap.fill(RED)
        self.assertEqual(state["data"], bytes(8 * 2))
        self.assertEqual(document.bitmap.rgba(0, 0), RED)


if __name__ == "__main__":
    unittest.main()
