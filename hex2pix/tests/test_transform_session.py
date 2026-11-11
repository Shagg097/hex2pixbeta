"""Regression tests for keeping one transform session across node drags."""

import unittest

from hex2pix.app import hex2pixApp
from hex2pix.model import Document, selection_rect_mask


RED = (255, 0, 0, 255)


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


if __name__ == "__main__":
    unittest.main()