"""Unit tests for the pure hex2pix model."""

import unittest

from hex2pix import model
from hex2pix.shapes import paint_shape
from hex2pix.model import (
    Bitmap, Document, HexError, Selection, apply_hue, apply_map, apply_op,
    apply_ramp, blit, block_from_bitmap, clean_path, clear_region, combine,
    flip_horizontal,
    flip_vertical, flood_fill, format_dump, hex_code, hex_str, hue_shift,
    invert_colors,
    paint_brush, paint_line, parse_block, parse_hex, parse_hex_map, pick_related,
    RotationDrag, extract_selection, piece_mask, place_block, rotate90,
    rotate_piece, rotate_ramp, scale_box,
    box_from_quad, quad_centre, quad_from_box, quad_handles,
    quad_move_corner, quad_rotate, quad_scale, quad_shear, quad_translate,
    warp_piece,
    selected_bytes, selection_ellipse_mask, selection_polygon_mask,
    selection_rect_mask, stamp_bitmap, transform_block, unique_colors,
    fill_holes,
)

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)


class HexColorTests(unittest.TestCase):
    def test_parse_formats(self):
        self.assertEqual(parse_hex("#f00"), (255, 0, 0, 255))
        self.assertEqual(parse_hex("0x00FF80"), (0, 255, 128, 255))
        self.assertEqual(parse_hex("#11223344"), (0x11, 0x22, 0x33, 0x44))
        self.assertEqual(parse_hex("abcd"), (0xAA, 0xBB, 0xCC, 0xDD))
        self.assertEqual(parse_hex(" #ff ff ff "), (255, 255, 255, 255))

    def test_parse_errors(self):
        for bad in ("", "#", "gg", "12345", "1234567"):
            with self.assertRaises(HexError):
                parse_hex(bad)

    def test_hex_str(self):
        self.assertEqual(hex_str(255, 0, 0), "FF0000")
        self.assertEqual(hex_str(255, 0, 0, 0), "FF000000")
        self.assertEqual(hex_str(1, 2, 3, 254), "010203FE")
        self.assertEqual(hex_code((1, 2, 3, 255), "BGR"), "030201")
        self.assertEqual(hex_code((1, 2, 3, 4), "BGR"), "03020104")
        self.assertEqual(hex_code((1, 2, 3, 255), "RGX"), "010203")

    def test_combine_reference_values(self):
        self.assertEqual(combine(250, 20, "add", 255, True), 14)
        self.assertEqual(combine(10, 20, "subtract", 255, True), 246)
        self.assertEqual(combine(0xF0, 0x0F, "nand", 255, False), 255)
        self.assertEqual(combine(100, 100, "add", 128, False), 150)
        self.assertEqual(combine(200, 200, "add", 255, False), 255)
        self.assertEqual(combine(255, 255, "multiply", 255, False), 255)
        self.assertEqual(combine(255, 128, "multiply", 255, False), 128)

    def test_hue_shift_roundtrip(self):
        r, g, b = hue_shift(255, 0, 0, 120)
        self.assertEqual((r, g, b), (0, 255, 0))
        self.assertEqual(hue_shift(255, 0, 0, 360), (255, 0, 0))

    def test_hex_map(self):
        mapping = parse_hex_map("FF0000=00FF00\n#0000FF -> #FFFFFF")
        self.assertEqual(mapping[(255, 0, 0)], (0, 255, 0))
        self.assertEqual(mapping[(0, 0, 255)], (255, 255, 255))
        with self.assertRaises(HexError):
            parse_hex_map("FF0000")

    def test_ramp(self):
        colors = [(0, 0, 0), (128, 128, 128), (255, 255, 255)]
        ramp = rotate_ramp(colors, 1)
        self.assertEqual(ramp[(0, 0, 0)], (128, 128, 128))
        self.assertEqual(ramp[(255, 255, 255)], (0, 0, 0))


class LayerReorderTests(unittest.TestCase):
    def test_reorder_active_layer(self):
        doc = Document(4, 4)
        doc.layers[0].name = "base"
        doc.add_layer("middle")
        doc.add_layer("top")
        self.assertEqual(doc.layer_index, 2)
        self.assertTrue(doc.reorder_layer(0))
        self.assertEqual([l.name for l in doc.layers],
                         ["top", "base", "middle"])
        self.assertEqual(doc.layer_index, 0)
        self.assertFalse(doc.reorder_layer(0))
        self.assertFalse(doc.reorder_layer(5))
        self.assertFalse(doc.reorder_layer(-1))
        self.assertTrue(doc.reorder_layer(2))
        self.assertEqual([l.name for l in doc.layers],
                         ["base", "middle", "top"])
        self.assertEqual(doc.layer_index, 2)

    def test_reorder_keeps_another_layer_active(self):
        doc = Document(4, 4)
        doc.layers[0].name = "base"
        doc.add_layer("middle")
        doc.add_layer("top")
        self.assertTrue(doc.reorder_layer(2, index=0))
        self.assertEqual([l.name for l in doc.layers],
                         ["middle", "top", "base"])
        # "top" is still the active layer, now one slot lower.
        self.assertEqual(doc.layer_index, 1)


class LayerGroupMoveTests(unittest.TestCase):
    def _stack(self):
        doc = Document(4, 4)
        doc.layers[0].name = "base"
        doc.add_layer("middle")
        doc.add_layer("top")
        return doc

    def test_a_layer_can_be_dragged_into_a_group(self):
        doc = self._stack()
        doc.group_layer(index=0, name="Art")
        self.assertTrue(doc.move_layer_to(1, index=2, group="Art"))
        self.assertEqual([layer.name for layer in doc.layers],
                         ["base", "top", "middle"])
        self.assertEqual(doc.layers[1].group, "Art")
        self.assertEqual([layer.name for layer in
                          (doc.layers[index]
                           for index in doc.group_members("Art"))],
                         ["base", "top"])
        self.assertEqual(doc.layer_index, 1)

    def test_dropping_outside_a_group_leaves_it(self):
        doc = self._stack()
        doc.group_layer(index=0, name="Art")
        self.assertTrue(doc.move_layer_to(0, index=1, group=""))
        self.assertEqual(doc.layers[0].group, "")
        self.assertEqual([doc.layers[index].name
                          for index in doc.group_members("Art")],
                         ["base"])

    def test_an_empty_group_is_forgotten(self):
        doc = self._stack()
        doc.group_layer(index=1, name="Art")
        self.assertTrue(doc.move_layer_to(1, index=1, group=""))
        self.assertEqual(doc.groups, [])

    def test_moves_that_change_nothing_report_false(self):
        doc = self._stack()
        self.assertFalse(doc.move_layer_to(1, index=1))
        doc.group_layer(index=1, name="Art")
        self.assertFalse(doc.move_layer_to(1, index=1, group="Art"))
        self.assertFalse(doc.move_layer_to(9, index=1))


class BlockTests(unittest.TestCase):
    def test_parse_basic(self):
        block = parse_block("FF0000 00FF00\n0000FF")
        self.assertEqual((block.width, block.height), (2, 2))
        self.assertEqual(block.rows[0][0], RED)
        self.assertEqual(block.rows[1][0], BLUE)
        self.assertIs(block.rows[1][1], model.SKIP)

    def test_separators_comments_skips(self):
        block = parse_block("FF0000,00FF00 | 0000FF . // note")
        self.assertEqual((block.width, block.height), (2, 2))
        self.assertIs(block.rows[1][1], model.SKIP)
        rows = parse_block("FF0000;00FF00")
        self.assertEqual((rows.width, rows.height), (1, 2))

    def test_long_runs(self):
        block = parse_block("FF000000FF00")
        self.assertEqual([p[:3] for p in block.rows[0]], [RED[:3], GREEN[:3]])
        with self.assertRaises(HexError):
            parse_block("FF00000")  # 7 digits

    def test_transform_rotate(self):
        block = parse_block("FF0000 00FF00")
        turned = transform_block(block, rotate=90)
        self.assertEqual((turned.width, turned.height), (1, 2))
        self.assertEqual(turned.rows[0][0], RED)
        self.assertEqual(turned.rows[1][0], GREEN)
        back = transform_block(transform_block(block, rotate=90), rotate=270)
        self.assertEqual((back.width, back.height), (2, 1))
        self.assertEqual(back.rows[0][:2], [RED, GREEN])

    def test_transform_scale_flip(self):
        block = parse_block("FF0000 00FF00")
        wide = transform_block(block, scale=2, flip_x=True)
        self.assertEqual((wide.width, wide.height), (4, 2))
        self.assertEqual(wide.rows[0][:2], [GREEN, GREEN])
        flipped_y = transform_block(block, rotate=90, flip_y=True)
        self.assertEqual(flipped_y.rows[0][0], GREEN)

    def test_blit_modes(self):
        bitmap = Bitmap(4, 1)
        block = parse_block("FF0000 00FF00")
        blit(bitmap.data, (0, 0, 4, 1), block, "stamp", (0, 0))
        self.assertEqual(bitmap.rgba(1, 0), GREEN)
        self.assertEqual(bitmap.rgba(2, 0), (0, 0, 0, 0))

        bitmap = Bitmap(4, 1)
        blit(bitmap.data, (0, 0, 4, 1), block, "tile", (0, 0), mirror=True)
        self.assertEqual([bitmap.rgba(x, 0)[:3] for x in range(4)],
                         [RED[:3], GREEN[:3], GREEN[:3], RED[:3]])

        bitmap = Bitmap(4, 1)
        blit(bitmap.data, (0, 0, 4, 1), block, "stretch", (0, 0))
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(2, 0), GREEN)

    def test_blit_selection(self):
        bitmap = Bitmap(2, 1)
        block = parse_block("FF0000 00FF00")
        mask = bytearray(b"\xff\x00")
        blit(bitmap.data, (0, 0, 2, 1), block, "stamp", (0, 0), False, mask)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(1, 0), (0, 0, 0, 0))

    def test_place_block_and_render(self):
        bitmap = Bitmap(2, 1)
        block = parse_block("FF0000 .")
        place_block(bitmap, None, block)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(1, 0), (0, 0, 0, 0))
        self.assertEqual(block_from_bitmap(bitmap), "FF0000 .")


class SelectionTests(unittest.TestCase):
    def test_rect_mask(self):
        mask = selection_rect_mask(10, 10, 2, 3, 4, 5)
        self.assertEqual(sum(1 for v in mask if v), 9)
        self.assertEqual(mask[3 * 10 + 2], 255)
        self.assertEqual(mask[3 * 10 + 1], 0)
        self.assertEqual(sum(1 for v in mask if v), 9)

    def test_rect_clipped(self):
        mask = selection_rect_mask(4, 4, -5, -5, 1, 1)
        self.assertEqual(sum(1 for v in mask if v), 4)

    def test_rect_outside_selects_nothing(self):
        mask = selection_rect_mask(4, 4, -9, -9, -3, -3)
        self.assertEqual(sum(1 for v in mask if v), 0)

    def test_ellipse_keeps_its_shape_past_the_edge(self):
        inside = selection_ellipse_mask(10, 10, 0, 0, 9, 9)
        past = selection_ellipse_mask(10, 10, -5, -5, 9, 9)
        self.assertLess(sum(1 for v in inside[0:10] if v), 10)
        self.assertEqual(sum(1 for v in past[0:10] if v), 10)

    def test_ellipse_outside_selects_nothing(self):
        mask = selection_ellipse_mask(4, 4, -9, -9, -3, -3)
        self.assertEqual(sum(1 for v in mask if v), 0)

    def test_ellipse_area(self):
        mask = selection_ellipse_mask(10, 10, 0, 0, 9, 9)
        area = sum(1 for v in mask if v)
        self.assertTrue(70 <= area <= 85, area)
        mask = selection_ellipse_mask(9, 9, 0, 0, 8, 8)
        self.assertTrue(50 <= sum(1 for v in mask if v) <= 70)

    def test_polygon_triangle(self):
        mask = selection_polygon_mask(10, 10, [(0, 0), (9, 0), (0, 9)])
        area = sum(1 for v in mask if v)
        self.assertTrue(40 <= area <= 60, area)
        self.assertEqual(mask[0], 255)
        self.assertEqual(mask[9 * 10 + 9], 0)

    def test_polygon_line(self):
        mask = selection_polygon_mask(5, 5, [(0, 0), (4, 4)])
        self.assertEqual(sum(1 for v in mask if v), 5)

    def test_pick_related_contiguous_and_global(self):
        bitmap = Bitmap(4, 1)
        for x, color in enumerate((RED, RED, BLUE, RED)):
            bitmap.set_rgba(x, 0, color)
        wand = pick_related(bitmap, 0, 0)
        self.assertEqual(sum(1 for v in wand if v), 2)
        similar = pick_related(bitmap, 0, 0, contiguous=False)
        self.assertEqual(sum(1 for v in similar if v), 3)

    def test_pick_related_tolerance(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, (100, 100, 100, 255))
        bitmap.set_rgba(1, 0, (110, 100, 100, 255))
        tight = pick_related(bitmap, 0, 0, tolerance=0)
        self.assertEqual(sum(1 for v in tight if v), 1)
        loose = pick_related(bitmap, 0, 0, tolerance=10)
        self.assertEqual(sum(1 for v in loose if v), 2)

    def test_selection_object(self):
        bitmap = Bitmap(6, 6)
        selection = Selection(6, 6)
        self.assertIsNone(selection.bounds())
        selection.apply_mask(selection_rect_mask(6, 6, 1, 1, 2, 2))
        self.assertEqual(selection.bounds(), (1, 1, 2, 2))
        self.assertEqual(selection.bytes_count(), 16)
        self.assertIsNotNone(selection.mask_or_full())
        selection.invert()
        self.assertEqual(selection.count(), 32)
        selection.clear()
        self.assertIsNone(selection.mask_or_full())
        selection.select_all()
        self.assertEqual(selection.count(), 36)
        self.assertEqual(bitmap.width, 6)

    def test_combine_modes(self):
        selection = Selection(3, 1)
        selection.apply_mask(bytearray(b"\xff\x00\xff"))
        selection.apply_mask(bytearray(b"\x00\xff\xff"), "add")
        self.assertEqual(list(selection.mask), [255, 255, 255])
        selection.apply_mask(bytearray(b"\xff\x00\x00"), "subtract")
        self.assertEqual(list(selection.mask), [0, 255, 255])
        selection.apply_mask(bytearray(b"\x00\x00\xff"), "intersect")
        self.assertEqual(list(selection.mask), [0, 0, 255])


class EditTests(unittest.TestCase):
    def test_paint_brush_and_mask(self):
        bitmap = Bitmap(4, 4)
        paint_brush(bitmap, None, 0, 0, 1, RED)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        count = paint_brush(bitmap, None, 1, 1, 3, GREEN)
        self.assertEqual(count, 9)
        self.assertEqual(bitmap.rgba(0, 0)[:3], GREEN[:3])

        bitmap = Bitmap(4, 4)
        mask = selection_rect_mask(4, 4, 0, 0, 0, 0)
        self.assertEqual(paint_brush(bitmap, mask, 1, 1, 3, GREEN), 1)
        self.assertEqual(bitmap.rgba(0, 0), GREEN)
        self.assertEqual(bitmap.rgba(1, 1), (0, 0, 0, 0))

    def test_paint_line(self):
        bitmap = Bitmap(5, 5)
        paint_line(bitmap, None, 0, 0, 4, 4, 1, BLUE)
        for i in range(5):
            self.assertEqual(bitmap.rgba(i, i), BLUE)

    def test_flood_fill(self):
        bitmap = Bitmap(4, 1)
        bitmap.set_rgba(1, 0, BLUE)
        bitmap.set_rgba(2, 0, BLUE)
        filled = flood_fill(bitmap, None, 1, 0, GREEN)
        self.assertEqual(filled, 2)
        self.assertEqual(bitmap.rgba(1, 0), GREEN)
        self.assertEqual(bitmap.rgba(2, 0), GREEN)
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 0))
        self.assertEqual(flood_fill(bitmap, None, 1, 0, GREEN), 0)

    def test_flood_fill_global_and_selection(self):
        bitmap = Bitmap(5, 1)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(2, 0, RED)
        bitmap.set_rgba(4, 0, RED)
        self.assertEqual(flood_fill(bitmap, None, 0, 0, GREEN, contiguous=False), 3)
        bitmap = Bitmap(5, 1)
        mask = selection_rect_mask(5, 1, 0, 0, 1, 0)
        self.assertEqual(flood_fill(bitmap, mask, 0, 0, GREEN), 2)
        self.assertEqual(bitmap.rgba(2, 0), (0, 0, 0, 0))

    def test_apply_op(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(1, 0, RED)
        mask = bytearray(b"\xff\x00")
        apply_op(bitmap, mask, (0, 255, 0, 255), "xor")
        self.assertEqual(bitmap.rgba(0, 0), (255, 255, 0, 255))
        self.assertEqual(bitmap.rgba(1, 0), RED)

    def test_apply_op_alpha(self):
        bitmap = Bitmap(1, 1)
        bitmap.set_rgba(0, 0, (255, 0, 0, 128))
        apply_op(bitmap, None, (0, 0, 0, 255), "xor", include_alpha=True)
        self.assertEqual(bitmap.rgba(0, 0), (255, 0, 0, 127))

    def test_apply_hue_and_map(self):
        bitmap = Bitmap(1, 1)
        bitmap.set_rgba(0, 0, RED)
        apply_hue(bitmap, None, 120)
        self.assertEqual(bitmap.rgba(0, 0)[:3], (0, 255, 0))

        bitmap.set_rgba(0, 0, (5, 5, 5, 255))
        apply_map(bitmap, None, {(0, 255, 0): (1, 2, 3)}, tolerance=6)
        self.assertEqual(bitmap.rgba(0, 0)[:3], (5, 5, 5))
        apply_map(bitmap, None, {(5, 5, 5): (9, 9, 9)})
        self.assertEqual(bitmap.rgba(0, 0)[:3], (9, 9, 9))

    def test_ramp_rotation(self):
        bitmap = Bitmap(3, 1)
        bitmap.set_rgba(0, 0, (0, 0, 0, 255))
        bitmap.set_rgba(1, 0, (128, 128, 128, 255))
        bitmap.set_rgba(2, 0, (255, 255, 255, 255))
        self.assertEqual(unique_colors(bitmap), [(0, 0, 0), (128, 128, 128), (255, 255, 255)])
        apply_ramp(bitmap, None, 1)
        self.assertEqual(bitmap.rgba(0, 0)[:3], (128, 128, 128))
        self.assertEqual(bitmap.rgba(2, 0)[:3], (0, 0, 0))

    def test_clear_and_invert(self):
        bitmap = Bitmap(2, 1)
        bitmap.fill(RED)
        invert_colors(bitmap, None)
        self.assertEqual(bitmap.rgba(0, 0)[:3], (0, 255, 255))
        clear_region(bitmap, None)
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 0))

    def test_dump_and_bytes(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(1, 0, (1, 2, 3, 4))
        dump = format_dump(bitmap)
        self.assertTrue(dump.startswith("00000000  FF0000FF 01020304"), dump)
        mask = bytearray(b"\x00\xff")
        self.assertEqual(format_dump(bitmap, mask), "00000004  01020304")
        self.assertEqual(selected_bytes(bitmap, mask), b"\x01\x02\x03\x04")


class TransformTests(unittest.TestCase):
    def test_flips(self):
        bitmap = Bitmap(3, 2)
        for x in range(3):
            bitmap.set_rgba(x, 0, (x, 0, 0, 255))
        flipped = flip_horizontal(bitmap)
        self.assertEqual(flipped.rgba(0, 0)[0], 2)
        self.assertEqual(flipped.rgba(2, 0)[0], 0)
        vertical = flip_vertical(bitmap)
        self.assertEqual(vertical.rgba(2, 1)[0], 2)
        self.assertEqual(vertical.rgba(2, 0)[0], 0)

    def test_rotate90_twice(self):
        bitmap = Bitmap(2, 3)
        bitmap.set_rgba(0, 0, RED)
        turned = rotate90(bitmap)
        self.assertEqual((turned.width, turned.height), (3, 2))
        self.assertEqual(turned.rgba(2, 0), RED)
        back = turned
        for _ in range(3):
            back = rotate90(back)
        self.assertEqual((back.width, back.height), (2, 3))
        self.assertEqual(back.rgba(0, 0), RED)
        counter = rotate90(bitmap, clockwise=False)
        self.assertEqual(counter.rgba(0, 1), RED)


class UndoTests(unittest.TestCase):
    def test_undo_redo(self):
        document = Document(2, 2)
        before = document.begin()
        document.bitmap.fill(RED)
        self.assertTrue(document.commit(before))
        self.assertTrue(document.undo.can_undo())
        document.undo_last()
        self.assertEqual(document.bitmap.rgba(0, 0), (0, 0, 0, 0))
        document.redo_last()
        self.assertEqual(document.bitmap.rgba(0, 0), RED)

    def test_noop_commit(self):
        document = Document(2, 2)
        before = document.begin()
        self.assertFalse(document.commit(before))
        self.assertFalse(document.undo.can_undo())

    def test_budget_drops_oldest(self):
        document = Document(4, 4)
        stack = model.UndoStack(budget=64)
        for value in range(5):
            before = document.state()
            document.bitmap.fill((value, 0, 0, 255))
            stack.push(before, document.state())
        self.assertEqual(len(stack._undo), 1)
        self.assertTrue(stack.undo(document))
        self.assertFalse(stack.can_undo())


class LayerTests(unittest.TestCase):
    def stacked(self, op="none", gate="none", blend="none"):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (200, 100, 50, 255))
        document.add_layer(op=op, gate=gate, blend=blend)
        document.bitmap.set_rgba(0, 0, (100, 100, 100, 255))
        return document.flatten().rgba(0, 0)

    def test_normal_is_source_over(self):
        self.assertEqual(combine(10, 200, "normal", 255, False), 200)
        self.assertEqual(combine(100, 200, "normal", 128, False), 150)

    def test_divide_reference_values(self):
        self.assertEqual(combine(200, 100, "divide", 255, False), 255)
        self.assertEqual(combine(100, 200, "divide", 255, False), 127)
        self.assertEqual(combine(100, 0, "divide", 255, False), 255)

    def test_layer_operations(self):
        self.assertEqual(self.stacked(op="add"), (255, 200, 150, 255))
        self.assertEqual(self.stacked(op="subtract"), (100, 0, 0, 255))
        self.assertEqual(self.stacked(op="multiply"), (78, 39, 19, 255))
        self.assertEqual(self.stacked(op="divide"), (255, 255, 127, 255))
        for op in model.LAYER_OPS:
            self.assertIn(op, model.LAYER_OP_LABELS)

    def test_layer_gate_and_blend_operations(self):
        self.assertEqual(combine(200, 100, "xor"), 172)
        self.assertEqual(combine(200, 100, "nand"), 191)
        self.assertEqual(combine(10, 200, "screen"), 202)
        self.assertEqual(combine(100, 200, "overlay"), 157)
        self.assertEqual(combine(10, 200, "darken"), 10)
        self.assertEqual(combine(10, 200, "lighten"), 200)
        self.assertEqual(combine(10, 200, "difference"), 190)
        self.assertEqual(self.stacked(gate="nand"), (191, 155, 223, 255))
        self.assertEqual(self.stacked(gate="xor"), (172, 0, 86, 255))
        self.assertEqual(self.stacked(blend="screen"), (222, 161, 130, 255))
        self.assertEqual(self.stacked(blend="overlay"), (188, 78, 39, 255))
        self.assertEqual(self.stacked(blend="darken"), (100, 100, 50, 255))
        self.assertEqual(self.stacked(blend="lighten"), (200, 100, 100, 255))
        self.assertEqual(self.stacked(blend="difference"), (100, 0, 50, 255))
        for gate in model.LAYER_GATES:
            self.assertIn(gate, model.LAYER_GATE_LABELS)
        for blend in model.LAYER_BLENDS:
            self.assertIn(blend, model.LAYER_BLEND_LABELS)

    def test_every_gate_type_computes_its_truth_table(self):
        below, above = 0xF0, 0x0F
        self.assertEqual(model.gate_value(below, above, "and"), 0x00)
        self.assertEqual(model.gate_value(below, above, "or"), 0xFF)
        self.assertEqual(model.gate_value(below, above, "xor"), 0xFF)
        self.assertEqual(model.gate_value(below, above, "nand"), 0xFF)
        self.assertEqual(model.gate_value(below, above, "nor"), 0x00)
        self.assertEqual(model.gate_value(below, above, "xnor"), 0x00)
        self.assertEqual(model.gate_value(below, above, "not"), 0xF0)
        for gate in model.LAYER_GATES:
            if gate != "none":
                value = model.gate_value(below, above, gate)
                self.assertTrue(0 <= value <= 255, gate)

    def test_three_option_groups_add_up_on_one_layer(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (200, 100, 50, 255))
        document.add_layer(op="multiply", gate="nand", blend="screen")
        document.bitmap.set_rgba(0, 0, (100, 100, 100, 255))
        all_three = document.flatten().rgba(0, 0)[:3]
        expected = tuple(model.combine_layer(value, 100, "multiply", "nand",
                                            "screen")
                         for value in (200, 100, 50))
        self.assertEqual(all_three, expected)
        self.assertNotEqual(all_three, self.stacked(op="multiply")[:3])
        self.assertNotEqual(all_three, self.stacked(gate="nand")[:3])
        self.assertNotEqual(all_three, self.stacked(blend="screen")[:3])

    def test_layer_state_keeps_the_three_groups_apart(self):
        document = Document(1, 1)
        layer = document.active_layer
        layer.op = "divide"
        layer.gate = "nor"
        layer.blend = "screen"
        state = layer.to_state()
        self.assertEqual((state["op"], state["gate"], state["blend"]),
                         ("divide", "nor", "screen"))
        restored = model.Layer.from_state(state, 1, 1)
        self.assertEqual((restored.op, restored.gate, restored.blend),
                         ("divide", "nor", "screen"))
        copied = layer.copy()
        self.assertEqual((copied.op, copied.gate, copied.blend),
                         ("divide", "nor", "screen"))

    def test_older_projects_move_op_into_the_right_group(self):
        for old, op, gate, blend in (("normal", "none", "none", "none"),
                                     ("screen", "none", "none", "screen"),
                                     ("multiply", "multiply", "none",
                                      "none"),
                                     ("xor", "none", "xor", "none"),
                                     ("nand", "none", "nand", "none")):
            state = {"name": "Layer", "op": old, "data": b"\x00" * 4}
            layer = model.Layer.from_state(state, 1, 1)
            self.assertEqual((layer.op, layer.gate, layer.blend),
                             (op, gate, blend), old)
        # Two-group projects saved earlier: "op" was the gate.
        for old_gate, old_blend, gate, blend in (("nand", "normal", "nand",
                                                 "none"),
                                                ("none", "screen", "none",
                                                 "screen")):
            state = {"name": "Layer", "op": old_gate, "blend": old_blend,
                     "data": b"\x00" * 4}
            layer = model.Layer.from_state(state, 1, 1)
            self.assertEqual((layer.op, layer.gate, layer.blend),
                             ("none", gate, blend), old_gate)

    def test_layer_alpha_is_the_strength(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (100, 100, 100, 255))
        document.add_layer(op="add")
        document.bitmap.set_rgba(0, 0, (100, 100, 100, 128))
        self.assertEqual(document.flatten().rgba(0, 0)[:3], (150, 150, 150))

    def test_transparent_layer_pixel_leaves_the_stack_alone(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (10, 20, 30, 255))
        document.add_layer(op="multiply")
        self.assertEqual(document.flatten().rgba(0, 0), (10, 20, 30, 255))

    def test_hidden_layer_is_skipped(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (10, 20, 30, 255))
        document.add_layer()
        document.active_layer.visible = False
        document.bitmap.set_rgba(0, 0, (200, 200, 200, 255))
        self.assertEqual(document.flatten().rgba(0, 0), (10, 20, 30, 255))

    def test_layers_survive_undo(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, RED)
        before = document.begin()
        document.add_layer(op="multiply")
        document.bitmap.fill(BLUE)
        document.commit(before)
        self.assertEqual(document.layer_count, 2)
        document.undo_last()
        self.assertEqual(document.layer_count, 1)
        self.assertEqual(document.bitmap.rgba(0, 0), RED)
        self.assertEqual(document.active_layer.op, "none")

    def test_normal_is_the_default_and_covers(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (200, 100, 50, 255))
        self.assertEqual(model.LAYER_OP_LABELS["none"], "None")
        self.assertEqual(model.LAYER_GATE_LABELS["none"], "None")
        self.assertEqual(model.LAYER_BLEND_LABELS["none"], "None")
        self.assertEqual(document.active_layer.op, "none")
        document.add_layer()
        self.assertEqual(document.active_layer.op, "none")
        document.bitmap.set_rgba(0, 0, (10, 20, 30, 255))
        self.assertEqual(document.flatten().rgba(0, 0), (10, 20, 30, 255))

    def test_normal_partial_alpha_blends(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (100, 100, 100, 255))
        document.add_layer()
        document.bitmap.set_rgba(0, 0, (200, 200, 200, 128))
        self.assertEqual(document.flatten().rgba(0, 0), (150, 150, 150, 255))

    def test_visibility_is_per_layer(self):
        document = Document(1, 1)
        document.bitmap.fill(RED)
        document.add_layer()
        document.bitmap.fill(BLUE)
        document.add_layer()
        document.bitmap.fill(GREEN)
        document.layers[1].visible = False
        self.assertEqual(document.flatten().rgba(0, 0), GREEN)
        document.layers[2].visible = False
        self.assertEqual(document.flatten().rgba(0, 0), RED)
        self.assertTrue(document.layers[0].visible)

    def test_layer_opacity_scales_the_operation(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (100, 100, 100, 255))
        document.add_layer()
        document.bitmap.set_rgba(0, 0, (200, 200, 200, 255))
        document.active_layer.alpha = 128
        self.assertEqual(document.flatten().rgba(0, 0), (150, 150, 150, 255))
        document.active_layer.alpha = 0
        self.assertEqual(document.flatten().rgba(0, 0), (100, 100, 100, 255))

    def test_single_layer_opacity_shows_in_flatten(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (10, 20, 30, 255))
        document.active_layer.alpha = 128
        self.assertEqual(document.flatten().rgba(0, 0), (10, 20, 30, 128))

    def test_layer_opacity_survives_copy_and_undo(self):
        document = Document(1, 1)
        document.active_layer.alpha = 64
        self.assertEqual(document.active_layer.copy().alpha, 64)
        before = document.begin()
        document.add_layer()
        document.commit(before)
        document.undo_last()
        self.assertEqual(document.active_layer.alpha, 64)

    def test_layer_order_controls_the_operation(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (200, 100, 50, 255))
        document.add_layer(op="subtract")
        document.bitmap.set_rgba(0, 0, (100, 100, 100, 255))
        self.assertEqual(document.flatten().rgba(0, 0), (100, 0, 0, 255))
        document.move_layer(-1)
        self.assertEqual(document.active_layer.op, "subtract")
        self.assertEqual(document.flatten().rgba(0, 0), (200, 100, 50, 255))


class LayerGroupTests(unittest.TestCase):
    def document(self):
        document = Document(1, 1)
        document.bitmap.set_rgba(0, 0, (10, 20, 30, 255))
        document.layers[0].name = "base"
        document.add_layer("ink")
        document.bitmap.set_rgba(0, 0, (200, 100, 50, 255))
        return document

    def test_group_and_members(self):
        document = self.document()
        group = document.group_layer()
        self.assertEqual(group.name, "Group 1")
        self.assertEqual(document.active_layer.group, "Group 1")
        self.assertEqual(document.group_members("Group 1"), [1])
        document.group_layer(index=0, name="Group 1")
        self.assertEqual(document.group_members("Group 1"), [0, 1])
        document.group_layer(index=1, name="Ink")
        self.assertEqual([l.group for l in document.layers],
                         ["Group 1", "Ink"])
        self.assertEqual([g.name for g in document.groups],
                         ["Group 1", "Ink"])
        self.assertEqual(document.ensure_group("Group 1").name, "Group 1")

    def test_group_visibility_and_alpha(self):
        document = self.document()
        document.group_layer()
        group = document.group("Group 1")
        group.visible = False
        self.assertEqual(document.flatten().rgba(0, 0), (10, 20, 30, 255))
        group.visible = True
        group.alpha = 128
        self.assertEqual(document.flatten().rgba(0, 0), (105, 60, 40, 255))

    def test_rename_group_renames_members(self):
        document = self.document()
        document.group_layer()
        self.assertTrue(document.rename_group("Group 1", "Sky"))
        self.assertEqual(document.group("Sky").name, "Sky")
        self.assertEqual(document.active_layer.group, "Sky")
        document.group_layer(index=0, name="Base")
        self.assertFalse(document.rename_group("Sky", "Base"))
        self.assertFalse(document.rename_group("Sky", ""))
        self.assertFalse(document.rename_group("Nope", "Mist"))

    def test_rename_layer(self):
        document = self.document()
        self.assertTrue(document.rename_layer(1, "Shade"))
        self.assertEqual(document.layers[1].name, "Shade")
        self.assertFalse(document.rename_layer(1, "  "))

    def test_remove_group_keeps_layers(self):
        document = self.document()
        document.group_layer()
        self.assertEqual(document.remove_group("Group 1"), 1)
        self.assertEqual(document.layer_count, 2)
        self.assertFalse(document.groups)
        self.assertEqual(document.active_layer.group, "")

    def test_remove_layer_prunes_the_group(self):
        document = self.document()
        document.group_layer()
        self.assertTrue(document.remove_layer())
        self.assertFalse(document.groups)

    def test_merge_down_uses_the_layer_operation(self):
        document = self.document()
        document.active_layer.op = "add"
        self.assertTrue(document.merge_down())
        self.assertEqual(document.layer_count, 1)
        self.assertEqual(document.flatten().rgba(0, 0), (210, 120, 80, 255))
        self.assertFalse(document.merge_down())

    def test_merge_group(self):
        document = self.document()
        document.group_layer()
        document.add_layer()
        document.layers[2].group = "Group 1"
        document.bitmap.set_rgba(0, 0, (0, 255, 0, 255))
        before = document.begin()
        self.assertTrue(document.merge_group("Group 1"))
        self.assertEqual(document.layer_count, 2)
        self.assertEqual(document.active_layer.name, "Group 1")
        self.assertEqual(document.active_layer.bitmap.rgba(0, 0),
                         (0, 255, 0, 255))
        self.assertIsNone(document.group("Group 1"))
        document.commit(before)
        document.undo_last()
        self.assertEqual(document.layer_count, 3)
        self.assertIsNotNone(document.group("Group 1"))

    def test_groups_survive_undo(self):
        document = self.document()
        before = document.begin()
        document.group_layer()
        document.commit(before)
        document.undo_last()
        self.assertFalse(document.groups)
        self.assertEqual(document.active_layer.group, "")


class HoleFillTests(unittest.TestCase):
    def test_enclosed_pocket_is_filled(self):
        width = height = 9
        mask = bytearray(width * height)
        for y in range(1, 8):
            for x in range(1, 8):
                mask[y * width + x] = 255
        mask[4 * width + 4] = 0
        filled = fill_holes(mask, width, height)
        self.assertEqual(filled[4 * width + 4], 255)
        self.assertEqual(filled[0], 0)

    def test_open_concavity_is_left_alone(self):
        width = height = 7
        mask = bytearray(width * height)
        for y in range(height):
            for x in range(width):
                if x < 2 or y < 2 or y > 4:
                    mask[y * width + x] = 255
        filled = fill_holes(mask, width, height)
        self.assertEqual(filled[3 * width + 5], 0)
        self.assertEqual(filled[3 * width], 255)

    def test_solid_mask_is_unchanged(self):
        mask = bytearray(b"\xff" * 12)
        self.assertEqual(fill_holes(mask, 4, 3), mask)

    def test_wrong_size_returns_a_copy(self):
        mask = bytearray(b"\xff\x00")
        filled = fill_holes(mask, 4, 3)
        self.assertEqual(filled, mask)
        self.assertIsNot(filled, mask)

    def test_polygon_with_a_nested_loop_has_no_hole(self):
        points = [(1, 1), (8, 1), (8, 8), (1, 8),
                  (2, 2), (7, 2), (7, 7), (2, 7)]
        mask = selection_polygon_mask(10, 10, points)
        for y in range(2, 8):
            for x in range(2, 8):
                self.assertEqual(mask[y * 10 + x], 255, (x, y))

    def test_lasso_like_path_leaves_no_gap(self):
        points = [(1, 1), (7, 1), (7, 7), (1, 7),
                  (2, 2), (6, 2), (6, 6), (2, 6), (2, 3)]
        mask = selection_polygon_mask(9, 9, points)
        self.assertEqual(mask[4 * 9 + 4], 255)
        self.assertGreaterEqual(sum(1 for value in mask if value), 47)


class LassoPathTests(unittest.TestCase):
    def test_clean_path_drops_repeats_and_straight_runs(self):
        points = [(0, 0), (1, 1), (2, 2), (2, 2), (2, 3)]
        self.assertEqual(clean_path(points), [(0, 0), (2, 2), (2, 3)])

    def test_clean_path_keeps_a_short_path_intact(self):
        self.assertEqual(clean_path([(3, 3), (3, 3)]), [(3, 3)])
        self.assertEqual(clean_path([(0, 0), (4, 0)]), [(0, 0), (4, 0)])

    def test_cleaned_path_still_fills_the_loop(self):
        points = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (4, 2),
                  (4, 3), (4, 4), (3, 4), (2, 4), (1, 4), (0, 4), (0, 3),
                  (0, 2), (0, 1)]
        mask = selection_polygon_mask(6, 6, clean_path(points))
        self.assertEqual(mask[2 * 6 + 2], 255)


class EllipseShapeTests(unittest.TestCase):
    def outline(self, box, size):
        bitmap = Bitmap(*size)
        paint_shape(bitmap, None, "ellipse", box[0], box[1], box[2], box[3],
                    1, (255, 255, 255, 255), False)
        return {(x, y) for y in range(size[1]) for x in range(size[0])
                if bitmap.rgba(x, y)[3]}

    def test_outline_has_no_floating_pixels(self):
        """Every ring pixel touches the ring, corner to corner is fine."""
        cases = (((0, 0, 15, 3), (16, 4)), ((0, 0, 31, 7), (32, 8)),
                 ((0, 0, 31, 31), (32, 32)), ((0, 0, 7, 31), (8, 32)),
                 ((0, 0, 12, 7), (13, 8)), ((0, 0, 5, 2), (6, 3)))
        for box, size in cases:
            pixels = self.outline(box, size)
            floating = [point for point in pixels
                        if not any((point[0] + dx, point[1] + dy) in pixels
                                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                                   if (dx, dy) != (0, 0))]
            self.assertEqual(floating, [], "gap in %s" % (box,))

    def test_outline_leaves_the_middle_empty(self):
        pixels = self.outline((0, 0, 31, 31), (32, 32))
        self.assertNotIn((16, 16), pixels)
        self.assertIn((0, 16), pixels)

    def test_filled_ellipse_covers_its_inside(self):
        bitmap = Bitmap(16, 16)
        paint_shape(bitmap, None, "ellipse", 2, 2, 13, 13, 1,
                    (255, 255, 255, 255), True)
        self.assertEqual(bitmap.rgba(7, 7), (255, 255, 255, 255))
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 0))


class MoveScaleTests(unittest.TestCase):
    def test_extract_and_stamp_moves_the_selection(self):
        bitmap = Bitmap(4, 4)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(1, 0, GREEN)
        mask = bytearray(16)
        mask[0] = 255
        mask[1] = 255
        piece = extract_selection(bitmap, mask, (0, 0, 2, 1))
        self.assertEqual(piece.rgba(0, 0), RED)
        self.assertEqual(piece.rgba(1, 0), GREEN)
        clear_region(bitmap, mask)
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 0))
        stamp_bitmap(bitmap, piece, (2, 3))
        self.assertEqual(bitmap.rgba(2, 3), RED)
        self.assertEqual(bitmap.rgba(3, 3), GREEN)
        moved = piece_mask(piece, (2, 3), 4, 4)
        self.assertEqual(moved[3 * 4 + 2], 255)
        self.assertEqual(moved[3 * 4 + 3], 255)
        self.assertEqual(sum(1 for value in moved if value), 2)

    def test_extract_keeps_unselected_pixels_transparent(self):
        bitmap = Bitmap(2, 1)
        bitmap.set_rgba(0, 0, RED)
        mask = bytearray(2)
        mask[0] = 255
        mask[1] = 255
        piece = extract_selection(bitmap, mask, (0, 0, 2, 1))
        self.assertEqual(piece.rgba(0, 0), RED)
        self.assertEqual(piece.rgba(1, 0), (0, 0, 0, 0))

    def test_stamp_clips_at_the_canvas_edge(self):
        bitmap = Bitmap(2, 2)
        piece = Bitmap(2, 2)
        piece.fill(BLUE)
        stamp_bitmap(bitmap, piece, (1, 1))
        self.assertEqual(bitmap.rgba(1, 1), BLUE)
        self.assertEqual(bitmap.rgba(0, 0), (0, 0, 0, 0))
        mask = piece_mask(piece, (1, 1), 2, 2)
        self.assertEqual(sum(1 for value in mask if value), 1)


class RotationTests(unittest.TestCase):
    def test_quarter_turn_is_exact(self):
        piece = Bitmap(2, 1)
        piece.set_rgba(0, 0, RED)
        piece.set_rgba(1, 0, GREEN)
        turned = rotate_piece(piece, 90)
        self.assertEqual((turned.width, turned.height), (1, 2))
        self.assertEqual(turned.rgba(0, 0), RED)
        self.assertEqual(turned.rgba(0, 1), GREEN)

    def test_half_turn_keeps_every_pixel(self):
        piece = Bitmap(3, 2)
        for x in range(3):
            for y in range(2):
                piece.set_rgba(x, y, (x, y, 0, 255))
        turned = rotate_piece(piece, 180)
        self.assertEqual((turned.width, turned.height), (3, 2))
        for x in range(3):
            for y in range(2):
                self.assertEqual(turned.rgba(2 - x, 1 - y),
                                 (x, y, 0, 255))

    def test_zero_degrees_is_a_copy(self):
        piece = Bitmap(2, 2)
        piece.fill(BLUE)
        copy = rotate_piece(piece, 0)
        self.assertEqual((copy.width, copy.height), (2, 2))
        self.assertEqual(copy.rgba(1, 1), BLUE)
        copy.set_rgba(0, 0, RED)
        self.assertEqual(piece.rgba(0, 0), BLUE)

    def test_odd_angle_grows_without_cropping(self):
        piece = Bitmap(4, 4)
        piece.fill(RED)
        turned = rotate_piece(piece, 45)
        self.assertGreaterEqual(turned.width, 5)
        self.assertGreaterEqual(turned.height, 5)
        opaque = sum(1 for y in range(turned.height)
                     for x in range(turned.width)
                     if turned.rgba(x, y)[3])
        self.assertGreater(opaque, 0)
        self.assertLessEqual(opaque, turned.width * turned.height)


class ScaleBoxTests(unittest.TestCase):
    def test_first_movement_keeps_the_piece_as_it_was(self):
        box = (0, 0, 10, 5)
        self.assertEqual(scale_box(box, (0, 0), (6, 3), (6, 3), (64, 64)),
                         (0, 0, 10, 5))

    def test_scale_keeps_the_proportions(self):
        scaled = scale_box((0, 0, 10, 5), (0, 0), (6, 3), (12, 6), (64, 64))
        self.assertEqual(scaled, (0, 0, 20, 10))

    def test_grab_close_to_the_corner_does_not_collapse_the_piece(self):
        box = (0, 0, 20, 10)
        self.assertEqual(scale_box(box, (0, 0), (1, 0), (1, 0), (64, 64)),
                         box)
        self.assertEqual(scale_box(box, (0, 0), (1, 0), (3, 0), (64, 64)),
                         box)

    def test_scale_stays_anchored_on_the_fixed_corner(self):
        nx, ny, new_w, new_h = scale_box((5, 5, 10, 5), (14, 9), (9, 7),
                                        (9, 7), (64, 64))
        self.assertEqual((nx + new_w - 1, ny + new_h - 1), (14, 9))

    def test_scale_caps_growth_at_the_canvas(self):
        self.assertEqual(
            scale_box((0, 0, 10, 5), (0, 0), (10, 5), (100, 50), (40, 20)),
            (0, 0, 40, 20))

    def test_scale_near_an_edge_stays_on_the_canvas(self):
        nx, ny, new_w, new_h = scale_box((4, 4, 8, 6), (11, 9), (7, 6),
                                        (100, 100), (32, 32))
        self.assertGreaterEqual(nx, 0)
        self.assertGreaterEqual(ny, 0)
        self.assertLessEqual(nx + new_w, 32)
        self.assertLessEqual(ny + new_h, 32)
        self.assertEqual((nx + new_w - 1, ny + new_h - 1), (11, 9))

    def test_scale_never_reaches_zero(self):
        _, _, width, height = scale_box((0, 0, 10, 5), (4, 2), (9, 4),
                                        (-100, -100), (64, 64))
        self.assertGreaterEqual(width, 1)
        self.assertGreaterEqual(height, 1)


class RotationDragTests(unittest.TestCase):
    def test_centre_wiggles_do_not_spin_the_piece(self):
        handle = RotationDrag((10.0, 10.0), (10, 10))
        self.assertEqual(handle.update((10, 10)), 0)
        self.assertEqual(handle.update((11, 10)), 0)
        self.assertEqual(handle.update((10, 11)), 0)

    def test_rotation_tracks_once_the_pointer_leaves_the_middle(self):
        handle = RotationDrag((10.0, 10.0), (10, 20))
        self.assertEqual(handle.update((10, 20)), 0)
        self.assertEqual(handle.update((0, 10)), 90)

    def test_angle_holds_still_inside_the_dead_zone(self):
        handle = RotationDrag((10.0, 10.0), (0, 10))
        turned = handle.update((10, 20))
        self.assertGreater(turned, 0)
        self.assertEqual(handle.update((11, 10)), turned)
        self.assertEqual(handle.update((10, 20)), turned)

    def test_angles_are_snapped_and_wrapped(self):
        handle = RotationDrag((10.0, 10.0), (10, 20))
        for pointer in ((19, 10), (10, 0), (0, 10), (10, 20)):
            angle = handle.update(pointer, 5)
            self.assertGreaterEqual(angle, 0)
            self.assertLess(angle, 360)
            self.assertEqual(angle % 5, 0)


class QuadGeometryTests(unittest.TestCase):
    def test_box_round_trip(self):
        self.assertEqual(quad_from_box((2, 3, 4, 2)),
                         [(2.0, 3.0), (6.0, 3.0), (6.0, 5.0), (2.0, 5.0)])
        self.assertEqual(box_from_quad(quad_from_box((2, 3, 4, 2))),
                         (2, 3, 4, 2))

    def test_translate_moves_every_corner(self):
        quad = quad_from_box((0, 0, 2, 2))
        moved = quad_translate(quad, 3, -1)
        self.assertEqual(moved[0], (3.0, -1.0))
        self.assertEqual(moved[2], (5.0, 1.0))

    def test_scale_around_anchor_keeps_it_fixed(self):
        quad = quad_from_box((2, 2, 4, 4))
        scaled = quad_scale(quad, (2.0, 2.0), 2.0)
        self.assertEqual(scaled[0], (2.0, 2.0))
        self.assertEqual(scaled[2], (10.0, 10.0))

    def test_rotation_turns_clockwise(self):
        quad = [(1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0)]
        turned = quad_rotate(quad, (1.0, 0.0), 90)
        self.assertAlmostEqual(turned[0][0], 1.0)
        self.assertAlmostEqual(turned[0][1], 0.0)
        self.assertAlmostEqual(turned[1][0], 1.0)
        self.assertAlmostEqual(turned[1][1], 1.0)

    def test_shear_moves_only_the_chosen_edge(self):
        quad = quad_from_box((0, 0, 10, 10))
        top = quad_shear(quad, 0, 3)
        self.assertEqual(top[0], (3.0, 0.0))
        self.assertEqual(top[1], (13.0, 0.0))
        self.assertEqual(top[2], (10.0, 10.0))
        right = quad_shear(quad, 1, 4)
        self.assertEqual(right[1], (10.0, 4.0))
        self.assertEqual(right[2], (10.0, 14.0))
        self.assertEqual(right[0], (0.0, 0.0))

    def test_move_one_corner_leaves_the_rest(self):
        quad = quad_from_box((0, 0, 4, 4))
        moved = quad_move_corner(quad, 2, -1, 2)
        self.assertEqual(moved[2], (3.0, 6.0))
        self.assertEqual(moved[0], quad[0])

    def test_handles_cover_corners_edges_and_rotation(self):
        handles = quad_handles(quad_from_box((0, 0, 10, 10)), 24.0)
        self.assertEqual(len(handles), 9)
        self.assertEqual(handles[0], ("corner", 0, (0.0, 0.0)))
        self.assertEqual(handles[4], ("edge", 0, (5.0, 0.0)))
        self.assertEqual(handles[8], ("rotate", -1, (5.0, -24.0)))


class WarpPieceTests(unittest.TestCase):
    def piece(self):
        bitmap = Bitmap(2, 2)
        bitmap.set_rgba(0, 0, RED)
        bitmap.set_rgba(1, 0, GREEN)
        bitmap.set_rgba(0, 1, BLUE)
        bitmap.set_rgba(1, 1, (255, 255, 255, 255))
        return bitmap

    def test_identity_box_keeps_every_pixel(self):
        piece = self.piece()
        left, top, warped = warp_piece(piece, quad_from_box((1, 1, 2, 2)),
                                       8, 8)
        self.assertEqual((left, top), (1, 1))
        self.assertEqual((warped.width, warped.height), (2, 2))
        for y in range(2):
            for x in range(2):
                self.assertEqual(warped.rgba(x, y), piece.rgba(x, y))

    def test_double_scale_repeats_pixels(self):
        piece = self.piece()
        _, _, warped = warp_piece(piece, quad_from_box((0, 0, 4, 4)), 8, 8)
        self.assertEqual(warped.rgba(0, 0), RED)
        self.assertEqual(warped.rgba(1, 1), RED)
        self.assertEqual(warped.rgba(2, 0), GREEN)
        self.assertEqual(warped.rgba(3, 1), GREEN)
        self.assertEqual(warped.rgba(0, 2), BLUE)
        self.assertEqual(warped.rgba(3, 3), (255, 255, 255, 255))

    def test_clipped_quad_returns_the_visible_box(self):
        _, _, warped = warp_piece(self.piece(),
                                  quad_from_box((-2, -2, 4, 4)), 8, 8)
        self.assertEqual((warped.width, warped.height), (2, 2))
        self.assertEqual(warped.rgba(0, 0), (255, 255, 255, 255))

    def test_transparent_source_pixels_stay_transparent(self):
        piece = Bitmap(1, 1)
        _, _, warped = warp_piece(piece, quad_from_box((0, 0, 2, 2)), 4, 4)
        self.assertEqual(warped.rgba(0, 0), (0, 0, 0, 0))

    def test_perspective_quad_tilts_the_piece(self):
        piece = Bitmap(2, 1)
        piece.set_rgba(0, 0, RED)
        piece.set_rgba(1, 0, GREEN)
        quad = [(0.0, 0.0), (4.0, 0.0), (2.0, 4.0), (1.0, 4.0)]
        left, top, warped = warp_piece(piece, quad, 8, 8)
        self.assertEqual((left, top), (0, 0))
        self.assertGreater(warped.height, 1)
        colours = set(warped.rgba(x, y) for y in range(warped.height)
                      for x in range(warped.width))
        self.assertIn(RED, colours)
        self.assertIn(GREEN, colours)


if __name__ == "__main__":
    unittest.main()
