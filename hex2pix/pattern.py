"""Pattern fills over byte selections.

Two fills, both driven by a small pattern block (hex rows or a micro pixel
canvas) and both restricted to a selection mask when one is active:

* `line` fills the selected lines one after another, taking the next pattern
  row for each line, so a two row pattern produces alternating stripes and a
  pattern row restarts at the beginning of every line.
* `structure` builds a super tile out of `super_width x super_height` copies,
  mirroring odd columns and rows (a kaleidoscope / fortress symmetry) and
  optionally staggering each pattern row band horizontally before repeating
  that super tile.

`.` / SKIP cells in the pattern leave the artwork untouched.

Pure Python, no tkinter, so the geometry is unit-testable.
"""

from .model import SKIP, Block


def block_from_grid(cells, width=None, height=None):
    """Build a Block from a grid of RGBA tuples; None cells mean skip."""
    height = int(height if height is not None else len(cells))
    if width is None:
        width = len(cells[0]) if cells else 0
    width = int(width)
    if width < 1 or height < 1:
        raise ValueError("a pattern needs at least one pixel")
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            cell = None
            if y < len(cells) and x < len(cells[y]):
                cell = cells[y][x]
            row.append(SKIP if cell is None else tuple(cell))
        rows.append(row)
    return Block(width, height, rows)


def grid_from_block(block):
    """A [(r, g, b, a) or None] grid from a Block."""
    return [[None if pixel is SKIP else tuple(pixel) for pixel in row]
            for row in block.rows]


class PatternSpec(object):
    """Everything a pattern fill needs besides the mask and the bitmap."""

    __slots__ = ("block", "mode", "scale", "row_shift", "mirror",
                 "super_width", "super_height")

    def __init__(self, block, mode="line", scale=1, row_shift=0, mirror=False,
                 super_width=2, super_height=2):
        if block is None or block.width < 1 or block.height < 1:
            raise ValueError("a pattern needs at least one pixel")
        self.block = block
        self.mode = "structure" if mode == "structure" else "line"
        self.scale = max(1, min(16, int(scale)))
        self.row_shift = int(row_shift)
        self.mirror = bool(mirror)
        self.super_width = max(1, min(16, int(super_width)))
        self.super_height = max(1, min(16, int(super_height)))

    def __repr__(self):
        return ("PatternSpec(%dx%d, %s, scale=%d, shift=%d, mirror=%s, "
                "super=%dx%d)"
                % (self.block.width, self.block.height, self.mode, self.scale,
                   self.row_shift, self.mirror, self.super_width,
                   self.super_height))


def apply_pattern(bitmap, mask, spec, origin=(0, 0)):
    """Paint the pattern into the masked pixels; returns pixels written."""
    if spec.mode == "structure":
        return _apply_structure(bitmap, mask, spec, origin)
    return _apply_lines(bitmap, mask, spec, origin)


def _put(bitmap, index, pixel):
    base = index * 4
    bitmap.data[base] = pixel[0]
    bitmap.data[base + 1] = pixel[1]
    bitmap.data[base + 2] = pixel[2]
    bitmap.data[base + 3] = pixel[3]


def _apply_lines(bitmap, mask, spec, origin):
    width, height = bitmap.width, bitmap.height
    block = spec.block
    pattern_width, pattern_height = block.width, block.height
    scale = spec.scale
    origin_x = origin[0]
    painted = 0
    line = 0
    for y in range(height):
        row_start = y * width
        if mask is not None and not any(mask[row_start:row_start + width]):
            continue
        pattern_row = block.rows[(line // scale) % pattern_height]
        offset = spec.row_shift * line
        for x in range(width):
            if mask is not None and mask[row_start + x] == 0:
                continue
            column = ((x - origin_x) // scale + offset) % pattern_width
            pixel = pattern_row[column]
            if pixel is SKIP:
                continue
            _put(bitmap, row_start + x, pixel)
            painted += 1
        line += 1
    return painted


def _apply_structure(bitmap, mask, spec, origin):
    width, height = bitmap.width, bitmap.height
    block = spec.block
    pattern_width, pattern_height = block.width, block.height
    scale = spec.scale
    origin_x, origin_y = origin
    super_width = pattern_width * spec.super_width
    super_height = pattern_height * spec.super_height
    painted = 0
    for y in range(height):
        row_start = y * width
        for x in range(width):
            if mask is not None and mask[row_start + x] == 0:
                continue
            mx = ((x - origin_x) // scale) % super_width
            my = ((y - origin_y) // scale) % super_height
            tile_x, tile_y = mx // pattern_width, my // pattern_height
            local_x, local_y = mx % pattern_width, my % pattern_height
            if spec.mirror and tile_x % 2:
                local_x = pattern_width - 1 - local_x
            if spec.mirror and tile_y % 2:
                local_y = pattern_height - 1 - local_y
            if spec.row_shift:
                local_x = (local_x + spec.row_shift * tile_y) % pattern_width
            pixel = block.rows[local_y][local_x]
            if pixel is SKIP:
                continue
            _put(bitmap, row_start + x, pixel)
            painted += 1
    return painted
