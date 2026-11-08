"""Destructive pixel effects: softening and jumbling a brush area.

Both effects work on a brush footprint (`size` and `shape`), can be mirrored
across the canvas centre lines and only write pixels the selection allows, so
they behave like the painting tools. They edit the `Bitmap` in place and return
how many pixels they touched.

* `blur` replaces each pixel with the average of its 3x3 neighbourhood, mixed
  back towards the original by `strength` (0-100%). A stroke passes one
  snapshot so overlapping dabs of the same drag do not compound.
* `jumble` reorders the colour bytes inside the footprint:
  `shuffle` permutes them, `scatter` picks a random colour per pixel (so some
  are duplicated), and `noise` jitters the RGB channels by `amount` percent
  while keeping each pixel's alpha.

Pure Python, no tkinter, so the behaviour is unit-testable.
"""

import random

from .model import clamp
from .shapes import brush_offsets, line_points, mirror_points

JUMBLE_MODES = ("shuffle", "scatter", "noise")


def _put(bitmap, x, y, color):
    base = (y * bitmap.width + x) * 4
    bitmap.data[base] = color[0]
    bitmap.data[base + 1] = color[1]
    bitmap.data[base + 2] = color[2]
    bitmap.data[base + 3] = color[3]


def _cells(bitmap, mask, x, y, size, shape, axes):
    """The writable pixels of a mirrored brush footprint, in order."""
    cells = []
    seen = set()
    for mx, my in mirror_points(x, y, bitmap.width, bitmap.height, axes):
        for dx, dy in brush_offsets(size, shape):
            px, py = mx + dx, my + dy
            if px < 0 or py < 0 or px >= bitmap.width or py >= bitmap.height:
                continue
            if mask is not None and mask[py * bitmap.width + px] == 0:
                continue
            if (px, py) in seen:
                continue
            seen.add((px, py))
            cells.append((px, py))
    return cells


def _count(value, low, high, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def blur_area(bitmap, mask, x, y, size=3, strength=100, shape="square",
              axes="", source=None):
    """Soften the pixels under the brush; returns the pixels written.

    `source` is the bitmap to read from. A stroke passes the snapshot taken
    when the drag started, which keeps a long drag from blurring the same
    pixels over and over.
    """
    cells = _cells(bitmap, mask, x, y, size, shape, axes)
    if not cells:
        return 0
    strength = _count(strength, 0, 100, 100)
    if strength == 0:
        return 0
    if source is None:
        source = bitmap.copy()
    width, height = source.width, source.height
    data = source.data
    amount = strength / 100.0
    written = 0
    for px, py in cells:
        total = [0, 0, 0, 0]
        count = 0
        for ny in range(max(0, py - 1), min(height, py + 2)):
            base = (ny * width + max(0, px - 1)) * 4
            for _nx in range(max(0, px - 1), min(width, px + 2)):
                total[0] += data[base]
                total[1] += data[base + 1]
                total[2] += data[base + 2]
                total[3] += data[base + 3]
                base += 4
                count += 1
        current = source.rgba(px, py)
        _put(bitmap, px, py,
             tuple(int(current[channel]
                       + (total[channel] / float(count) - current[channel])
                       * amount + 0.5)
                   for channel in range(4)))
        written += 1
    return written


def blur_stroke(bitmap, mask, x0, y0, x1, y1, size=3, strength=100,
                shape="square", axes="", source=None):
    """Blur along a stroke between two points, mirrored."""
    if source is None:
        source = bitmap.copy()
    written = 0
    for x, y in line_points(x0, y0, x1, y1):
        written += blur_area(bitmap, mask, x, y, size, strength, shape, axes,
                             source)
    return written


def jumble_area(bitmap, mask, x, y, size=3, mode="shuffle", amount=50,
                shape="square", axes="", rng=None):
    """Mix the pixels under the brush; returns the pixels written."""
    cells = _cells(bitmap, mask, x, y, size, shape, axes)
    if len(cells) < 2:
        return 0
    if mode not in JUMBLE_MODES:
        mode = "shuffle"
    rng = rng if rng is not None else random.Random()
    colors = [bitmap.rgba(px, py) for px, py in cells]
    if mode == "scatter":
        mixed = [colors[rng.randrange(len(colors))] for _ in cells]
    elif mode == "noise":
        span = int(round(255 * _count(amount, 0, 100, 50) / 100.0))
        mixed = []
        for color in colors:
            mixed.append((clamp(color[0] + rng.randint(-span, span), 0, 255),
                          clamp(color[1] + rng.randint(-span, span), 0, 255),
                          clamp(color[2] + rng.randint(-span, span), 0, 255),
                          color[3]))
    else:
        mixed = list(colors)
        rng.shuffle(mixed)
    for (px, py), color in zip(cells, mixed):
        _put(bitmap, px, py, color)
    return len(cells)


def jumble_stroke(bitmap, mask, x0, y0, x1, y1, size=3, mode="shuffle",
                  amount=50, shape="square", axes="", rng=None):
    """Jumble along a stroke between two points, mirrored."""
    rng = rng if rng is not None else random.Random()
    written = 0
    for x, y in line_points(x0, y0, x1, y1):
        written += jumble_area(bitmap, mask, x, y, size, mode, amount, shape,
                               axes, rng)
    return written


def spray_area(bitmap, mask, x, y, size=8, flow=25, color=(255, 255, 255,
                                                           255),
               shape="circle", axes="", rng=None):
    """Spray `color` inside the brush footprint; `flow` is a percentage.

    Every writable pixel of the footprint is painted with `flow`% probability,
    which is what makes an airbrush build up on the pixels it keeps hitting.
    """
    cells = _cells(bitmap, mask, x, y, size, shape, axes)
    if not cells:
        return 0
    flow = _count(flow, 0, 100, 25)
    if flow <= 0:
        return 0
    rng = rng if rng is not None else random.Random()
    painted = 0
    for px, py in cells:
        if rng.random() * 100.0 >= flow:
            continue
        _put(bitmap, px, py, color)
        painted += 1
    return painted


def spray_stroke(bitmap, mask, x0, y0, x1, y1, size=8, flow=25,
                 color=(255, 255, 255, 255), shape="circle", axes="",
                 rng=None):
    """Spray along a stroke between two points, mirrored."""
    rng = rng if rng is not None else random.Random()
    painted = 0
    for x, y in line_points(x0, y0, x1, y1):
        painted += spray_area(bitmap, mask, x, y, size, flow, color, shape,
                              axes, rng)
    return painted


# ---------------------------------------------------------------- contours
# `contour_mask` turns a filled region into the ring around it, so the Contour
# tool can outline a selection or the artwork itself.
CONTOUR_SIDES = ("outside", "inside")

_NEIGHBOURS = {
    4: ((-1, 0), (1, 0), (0, -1), (0, 1)),
    8: ((-1, 0), (1, 0), (0, -1), (0, 1),
        (-1, -1), (1, -1), (-1, 1), (1, 1)),
}


def _as_mask(values):
    return bytearray(255 if value else 0 for value in values)


def _neighbours(connectivity):
    return _NEIGHBOURS[8 if int(connectivity) == 8 else 4]


def content_mask(bitmap, region=None):
    """The pixels that count as filled: a selection, or everything opaque."""
    if region is not None:
        return _as_mask(region)
    data = bitmap.data
    return bytearray(255 if data[index * 4 + 3] else 0
                     for index in range(bitmap.width * bitmap.height))


def grow_mask(mask, width, height, steps=1, connectivity=4):
    """Grow a mask outwards by `steps` pixels."""
    steps = _count(steps, 1, 64, 1)
    offsets = _neighbours(connectivity)
    current = _as_mask(mask)
    for _step in range(steps):
        grown = bytearray(current)
        for y in range(height):
            row = y * width
            for x in range(width):
                if current[row + x]:
                    continue
                for dx, dy in offsets:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height \
                            and current[ny * width + nx]:
                        grown[row + x] = 255
                        break
        current = grown
    return current


def shrink_mask(mask, width, height, steps=1, connectivity=4):
    """Erode a mask inwards by `steps` pixels; outside the canvas is empty."""
    steps = _count(steps, 1, 64, 1)
    offsets = _neighbours(connectivity)
    current = _as_mask(mask)
    for _step in range(steps):
        shrunk = bytearray(len(current))
        for y in range(height):
            row = y * width
            for x in range(width):
                if not current[row + x]:
                    continue
                keep = True
                for dx, dy in offsets:
                    nx, ny = x + dx, y + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height \
                            or not current[ny * width + nx]:
                        keep = False
                        break
                if keep:
                    shrunk[row + x] = 255
        current = shrunk
    return current


def contour_mask(mask, width, height, steps=1, side="outside",
                 connectivity=4):
    """The ring just outside or just inside a filled region."""
    steps = _count(steps, 1, 64, 1)
    side = "inside" if side == "inside" else "outside"
    solid = _as_mask(mask)
    if side == "inside":
        inner = shrink_mask(solid, width, height, steps, connectivity)
        return bytearray(255 if solid[index] and not inner[index] else 0
                         for index in range(len(solid)))
    grown = grow_mask(solid, width, height, steps, connectivity)
    return bytearray(255 if grown[index] and not solid[index] else 0
                     for index in range(len(solid)))
