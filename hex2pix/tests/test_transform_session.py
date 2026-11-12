"""Regression tests for keeping one transform session across node drags."""

import math
import unittest

from hex2pix import app as app_module
from hex2pix.app import hex2pixApp
from hex2pix.model import Bitmap, Document, quad_from_box, selection_rect_mask


RED = (255, 0, 0, 255)


class _ValueVar(object):
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _TransformHarness(hex2pixApp):
    """The transform methods without building a Tk window."""

    def __init__(self, document):
        self.doc = document
        self.transform_before = None
        self.transform_base = None
        self.transform_piece = None
        self.transform_mask = None
        self.transform_box = None
        self.transform_origin_box = None
        self.transform_grab = None
        self.transform_fixed = None
        self.transform_mode = "move"
        self.transform_angle = 0.0
        self.transform_rotation = None
        self.transform_start_quad = None
        self.transform_origin_quad = None
        self.transform_quad = None
        self.transform_corner = None
        self.transform_edge = None
        self.transform_handle_kind = None
        self.transform_handle_index = None
        self.transform_perspective = False
        self.transform_smooth = False
        self.transform_algorithm = "Nearest"
        self.rotate_snap = 15
        self._hex_incremental_ok = True

    def report_error(self, message):
        raise AssertionError(message)

    def refresh_status(self):
        pass

    def refresh_bytes(self):
        pass

    def request_redraw(self):
        pass


class TransformSessionTests(unittest.TestCase):
    def test_box_refits_the_selection_when_no_session_is_live(self):
        document = Document(8, 8)
        document.selection.apply_mask(
            selection_rect_mask(8, 8, 2, 3, 5, 6), "replace")
        app = _TransformHarness(document)
        app.transform_quad = [(0.0, 0.0), (8.0, 0.0),
                              (8.0, 8.0), (0.0, 8.0)]

        self.assertEqual(app.transform_display_quad(),
                         quad_from_box((2, 3, 4, 4)))

        app.transform_base = document.bitmap.copy()
        self.assertEqual(app.transform_display_quad(), app.transform_quad)

    def test_auto_selection_fits_the_artwork_not_the_canvas(self):
        document = Document(10, 10)
        document.bitmap.set_rgba(3, 2, RED)
        document.bitmap.set_rgba(6, 5, RED)
        app = _TransformHarness(document)

        hex2pixApp.begin_transform(app, 3, 2, "move")

        self.assertEqual(document.selection.bounds(), (3, 2, 4, 4))
        self.assertEqual(app.transform_origin_box, (3, 2, 4, 4))
        self.assertEqual(app.transform_quad, quad_from_box((3, 2, 4, 4)))

    def test_a_drag_near_a_quarter_turn_lands_on_it_exactly(self):
        document = Document(8, 8)
        for x in range(1, 4):
            document.bitmap.set_rgba(x, 1, RED)
        document.selection.apply_mask(
            selection_rect_mask(8, 8, 1, 1, 2, 1), "replace")
        app = _TransformHarness(document)

        # Grab the rotate handle due right of the box, then aim one degree
        # past a quarter turn: the drag should still end exactly on 90.
        hex2pixApp.begin_transform(app, 9.5, 1.5, "rotate")
        target = (1.5 + 10.0 * math.cos(math.radians(91.0)),
                  1.5 + 10.0 * math.sin(math.radians(91.0)))
        app.apply_transform(target[0], target[1], "rotate")

        self.assertEqual(app.transform_angle, 90.0)
        # A 2x1 box quarter-turned about (2, 1.5) lands on half pixels and
        # is shifted onto the grid, so both pixels survive exactly.
        self.assertEqual(app.transform_quad,
                         [(3.0, 1.0), (3.0, 3.0), (2.0, 3.0), (2.0, 1.0)])

    def test_rotsprite_options_are_applied_and_clamped(self):
        app = _TransformHarness(Document(8, 8))
        app.rotsprite_scale_var = _ValueVar("4x")
        app.rotsprite_similar_var = _ValueVar(400)

        hex2pixApp.apply_rotsprite_scale(app)
        hex2pixApp.apply_rotsprite_similar(app)

        self.assertEqual(app.rotsprite_scale, 4)
        self.assertEqual(app.rotsprite_similar, 255)

    def test_auto_upscale_sends_no_factor(self):
        app = _TransformHarness(Document(8, 8))
        app.rotsprite_scale_var = _ValueVar("Auto")

        hex2pixApp.apply_rotsprite_scale(app)

        self.assertEqual(app.rotsprite_scale, 0)

    def test_the_algorithm_menu_sets_the_sampler(self):
        app = _TransformHarness(Document(8, 8))
        app.transform_algorithm_var = _ValueVar("CleanEdge")

        hex2pixApp.apply_transform_algorithm(app)

        self.assertEqual(app.transform_algorithm, "CleanEdge")
        self.assertFalse(app.transform_smooth)

    def test_smooth_toggle_is_separate_from_the_algorithm(self):
        app = _TransformHarness(Document(8, 8))
        app.transform_algorithm = "RotSprite"
        app.transform_smooth_var = _ValueVar(True)

        hex2pixApp.apply_transform_smooth(app)

        self.assertTrue(app.transform_smooth)
        self.assertEqual(app.transform_algorithm, "RotSprite")

    def test_smooth_overrides_the_algorithm_mode(self):
        document = Document(8, 8)
        for y in range(2, 6):
            for x in range(2, 6):
                document.bitmap.set_rgba(x, y, RED)
        document.selection.apply_mask(
            selection_rect_mask(8, 8, 2, 2, 4, 4), "replace")
        app = _TransformHarness(document)
        app.transform_algorithm = "RotSprite"
        app.transform_smooth = True
        seen = []
        original = app_module.warp_piece

        def fake(piece, quad, width, height, smooth=False, mode=None,
                 **kwargs):
            seen.append((smooth, mode))
            return 0, 0, Bitmap(1, 1)

        app_module.warp_piece = fake
        try:
            hex2pixApp.begin_transform(app, 4, 4, "move")
            hex2pixApp.apply_transform(app, 5, 5, "move")
        finally:
            app_module.warp_piece = original

        self.assertEqual(seen, [(True, "Smooth")])

    def test_second_drag_keeps_source_pixels_and_current_nodes(self):
        document = Document(8, 8)
        for y in range(1, 3):
            for x in range(1, 3):
                document.bitmap.set_rgba(x, y, RED)
        document.selection.apply_mask(
            selection_rect_mask(8, 8, 1, 1, 2, 2), "replace")
        app = _TransformHarness(document)

        hex2pixApp.begin_transform(app, 1, 1, "move")
        source = app.transform_piece
        base = app.transform_base
        origin_quad = list(app.transform_origin_quad)
        hex2pixApp.apply_transform(app, 3, 3, "move")
        moved_quad = list(app.transform_quad)
        hex2pixApp.finish_transform_drag(app)

        self.assertIs(app.transform_piece, source)
        self.assertIs(app.transform_base, base)
        self.assertEqual(app.transform_quad, moved_quad)
        self.assertEqual(app.transform_start_quad, moved_quad)

        hex2pixApp.begin_transform(app, 3, 3, "move")
        self.assertIs(app.transform_piece, source)
        self.assertIs(app.transform_base, base)
        self.assertEqual(app.transform_start_quad, moved_quad)
        self.assertEqual(app.transform_origin_quad, origin_quad)

        hex2pixApp.apply_transform(app, 4, 4, "move")
        self.assertEqual(app.transform_quad, [
            (4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)])
        self.assertEqual(document.bitmap.rgba(4, 4), RED)

        hex2pixApp.finish_transform(app)
        self.assertTrue(document.undo_last())
        self.assertEqual(document.bitmap.rgba(1, 1), RED)
        self.assertEqual(document.bitmap.rgba(3, 3), (0, 0, 0, 0))

    def test_session_survives_moving_the_piece_fully_off_canvas(self):
        document = Document(8, 8)
        document.bitmap.fill(RED)
        document.selection.select_all()
        app = _TransformHarness(document)

        hex2pixApp.begin_transform(app, 4, 4, "move")
        hex2pixApp.apply_transform(app, -20, -20, "move")
        self.assertFalse(document.selection.active)
        hex2pixApp.finish_transform_drag(app)

        self.assertIsNotNone(app.transform_piece)
        self.assertIsNotNone(app.transform_quad)
        hex2pixApp.begin_transform(app, 4, 4, "rotate")
        self.assertIsNotNone(app.transform_piece)

    def test_drag_can_move_the_nodes_past_the_canvas_edge(self):
        document = Document(8, 8)
        document.bitmap.fill(RED)
        document.selection.select_all()
        app = _TransformHarness(document)

        hex2pixApp.begin_transform(app, 4, 4, "move")
        hex2pixApp.apply_transform(app, -3, -2, "move")

        self.assertEqual(app.transform_quad, [
            (-7.0, -6.0), (1.0, -6.0), (1.0, 2.0), (-7.0, 2.0)])

    def test_rotating_a_thin_selection_by_90_keeps_both_pixels(self):
        document = Document(12, 8)
        document.bitmap.set_rgba(5, 4, RED)
        document.bitmap.set_rgba(6, 4, (0, 255, 0, 255))
        document.selection.apply_mask(
            selection_rect_mask(12, 8, 5, 4, 6, 4), "replace")
        app = _TransformHarness(document)

        hex2pixApp.begin_transform(app, 8, 4, "rotate")
        # A quarter turn clockwise from the grab to the right of the centre.
        hex2pixApp.apply_transform(app, 6, 25, "rotate")

        # The two source pixels become a vertical pair; neither may be
        # duplicated or dropped just because its centre lands on an edge.
        pixels = [(x, y, document.bitmap.rgba(x, y))
                  for y in range(8) for x in range(12)
                  if document.bitmap.rgba(x, y)[3]]
        self.assertEqual(len(pixels), 2)
        pixels.sort(key=lambda item: item[1])
        self.assertEqual([item[2] for item in pixels],
                         [RED, (0, 255, 0, 255)])

    def test_smooth_toggle_reaches_the_sampler(self):
        document = Document(8, 8)
        document.bitmap.fill(RED)
        document.selection.select_all()
        app = _TransformHarness(document)
        app.transform_smooth = True
        app.transform_smooth_var = _ValueVar(True)
        seen = []
        original = app_module.warp_piece

        def fake(piece, quad, width, height, smooth=True, mode=None,
                 **kwargs):
            seen.append(smooth)
            return 0, 0, Bitmap(1, 1)

        app_module.warp_piece = fake
        try:
            hex2pixApp.begin_transform(app, 4, 4, "move")
            hex2pixApp.apply_transform(app, 5, 5, "move")
            app.transform_smooth_var.value = False
            hex2pixApp.apply_transform_smooth(app)
        finally:
            app_module.warp_piece = original

        self.assertEqual(seen, [True, False])
        self.assertFalse(app.transform_smooth)


if __name__ == "__main__":
    unittest.main()