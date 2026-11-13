"""Pixel-art drawing: mirrored dabs, shape tools and gradients.

Aseprite-inspired behaviour on top of the byte-mask model: a brush can be a
square or a circle, strokes can be mirrored across the horizontal and/or
vertical centre line, shapes are drawn between two points (outline or filled),
and a linear gradient interpolates between the foreground and background
colour inside the mask.

Pure Python, no tkinter, so the geometry is unit-testable.
"""

import math
from functools import lru_cache

from .bitmapfont import text_points
from .dither import apply_gradient_dither
from .model import HexError, clamp

POLYGON_KINDS = ("triangle", "diamond", "star", "arrow")
SHAPE_KINDS = ("line", "rect", "ellipse") + POLYGON_KINDS
ARROW_HEAD_SHAPES = ("triangle", "barbed")


def shape_color(foreground, background, use_background=False):
    """The stroke colour for line and curve tools.

    The Background toggle swaps the foreground colour for the background one,
    so a line can carve into existing artwork instead of adding to it. Preset
    filled shapes keep their outline in the foreground and use the background
    colour for the interior only.
    """
    return background if use_background else foreground


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


DEFAULT_CUSTOM_BRUSH = (
    "000010000",
    "000010000",
    "000010000",
    "000010000",
    "111111111",
    "000010000",
    "000010000",
    "000010000",
    "000010000",
)

_custom_brush = None


def set_custom_brush(rows):
    """Store the custom tip as rows of 0/1 cells; `None` forgets it."""
    global _custom_brush
    if not rows:
        _custom_brush = None
        return None
    clean = []
    for row in rows:
        if not row:
            continue
        if isinstance(row, str):
            clean.append([1 if char == "1" else 0 for char in row])
        else:
            clean.append([1 if cell else 0 for cell in row])
    if not clean:
        _custom_brush = None
        return None
    width = max(len(row) for row in clean)
    _custom_brush = tuple(tuple(row + [0] * (width - len(row)))
                          for row in clean)
    return _custom_brush


def custom_brush():
    """The custom tip, falling back to a plus so Custom always paints."""
    if _custom_brush:
        return _custom_brush
    return tuple(tuple(1 if cell == "1" else 0 for cell in row)
                 for row in DEFAULT_CUSTOM_BRUSH)


def custom_brush_text():
    """The custom tip as `"0110|1001"`, for the settings file."""
    return "|".join("".join(str(cell) for cell in row)
                    for row in custom_brush())


def custom_brush_from_text(text):
    """Restore a tip saved by `custom_brush_text`; None when unreadable."""
    rows = []
    for chunk in str(text or "").split("|"):
        chunk = chunk.strip()
        if chunk:
            rows.append([1 if char == "1" else 0 for char in chunk])
    return set_custom_brush(rows)


def custom_tip_rows(bitmap, max_size=32):
    """Turn a bitmap's visible pixels into custom brush-tip rows.

    A cell is filled when any visible pixel falls inside it. Artwork larger
    than `max_size` is sampled down as a whole, preserving its proportions
    instead of cropping the right or bottom edge.
    """
    max_size = max(1, int(max_size))
    width = getattr(bitmap, "width", 0)
    height = getattr(bitmap, "height", 0)
    if width < 1 or height < 1:
        return []
    if max(width, height) <= max_size:
        columns = width
        rows_count = height
    else:
        scale = max(width, height) / float(max_size)
        columns = max(1, int(width / scale + 0.5))
        rows_count = max(1, int(height / scale + 0.5))
    data = bitmap.data
    rows = []
    for y in range(rows_count):
        top = y * height // rows_count
        bottom = (y + 1) * height // rows_count
        row = []
        for x in range(columns):
            left = x * width // columns
            right = (x + 1) * width // columns
            filled = False
            for py in range(top, bottom):
                base = py * width
                for px in range(left, right):
                    if data[(base + px) * 4 + 3]:
                        filled = True
                        break
                if filled:
                    break
            row.append(1 if filled else 0)
        rows.append(row)
    return rows


def _custom_offsets(size):
    """Scale the custom tip into a `size` x `size` brush box.

    Each box cell takes every grid cell it covers, so a thin tip still shows
    up when the tool's Size is smaller than the tip's own grid. Rectangular
    tips keep their proportions instead of being stretched into a square.
    """
    mask = custom_brush()
    rows = len(mask)
    columns = max(len(row) for row in mask) if rows else 0
    if not rows or not columns:
        return []
    span = max(rows, columns)
    box_rows = max(1, min(size, (size * rows + span // 2) // span))
    box_columns = max(1, min(size, (size * columns + span // 2) // span))
    left = -(box_columns // 2)
    top = -(box_rows // 2)
    offsets = []
    for oy in range(box_rows):
        y0 = oy * rows // box_rows
        y1 = min(rows, max(y0 + 1, (oy + 1) * rows // box_rows))
        for ox in range(box_columns):
            x0 = ox * columns // box_columns
            x1 = min(columns, max(x0 + 1,
                                  (ox + 1) * columns // box_columns))
            if any(x < len(mask[y]) and mask[y][x]
                   for y in range(y0, y1) for x in range(x0, x1)):
                offsets.append((left + ox, top + oy))
    return offsets


@lru_cache(maxsize=512)
def _tip_offsets(size, shape):
    """The square or circle tip, built once per size and shape.

    A stroke asks for the same tip on every dab, and a large tip is hundreds
    of offsets, so rebuilding it per dab was pure overhead.
    """
    half = size // 2
    offsets = []
    for dy in range(-half, size - half):
        for dx in range(-half, size - half):
            if shape == "circle" and size > 2:
                if dx * dx + dy * dy > half * half:
                    continue
            offsets.append((dx, dy))
    return tuple(offsets)


def brush_offsets(size, shape="square"):
    """Pixel offsets relative to the brush centre."""
    size = max(1, int(size))
    if shape == "custom":
        offsets = _custom_offsets(size)
        if offsets:
            return offsets
        shape = "square"
    if shape not in ("square", "circle"):
        shape = "square"
    return list(_tip_offsets(size, shape))


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


def _arrow_vertices(x0, y0, x1, y1, head_shape="triangle", body_size=50.0,
                    head_size=45.0):
    """Corner points of a right-pointing arrow inside the drag box.

    `body_size` is the shaft thickness and `head_size` is the head length, both
    percentages of the box. A barbed head cuts a V-notch into the back of the
    head; a triangle head keeps the straight back.
    """
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    cy = (ya + yb) / 2.0
    width = float(xb - xa)
    height = float(yb - ya)
    try:
        body = float(body_size)
    except (TypeError, ValueError):
        body = 50.0
    body = max(10.0, min(90.0, body)) / 100.0
    try:
        head = float(head_size)
    except (TypeError, ValueError):
        head = 45.0
    head = max(10.0, min(90.0, head)) / 100.0
    shoulder = cy - height * body / 2.0
    lower = cy + height * body / 2.0
    head_start = max(float(xa), xb - width * head)
    shape = str(head_shape or "").lower()
    if shape not in ARROW_HEAD_SHAPES:
        shape = "triangle"
    notch = 0.0
    if shape == "barbed":
        notch = min(width * 0.25, (xb - head_start) * 0.35)
    neck = min(float(xb), head_start + notch)
    return [(float(xa), shoulder), (neck, shoulder),
            (head_start, float(ya)), (float(xb), cy),
            (head_start, float(yb)), (neck, lower),
            (float(xa), lower)]


def _polygon_vertices(kind, x0, y0, x1, y1, points=None, angle=0.0,
                      depth=None, head_shape="triangle", body_size=50.0,
                      head_size=45.0):
    """Corner points of a preset polygon inside the drag box.

    Triangle, diamond and star share `points`, `angle` and `depth`. `points`
    counts the outer points, `angle` rotates them clockwise in degrees, and
    `depth` is the inner radius as a percentage of the outer radius. At 100%
    depth the result is a regular polygon; lower values pull alternate
    vertices inward into star points. The arrow uses its own head, body and
    head-size options.
    """
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    cx = (xa + xb) / 2.0
    cy = (ya + yb) / 2.0
    width = float(xb - xa)
    height = float(yb - ya)
    if kind == "arrow":
        return _arrow_vertices(x0, y0, x1, y1, head_shape, body_size,
                               head_size)
    default_count = {"triangle": 3, "diamond": 4}.get(kind, 5)
    try:
        count = int(points)
    except (TypeError, ValueError):
        count = default_count
    count = max(3, min(24, count))
    try:
        spin = math.radians(float(angle))
    except (TypeError, ValueError):
        spin = 0.0
    if depth is None:
        depth = 100.0 if kind in ("triangle", "diamond") else 38.2
    try:
        depth = max(1.0, min(100.0, float(depth))) / 100.0
    except (TypeError, ValueError):
        depth = 1.0 if kind in ("triangle", "diamond") else 0.382
    regular = depth >= 1.0
    step = (2.0 if regular else 1.0) * math.pi / count
    vertices = []
    for index in range(count if regular else count * 2):
        theta = -math.pi / 2.0 + spin + index * step
        reach = 1.0 if regular or index % 2 == 0 else depth
        vertices.append((cx + math.cos(theta) * width / 2.0 * reach,
                         cy + math.sin(theta) * height / 2.0 * reach))
    return vertices


def _polygon_rows(vertices):
    """Row extents (y, first_x, last_x) of a filled polygon (even-odd)."""
    tops = [point[1] for point in vertices]
    rows = []
    for y in range(int(math.floor(min(tops))), int(math.ceil(max(tops))) + 1):
        crossings = []
        count = len(vertices)
        for index in range(count):
            ax, ay = vertices[index]
            bx, by = vertices[(index + 1) % count]
            if ay == by:
                continue
            low, high = sorted((ay, by))
            if low <= y < high:
                crossings.append(ax + (y - ay) * (bx - ax) / (by - ay))
        crossings.sort()
        for index in range(0, len(crossings) - 1, 2):
            first = int(math.ceil(crossings[index] - 0.5))
            last = int(math.floor(crossings[index + 1] - 0.5))
            if last >= first:
                rows.append((y, first, last))
    return rows


def _polygon_points(kind, x0, y0, x1, y1, fill, bounds, points=None,
                    angle=0.0, depth=None, head_shape="triangle",
                    body_size=50.0, head_size=45.0):
    vertices = _polygon_vertices(kind, x0, y0, x1, y1, points, angle, depth,
                                 head_shape, body_size, head_size)
    points = []
    if fill:
        for y, first, last in _polygon_rows(vertices):
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
    corners = [(int(round(x)), int(round(y))) for x, y in vertices]
    for index, corner in enumerate(corners):
        following = corners[(index + 1) % len(corners)]
        points.extend(_bresenham(corner[0], corner[1],
                                 following[0], following[1]))
    if bounds is not None:
        points = [(x, y) for x, y in points
                  if 0 <= x < bounds[0] and 0 <= y < bounds[1]]
    return points


def shape_points(kind, x0, y0, x1, y1, fill=False, bounds=None,
                 points=None, angle=0.0, depth=None, head_shape="triangle",
                 body_size=50.0, head_size=45.0):
    """The pixels a line, rectangle, ellipse or preset polygon covers.

    The endpoints may sit outside the canvas, so a drag can run past the edge
    and keep the shape it describes. `bounds` is the canvas (width, height);
    it only limits how much of the shape is walked, not its geometry, so an
    ellipse whose box starts off-canvas keeps its curvature. Triangle,
    diamond and star use the polygon options; arrow uses the head options;
    other shapes ignore both sets.
    """
    kind = kind if kind in SHAPE_KINDS else "rect"
    if kind == "line":
        return _bresenham(x0, y0, x1, y1)
    if kind in POLYGON_KINDS:
        return _polygon_points(kind, x0, y0, x1, y1, fill, bounds, points,
                               angle, depth, head_shape, body_size,
                               head_size)
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


def stamp_pattern(bitmap, mask, x, y, cells, axes=""):
    """Stamp a pattern grid centred on (x, y) in the pattern's own colours.

    `None` cells leave the artwork alone, so a pattern can be a sparse tip.
    """
    height = len(cells)
    width = max([len(row) for row in cells] or [0])
    if not height or not width:
        return 0
    half_x, half_y = width // 2, height // 2
    painted = 0
    for mx, my in mirror_points(x, y, bitmap.width, bitmap.height, axes):
        for py in range(height):
            row = cells[py]
            for px in range(min(width, len(row))):
                color = row[px]
                if color is None:
                    continue
                painted += _plot(bitmap, mask, mx + px - half_x,
                                 my + py - half_y, color)
    return painted


def paint_pattern_dab(bitmap, mask, x, y, size, cells, shape="square",
                      axes="", fallback=None, seen=None):
    """Paint a brush dab using the Pattern grid for the output colours.

    The brush's own size and tip choose the pixels; the pattern is sampled by
    canvas position, so its colours continue across dabs. `None` cells use
    `fallback` instead of punching holes, which keeps the tip shape intact.
    """
    height = len(cells)
    width = max([len(row) for row in cells] or [0])
    if not height or not width:
        return 0
    if seen is None:
        seen = set()
    painted = 0
    for mx, my in mirror_points(x, y, bitmap.width, bitmap.height, axes):
        for dx, dy in brush_offsets(size, shape):
            px, py = mx + dx, my + dy
            if (px, py) in seen:
                continue
            seen.add((px, py))
            row = cells[py % height]
            column = px % width
            color = row[column] if column < len(row) else None
            if color is None:
                color = fallback
            if color is None:
                continue
            painted += _plot(bitmap, mask, px, py, color)
    return painted


def paint_pattern_stroke(bitmap, mask, x0, y0, x1, y1, size, cells,
                         shape="square", axes="", fallback=None):
    """Paint a line of pattern-coloured dabs with the brush's own tip."""
    centers = set()
    seen = set()
    painted = 0
    for x, y in _bresenham(int(x0), int(y0), int(x1), int(y1)):
        for mx, my in mirror_points(x, y, bitmap.width, bitmap.height, axes):
            if (mx, my) in centers:
                continue
            centers.add((mx, my))
            painted += paint_pattern_dab(
                bitmap, mask, mx, my, size, cells, shape, "", fallback,
                seen=seen)
    return painted


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


def _fill_mask_holes(filled, width, left, top, right, bottom):
    """Fill transparent pockets enclosed by a mask inside its bounding box."""
    box_width = right - left + 1
    box_height = bottom - top + 1
    outside = bytearray(box_width * box_height)
    stack = []
    for x in range(left, right + 1):
        stack.append((x, top))
        stack.append((x, bottom))
    for y in range(top, bottom + 1):
        stack.append((left, y))
        stack.append((right, y))
    while stack:
        x, y = stack.pop()
        if not (left <= x <= right and top <= y <= bottom):
            continue
        box_index = (y - top) * box_width + (x - left)
        if outside[box_index] or filled[y * width + x]:
            continue
        outside[box_index] = 1
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            index = y * width + x
            box_index = (y - top) * box_width + (x - left)
            if not filled[index] and not outside[box_index]:
                filled[index] = 1


def paint_shape(bitmap, mask, kind, x0, y0, x1, y1, size, color,
                fill=False, shape="square", axes="", points=None,
                angle=0.0, depth=None, head_shape="triangle",
                body_size=50.0, head_size=45.0, fill_color=None):
    """Paint a shape, mirrored and with a brush width.

    With `fill` and a `fill_color`, the shape and its outline are rasterized
    into one solid mask, transparent pockets are closed, and the interior is
    painted with `fill_color`. The outline is then painted with `color`, so
    the fill cannot leak through it or leave holes.
    """
    pairs = [(x0, y0, x1, y1)]
    width, height = bitmap.width, bitmap.height
    if "x" in axes:
        pairs.append((width - 1 - x0, y0, width - 1 - x1, y1))
    if "y" in axes:
        pairs.append((x0, height - 1 - y0, x1, height - 1 - y1))
    if "x" in axes and "y" in axes:
        pairs.append((width - 1 - x0, height - 1 - y0,
                      width - 1 - x1, height - 1 - y1))
    painted = 0
    if fill and fill_color is not None:
        filled = bytearray(width * height)
        left, top = width, height
        right = bottom = -1
        for pass_fill in (True, False):
            for ax0, ay0, ax1, ay1 in pairs:
                for x, y in shape_points(kind, ax0, ay0, ax1, ay1, pass_fill,
                                         bounds=(width, height),
                                         points=points, angle=angle,
                                         depth=depth, head_shape=head_shape,
                                         body_size=body_size,
                                         head_size=head_size):
                    if not (0 <= x < width and 0 <= y < height):
                        continue
                    filled[y * width + x] = 1
                    left = min(left, x)
                    top = min(top, y)
                    right = max(right, x)
                    bottom = max(bottom, y)
        if right < left:
            return 0
        _fill_mask_holes(filled, width, left, top, right, bottom)
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                index = y * width + x
                if filled[index]:
                    painted += _plot(bitmap, mask, x, y, fill_color)
        seen = set()
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                index = y * width + x
                if not filled[index]:
                    continue
                if (x > 0 and x < width - 1 and y > 0 and y < height - 1
                        and filled[index - 1] and filled[index + 1]
                        and filled[index - width]
                        and filled[index + width]):
                    continue
                painted += _plot(bitmap, mask, x, y, color)
                painted += paint_dab(bitmap, mask, x, y, size, color, shape,
                                     seen=seen)
        for x, y in seen:
            if 0 <= x < width and 0 <= y < height:
                filled[y * width + x] = 1
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)
        pad = max(1, size)
        pad_left = max(0, left - pad)
        pad_top = max(0, top - pad)
        pad_right = min(width - 1, right + pad)
        pad_bottom = min(height - 1, bottom + pad)
        before = filled[:]
        _fill_mask_holes(filled, width, pad_left, pad_top, pad_right,
                         pad_bottom)
        for y in range(pad_top, pad_bottom + 1):
            for x in range(pad_left, pad_right + 1):
                index = y * width + x
                if filled[index] and not before[index]:
                    painted += _plot(bitmap, mask, x, y, fill_color)
        return painted
    passes = [(fill, color)]
    for pass_fill, pass_color in passes:
        centers = set()
        seen = set()
        for ax0, ay0, ax1, ay1 in pairs:
            for x, y in shape_points(kind, ax0, ay0, ax1, ay1, pass_fill,
                                     bounds=(width, height),
                                     points=points, angle=angle, depth=depth,
                                     head_shape=head_shape,
                                     body_size=body_size,
                                     head_size=head_size):
                if (x, y) in centers:
                    continue
                centers.add((x, y))
                painted += paint_dab(bitmap, mask, x, y, size, pass_color,
                                     shape, seen=seen)
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
