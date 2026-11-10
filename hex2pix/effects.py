"""Destructive pixel effects: softening and mixing a brush area.

Both effects work on a brush footprint (`size` and `shape`), can be mirrored
across the canvas centre lines and only write pixels the selection allows, so
they behave like the painting tools. They edit the `Bitmap` in place and return
how many pixels they touched.

* `blur` replaces each pixel with the average of its neighbourhood, mixed
  back towards the original by `strength` (0-100%). `amount` sets the
  neighbourhood radius (0-8 pixels). A stroke passes one snapshot so
  overlapping dabs of the same drag do not compound.
* `shuffle` reorders the colour bytes inside the footprint:
  `shuffle` permutes them, `scatter` picks a random colour per pixel (so some
  are duplicated), and `noise` jitters the RGB channels by `amount` percent
  while keeping each pixel's alpha.

Pure Python, no tkinter, so the behaviour is unit-testable.
"""

import random

from .model import clamp
from .shapes import brush_offsets, line_points, mirror_points

SHUFFLE_MODES = ("shuffle", "scatter", "noise")


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
              axes="", source=None, amount=1):
    """Soften the pixels under the brush; returns the pixels written.

    `amount` is the blur radius in pixels (0-8): the average is taken over
    a (2 * amount + 1) square. `source` is the bitmap to read from. A
    stroke passes the snapshot taken when the drag started, which keeps a
    long drag from blurring the same pixels over and over.
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
    radius = _count(amount, 0, 8, 1)
    mix = strength / 100.0
    written = 0
    for px, py in cells:
        total = [0, 0, 0, 0]
        count = 0
        for ny in range(max(0, py - radius),
                        min(height, py + radius + 1)):
            base = (ny * width + max(0, px - radius)) * 4
            for _nx in range(max(0, px - radius),
                             min(width, px + radius + 1)):
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
                       * mix + 0.5)
                   for channel in range(4)))
        written += 1
    return written


def blur_stroke(bitmap, mask, x0, y0, x1, y1, size=3, strength=100,
                shape="square", axes="", source=None, smear=0,
                amount=1):
    """Blur along a stroke between two points, mirrored.

    `smear` drags the pixels the brush just left along with the stroke; it
    reads the layer as it now looks, so the colour travels instead of being
    pulled again and again from where the stroke started. `amount` widens
    the blur neighbourhood, independently of the brush footprint.
    """
    if source is None:
        source = bitmap.copy()
    smear = _count(smear, 0, 100, 0)
    written = 0
    previous = (x0, y0)
    for x, y in line_points(x0, y0, x1, y1):
        written += blur_area(bitmap, mask, x, y, size, strength, shape, axes,
                             source, amount)
        if smear and (x, y) != previous:
            written += smear_area(bitmap, mask, x, y, size, smear, shape,
                                  axes, x - previous[0], y - previous[1])
        previous = (x, y)
    return written


def _neighbour_average(source, px, py):
    """The mean colour of the 3x3 block around one pixel."""
    total = [0, 0, 0, 0]
    count = 0
    for ny in range(max(0, py - 1), min(source.height, py + 2)):
        for nx in range(max(0, px - 1), min(source.width, px + 2)):
            color = source.rgba(nx, ny)
            for channel in range(4):
                total[channel] += color[channel]
            count += 1
    if not count:
        return (0, 0, 0, 0)
    return tuple(int(value / float(count) + 0.5) for value in total)


def smear_area(bitmap, mask, x, y, size=3, amount=50, shape="square",
               axes="", dx=0, dy=0, source=None):
    """Drag colour along the stroke, like a finger through wet paint.

    Each cell mixes toward the pixel `dx`, `dy` back along the stroke, which
    is the pixel the brush has just left. A stationary brush moves nothing, so
    a click without a drag leaves the layer alone.
    """
    if not dx and not dy:
        return 0
    amount = _count(amount, 0, 100, 50)
    if amount == 0:
        return 0
    cells = _cells(bitmap, mask, x, y, size, shape, axes)
    if not cells:
        return 0
    source = source if source is not None else bitmap
    mix = amount / 100.0
    written = 0
    for px, py in cells:
        sx, sy = px - dx, py - dy
        if sx < 0 or sy < 0 or sx >= source.width or sy >= source.height:
            continue
        color = source.rgba(sx, sy)
        current = bitmap.rgba(px, py)
        _put(bitmap, px, py,
             tuple(int(current[channel]
                       + (color[channel] - current[channel]) * mix + 0.5)
                   for channel in range(4)))
        written += 1
    return written


def shuffle_area(bitmap, mask, x, y, size=3, mode="shuffle", amount=50,
                shape="square", axes="", rng=None, blur=0, source=None):
    """Mix the pixels under the brush; returns the pixels written."""
    cells = _cells(bitmap, mask, x, y, size, shape, axes)
    if len(cells) < 2:
        return 0
    if mode not in SHUFFLE_MODES:
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
    soften = _count(blur, 0, 100, 0) / 100.0
    if soften and source is None:
        # The averages must come from the pixels as they were before this dab,
        # or the cells already written would feed back into the later ones.
        source = bitmap.copy()
    read_from = source if source is not None else bitmap
    written = 0
    for (px, py), color in zip(cells, mixed):
        if soften:
            average = _neighbour_average(read_from, px, py)
            color = tuple(int(color[channel]
                              + (average[channel] - color[channel]) * soften
                              + 0.5)
                          for channel in range(4))
        _put(bitmap, px, py, color)
        written += 1
    return written


def shuffle_stroke(bitmap, mask, x0, y0, x1, y1, size=3, mode="shuffle",
                   amount=50, shape="square", axes="", rng=None, blur=0,
                   source=None):
    """Shuffle along a stroke between two points, mirrored."""
    rng = rng if rng is not None else random.Random()
    if blur and source is None:
        # One snapshot for the whole stroke, so the soften averages stay put
        # instead of drifting as the stroke writes into the layer.
        source = bitmap.copy()
    written = 0
    for x, y in line_points(x0, y0, x1, y1):
        written += shuffle_area(bitmap, mask, x, y, size, mode, amount, shape,
                                axes, rng, blur, source)
    return written


def _tip_depths(cells):
    """How far inside the footprint each pixel sits; 1 is on the rim.

    A soft spray used to fade by distance from the brush centre, so a hollow
    or oddly shaped tip had every pixel on that circle's rim and vanished
    under Blur. Walking inwards from the tip's own edge fades whatever shape
    the tip has.
    """
    inside = set(cells)
    steps = ((-1, 0), (1, 0), (0, -1), (0, 1))
    depths = {}
    edge = []
    for px, py in inside:
        for dx, dy in steps:
            if (px + dx, py + dy) not in inside:
                depths[(px, py)] = 1
                edge.append((px, py))
                break
    head = 0
    while head < len(edge):
        px, py = edge[head]
        head += 1
        deeper = depths[(px, py)] + 1
        for dx, dy in steps:
            other = (px + dx, py + dy)
            if other in inside and other not in depths:
                depths[other] = deeper
                edge.append(other)
    return depths


def _spray_pixel(bitmap, px, py, color, blur, feather):
    """Paint `color`, fading into the pixel underneath near the tip's edge."""
    if not blur:
        _put(bitmap, px, py, color)
        return
    mix = 1.0 - (blur / 100.0) * feather
    base = bitmap.rgba(px, py)
    _put(bitmap, px, py,
         tuple(int(base[channel]
                   + (color[channel] - base[channel]) * mix + 0.5)
               for channel in range(4)))


def spray_area(bitmap, mask, x, y, size=8, flow=25, color=(255, 255, 255,
                                                           255),
               shape="circle", axes="", rng=None, blur=0, colors=None):
    """Spray inside the brush footprint; `flow` and `blur` are percentages.

    Every writable pixel of the footprint is painted with `flow`% probability,
    which is what makes the spray can build up on the pixels it keeps hitting.
    `colors` picks one of several colours per sprayed pixel instead of `color`.
    `blur` feathers the rim of each dab: at 0 every hit is flat colour, and
    higher values fade the pixels nearest the tip's edge into whatever is
    already underneath, so the spray gets a soft edge that follows the tip's
    shape without becoming a different tool.
    """
    cells = _cells(bitmap, mask, x, y, size, shape, axes)
    if not cells:
        return 0
    flow = _count(flow, 0, 100, 25)
    if flow <= 0:
        return 0
    rng = rng if rng is not None else random.Random()
    chosen = [cell for cell in cells if rng.random() * 100.0 < flow]
    if not chosen:
        return 0
    blur = _count(blur, 0, 100, 0)
    palette = [tuple(entry) for entry in colors] if colors else []
    depths = _tip_depths(cells) if blur else {}
    deepest = max(depths.values()) if depths else 1
    painted = 0
    for px, py in chosen:
        picked = rng.choice(palette) if palette else color
        feather = 0.0
        if blur:
            feather = 1.0 - depths.get((px, py), 1) / float(deepest)
        _spray_pixel(bitmap, px, py, picked, blur, feather)
        painted += 1
    return painted


def spray_stroke(bitmap, mask, x0, y0, x1, y1, size=8, flow=25,
                 color=(255, 255, 255, 255), shape="circle", axes="",
                 rng=None, blur=False, colors=None):
    """Spray along a stroke between two points, mirrored."""
    rng = rng if rng is not None else random.Random()
    painted = 0
    for x, y in line_points(x0, y0, x1, y1):
        painted += spray_area(bitmap, mask, x, y, size, flow, color, shape,
                              axes, rng, blur=blur, colors=colors)
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
