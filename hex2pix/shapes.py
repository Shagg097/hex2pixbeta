"""Pixel-art drawing: mirrored dabs, shape tools and gradients.

Aseprite-inspired behaviour on top of the byte-mask model: a brush can be a
square or a circle, strokes can be mirrored across the horizontal and/or
vertical centre line, shapes are drawn between two points (outline or filled),
and a linear gradient interpolates between the foreground and background
colour inside the mask.

Pure Python, no tkinter, so the geometry is unit-testable.
"""

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


def shape_points(kind, x0, y0, x1, y1, fill=False):
    """The pixels a line, rectangle or ellipse covers between two points."""
    kind = kind if kind in SHAPE_KINDS else "rect"
    if kind == "line":
        return _bresenham(x0, y0, x1, y1)
    points = []
    if kind == "rect":
        xa, xb = sorted((int(x0), int(x1)))
        ya, yb = sorted((int(y0), int(y1)))
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
            for x in range(first, last + 1):
                points.append((x, y))
        return points
    # Outline: the flat caps plus a joined side on each row. Connecting the
    # rows with lines keeps a flat or very wide ellipse from leaving single
    # pixels floating apart from the rest of the ring.
    for index, (y, first, last) in enumerate(rows):
        if index == 0 or index == len(rows) - 1:
            for x in range(first, last + 1):
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
        for x, y in shape_points(kind, ax0, ay0, ax1, ay1, fill):
            if (x, y) in centers:
                continue
            centers.add((x, y))
            painted += paint_dab(bitmap, mask, x, y, size, color, shape,
                                 seen=seen)
    return painted


def gradient_fill(bitmap, mask, x0, y0, x1, y1, start, end):
    """Linear gradient from `start` to `end` along the stroke direction."""
    dx = int(x1) - int(x0)
    dy = int(y1) - int(y0)
    denominator = dx * dx + dy * dy
    if denominator == 0:
        raise HexError("drag the gradient tool to set a direction")
    painted = 0
    for y in range(bitmap.height):
        row = y * bitmap.width
        for x in range(bitmap.width):
            if mask is not None and mask[row + x] == 0:
                continue
            t = ((x - x0) * dx + (y - y0) * dy) / float(denominator)
            t = clamp(t, 0.0, 1.0)
            base = (row + x) * 4
            for channel in range(4):
                a = start[channel]
                b = end[channel]
                value = int(a + (b - a) * t + 0.5)
                bitmap.data[base + channel] = clamp(value, 0, 255)
            painted += 1
    return painted
