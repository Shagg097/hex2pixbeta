"""Pixel-art drawing: mirrored dabs, shape tools and gradients.

Aseprite-inspired behaviour on top of the byte-mask model: a brush can be a
square or a circle, strokes can be mirrored across the horizontal and/or
vertical centre line, shapes are drawn between two points (outline or filled),
and a linear gradient interpolates between the foreground and background
colour inside the mask.

Pure Python, no tkinter, so the geometry is unit-testable.
"""

import math

from .bitmapfont import text_points
from .dither import apply_gradient_dither
from .model import HexError, clamp

SHAPE_KINDS = ("line", "rect", "ellipse")


def mirror_points(x, y, width, height, axes=""):
    """The point plus its mirrors across the centre lines named in `axes`."""
    points = [(x, y)]
    if "x" in axes:
        points.append((width - 1 - x, y))
    if "y" in axes:
        points.append((x, height - 1 - y))
    if "x" in axes and "y" in axes:
        points.append((width - 1 - x, height - 1 - y))
    seen = []
    for point in points:
        if point not in seen:
            seen.append(point)
    return seen


def brush_offsets(size, shape="square"):
    """Pixel offsets relative to the brush centre."""
    size = max(1, int(size))
    half = size // 2
    offsets = []
    for dy in range(-half, size - half):
        for dx in range(-half, size - half):
            if shape == "circle" and size > 2:
                if dx * dx + dy * dy > half * half:
                    continue
            offsets.append((dx, dy))
    return offsets


def _bresenham(x0, y0, x1, y1):
    points = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = int(x0), int(y0)
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return points


def _ellipse_rows(x0, y0, x1, y1):
    """Row extents (y, first_x, last_x) of the ellipse inside the box."""
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    cx = (xa + xb + 1) / 2.0
    cy = (ya + yb + 1) / 2.0
    rx = max(0.5, (xb - xa + 1) / 2.0)
    ry = max(0.5, (yb - ya + 1) / 2.0)
    rows = []
    for y in range(ya, yb + 1):
        dy = (y + 0.5 - cy) / ry
        if abs(dy) > 1.0:
            continue
        span = rx * (1.0 - dy * dy) ** 0.5
        first = int(round(cx - span - 0.5))
        last = int(round(cx + span - 0.5))
        first = clamp(first, xa, xb)
        last = clamp(last, xa, xb)
        rows.append((y, first, last))
    return rows


def _clip_range(low, high, limit):
    """Shrink an inclusive pixel range to 0..limit-1, or None when it misses."""
    low = max(int(low), 0)
    high = min(int(high), limit - 1)
    if high < low:
        return None
    return low, high


def shape_points(kind, x0, y0, x1, y1, fill=False, bounds=None):
    """The pixels a line, rectangle or ellipse covers between two points.

    The endpoints may sit outside the canvas, so a drag can run past the edge
    and keep the shape it describes. `bounds` is the canvas (width, height);
    it only limits how much of the shape is walked, not its geometry, so an
    ellipse whose box starts off-canvas keeps its curvature.
    """
    kind = kind if kind in SHAPE_KINDS else "rect"
    if kind == "line":
        return _bresenham(x0, y0, x1, y1)
    points = []
    if kind == "rect":
        xa, xb = sorted((int(x0), int(x1)))
        ya, yb = sorted((int(y0), int(y1)))
        if bounds is not None:
            columns = _clip_range(xa, xb, bounds[0])
            rows = _clip_range(ya, yb, bounds[1])
            if columns is None or rows is None:
                return points
            xa, xb = columns
            ya, yb = rows
        if fill:
            for y in range(ya, yb + 1):
                for x in range(xa, xb + 1):
                    points.append((x, y))
            return points
        for x in range(xa, xb + 1):
            points.append((x, ya))
            if yb != ya:
                points.append((x, yb))
        for y in range(ya + 1, yb):
            points.append((xa, y))
            if xb != xa:
                points.append((xb, y))
        return points
    rows = _ellipse_rows(x0, y0, x1, y1)
    if not rows:
        return points
    if fill:
        for y, first, last in rows:
            if bounds is not None:
                if not 0 <= y < bounds[1]:
                    continue
                columns = _clip_range(first, last, bounds[0])
                if columns is None:
                    continue
                first, last = columns
            for x in range(first, last + 1):
                points.append((x, y))
        return points
    # Outline: the flat caps plus a joined side on each row. Connecting the
    # rows with lines keeps a flat or very wide ellipse from leaving single
    # pixels floating apart from the rest of the ring.
    for index, (y, first, last) in enumerate(rows):
        if index == 0 or index == len(rows) - 1:
            cap = (first, last)
            if bounds is not None:
                cap = _clip_range(first, last, bounds[0]) or (0, -1)
            for x in range(cap[0], cap[1] + 1):
                points.append((x, y))
        else:
            points.append((first, y))
            if last != first:
                points.append((last, y))
        if index:
            previous_y, previous_first, previous_last = rows[index - 1]
            points.extend(_bresenham(previous_first, previous_y, first, y))
            if last != first:
                points.extend(_bresenham(previous_last, previous_y, last, y))
    if bounds is not None:
        points = [(x, y) for x, y in points
                  if 0 <= x < bounds[0] and 0 <= y < bounds[1]]
    return points


def _plot(bitmap, mask, x, y, color):
    if x < 0 or y < 0 or x >= bitmap.width or y >= bitmap.height:
        return 0
    index = y * bitmap.width + x
    if mask is not None and mask[index] == 0:
        return 0
    base = index * 4
    bitmap.data[base] = color[0]
    bitmap.data[base + 1] = color[1]
    bitmap.data[base + 2] = color[2]
    bitmap.data[base + 3] = color[3]
    return 1


def paint_dab(bitmap, mask, x, y, size, color, shape="square", axes="",
              seen=None):
    """Paint one brush dab, mirrored across the requested axes.

    `seen` lets a stroke or shape share one set of already-painted pixels so
    overlapping dabs are not counted twice.
    """
    offsets = brush_offsets(size, shape)
    if seen is None:
        seen = set()
    painted = 0
    for mx, my in mirror_points(x, y, bitmap.width, bitmap.height, axes):
        for dx, dy in offsets:
            px, py = mx + dx, my + dy
            if (px, py) in seen:
                continue
            seen.add((px, py))
            painted += _plot(bitmap, mask, px, py, color)
    return painted


def paint_stroke(bitmap, mask, x0, y0, x1, y1, size, color, shape="square",
                 axes=""):
    """Paint a brush stroke between two points, mirrored."""
    painted = 0
    centers = set()
    seen = set()
    for x, y in _bresenham(int(x0), int(y0), int(x1), int(y1)):
        for mx, my in mirror_points(x, y, bitmap.width, bitmap.height, axes):
            if (mx, my) in centers:
                continue
            centers.add((mx, my))
            painted += paint_dab(bitmap, mask, mx, my, size, color, shape,
                                 seen=seen)
    return painted


def line_points(x0, y0, x1, y1):
    """The pixels of a straight stroke, for effects that follow a drag."""
    return _bresenham(int(x0), int(y0), int(x1), int(y1))


def paint_points(bitmap, mask, points, color, axes=""):
    """Paint arbitrary pixels, mirrored and inside the mask."""
    painted = 0
    seen = set()
    for x, y in points:
        for mx, my in mirror_points(x, y, bitmap.width, bitmap.height, axes):
            if (mx, my) in seen:
                continue
            seen.add((mx, my))
            painted += _plot(bitmap, mask, mx, my, color)
    return painted


def paint_mask(bitmap, mask, region, color):
    """Paint `color` into the pixels a byte mask selects."""
    painted = 0
    width = bitmap.width
    for index, value in enumerate(region):
        if not value:
            continue
        painted += _plot(bitmap, mask, index % width, index // width, color)
    return painted


CURVE_DIRECTIONS = ("left", "right", "alternate")


def curve_points(x0, y0, x1, y1, bend=30.0, vectors=1, direction="left"):
    """A chain of quadratic arcs from (x0, y0) to (x1, y1).

    `bend` is each arc's apex offset from its stretch of the chord, as a
    percentage of half the chord length. `vectors` splits the chord into that
    many arcs, so 2 makes an S-wave and more make a ripple. `direction` picks
    the side each arc bulges to: `left` or `right` of the drag, or `alternate`
    to flip sides on every vector. A negative `bend` flips all of them.

    The arc through (x0, y0) and (x1, y1) with `bend=0` is a straight line;
    `bend=100` reaches half the chord out, because a quadratic only ever gets
    half way to its control point.
    """
    dx = float(x1) - float(x0)
    dy = float(y1) - float(y0)
    length = math.hypot(dx, dy)
    if length == 0:
        return [(int(x0), int(y0))]
    try:
        vectors = int(vectors)
    except (TypeError, ValueError):
        vectors = 1
    vectors = max(1, min(8, vectors))
    side = str(direction).lower()
    if side not in CURVE_DIRECTIONS:
        side = "left"
    offset = (float(bend) / 100.0) * length
    normal_x = -dy / length
    normal_y = dx / length
    points = []
    for index in range(vectors):
        start_t = index / float(vectors)
        end_t = (index + 1) / float(vectors)
        start_x = x0 + dx * start_t
        start_y = y0 + dy * start_t
        end_x = x0 + dx * end_t
        end_y = y0 + dy * end_t
        if side == "right":
            sign = -1.0
        elif side == "alternate":
            sign = 1.0 if index % 2 == 0 else -1.0
        else:
            sign = 1.0
        middle_t = (start_t + end_t) / 2.0
        control_x = x0 + dx * middle_t + normal_x * offset * sign
        control_y = y0 + dy * middle_t + normal_y * offset * sign
        steps = max(6, int(length / vectors * 2))
        for step in range(steps + 1):
            t = step / float(steps)
            rest = 1.0 - t
            x = (rest * rest * start_x + 2 * rest * t * control_x
                 + t * t * end_x)
            y = (rest * rest * start_y + 2 * rest * t * control_y
                 + t * t * end_y)
            point = (int(round(x)), int(round(y)))
            if not points or points[-1] != point:
                points.append(point)
    return points


def paint_curve(bitmap, mask, x0, y0, x1, y1, size, color, bend=30.0,
                shape="square", axes="", vectors=1, direction="left"):
    """Paint a curved line between two points, mirrored and with a width."""
    painted = 0
    pairs = [(x0, y0, x1, y1)]
    width, height = bitmap.width, bitmap.height
    if "x" in axes:
        pairs.append((width - 1 - x0, y0, width - 1 - x1, y1))
    if "y" in axes:
        pairs.append((x0, height - 1 - y0, x1, height - 1 - y1))
    if "x" in axes and "y" in axes:
        pairs.append((width - 1 - x0, height - 1 - y0,
                      width - 1 - x1, height - 1 - y1))
    centers = set()
    seen = set()
    for ax0, ay0, ax1, ay1 in pairs:
        for x, y in curve_points(ax0, ay0, ax1, ay1, bend, vectors,
                                 direction):
            if (x, y) in centers:
                continue
            centers.add((x, y))
            painted += paint_dab(bitmap, mask, x, y, size, color, shape,
                                 seen=seen)
    return painted


def stamp_text(bitmap, mask, x, y, text, color, scale=1, spacing=1,
               axes=""):
    """Stamp one line of text with its top-left corner at (x, y)."""
    points = [(x + dx, y + dy) for dx, dy in text_points(text, scale, spacing)]
    return paint_points(bitmap, mask, points, color, axes)


def paint_shape(bitmap, mask, kind, x0, y0, x1, y1, size, color,
                fill=False, shape="square", axes=""):
    """Paint a line, rectangle or ellipse, mirrored and with a brush width."""
    painted = 0
    pairs = [(x0, y0, x1, y1)]
    width, height = bitmap.width, bitmap.height
    if "x" in axes:
        pairs.append((width - 1 - x0, y0, width - 1 - x1, y1))
    if "y" in axes:
        pairs.append((x0, height - 1 - y0, x1, height - 1 - y1))
    if "x" in axes and "y" in axes:
        pairs.append((width - 1 - x0, height - 1 - y0,
                      width - 1 - x1, height - 1 - y1))
    centers = set()
    seen = set()
    for ax0, ay0, ax1, ay1 in pairs:
        for x, y in shape_points(kind, ax0, ay0, ax1, ay1, fill,
                                 bounds=(width, height)):
            if (x, y) in centers:
                continue
            centers.add((x, y))
            painted += paint_dab(bitmap, mask, x, y, size, color, shape,
                                 seen=seen)
    return painted


GRADIENT_SPREADS = ("clamp", "repeat", "reflect")


def _spread_position(value, spread):
    """Fold a raw gradient position into 0..1 for the chosen spread."""
    if spread == "repeat":
        return float(value) - math.floor(float(value))
    if spread == "reflect":
        cycle = float(value) % 2.0
        return cycle if cycle <= 1.0 else 2.0 - cycle
    return clamp(value, 0.0, 1.0)


def _gradient_color(stops, position):
    """The colour at `position` (0..1) along a list of RGBA stops."""
    if len(stops) == 1:
        return tuple(stops[0])
    span = position * (len(stops) - 1)
    index = int(span)
    if index >= len(stops) - 1:
        return tuple(stops[-1])
    fraction = span - index
    first = stops[index]
    second = stops[index + 1]
    return tuple(int(first[channel]
                     + (second[channel] - first[channel]) * fraction + 0.5)
                 for channel in range(4))


def gradient_fill(bitmap, mask, x0, y0, x1, y1, start, end, dither="off",
                  pattern=None, scale=1, bias=0.0, strength=100,
                  serpentine=False, colors=None, radial=False,
                  spread="clamp"):
    """Gradient from `start` to `end`, or through a list of palette stops.

    `colors` replaces the two-colour ramp with evenly spaced RGBA stops, so a
    whole palette can drive one fill. With `radial` the position is the
    distance from the drag start instead of the projection along the drag.
    `spread` clamps the ramp outside the stroke, repeats it or reflects it.

    With a `dither` mode a two-colour linear gradient is reduced to those two
    colours with a Bayer matrix, Floyd-Steinberg error diffusion or a custom
    pattern block instead of interpolating every pixel. Multi-stop, radial and
    folded ramps stay smooth because dithering is defined over one axis.
    """
    dx = int(x1) - int(x0)
    dy = int(y1) - int(y0)
    denominator = dx * dx + dy * dy
    if denominator == 0:
        raise HexError("drag the gradient tool to set a direction")
    stops = [tuple(color) for color in (colors or (start, end))]
    if not stops:
        raise HexError("the gradient needs at least one colour")
    spread = str(spread or "clamp").lower()
    if spread not in GRADIENT_SPREADS:
        spread = "clamp"
    if (dither and dither != "off" and len(stops) == 2 and not radial
            and spread == "clamp"):
        return apply_gradient_dither(bitmap, mask, x0, y0, x1, y1, start,
                                     end, dither, pattern, scale, bias,
                                     strength, serpentine)
    radius = math.hypot(dx, dy)
    painted = 0
    for y in range(bitmap.height):
        row = y * bitmap.width
        for x in range(bitmap.width):
            if mask is not None and mask[row + x] == 0:
                continue
            if radial:
                position = math.hypot(x - x0, y - y0) / radius
            else:
                position = ((x - x0) * dx + (y - y0) * dy) / float(denominator)
            position = _spread_position(position, spread)
            color = _gradient_color(stops, position)
            base = (row + x) * 4
            bitmap.data[base:base + 4] = bytes(color)
            painted += 1
    return painted
