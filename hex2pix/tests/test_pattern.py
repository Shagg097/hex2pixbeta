"""Tests for the pattern fill geometry."""

import unittest

from hex2pix.model import SKIP, Bitmap, Block
from hex2pix.pattern import (PatternSpec, apply_pattern, block_from_grid,
                              grid_from_block)

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
CLEAR = (0, 0, 0, 0)


def full_mask(width, height):
    return bytearray(b"\xff" * (width * height))


class GridTest(unittest.TestCase):
    def test_none_cells_become_skip(self):
        block = block_from_grid([[RED, None], [None, BLUE]])
        self.assertEqual((block.width, block.height), (2, 2))
        self.assertEqual(block.rows[0][0], RED)
        self.assertIs(block.rows[0][1], SKIP)
        self.assertEqual(block.rows[1][1], BLUE)

    def test_ragged_grid_is_padded(self):
        block = block_from_grid([[RED]], width=3, height=2)
        self.assertEqual(block.width, 3)
        self.assertEqual(len(block.rows), 2)
        self.assertIs(block.rows[1][2], SKIP)

    def test_grid_round_trip(self):
        grid = [[RED, None], [None, BLUE]]
        self.assertEqual(grid_from_block(block_from_grid(grid)), grid)


class LineFillTest(unittest.TestCase):
    def test_rows_cycle_line_by_line(self):
        bitmap = Bitmap(4, 3)
        block = Block(2, 2, [[RED, RED], [BLUE, BLUE]])
        painted = apply_pattern(bitmap, full_mask(4, 3),
                                PatternSpec(block, "line"))
        self.assertEqual(painted, 12)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(3, 0), RED)
        self.assertEqual(bitmap.rgba(0, 1), BLUE)
        self.assertEqual(bitmap.rgba(0, 2), RED)

    def test_skip_leaves_pixels_alone(self):
        bitmap = Bitmap(4, 1, bytes(CLEAR) * 4)
        block = Block(2, 1, [[RED, SKIP]])
        apply_pattern(bitmap, full_mask(4, 1), PatternSpec(block))
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(1, 0), CLEAR)
        self.assertEqual(bitmap.rgba(2, 0), RED)

    def test_mask_limits_the_fill(self):
        bitmap = Bitmap(4, 1)
        mask = bytearray(4)
        mask[1] = 255
        mask[3] = 255
        block = Block(1, 1, [[RED]])
        painted = apply_pattern(bitmap, mask, PatternSpec(block))
        self.assertEqual(painted, 2)
        self.assertEqual(bitmap.rgba(0, 0), CLEAR)
        self.assertEqual(bitmap.rgba(1, 0), RED)
        self.assertEqual(bitmap.rgba(3, 0), RED)

    def test_row_shift_staggers_lines(self):
        bitmap = Bitmap(2, 2)
        block = Block(2, 1, [[RED, BLUE]])
        apply_pattern(bitmap, full_mask(2, 2),
                      PatternSpec(block, "line", row_shift=1))
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(1, 0), BLUE)
        self.assertEqual(bitmap.rgba(0, 1), BLUE)
        self.assertEqual(bitmap.rgba(1, 1), RED)

    def test_scale_widens_pattern_pixels(self):
        bitmap = Bitmap(4, 1)
        block = Block(2, 1, [[RED, BLUE]])
        apply_pattern(bitmap, full_mask(4, 1),
                      PatternSpec(block, "line", scale=2))
        self.assertEqual([bitmap.rgba(x, 0) for x in range(4)],
                         [RED, RED, BLUE, BLUE])

    def test_empty_selection_paints_nothing(self):
        bitmap = Bitmap(3, 3)
        block = Block(1, 1, [[RED]])
        painted = apply_pattern(bitmap, bytearray(9),
                                PatternSpec(block, "line"))
        self.assertEqual(painted, 0)
        self.assertEqual(bitmap.rgba(0, 0), CLEAR)


class StructureFillTest(unittest.TestCase):
    def test_mirrored_quadrants(self):
        bitmap = Bitmap(4, 1)
        block = Block(2, 2, [[RED, BLUE], [SKIP, SKIP]])
        spec = PatternSpec(block, "structure", mirror=True, super_width=2,
                           super_height=1)
        apply_pattern(bitmap, full_mask(4, 1), spec)
        self.assertEqual([bitmap.rgba(x, 0) for x in range(4)],
                         [RED, BLUE, BLUE, RED])

    def test_without_mirror_tiles_repeat(self):
        bitmap = Bitmap(4, 1)
        block = Block(2, 1, [[RED, BLUE]])
        spec = PatternSpec(block, "structure", mirror=False, super_width=2,
                           super_height=1)
        apply_pattern(bitmap, full_mask(4, 1), spec)
        self.assertEqual([bitmap.rgba(x, 0) for x in range(4)],
                         [RED, BLUE, RED, BLUE])

    def test_row_shift_staggers_tile_rows(self):
        bitmap = Bitmap(2, 2)
        block = Block(2, 1, [[RED, BLUE]])
        spec = PatternSpec(block, "structure", row_shift=1, mirror=False,
                           super_width=1, super_height=2)
        apply_pattern(bitmap, full_mask(2, 2), spec)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(1, 0), BLUE)
        self.assertEqual(bitmap.rgba(0, 1), BLUE)
        self.assertEqual(bitmap.rgba(1, 1), RED)

    def test_mirrored_rows_flip_vertically(self):
        bitmap = Bitmap(1, 2)
        block = Block(1, 2, [[RED], [BLUE]])
        spec = PatternSpec(block, "structure", mirror=True, super_width=1,
                           super_height=2)
        apply_pattern(bitmap, full_mask(1, 2), spec)
        self.assertEqual(bitmap.rgba(0, 0), RED)
        self.assertEqual(bitmap.rgba(0, 1), BLUE)

    def test_scale_repeats_cells(self):
        bitmap = Bitmap(4, 2)
        block = Block(2, 1, [[RED, BLUE]])
        spec = PatternSpec(block, "structure", scale=2, mirror=False,
                           super_width=1, super_height=1)
        apply_pattern(bitmap, full_mask(4, 2), spec)
        self.assertEqual([bitmap.rgba(x, 0) for x in range(4)],
                         [RED, RED, BLUE, BLUE])
        self.assertEqual([bitmap.rgba(x, 1) for x in range(4)],
                         [RED, RED, BLUE, BLUE])


class SpecTest(unittest.TestCase):
    def test_mode_and_clamps(self):
        block = Block(1, 1, [[RED]])
        spec = PatternSpec(block, "structure", scale=99, super_width=0,
                           super_height=99, row_shift=-2)
        self.assertEqual(spec.mode, "structure")
        self.assertEqual(spec.scale, 16)
        self.assertEqual(spec.super_width, 1)
        self.assertEqual(spec.super_height, 16)
        self.assertEqual(spec.row_shift, -2)

    def test_line_is_the_default_mode(self):
        self.assertEqual(PatternSpec(Block(1, 1, [[RED]])).mode, "line")


if __name__ == "__main__":
    unittest.main()
