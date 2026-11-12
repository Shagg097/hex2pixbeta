"""hex2pix model: hex colors, pixel blocks, bitmaps, byte selections, edits.

Pure Python with no tkinter or Pillow imports, so it can be unit-tested on its
own and reused by the GUI.

Coordinate model: a `Bitmap` holds `width * height * 4` RGBA bytes, row-major,
top row first. A selection is a byte per pixel, 0 or 255, so selections are
literally selections of pixel data, and the tools that build them are byte
tools (contiguous mask, polygon mask, similarity mask).
"""

import math

# A pixel the block parser was told to skip ('.' / '-' / '_').
# Compared by identity, never by value.
SKIP = object()

OPS = ("add", "subtract", "multiply", "xor", "nand")
OP_LABELS = {
    "add": "Add",
    "subtract": "Subtract",
    "multiply": "Multiply",
    "xor": "Xor",
    "nand": "Nand",
}
OP_BY_LABEL = {label: op for op, label in OP_LABELS.items()}

# A layer reaches the stack below through three separate option groups.
# `blend` shapes the layer against the stack below first, `op` combines that
# result with the below stack and `gate` is the final bitwise pass; "none"
# switches a group off.
LAYER_OPS = ("none", "add", "subtract", "multiply", "divide")
LAYER_OP_LABELS = {
    "none": "None",
    "add": "Add",
    "subtract": "Subtract",
    "multiply": "Multiply",
    "divide": "Divide",
}
LAYER_OP_BY_LABEL = {label: op for op, label in LAYER_OP_LABELS.items()}

LAYER_GATES = ("none", "and", "or", "xor", "not", "nand", "nor", "xnor")
LAYER_GATE_LABELS = {
    "none": "None",
    "and": "And",
    "or": "Or",
    "xor": "Xor",
    "not": "Not",
    "nand": "Nand",
    "nor": "Nor",
    "xnor": "Xnor",
}
LAYER_GATE_BY_LABEL = {label: gate for gate, label
                       in LAYER_GATE_LABELS.items()}

LAYER_BLENDS = ("none", "screen", "overlay", "darken", "lighten",
                "difference")
LAYER_BLEND_LABELS = {
    "none": "None",
    "screen": "Screen",
    "overlay": "Overlay",
    "darken": "Darken",
    "lighten": "Lighten",
    "difference": "Difference",
}
LAYER_BLEND_BY_LABEL = {label: blend for blend, label
                        in LAYER_BLEND_LABELS.items()}

HEX_DIGITS = set("0123456789abcdefABCDEF")

MATTE_WHITE = (255, 255, 255)


class HexError(ValueError):
    """Raised for invalid hex colors, blocks or geometry."""


# --------------------------------------------------------------- small helpers
def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def round_half_up(value):
    return int(math.floor(value + 0.5))


def mod(n, m):
    return n % m


def band8(a, b):
    return (a & b) & 255


def bxor8(a, b):
    return (a ^ b) & 255


def bnot8(a):
    return 255 - (a % 256)


def combine(a, b, op, strength=255, wrap=False):
    """Combine channel `a` with channel `b` (both 0..255).

    `strength` 0 leaves the pixel untouched, 255 applies the full result.
    `wrap` wraps overflow instead of clamping it.
    """
    if op == "normal":
        raw = b
    elif op == "add":
        raw = a + b
    elif op == "subtract":
        raw = a - b
    elif op == "multiply":
        raw = (a * b) // 255
    elif op == "divide":
        raw = (a * 255) // b if b else (255 if a else 0)
    elif op == "xor":
        raw = bxor8(a, b)
    elif op == "nand":
        raw = bnot8(band8(a, b))
    elif op == "screen":
        raw = 255 - (255 - a) * (255 - b) / 255.0
    elif op == "overlay":
        if a < 128:
            raw = 2.0 * a * b / 255.0
        else:
            raw = 255 - 2.0 * (255 - a) * (255 - b) / 255.0
    elif op == "darken":
        raw = min(a, b)
    elif op == "lighten":
        raw = max(a, b)
    elif op == "difference":
        raw = abs(a - b)
    else:
        raise HexError("unknown operation: %s" % op)
    raw = raw % 256 if wrap else clamp(raw, 0, 255)
    return clamp(round_half_up(a + (raw - a) * (strength / 255.0)), 0, 255)


def gate_value(below, above, gate):
    """Bitwise gate of two 0..255 channel values."""
    if gate == "and":
        return band8(below, above)
    if gate == "or":
        return (below | above) & 255
    if gate == "xor":
        return bxor8(below, above)
    if gate == "not":
        return bnot8(above)
    if gate == "nand":
        return bnot8(band8(below, above))
    if gate == "nor":
        return bnot8((below | above) & 255)
    if gate == "xnor":
        return bnot8(bxor8(below, above))
    raise HexError("unknown gate: %s" % gate)


def combine_layer(below, above, op="none", gate="none", blend="none",
                  strength=255, wrap=False):
    """Combine one channel through a layer's three option groups.

    The groups apply in order: the blend mode first, then the arithmetic
    operation, then the bitwise gate, each working on the result so far and
    the stack below.  A blend applied last could only pick the backdrop and
    hide the layer (Add followed by Darken, for example), so the blend runs
    first and the operation still reaches the stack below.  "none" leaves a
    group out, and the layer's alpha is the strength of the final mix.
    """
    value = above
    if blend not in ("none", "", None):
        value = combine(below, value, blend, 255, wrap)
    if op not in ("none", "", None):
        value = combine(below, value, op, 255, wrap)
    if gate not in ("none", "", None):
        value = gate_value(below, value, gate)
    return combine(below, value, "normal", strength, wrap)


# ------------------------------------------------------------------ hex colors
def strip_hex(text):
    s = str(text or "")
    s = "".join(s.split())
    while s.startswith("#"):
        s = s[1:]
    if s[:2].lower() == "0x":
        s = s[2:]
    return s


def parse_hex(text):
    """Parse '#RGB', '#RGBA', '#RRGGBB' or '#RRGGBBAA' into (r, g, b, a)."""
    h = strip_hex(text)
    if not h:
        raise HexError("empty hex value")
    if any(c not in HEX_DIGITS for c in h):
        raise HexError("not a hexadecimal value: %r" % (text,))
    n = len(h)
    if n == 3:
        return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), 255)
    if n == 4:
        return (int(h[0] * 2, 16), int(h[1] * 2, 16),
                int(h[2] * 2, 16), int(h[3] * 2, 16))
    if n == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    if n == 8:
        return (int(h[0:2], 16), int(h[2:4], 16),
                int(h[4:6], 16), int(h[6:8], 16))
    raise HexError("expected 3, 4, 6 or 8 hex digits, got %d" % n)


def hex_str(r, g, b, a=255):
    """Format a color as RRGGBB, or RRGGBBAA when it is not fully opaque."""
    if a is None or a >= 255:
        return "%02X%02X%02X" % (r, g, b)
    return "%02X%02X%02X%02X" % (r, g, b, a)


def hex_code(color, order="RGB"):
    """Format an RGBA color in `order` (RGB, BGR and the other permutations).

    Alpha follows the colour bytes, and is only shown when it is not fully
    opaque, so the code keeps the same length as `hex_str`.
    """
    order = str(order or "RGB").upper()
    if len(order) != 3 or sorted(order) != ["B", "G", "R"]:
        order = "RGB"
    values = {"R": color[0], "G": color[1], "B": color[2]}
    text = "".join("%02X" % values[letter] for letter in order)
    if len(color) > 3 and color[3] < 255:
        text += "%02X" % color[3]
    return text


def split_lines(text):
    """Split text into lines, treating ';' and '|' as line breaks too."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for sep in (";", "|"):
        s = s.replace(sep, "\n")
    return s.split("\n")


# ----------------------------------------------------------------- hex blocks
class Block(object):
    """A parsed hex block: rows of (r, g, b, a) tuples or SKIP."""

    __slots__ = ("width", "height", "rows")

    def __init__(self, width, height, rows):
        self.width = width
        self.height = height
        self.rows = rows

    def __repr__(self):
        return "Block(%dx%d)" % (self.width, self.height)


def parse_block(text):
    """Parse hex pixel data into a Block.

    Rows are separated by newlines, ';' or '|'. Pixels are separated by
    spaces, tabs or commas and may be 3/4/6/8 hex digits, optionally with a
    '#' or '0x' prefix. '.', '-' or '_' skips a pixel. A run longer than 8
    digits is split into 3-byte pixels (or 4-byte when it does not divide
    by 3).
    """
    rows = []
    width = 0
    for raw in split_lines(text):
        line = raw.split("//", 1)[0].replace(",", " ").strip()
        if not line:
            continue
        row = []
        for token in line.split():
            low = token.lower()
            if low in (".", "-", "_", ".."):
                row.append(SKIP)
                continue
            h = strip_hex(token)
            if not h or any(c not in HEX_DIGITS for c in h):
                raise HexError(
                    "'%s' is not hex data (use hex digits or '.' to skip)" % token)
            if len(h) <= 8:
                row.append(parse_hex(h))
            else:
                if len(h) % 6 == 0:
                    chunk = 6
                elif len(h) % 8 == 0:
                    chunk = 8
                else:
                    raise HexError(
                        "a run of %d hex digits does not split into 3- or "
                        "4-byte pixels" % len(h))
                for i in range(0, len(h), chunk):
                    row.append(parse_hex(h[i:i + chunk]))
        if row:
            rows.append(row)
            width = max(width, len(row))
    if not rows:
        raise HexError("no hex pixel data found")
    for row in rows:
        row.extend([SKIP] * (width - len(row)))
    return Block(width, len(rows), rows)


def transform_block(block, scale=1, flip_x=False, flip_y=False, rotate=0):
    """Return a new block rotated, flipped and scaled. The input is untouched."""
    w, h = block.width, block.height
    rows = []
    for y in range(h):
        src = block.rows[y] if y < len(block.rows) else []
        rows.append([src[x] if x < len(src) else SKIP for x in range(w)])

    q = mod(int(rotate or 0), 360)
    if q == 90:
        rows = [[rows[h - 1 - x][y] for x in range(h)] for y in range(w)]
        w, h = h, w
    elif q == 180:
        rows = [[rows[h - 1 - y][w - 1 - x] for x in range(w)]
                for y in range(h)]
    elif q == 270:
        rows = [[rows[x][w - 1 - y] for x in range(h)] for y in range(w)]
        w, h = h, w
    elif q != 0:
        raise HexError("rotation must be 0, 90, 180 or 270")

    if flip_x:
        rows = [list(reversed(row)) for row in rows]
    if flip_y:
        rows = list(reversed(rows))

    s = int(scale or 1)
    if s > 1:
        out = []
        for row in rows:
            wide = []
            for pixel in row:
                wide.extend([pixel] * s)
            for _ in range(s):
                out.append(list(wide))
        rows = out
        w, h = w * s, h * s

    return Block(w, h, rows)


def clip(rect, bounds):
    """Clip (x, y, w, h) to another (x, y, w, h)."""
    x1 = max(rect[0], bounds[0])
    y1 = max(rect[1], bounds[1])
    x2 = min(rect[0] + rect[2], bounds[0] + bounds[2])
    y2 = min(rect[1] + rect[3], bounds[1] + bounds[3])
    return (x1, y1, max(0, x2 - x1), max(0, y2 - y1))


def opaque_bounds(bitmap):
    """The bounding box of the bitmap's non-transparent pixels, or None."""
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    left, top = width, height
    right, bottom = -1, -1
    for y in range(height):
        base = y * width * 4
        for x in range(width):
            if data[base + x * 4 + 3]:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                bottom = y
    if right < left or bottom < top:
        return None
    return (left, top, right - left + 1, bottom - top + 1)


def blit(data, rect, block, mode="stamp", origin=None, mirror=False, mask=None):
    """Draw `block` into an RGBA bytearray covering `rect`.

    `rect` is (x, y, w, h) in sprite coordinates and `data` holds w*h*4 RGBA
    bytes for exactly that rectangle. `origin` is the sprite position that
    maps to the block's top-left corner for stamp/tile modes. `mask`, when
    given, is w*h bytes of selection coverage.
    """
    x0, y0, rw, rh = rect
    if rw <= 0 or rh <= 0:
        return data
    bx, by = origin if origin is not None else (x0, y0)
    bw, bh = block.width, block.height
    for yy in range(rh):
        y = y0 + yy
        for xx in range(rw):
            if mask is not None and mask[yy * rw + xx] == 0:
                continue
            x = x0 + xx
            if mode == "tile":
                tx, ty = x - bx, y - by
                sx = mod(tx, bw)
                sy = mod(ty, bh)
                if mirror and mod(tx // bw, 2) == 1:
                    sx = bw - 1 - sx
                if mirror and mod(ty // bh, 2) == 1:
                    sy = bh - 1 - sy
                pixel = block.rows[sy][sx]
            elif mode == "stretch":
                sx = clamp((x - x0) * bw // rw, 0, bw - 1)
                sy = clamp((y - y0) * bh // rh, 0, bh - 1)
                pixel = block.rows[sy][sx]
            else:  # stamp
                sx, sy = x - bx, y - by
                if 0 <= sx < bw and 0 <= sy < bh:
                    pixel = block.rows[sy][sx]
                else:
                    continue
            if pixel is SKIP:
                continue
            i = (yy * rw + xx) * 4
            data[i] = pixel[0]
            data[i + 1] = pixel[1]
            data[i + 2] = pixel[2]
            data[i + 3] = pixel[3]
    return data


def format_block(data, width, height, skip_transparent=True):
    """Render an RGBA bytearray as hex rows, '.' for fully transparent pixels."""
    lines = []
    for y in range(height):
        row = []
        for x in range(width):
            i = (y * width + x) * 4
            r, g, b, a = data[i], data[i + 1], data[i + 2], data[i + 3]
            if skip_transparent and a == 0:
                row.append(".")
            else:
                row.append(hex_str(r, g, b, a))
        lines.append(" ".join(row))
    return "\n".join(lines)


# ------------------------------------------------------------------ HSV colors
def rgb_to_hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    maxv = max(r, g, b)
    minv = min(r, g, b)
    d = maxv - minv
    h = 0.0
    if d > 0:
        if maxv == r:
            h = 60.0 * mod((g - b) / d, 6)
        elif maxv == g:
            h = 60.0 * (((b - r) / d) + 2)
        else:
            h = 60.0 * (((r - g) / d) + 4)
    s = 0.0 if maxv <= 0 else d / maxv
    return h, s, maxv


def hsv_to_rgb(h, s, v):
    h = mod(h, 360)
    c = v * s
    x = c * (1 - abs(mod(h / 60.0, 2) - 1))
    m = v - c
    seg = int(h // 60)
    if seg == 0:
        r, g, b = c, x, 0
    elif seg == 1:
        r, g, b = x, c, 0
    elif seg == 2:
        r, g, b = 0, c, x
    elif seg == 3:
        r, g, b = 0, x, c
    elif seg == 4:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return (clamp(round_half_up((r + m) * 255), 0, 255),
            clamp(round_half_up((g + m) * 255), 0, 255),
            clamp(round_half_up((b + m) * 255), 0, 255))


def hue_shift(r, g, b, degrees):
    h, s, v = rgb_to_hsv(r, g, b)
    return hsv_to_rgb(h + degrees, s, v)


def luminance(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def parse_hex_map(text):
    """Parse `OLD=NEW` lines (also accepts '->') into {(r,g,b): (r,g,b)}."""
    mapping = {}
    for raw in split_lines(text):
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        line = line.replace("->", "=")
        if "=" not in line:
            raise HexError("expected OLD=NEW in mapping line: %s" % line)
        old, new = line.split("=", 1)
        src = parse_hex(old.strip())
        dst = parse_hex(new.strip())
        mapping[(src[0], src[1], src[2])] = (dst[0], dst[1], dst[2])
    return mapping


def rotate_ramp(colors, shift):
    """Map each color in `colors` to the one `shift` steps along the ramp."""
    if not colors:
        return {}
    out = {}
    n = len(colors)
    for i, color in enumerate(colors):
        out[(color[0], color[1], color[2])] = colors[(i + shift) % n]
    return out


def matches(color, target, tolerance):
    return (abs(color[0] - target[0]) <= tolerance
            and abs(color[1] - target[1]) <= tolerance
            and abs(color[2] - target[2]) <= tolerance)


# --------------------------------------------------------------------- bitmap
class Bitmap(object):
    """An RGBA pixel buffer, 4 bytes per pixel, top-left first."""

    __slots__ = ("width", "height", "data")

    def __init__(self, width, height, data=None):
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise HexError("image size must be positive")
        self.width = width
        self.height = height
        size = width * height * 4
        if data is None:
            self.data = bytearray(size)
        else:
            if len(data) != size:
                raise HexError(
                    "expected %d bytes for %dx%d, got %d"
                    % (size, width, height, len(data)))
            self.data = bytearray(data)

    def copy(self):
        return Bitmap(self.width, self.height, self.data)

    def rgba(self, x, y):
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return (0, 0, 0, 0)
        i = (y * self.width + x) * 4
        return (self.data[i], self.data[i + 1], self.data[i + 2], self.data[i + 3])

    def set_rgba(self, x, y, color):
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        i = (y * self.width + x) * 4
        self.data[i] = color[0]
        self.data[i + 1] = color[1]
        self.data[i + 2] = color[2]
        self.data[i + 3] = color[3]

    def clear(self):
        self.data = bytearray(len(self.data))

    def fill(self, color):
        for i in range(0, len(self.data), 4):
            self.data[i] = color[0]
            self.data[i + 1] = color[1]
            self.data[i + 2] = color[2]
            self.data[i + 3] = color[3]

    def rect_rgba(self, x, y, width, height):
        """A new Bitmap of one rectangle of this image (clipped)."""
        rect = clip((x, y, width, height), (0, 0, self.width, self.height))
        rx, ry, rw, rh = rect
        out = bytearray(rw * rh * 4)
        row_bytes = rw * 4
        for yy in range(rh):
            src = ((ry + yy) * self.width + rx) * 4
            out[yy * row_bytes:(yy + 1) * row_bytes] = \
                self.data[src:src + row_bytes]
        return Bitmap(max(1, rw), max(1, rh), out if rw and rh else None)


def flip_horizontal(bitmap):
    out = Bitmap(bitmap.width, bitmap.height)
    w, h = bitmap.width, bitmap.height
    row = w * 4
    for y in range(h):
        src = bitmap.data[y * row:(y + 1) * row]
        flipped = bytearray(row)
        for x in range(w):
            flipped[x * 4:(x + 1) * 4] = src[(w - 1 - x) * 4:(w - x) * 4]
        out.data[y * row:(y + 1) * row] = flipped
    return out


def flip_vertical(bitmap):
    out = Bitmap(bitmap.width, bitmap.height)
    row = bitmap.width * 4
    for y in range(bitmap.height):
        src = ((bitmap.height - 1 - y) * row)
        out.data[y * row:(y + 1) * row] = bitmap.data[src:src + row]
    return out


def rotate90(bitmap, clockwise=True):
    w, h = bitmap.width, bitmap.height
    out = Bitmap(h, w)
    for y in range(h):
        for x in range(w):
            src = (y * w + x) * 4
            if clockwise:
                nx, ny = h - 1 - y, x
            else:
                nx, ny = y, w - 1 - x
            dst = (ny * h + nx) * 4
            out.data[dst:dst + 4] = bitmap.data[src:src + 4]
    return out


def rotate_piece(bitmap, degrees, smooth=False):
    """Rotate a bitmap clockwise by any angle, keeping the whole piece.

    Quarter turns go through the exact `rotate90` path. Other angles sample
    with an inverse rotation about the piece centre, so there is no
    projective solve that can distort thin pieces.

    Hard mode is plain nearest neighbour: every destination pixel reads
    the source pixel under its centre, so the result holds only real source
    colours, never grows thicker than the artwork and cannot scatter stray
    specks. `smooth=True` blends bilinearly on premultiplied colours for
    anti-aliased edges without dark halos. The output is the bounding box of
    the rotated rectangle, so nothing is cropped.
    """
    angle = float(degrees) % 360.0
    if not angle:
        return bitmap.copy()
    if angle == 90.0:
        return rotate90(bitmap, True)
    if angle == 180.0:
        return rotate90(rotate90(bitmap, True), True)
    if angle == 270.0:
        return rotate90(bitmap, False)
    radians = math.radians(angle)
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    w, h = bitmap.width, bitmap.height
    cx, cy = w / 2.0, h / 2.0
    points = []
    for x, y in ((0, 0), (w, 0), (w, h), (0, h)):
        dx, dy = x - cx, y - cy
        points.append((cx + dx * cos_a - dy * sin_a,
                       cy + dx * sin_a + dy * cos_a))
    origin_x = int(math.floor(min(x for x, _y in points)))
    origin_y = int(math.floor(min(y for _x, y in points)))
    out_w = max(1, int(math.ceil(max(x for x, _y in points))) - origin_x)
    out_h = max(1, int(math.ceil(max(y for _x, y in points))) - origin_y)
    out = Bitmap(out_w, out_h)
    if smooth:
        _rotate_smooth_into(out, bitmap, cos_a, sin_a, cx, cy,
                            origin_x, origin_y)
    else:
        _rotate_hard_into(out, bitmap, cos_a, sin_a, cx, cy,
                          origin_x, origin_y)
    return out


def _rotate_hard_into(out, piece, cos_a, sin_a, cx, cy,
                      origin_x, origin_y):
    """Nearest-neighbour rotation fill: one sample per output pixel.

    Each output pixel reads the source pixel under its centre, so the
    result holds only real source colours, never grows thicker than the
    artwork and cannot scatter isolated specks. Pixels whose centre maps
    outside the piece stay transparent, which is what keeps a rotated
    bounding box from filling its corners.
    """
    sw, sh = piece.width, piece.height
    source = piece.data
    dst = out.data
    out_w = out.width
    for y in range(out.height):
        py = y + 0.5 + origin_y - cy
        for x in range(out_w):
            px = x + 0.5 + origin_x - cx
            u = cx + px * cos_a + py * sin_a
            v = cy - px * sin_a + py * cos_a
            if u < 0.0 or v < 0.0 or u >= sw or v >= sh:
                continue
            index = (int(v) * sw + int(u)) * 4
            if not source[index + 3]:
                continue
            target = (y * out_w + x) * 4
            dst[target:target + 4] = source[index:index + 4]


def _bilinear_sample(data, sw, sh, sx, sy):
    """Smoothly sample source pixels at a fractional position.

    A true bilinear blend on premultiplied RGBA: texels outside the source
    count as transparent, so edges fade out instead of growing dark halos.
    Positions are snapped to a 1e-9 grid first, so whole-pixel moves and
    integer scales land exactly on texel centres and stay perfectly sharp.
    Returns an (r, g, b, a) tuple, or None when nothing is covered.
    """
    u = round((sx - 0.5) * 1e9) / 1e9
    v = round((sy - 0.5) * 1e9) / 1e9
    x0 = int(math.floor(u))
    y0 = int(math.floor(v))
    fx = u - x0
    fy = v - y0
    taps = []
    for dy in (0, 1):
        weight_y = (1.0 - fy) if dy == 0 else fy
        if weight_y == 0.0:
            continue
        iy = y0 + dy
        if iy < 0 or iy >= sh:
            continue
        for dx in (0, 1):
            weight_x = (1.0 - fx) if dx == 0 else fx
            if weight_x == 0.0:
                continue
            ix = x0 + dx
            if ix < 0 or ix >= sw:
                continue
            offset = (iy * sw + ix) * 4
            alpha = data[offset + 3]
            if not alpha:
                continue
            taps.append((weight_x * weight_y, data[offset],
                         data[offset + 1], data[offset + 2], alpha))
    if not taps:
        return None
    if len(taps) == 4:
        first = taps[0][1:]
        if all(tap[1:] == first for tap in taps) and first[3] == 255:
            return (first[0], first[1], first[2], 255)
    total = 0.0
    red = green = blue = 0.0
    for weight, r, g, b, a in taps:
        strength = weight * (a / 255.0)
        total += strength
        red += r * strength
        green += g * strength
        blue += b * strength
    if total <= 0.0:
        return None
    alpha = clamp(round_half_up(total * 255.0), 0, 255)
    if not alpha:
        return None
    return (clamp(round_half_up(red / total), 0, 255),
            clamp(round_half_up(green / total), 0, 255),
            clamp(round_half_up(blue / total), 0, 255),
            alpha)


def _rotate_smooth_into(out, piece, cos_a, sin_a, cx, cy,
                        origin_x, origin_y):
    """Bilinear rotation fill for `rotate_piece`'s smooth mode."""
    sw, sh = piece.width, piece.height
    source = piece.data
    dst = out.data
    out_w, out_h = out.width, out.height
    for y in range(out_h):
        for x in range(out_w):
            dx = (x + 0.5 + origin_x) - cx
            dy = (y + 0.5 + origin_y) - cy
            sx = cx + dx * cos_a + dy * sin_a
            sy = cy - dx * sin_a + dy * cos_a
            colour = _bilinear_sample(source, sw, sh, sx, sy)
            if colour is None:
                continue
            target = (y * out_w + x) * 4
            dst[target] = colour[0]
            dst[target + 1] = colour[1]
            dst[target + 2] = colour[2]
            dst[target + 3] = colour[3]


def scale_box(box, fixed, grab, pointer, bounds, min_radius=4.0):
    """Size a box scaled from the `fixed` corner without distorting it.

    The drag started at `grab` and is now at `pointer`. Their distances from
    the fixed corner give one scale factor for both axes, so the piece keeps
    its proportions, and the box stays anchored on the corner's side of the
    original box instead of flipping when the pointer crosses it. Both
    distances are floored by `min_radius` so a grab close to the corner
    cannot collapse the piece on the first movement.

    Returns (x, y, width, height); growth stops when the piece would leave
    `bounds`, measured from the fixed corner.
    """
    left, top, width, height = box
    fixed_x, fixed_y = fixed

    def reach(point):
        return max(float(min_radius),
                   math.hypot(point[0] - fixed_x, point[1] - fixed_y))

    factor = reach(pointer) / reach(grab)
    right = fixed_x >= left + width - 1
    bottom = fixed_y >= top + height - 1
    # Only grow into the space the fixed corner has on the canvas, so a
    # piece scaled near an edge is not silently cropped.
    room_x = fixed_x + 1 if right else bounds[0] - fixed_x
    room_y = fixed_y + 1 if bottom else bounds[1] - fixed_y
    limit = max(1.0, min(room_x / float(width), room_y / float(height),
                         bounds[0] / float(width),
                         bounds[1] / float(height)))
    factor = max(1.0 / max(width, height), min(limit, factor))
    new_w = max(1, int(math.floor(width * factor + 0.5)))
    new_h = max(1, int(math.floor(height * factor + 0.5)))
    nx = fixed_x - new_w + 1 if right else fixed_x
    ny = fixed_y - new_h + 1 if bottom else fixed_y
    return nx, ny, new_w, new_h


class RotationDrag(object):
    """Turn pointer positions around a centre into a snapped rotation angle.

    Positions are continuous canvas coordinates, not whole cells: a rotation
    has to follow the pointer at zoom, and rounding every event to a cell
    would make the piece advance in visible jumps.

    A grab close to the centre would otherwise sweep a huge angle for every
    pixel of movement. Inside `min_radius` the current angle is held, and the
    first direction measured outside it becomes the new reference, so the
    piece continues from where it already points instead of jumping.
    `min_radius` is in canvas cells; callers scale it by the zoom so the dead
    zone stays a few screen pixels wide at every magnification.
    """

    def __init__(self, centre, grab, min_radius=3.0):
        self.centre = centre
        self.min_radius = float(min_radius)
        self.angle = 0.0
        self.base = 0.0
        self.grab_angle = None
        if self._radius(grab) >= self.min_radius:
            self.grab_angle = self._direction(grab)

    def _radius(self, point):
        return math.hypot(point[0] - self.centre[0],
                          point[1] - self.centre[1])

    def _direction(self, point):
        return math.degrees(math.atan2(point[1] - self.centre[1],
                                       point[0] - self.centre[0]))

    def update(self, pointer, snap=15.0):
        """Return the angle for the pointer, snapped to `snap` degrees."""
        radius = self._radius(pointer)
        if radius <= 0.0:
            return self.angle
        if radius < self.min_radius:
            # Too close to the centre to measure an angle: hold still and
            # wait for a fresh reference once the pointer is far enough out.
            self.grab_angle = None
            return self.angle
        direction = self._direction(pointer)
        if self.grab_angle is None:
            self.grab_angle = direction
            self.base = self.angle
        snap = max(1.0, float(snap or 1.0))
        turned = (direction - self.grab_angle) % 360.0
        self.angle = (self.base + round(turned / snap) * snap) % 360.0
        return self.angle


def quad_from_box(box):
    """The four corners of an (x, y, w, h) box, clockwise from top left."""
    left, top, width, height = box
    return [(float(left), float(top)),
            (float(left + width), float(top)),
            (float(left + width), float(top + height)),
            (float(left), float(top + height))]


def box_from_quad(quad):
    """The integer bounding box of a quad, rounded outwards."""
    xs = [point[0] for point in quad]
    ys = [point[1] for point in quad]
    left = int(math.floor(min(xs)))
    top = int(math.floor(min(ys)))
    right = int(math.ceil(max(xs)))
    bottom = int(math.ceil(max(ys)))
    return (left, top, max(1, right - left), max(1, bottom - top))


def quad_centre(quad):
    return (sum(point[0] for point in quad) / 4.0,
            sum(point[1] for point in quad) / 4.0)


def quad_translate(quad, dx, dy):
    return [(x + dx, y + dy) for x, y in quad]


def quad_scale(quad, anchor, factor):
    """Scale every corner around `anchor` by one factor."""
    ax, ay = anchor
    return [(ax + (x - ax) * factor, ay + (y - ay) * factor)
            for x, y in quad]


def quad_rotate(quad, centre, degrees):
    """Turn the quad clockwise around `centre` by `degrees`."""
    radians = math.radians(float(degrees))
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    cx, cy = centre
    result = []
    for x, y in quad:
        dx, dy = x - cx, y - cy
        result.append((cx + dx * cos_a - dy * sin_a,
                       cy + dx * sin_a + dy * cos_a))
    return result


def snap_quad_to_pixel_grid(quad):
    """Move an axis-aligned quad onto whole-pixel edges.

    A quarter turn can leave a rectangle centred half a pixel between output
    pixels (for example a 3x2 piece becoming 2x3). None of those destination
    pixel centres then falls inside the quad, so inverse sampling repeats one
    edge row and drops the other. Shift the whole quad to the nearest pixel
    grid before sampling so every source row and column survives exactly.
    """
    for index in range(4):
        ax, ay = quad[index]
        bx, by = quad[(index + 1) % 4]
        if abs(ax - bx) > 1e-9 and abs(ay - by) > 1e-9:
            return list(quad)
    xs = [point[0] for point in quad]
    ys = [point[1] for point in quad]
    dx = math.floor(min(xs) + 0.5) - min(xs)
    dy = math.floor(min(ys) + 0.5) - min(ys)
    return [(x + dx, y + dy) for x, y in quad]


def quad_shear(quad, edge, amount):
    """Slide one edge of the quad along the canvas axes.

    `edge` is 0 for the top, 1 for the right, 2 for the bottom and 3 for the
    left. Top and bottom edges slide sideways, left and right edges slide up
    and down, which is the shear a handle on that edge should produce.
    """
    result = list(quad)
    if edge in (0, 2):
        indices = (0, 1) if edge == 0 else (2, 3)
        for index in indices:
            result[index] = (result[index][0] + amount, result[index][1])
    elif edge in (1, 3):
        indices = (1, 2) if edge == 1 else (3, 0)
        for index in indices:
            result[index] = (result[index][0], result[index][1] + amount)
    return result


def quad_move_corner(quad, index, dx, dy):
    """Move one corner by a delta, leaving the other three in place."""
    result = list(quad)
    index = index % 4
    result[index] = (result[index][0] + dx, result[index][1] + dy)
    return result


def quad_handles(quad, rotation_gap=24.0):
    """Corner, edge and rotation handles for a transform box.

    Returns (kind, index, (x, y)) tuples: `corner` handles scale from the
    opposite corner (or move one corner in perspective mode), `edge` handles
    shear the edge they sit on, and the single `rotate` handle sits
    `rotation_gap` units above the middle of the top edge.
    """
    handles = []
    for index, point in enumerate(quad):
        handles.append(("corner", index, point))
    for index in range(4):
        first = quad[index]
        second = quad[(index + 1) % 4]
        handles.append(("edge", index,
                        ((first[0] + second[0]) / 2.0,
                         (first[1] + second[1]) / 2.0)))
    top_x = (quad[0][0] + quad[1][0]) / 2.0
    top_y = (quad[0][1] + quad[1][1]) / 2.0
    edge_x = quad[1][0] - quad[0][0]
    edge_y = quad[1][1] - quad[0][1]
    length = math.hypot(edge_x, edge_y)
    if length > 0:
        normal_x, normal_y = -edge_y / length, edge_x / length
        centre_x, centre_y = quad_centre(quad)
        if (normal_x * (top_x - centre_x)
                + normal_y * (top_y - centre_y)) < 0:
            normal_x, normal_y = -normal_x, -normal_y
        handles.append(("rotate", -1, (top_x + normal_x * rotation_gap,
                                       top_y + normal_y * rotation_gap)))
    return handles


def _quad_homography(quad, source):
    """Solve the projective map from a destination quad to source corners.

    The returned coefficients map a destination point (x, y) to a source
    point (u, v) with `u = (h0*x + h1*y + h2) / (h6*x + h7*y + 1)` and the
    matching expression for v. Four point pairs determine the eight
    coefficients, including true perspective rather than a triangle affine
    approximation.
    """
    rows = []
    for (x, y), (u, v) in zip(quad, source):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0,
                     -u * x, -u * y, u])
        rows.append([0.0, 0.0, 0.0, x, y, 1.0,
                     -v * x, -v * y, v])
    for column in range(8):
        pivot = max(range(column, 8),
                    key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        if pivot != column:
            rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        for index in range(column, 9):
            rows[column][index] /= divisor
        for row in range(8):
            if row == column:
                continue
            factor = rows[row][column]
            if abs(factor) < 1e-15:
                continue
            for index in range(column, 9):
                rows[row][index] -= factor * rows[column][index]
    return [rows[index][8] for index in range(8)]


def _opaque_neighbor_count(bitmap, x, y, ignore=None):
    """How many surrounding pixels are opaque, optionally skipping one."""
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    count = 0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if not dx and not dy:
                continue
            nx, ny = x + dx, y + dy
            if (nx, ny) == ignore:
                continue
            if 0 <= nx < width and 0 <= ny < height:
                if data[(ny * width + nx) * 4 + 3]:
                    count += 1
    return count


def _point_in_quad(quad, x, y, epsilon=1e-9):
    """Whether a point lies inside a convex quad, including its edges."""
    positive = negative = False
    for index in range(4):
        ax, ay = quad[index]
        bx, by = quad[(index + 1) % 4]
        cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        if cross > epsilon:
            positive = True
        elif cross < -epsilon:
            negative = True
        if positive and negative:
            return False
    return True


def _quad_is_convex(quad, epsilon=1e-9):
    """Whether a quad is convex (no dented or bow-tie corner)."""
    sign = 0
    for index in range(4):
        ax, ay = quad[index]
        bx, by = quad[(index + 1) % 4]
        cx, cy = quad[(index + 2) % 4]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross > epsilon:
            if sign < 0:
                return False
            sign = 1
        elif cross < -epsilon:
            if sign > 0:
                return False
            sign = -1
    return sign != 0


def _point_in_triangle(triangle, x, y, epsilon=1e-9):
    """Whether a point lies inside a triangle, including its edges."""
    (ax, ay), (bx, by), (cx, cy) = triangle
    d1 = (x - bx) * (ay - by) - (ax - bx) * (y - by)
    d2 = (x - cx) * (by - cy) - (bx - cx) * (y - cy)
    d3 = (x - ax) * (cy - ay) - (cx - ax) * (y - ay)
    has_neg = (d1 < -epsilon) or (d2 < -epsilon) or (d3 < -epsilon)
    has_pos = (d1 > epsilon) or (d2 > epsilon) or (d3 > epsilon)
    return not (has_neg and has_pos)


def _triangle_affine_inverse(triangle, x, y):
    """Barycentric coordinates of a point in a triangle, or None."""
    (ax, ay), (bx, by), (cx, cy) = triangle
    det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if abs(det) < 1e-12:
        return None
    dx, dy = x - ax, y - ay
    u = (dx * (cy - ay) - dy * (cx - ax)) / det
    v = ((bx - ax) * dy - (by - ay) * dx) / det
    return (u, v)


def _affine_from_parallelogram(quad, sw, sh):
    """Exact inverse for parallelogram quads (move/scale/shear/rotate).

    Returns (inv_a, inv_b, inv_c, inv_d, inv_tx, inv_ty) mapping a
    destination point to source pixels, or None when the quad is not a
    parallelogram or is degenerate. This avoids the projective solver
    entirely for the common transforms, so thin pieces cannot distort
    through an ill-conditioned 8x8 system.
    """
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    if sw <= 0 or sh <= 0:
        return None
    spread = (abs(x0) + abs(y0) + abs(x1) + abs(y1) + abs(x2)
              + abs(y2) + abs(x3) + abs(y3) + 4.0)
    tol = 1e-6 * spread
    if abs((x1 + x3 - x0) - x2) > tol:
        return None
    if abs((y1 + y3 - y0) - y2) > tol:
        return None
    a = (x1 - x0) / float(sw)
    b = (x3 - x0) / float(sh)
    c = (y1 - y0) / float(sw)
    d = (y3 - y0) / float(sh)
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    return (d / det, -b / det, -c / det, a / det,
            -(d / det) * x0 + (b / det) * y0,
            (c / det) * x0 - (a / det) * y0)


def _warp_source(piece, px, py, homography, affine, triangles, quad=None):
    """Map a destination point to continuous source coordinates, or None."""
    sw, sh = piece.width, piece.height
    if quad is not None and triangles is None:
        if not _point_in_quad(quad, px, py):
            return None
    if triangles is not None:
        for triangle, (sax, say), (sbx, sby), (scx, scy) in triangles:
            if not _point_in_triangle(triangle, px, py):
                continue
            uv = _triangle_affine_inverse(triangle, px, py)
            if uv is None:
                continue
            u, v = uv
            if u < -1e-9 or v < -1e-9 or u + v > 1.0 + 1e-9:
                continue
            sx = sax + (sbx - sax) * u + (scx - sax) * v
            sy = say + (sby - say) * u + (scy - say) * v
            if sx < 0.0 or sy < 0.0 or sx >= sw or sy >= sh:
                return None
            return (sx, sy)
        return None
    if affine is not None:
        ia, ib, ic, id_, itx, ity = affine
        sx = ia * px + ib * py + itx
        sy = ic * px + id_ * py + ity
    else:
        h0, h1, h2, h3, h4, h5, h6, h7 = homography
        divisor = h6 * px + h7 * py + 1.0
        if abs(divisor) < 1e-12:
            return None
        sx = (h0 * px + h1 * py + h2) / divisor
        sy = (h3 * px + h4 * py + h5) / divisor
    epsilon = 1e-7
    if sx < -epsilon or sy < -epsilon:
        return None
    if affine is not None:
        # Half-open bounds for the affine transforms: pixel (i, j) covers
        # [i, i + 1) x [j, j + 1). Accepting a centre that lands exactly on
        # the far edge read the last source row or column a second time, so
        # every odd-sized piece came back one row or column too fat.
        if sx >= sw or sy >= sh:
            return None
    elif sx > sw + epsilon or sy > sh + epsilon:
        # A projective quad can pinch the far edge onto the destination grid,
        # so keep the closed bound there and let the last pixel survive.
        return None
    return (sx, sy)


def _warp_sample(piece, px, py, homography, affine, triangles, quad=None):
    """Map one destination point to a source pixel, or None if outside."""
    point = _warp_source(piece, px, py, homography, affine, triangles, quad)
    if point is None:
        return None
    return (max(0, min(piece.width - 1, int(math.floor(point[0] + 1e-9)))),
            max(0, min(piece.height - 1, int(math.floor(point[1] + 1e-9)))))


def _warp_smooth(out, piece, homography, left, top, affine=None,
                 triangles=None):
    """Fill a transformed bitmap with smoothly blended pixels.

    Each destination pixel centre is mapped back into the source and read
    with a premultiplied bilinear blend, so rotated edges fade out instead
    of stair-stepping, and transparent edges never grow dark halos. Whole
    pixels landing on one opaque texel stay perfectly sharp.
    """
    sw, sh = piece.width, piece.height
    source = piece.data
    dst = out.data
    if homography is not None:
        h0, h1, h2, h3, h4, h5, h6, h7 = homography
    for y in range(out.height):
        row = y * out.width
        for x in range(out.width):
            px = left + x + 0.5
            py = top + y + 0.5
            if triangles is not None:
                found = None
                for triangle, (sax, say), (sbx, sby), (scx, scy) \
                        in triangles:
                    if not _point_in_triangle(triangle, px, py):
                        continue
                    uv = _triangle_affine_inverse(triangle, px, py)
                    if uv is None:
                        continue
                    u, v = uv
                    if u < -1e-9 or v < -1e-9 or u + v > 1.0 + 1e-9:
                        continue
                    found = (sax + (sbx - sax) * u + (scx - sax) * v,
                             say + (sby - say) * u + (scy - say) * v)
                    break
                if found is None:
                    continue
                sx, sy = found
            elif affine is not None:
                ia, ib, ic, id_, itx, ity = affine
                sx = ia * px + ib * py + itx
                sy = ic * px + id_ * py + ity
            else:
                divisor = h6 * px + h7 * py + 1.0
                if abs(divisor) < 1e-12:
                    continue
                sx = (h0 * px + h1 * py + h2) / divisor
                sy = (h3 * px + h4 * py + h5) / divisor
            colour = _bilinear_sample(source, sw, sh, sx, sy)
            if colour is None:
                continue
            target = (row + x) * 4
            dst[target] = colour[0]
            dst[target + 1] = colour[1]
            dst[target + 2] = colour[2]
            dst[target + 3] = colour[3]


CLEAN_EDGE_SIMILAR = 0.0      # RGBA distance in 0..1 units; 0 = exact
CLEAN_EDGE_LINE_WIDTH = 1.0

TRANSFORM_ALGORITHMS = ("RotSprite", "Nearest", "CleanEdge",
                        "Transpose 90")

# RotSprite (Xenowhirl): enlarge with a pixel-choosing Scale2x that treats
# similar colours as a match, then rotate and shrink with nearest neighbour.
# The cap keeps a live drag responsive on large pieces; 8x is the classic
# factor and is used for anything close to sprite size.
ROTSPRITE_MAX_PIXELS = 600000
ROTSPRITE_SIMILAR = 16
_ROTSPRITE_CACHE = {"piece": None, "scale": 0, "similar": None,
                    "bitmap": None}


def _similar_colour(first, second, threshold=ROTSPRITE_SIMILAR):
    """Whether two RGBA pixels are close enough to be one shape."""
    if first == second:
        return True
    if not first[3] and not second[3]:
        return True
    return (abs(first[0] - second[0]) + abs(first[1] - second[1])
            + abs(first[2] - second[2]) + abs(first[3] - second[3])
            <= threshold)


def _scale2x(bitmap, similar=ROTSPRITE_SIMILAR):
    """Double a bitmap with the Scale2x pixel-choosing enlargement.

    Never blends: each output pixel is copied from an existing one, which is
    what lets RotSprite add detail and still keep the original palette.
    `similar` is how far apart two colours may be and still count as one
    shape; 0 makes the comparison strict equality.
    """
    width, height = bitmap.width, bitmap.height
    data = bytes(bitmap.data)
    out = Bitmap(width * 2, height * 2)
    dst = out.data
    stride = width * 4
    out_stride = out.width * 4
    empty = b"\x00\x00\x00\x00"

    def alike(first, second):
        return _similar_colour(first, second, similar)

    def pixel(x, y):
        if 0 <= x < width and 0 <= y < height:
            base = y * stride + x * 4
            return data[base:base + 4]
        return empty

    for y in range(height):
        row = y * out_stride * 2
        for x in range(width):
            here = pixel(x, y)
            up = pixel(x, y - 1)
            right = pixel(x + 1, y)
            left = pixel(x - 1, y)
            down = pixel(x, y + 1)
            if (alike(left, up) and not alike(left, down)
                    and not alike(up, right)):
                top_left = up
            else:
                top_left = here
            if (alike(up, right) and not alike(up, left)
                    and not alike(right, down)):
                top_right = right
            else:
                top_right = here
            if (alike(left, down) and not alike(left, up)
                    and not alike(down, right)):
                bottom_left = left
            else:
                bottom_left = here
            if (alike(right, down) and not alike(right, up)
                    and not alike(down, left)):
                bottom_right = down
            else:
                bottom_right = here
            base = row + x * 8
            dst[base:base + 4] = top_left
            dst[base + 4:base + 8] = top_right
            base += out_stride
            dst[base:base + 4] = bottom_left
            dst[base + 4:base + 8] = bottom_right
    return out


def _rotsprite_scale(piece, requested=None):
    """The enlargement to use: the requested factor inside the pixel cap.

    `requested` is 2, 4 or 8, or None/0 for the classic 8x. The cap still
    applies, so a large selection cannot stall a live drag.
    """
    try:
        scale = int(requested)
    except (TypeError, ValueError):
        scale = 0
    if scale <= 0:
        scale = 8
    scale = max(1, min(8, scale))
    while (scale > 1
           and piece.width * piece.height * scale * scale
           > ROTSPRITE_MAX_PIXELS):
        scale //= 2
    return scale


def _rotsprite_upscale(piece, scale, similar=ROTSPRITE_SIMILAR):
    """The enlarged piece, cached across the drags of one session."""
    cache = _ROTSPRITE_CACHE
    if (cache["piece"] is piece and cache["scale"] == scale
            and cache["similar"] == similar):
        return cache["bitmap"]
    big = piece
    size = 1
    while size < scale:
        big = _scale2x(big, similar)
        size *= 2
    cache.update(piece=piece, scale=scale, similar=similar, bitmap=big)
    return big


def _stamp_quarter_turn(out, turned, xs, ys, left, top):
    """Copy an exactly turned piece into the clipped box, or False."""
    offset_x = int(round(min(xs) - left))
    offset_y = int(round(min(ys) - top))
    if (offset_x < 0 or offset_y < 0
            or turned.width > out.width - offset_x
            or turned.height > out.height - offset_y):
        return False
    # The box can pad a half pixel around the quad; stamp the turned pixels
    # at the quad's corner instead of stretching them.
    out.data[:] = bytes(out.width * out.height * 4)
    for row in range(turned.height):
        source_start = row * turned.width * 4
        target = ((offset_y + row) * out.width + offset_x) * 4
        out.data[target:target + turned.width * 4] = \
            turned.data[source_start:source_start + turned.width * 4]
    return True


def _warp_rotsprite(out, piece, homography, left, top, affine, triangles,
                    quad, scale=None, similar=None):
    """Rotate by sampling the enlarged piece and shrinking it in one go."""
    scale = _rotsprite_scale(piece, scale)
    threshold = ROTSPRITE_SIMILAR if similar is None else int(similar)
    big = _rotsprite_upscale(piece, scale, threshold)
    big_width, big_height = big.width, big.height
    source = big.data
    dst = out.data
    for y in range(out.height):
        py = top + y + 0.5
        row = y * out.width
        for x in range(out.width):
            point = _warp_source(piece, left + x + 0.5, py, homography,
                                 affine, triangles, quad)
            if point is None:
                continue
            sx = int(point[0] * scale)
            sy = int(point[1] * scale)
            if sx < 0 or sy < 0 or sx >= big_width or sy >= big_height:
                continue
            index = (sy * big_width + sx) * 4
            if not source[index + 3]:
                continue
            target = (row + x) * 4
            dst[target:target + 4] = source[index:index + 4]
    return out


def _quarter_turn_piece(piece, affine):
    """The piece turned by an exact quarter turn, or None.

    Transpose plus row reversal is the classic matrix rotation from
    waelby99/pixel-rotate: it moves pixels around without sampling them, so
    a quarter turn keeps every pixel exactly as it was, including colours
    that a nearest-neighbour resample would shift by half a pixel.
    """
    if affine is None:
        return None
    ia, ib, ic, id_ = affine[0], affine[1], affine[2], affine[3]
    epsilon = 1e-6
    if abs(ia) < epsilon and abs(id_) < epsilon:
        if ib > 0.0 and ic < 0.0:
            return rotate90(piece, True)
        if ib < 0.0 and ic > 0.0:
            return rotate90(piece, False)
        return None
    if abs(ib) < epsilon and abs(ic) < epsilon:
        if abs(ia + 1.0) < epsilon and abs(id_ + 1.0) < epsilon:
            return rotate90(rotate90(piece, True), True)
        if abs(ia - 1.0) < epsilon and abs(id_ - 1.0) < epsilon:
            return piece.copy()
    return None


def _clean_distance(first, second):
    """Euclidean RGBA distance in cleanEdge's 0..1 colour space."""
    return math.sqrt((first[0] - second[0]) ** 2
                     + (first[1] - second[1]) ** 2
                     + (first[2] - second[2]) ** 2
                     + (first[3] - second[3]) ** 2)


def _clean_similar(first, second, threshold):
    if not first[3] and not second[3]:
        return True
    return _clean_distance(first, second) <= threshold


def _clean_lift(colour):
    """How close a colour is to the shader's white priority colour."""
    return ((colour[0] - 1.0) ** 2 + (colour[1] - 1.0) ** 2
            + (colour[2] - 1.0) ** 2)


def _clean_higher(first, second, threshold):
    """True when `first` would be drawn over `second`, as in the shader."""
    if _clean_similar(first, second, threshold):
        return False
    if first[3] == second[3]:
        return _clean_lift(first) < _clean_lift(second)
    return first[3] > second[3]


def _clean_dist_to_line(test, start, end, direction):
    """Signed distance from `test` to the line through `start` and `end`."""
    line_x, line_y = end[0] - start[0], end[1] - start[1]
    perp_x, perp_y = line_y, -line_x
    to_x, to_y = start[0] - test[0], start[1] - test[1]
    sign = 1.0 if perp_x * direction[0] + perp_y * direction[1] > 0.0 else -1.0
    length = math.hypot(perp_x, perp_y)
    if not length:
        return 0.0
    return sign * ((perp_x * to_x + perp_y * to_y) / length)


def _clean_slice(point, direction, main, cells, similar, line_width,
                 lengths=None):
    """One quadrant's cleanEdge slice, ported from torcado194's shader.

    `point` is where the sample sits inside its source pixel and `main` names
    the corner of that pixel being filled, so the neighbour grid is read
    through a frame mirrored to face that corner. The slice only covers the
    sample when a 45-degree or 2:1 line crosses it; then it returns the
    artwork colour on the covered side. None means "no line here".
    """
    mx, my = main
    point = (mx * (point[0] - 0.5) + 0.5,
             my * (point[1] - 0.5) + 0.5)

    def colour(dx, dy):
        return cells[(my * dy + 2) * 5 + (mx * dx + 2)]

    # The slices ask for the same few pair distances over and over, so one
    # small cache shared by the three of them keeps sqrt calls to unique
    # pairs only.
    lengths = {} if lengths is None else lengths

    def cd(first, second):
        key = (first, second)
        value = lengths.get(key)
        if value is None:
            key = (second, first)
            value = lengths.get(key)
            if value is None:
                value = _clean_distance(first, second)
                lengths[key] = value
        return value

    def sim(first, second):
        if not first[3] and not second[3]:
            return True
        return cd(first, second) <= similar

    def higher(first, second):
        if sim(first, second):
            return False
        if first[3] == second[3]:
            return _clean_lift(first) < _clean_lift(second)
        return first[3] > second[3]

    def sim3(first, second, third):
        return sim(first, second) and sim(second, third)

    def sim4(first, second, third, fourth):
        return sim3(first, second, third) and sim(third, fourth)

    def line(start, end, towards):
        return _clean_dist_to_line(
            point,
            (0.5 + start[0] * direction[0],
             0.5 + start[1] * direction[1]),
            (0.5 + end[0] * direction[0],
             0.5 + end[1] * direction[1]),
            direction if towards else (-direction[0], -direction[1]))

    c = colour(0, 0)
    u, d = colour(0, -1), colour(0, 1)
    f, b = colour(1, 0), colour(-1, 0)
    uf, ub = colour(1, -1), colour(-1, -1)
    df, db = colour(1, 1), colour(-1, 1)
    ff, dd = colour(2, 0), colour(0, 2)
    dff, ddf, ddb = colour(2, 1), colour(1, 2), colour(-1, 2)
    uff = colour(2, -1)

    # The shader's edge detector: a slice only exists when the colour runs
    # against the line more strongly than it runs along it.
    against = (4.0 * cd(f, d) + cd(uf, c) + cd(c, db) + cd(ff, df)
               + cd(df, dd))
    towards = (4.0 * cd(c, df) + cd(u, f) + cd(f, dff) + cd(b, d)
               + cd(d, ddf))
    if not (against < towards
            or (against < towards + 0.001 and not higher(c, f))):
        return None
    if sim4(f, d, b, u) and sim4(uf, df, db, ub) and not sim(c, f):
        return None

    if sim3(f, d, db) and not sim3(f, d, b) and not sim(uf, db):
        # lower shallow 2:1 slant
        flip = False
        if not (sim(c, df) and higher(c, f)):
            if higher(c, f):
                flip = True
            if sim(u, f) and not sim(c, df) and not higher(c, u):
                flip = True
        if flip:
            dist = line_width - line((1.5, -1.0), (-0.5, 0.0), False)
        else:
            dist = line((1.5, 0.0), (-0.5, 1.0), True)
        dist -= line_width / 2.0
        if dist <= 0.0:
            return f if cd(c, f) <= cd(c, d) else d
        return None

    if sim3(uf, f, d) and not sim3(u, f, d) and not sim(uf, db):
        # forward steep 2:1 slant
        flip = False
        if not (sim(c, df) and higher(c, d)):
            if higher(c, d):
                flip = True
            if sim(b, d) and not sim(c, df) and not higher(c, d):
                flip = True
        if flip:
            dist = line_width - line((0.0, -0.5), (-1.0, 1.5), False)
        else:
            dist = line((1.0, -0.5), (0.0, 1.5), True)
        dist -= line_width / 2.0
        if dist <= 0.0:
            return f if cd(c, f) <= cd(c, d) else d
        return None

    if sim(f, d):
        # 45-degree diagonal
        flip = False
        if sim(c, df) and higher(c, f):
            # single pixel wide diagonal along the neighbours, don't flip
            if not sim(c, dd) and not sim(c, ff):
                flip = True
        else:
            if higher(c, f):
                flip = True
            if not sim(c, b) and sim4(b, f, d, u):
                flip = True
        if (((sim(f, db) and sim3(u, f, df))
             or (sim(uf, d) and sim3(b, d, df)))
                and not sim(c, df)):
            # single pixel 2:1 slope, don't flip
            flip = True
        if flip:
            dist = line_width - line((1.0, -1.0), (-1.0, 1.0), False)
        else:
            dist = line((1.0, 0.0), (0.0, 1.0), True)
        dist -= line_width / 2.0
        if dist <= 0.0:
            return f if cd(c, f) <= cd(c, d) else d
        return None

    if sim3(ff, df, d) and not sim3(ff, df, c) and not sim(uff, d):
        # far corner of a shallow slant
        flip = False
        if not (sim(f, dff) and higher(f, ff)):
            if higher(f, ff):
                flip = True
            if sim(uf, ff) and not sim(f, dff) and not higher(f, uf):
                flip = True
        if flip:
            dist = line_width - line((2.5, -1.0), (0.5, 0.0), False)
        else:
            dist = line((2.5, 0.0), (0.5, 1.0), True)
        dist -= line_width / 2.0
        if dist <= 0.0:
            return ff if cd(f, ff) <= cd(f, df) else df
        return None

    if sim3(f, df, dd) and not sim3(c, df, dd) and not sim(f, ddb):
        # far corner of a steep slant
        flip = False
        if not (sim(d, ddf) and higher(d, dd)):
            if higher(d, dd):
                flip = True
            if sim(db, dd) and not sim(d, ddf) and not higher(d, dd):
                flip = True
        if flip:
            dist = line_width - line((0.0, 0.5), (-1.0, 2.5), False)
        else:
            dist = line((1.0, 0.5), (0.0, 2.5), True)
        dist -= line_width / 2.0
        if dist <= 0.0:
            return df if cd(d, df) <= cd(d, dd) else dd
        return None
    return None


def _clean_edge_colour(swatch, width, height, sx, sy, similar, line_width):
    """The cleanEdge colour for a sample at source position (sx, sy)."""
    cell_x = int(math.floor(sx))
    cell_y = int(math.floor(sy))
    point = (sx - cell_x, sy - cell_y)
    dir_x = 1 if point[0] >= 0.5 else -1
    dir_y = 1 if point[1] >= 0.5 else -1
    cells = []
    for oy in range(-2, 3):
        y = cell_y + oy * dir_y
        y = 0 if y < 0 else (height - 1 if y >= height else y)
        row = y * width
        for ox in range(-2, 3):
            x = cell_x + ox * dir_x
            x = 0 if x < 0 else (width - 1 if x >= width else x)
            cells.append(swatch[row + x])
    base = cells[12]
    flat = (all(cell == base for cell in cells)
            if similar <= 0.0
            else all(_clean_similar(cell, base, similar)
                     for cell in cells))
    if flat:
        # Nothing around to draw a line against: the nearest pixel stands.
        return base
    colour = base
    solid = max(0.45, min(1.142, line_width))
    lengths = {}
    for main in ((1, 1), (-1, 1), (1, -1)):
        picked = _clean_slice(point, (dir_x, dir_y), main, cells, similar,
                              solid, lengths)
        if picked is not None:
            colour = picked
    return colour


def _warp_clean_edge(out, piece, homography, left, top, affine, triangles,
                     quad, similar=None, line_width=None):
    """Warp with cleanEdge's quadrant slices instead of a nearest sample.

    Every destination pixel asks the source grid which corner of its pixel
    the sample falls in, and the slices redraw a turned outline as a clean
    line at that corner. Colours are only ever copied from the artwork, so
    a warp still cannot invent a colour that was not there.
    """
    threshold = CLEAN_EDGE_SIMILAR if similar is None else float(similar)
    width = CLEAN_EDGE_LINE_WIDTH if line_width is None else float(line_width)
    sw, sh = piece.width, piece.height
    data = piece.data
    swatch = [(data[index] / 255.0, data[index + 1] / 255.0,
               data[index + 2] / 255.0, data[index + 3] / 255.0)
              for index in range(0, len(data), 4)]
    dst = out.data
    for y in range(out.height):
        wy = top + y + 0.5
        row = y * out.width
        for x in range(out.width):
            point = _warp_source(piece, left + x + 0.5, wy, homography,
                                 affine, triangles, quad)
            if point is None:
                continue
            sx, sy = point
            if sx < 0.0 or sy < 0.0 or sx >= sw or sy >= sh:
                continue
            colour = _clean_edge_colour(swatch, sw, sh, sx, sy, threshold,
                                        width)
            if colour[3] <= 0.0:
                continue
            target = (row + x) * 4
            dst[target] = min(255, int(colour[0] * 255.0 + 0.5))
            dst[target + 1] = min(255, int(colour[1] * 255.0 + 0.5))
            dst[target + 2] = min(255, int(colour[2] * 255.0 + 0.5))
            dst[target + 3] = min(255, int(colour[3] * 255.0 + 0.5))
    return out


def warp_piece(piece, quad, width, height, smooth=False, mode=None,
               rotsprite_scale=None, rotsprite_similar=None):
    """Draw the piece into a quadrilateral on a width x height canvas.

    Parallelogram quads (move, scale, shear and rotation) use an exact
    affine inverse, so thin pieces cannot distort through an
    ill-conditioned projective solve. True perspective quads use one
    projective map, and twisted or dented quads fall back to two affine
    triangles so they shear instead of exploding across the canvas.

    `mode` picks the sampling algorithm: "CleanEdge" (the default: cleanEdge's
    quadrant slices redraw a turned outline as a clean line), "RotSprite"
    (8x Scale2x pixel guessing, then a nearest rotate and shrink), "Nearest"
    (one source sample per destination pixel), "Smooth" (bilinear, the
    faiface/pixel smooth toggle), or "Transpose 90" (exact quarter turns by
    transpose and row reversal, falling back to nearest when the quad is not
    a right angle).
    `smooth`
    is the older spelling of "Smooth" and still works. Pixels outside the
    canvas or the artwork stay transparent. Returns (left, top, bitmap) for
    the clipped box to stamp.
    """
    if len(quad) != 4:
        raise HexError("a transform box needs four corners")
    quad = [(float(x), float(y)) for x, y in quad]
    xs = [point[0] for point in quad]
    ys = [point[1] for point in quad]
    # One pixel convention everywhere: pixel (x, y) covers the square
    # [x, x + 1) x [y, y + 1), so the clipped box holds every pixel whose
    # square meets the quad's bounding box. This matches box_from_quad
    # and keeps rotate_piece and the live preview on the same grid.
    left = max(0, int(math.floor(min(xs))))
    top = max(0, int(math.floor(min(ys))))
    right = min(width - 1, int(math.ceil(max(xs))) - 1)
    bottom = min(height - 1, int(math.ceil(max(ys))) - 1)
    if right < left or bottom < top:
        return left, top, Bitmap(1, 1)
    out = Bitmap(right - left + 1, bottom - top + 1)
    source = piece.data
    sw, sh = piece.width, piece.height
    dst = out.data
    corners = [(0.0, 0.0), (float(sw), 0.0), (float(sw), float(sh)),
               (0.0, float(sh))]
    convex = _quad_is_convex(quad)
    affine = _affine_from_parallelogram(quad, sw, sh) if convex else None
    homography = None
    triangles = None
    if affine is None and convex:
        homography = _quad_homography(quad, corners)
        if homography is None:
            return left, top, Bitmap(1, 1)
    elif not convex:
        # Two affine triangles along the source diagonal. Concave and
        # bow-tie quads stay bounded; each destination pixel is sampled
        # from the triangle that contains it.
        triangles = [
            ([quad[0], quad[1], quad[2]],
             (0.0, 0.0), (float(sw), 0.0), (float(sw), float(sh))),
            ([quad[0], quad[2], quad[3]],
             (0.0, 0.0), (float(sw), float(sh)), (0.0, float(sh))),
        ]
    if mode is None:
        mode = "Smooth" if smooth else "CleanEdge"
    mode = str(mode)
    if mode == "RotSprite":
        # RotSprite is a rotation algorithm. A move, scale or perspective
        # warp keeps plain nearest neighbour, so dragging a selection can
        # never redraw the artwork and a scale stays pixel exact.
        if affine is None or (abs(affine[1]) < 1e-6
                              and abs(affine[2]) < 1e-6):
            mode = "Nearest"
    if mode in ("Transpose 90", "RotSprite", "CleanEdge"):
        turned = _quarter_turn_piece(piece, affine if convex else None)
        if (turned is not None
                and _stamp_quarter_turn(out, turned, xs, ys, left, top)):
            return left, top, out
        if mode == "Transpose 90":
            mode = "Nearest"
        elif mode == "RotSprite":
            return left, top, _warp_rotsprite(
                out, piece, homography, left, top, affine, triangles, quad,
                scale=rotsprite_scale, similar=rotsprite_similar)
    if mode == "Smooth":
        _warp_smooth(out, piece, homography, left, top,
                     affine=affine, triangles=triangles)
        return left, top, out
    if mode == "CleanEdge":
        # Like RotSprite, cleanEdge only earns its keep on a turned piece.
        # An axis-aligned move or scale stays an exact nearest sample.
        if affine is None or (abs(affine[1]) < 1e-6
                              and abs(affine[2]) < 1e-6):
            mode = "Nearest"
        else:
            return left, top, _warp_clean_edge(out, piece, homography,
                                               left, top, affine, triangles,
                                               quad)

    for y in range(top, bottom + 1):
        py = y + 0.5
        row = (y - top) * out.width
        for x in range(left, right + 1):
            sampled = _warp_sample(piece, x + 0.5, py, homography, affine,
                                   triangles, quad)
            if sampled is None:
                continue
            index = (sampled[1] * sw + sampled[0]) * 4
            if not source[index + 3]:
                continue
            target = (row + (x - left)) * 4
            dst[target:target + 4] = source[index:index + 4]
    return left, top, out


# --------------------------------------------------------------------- layers
class LayerGroup(object):
    """A named set of layers that show, hide and merge together.

    Membership lives on the layers themselves (`Layer.group`), so a group can
    be reopened from any project without a separate tree structure.
    """

    __slots__ = ("name", "visible", "alpha")

    def __init__(self, name="Group 1", visible=True, alpha=255):
        self.name = str(name)
        self.visible = bool(visible)
        self.alpha = clamp(int(alpha), 0, 255)

    def __repr__(self):
        return "LayerGroup(%r)" % self.name

    def copy(self):
        return LayerGroup(self.name, self.visible, self.alpha)

    def to_state(self):
        return {"name": self.name, "visible": self.visible,
                "alpha": self.alpha}

    @classmethod
    def from_state(cls, state):
        return cls(state.get("name", "Group"), state.get("visible", True),
                   state.get("alpha", 255))


class Layer(object):
    """One bitmap in a document's layer stack.

    `op`, `gate` and `blend` are the layer's three separate option groups:
    the arithmetic operation, the bitwise gate and the blend mode. Each is
    "none" unless chosen; the blend applies first, then the operation, then
    the gate.
    `alpha` is the layer's own opacity, which scales the strength of the
    result for every pixel. `group` names the layer's group, or is empty when
    it is ungrouped. `Document.layers[0]` is the bottom layer.
    """

    __slots__ = ("bitmap", "name", "op", "gate", "blend", "visible",
                 "alpha", "group")

    def __init__(self, width, height, name="Layer 1", op="none",
                 visible=True, data=None, alpha=255, group="", blend="none",
                 gate="none"):
        self.bitmap = Bitmap(width, height, data)
        self.name = str(name)
        self.op = op if op in LAYER_OPS else "none"
        self.gate = gate if gate in LAYER_GATES else "none"
        self.blend = blend if blend in LAYER_BLENDS else "none"
        self.visible = bool(visible)
        self.alpha = clamp(int(alpha), 0, 255)
        self.group = str(group or "")

    def __repr__(self):
        return "Layer(%r, %s, %s, %s)" % (self.name, self.op, self.gate,
                                          self.blend)

    def copy(self):
        return Layer(self.bitmap.width, self.bitmap.height, self.name, self.op,
                     self.visible, bytes(self.bitmap.data), self.alpha,
                     self.group, self.blend, self.gate)

    def to_state(self):
        return {"name": self.name, "op": self.op, "gate": self.gate,
                "blend": self.blend, "visible": self.visible,
                "alpha": self.alpha, "group": self.group,
                "data": bytes(self.bitmap.data)}

    @classmethod
    def from_state(cls, state, width, height):
        op = state.get("op", "none")
        gate = state.get("gate", "none")
        blend = state.get("blend", "none")
        if "gate" not in state:
            # Projects from before the three groups held a bitwise gate, or
            # one of the old blend names, in "op".
            gate = op if op in LAYER_GATES else "none"
            if "blend" in state:
                # Two-group projects: "op" was the gate, "blend" the mode.
                op = "none"
            elif op in ("screen", "overlay", "darken", "lighten",
                        "difference", "normal"):
                op, blend = "none", op
            elif op not in LAYER_OPS:
                op = "none"
        if blend == "normal":
            blend = "none"
        return cls(width, height, state.get("name", "Layer"), op,
                   state.get("visible", True), state.get("data"),
                   state.get("alpha", 255), state.get("group", ""), blend,
                   gate)


def composite_layers(layers, rect=None, frame=None, groups=None):
    """Flatten the visible layers into one Bitmap.

    `frame` is the full canvas size and `rect` limits the result to one
    rectangle, which is what the canvas view composites while painting. Each
    layer combines with the stack below it using its own operation, with its
    alpha as the strength, so transparent pixels leave the result unchanged.
    `groups` maps group names to LayerGroup objects; a hidden group hides its
    members and a group opacity scales theirs.
    """
    if not layers:
        raise HexError("a document needs at least one layer")
    fw, fh = frame or (layers[0].bitmap.width, layers[0].bitmap.height)
    x, y, w, h = clip(rect or (0, 0, fw, fh), (0, 0, fw, fh))
    out = Bitmap(max(1, w), max(1, h))
    if w <= 0 or h <= 0:
        return out
    dst = out.data
    for layer in layers:
        if not layer.visible:
            continue
        src = layer.bitmap.data
        op = layer.op
        gate = layer.gate
        blend = layer.blend
        opacity = clamp(int(layer.alpha), 0, 255)
        group = groups.get(layer.group) if groups and layer.group else None
        if group is not None:
            if not group.visible:
                continue
            opacity = opacity * clamp(int(group.alpha), 0, 255) // 255
        if not opacity:
            continue
        for row in range(h):
            start = ((y + row) * fw + x) * 4
            dest_row = row * w * 4
            for col in range(w):
                s = start + col * 4
                d = dest_row + col * 4
                above = src[s + 3]
                if not above:
                    continue
                if opacity < 255:
                    above = above * opacity // 255
                    if not above:
                        continue
                below = dst[d + 3]
                if not below:
                    dst[d:d + 3] = src[s:s + 3]
                    dst[d + 3] = above
                    continue
                dst[d] = combine_layer(dst[d], src[s], op, gate, blend,
                                       above)
                dst[d + 1] = combine_layer(dst[d + 1], src[s + 1], op, gate,
                                           blend, above)
                dst[d + 2] = combine_layer(dst[d + 2], src[s + 2], op, gate,
                                           blend, above)
                dst[d + 3] = below + (255 - below) * above // 255
    return out


# ------------------------------------------------------------------ selection
class Selection(object):
    """A per-pixel byte mask: 0 unselected, 255 selected."""

    __slots__ = ("width", "height", "mask", "active")

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.mask = bytearray(width * height)
        self.active = False

    def snapshot(self):
        return bytes(self.mask)

    def restore(self, data):
        self.mask = bytearray(data)
        self.active = any(self.mask)

    def clear(self):
        self.mask = bytearray(self.width * self.height)
        self.active = False

    def select_all(self):
        self.mask = bytearray(b"\xff" * (self.width * self.height))
        self.active = True

    def invert(self):
        self.mask = bytearray(255 - v for v in self.mask)
        self.active = any(self.mask)

    def get(self, x, y):
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return 0
        return self.mask[y * self.width + x]

    def count(self):
        return sum(1 for v in self.mask if v)

    def bytes_count(self):
        return self.count() * 4

    def bounds(self):
        """(x, y, w, h) of the selected area, or None when empty."""
        if not self.active:
            return None
        w = self.width
        top = None
        bottom = None
        left = w
        right = -1
        for y in range(self.height):
            row = self.mask[y * w:(y + 1) * w]
            if not any(row):
                continue
            if top is None:
                top = y
            bottom = y
            for x, v in enumerate(row):
                if v:
                    if x < left:
                        left = x
                    if x > right:
                        right = x
        if top is None:
            return None
        return (left, top, right - left + 1, bottom - top + 1)

    def apply_mask(self, mask, mode="replace"):
        """Combine a full-canvas 0/255 mask into this selection."""
        if len(mask) != len(self.mask):
            raise HexError("mask size mismatch")
        if mode == "replace":
            self.mask = bytearray(mask)
        elif mode == "add":
            self.mask = bytearray(
                255 if (a or b) else 0 for a, b in zip(self.mask, mask))
        elif mode == "subtract":
            self.mask = bytearray(
                a if not b else 0 for a, b in zip(self.mask, mask))
        elif mode == "intersect":
            self.mask = bytearray(
                a if b else 0 for a, b in zip(self.mask, mask))
        else:
            raise HexError("unknown selection mode: %s" % mode)
        self.active = any(self.mask)

    def mask_or_full(self):
        """The mask, or None when nothing is selected (meaning everything)."""
        return self.mask if self.active else None


def selection_rect_mask(width, height, x0, y0, x1, y1):
    """The pixels of the rectangle between two points, clipped to the canvas.

    The points may sit outside the canvas, which is how a marquee dragged past
    the edge still selects everything between itself and the border. A box
    that misses the canvas entirely selects nothing.
    """
    mask = bytearray(width * height)
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    xa, xb = max(xa, 0), min(xb, width - 1)
    ya, yb = max(ya, 0), min(yb, height - 1)
    if xb < xa or yb < ya:
        return mask
    for y in range(ya, yb + 1):
        start = y * width + xa
        mask[start:start + (xb - xa + 1)] = b"\xff" * (xb - xa + 1)
    return mask


def selection_ellipse_mask(width, height, x0, y0, x1, y1):
    """The pixels of the ellipse in the box between two points.

    The box is measured before it is clipped, so a drag that runs past the
    canvas edge keeps the ellipse it describes instead of being squashed into
    the corner. Only the rows and columns inside the canvas are written.
    """
    mask = bytearray(width * height)
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    cx = (xa + xb + 1) / 2.0
    cy = (ya + yb + 1) / 2.0
    rx = max(0.5, (xb - xa + 1) / 2.0)
    ry = max(0.5, (yb - ya + 1) / 2.0)
    first_row = max(ya, 0)
    last_row = min(yb, height - 1)
    x_lo, x_hi = max(xa, 0), min(xb, width - 1)
    if x_hi < x_lo or last_row < first_row:
        return mask
    for y in range(first_row, last_row + 1):
        dy = (y + 0.5 - cy) / ry
        if abs(dy) > 1.0:
            continue
        span = rx * (1.0 - dy * dy) ** 0.5
        first = max(x_lo, int(round(cx - span - 0.5)))
        last = min(x_hi, int(round(cx + span - 0.5)))
        if last < first:
            continue
        base = y * width
        for x in range(first, last + 1):
            dx = (x + 0.5 - cx) / rx
            if dx * dx + dy * dy <= 1.0:
                mask[base + x] = 255
    return mask


def _line_points(x0, y0, x1, y1):
    """Bresenham points, endpoints included."""
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


def fill_holes(mask, width, height):
    """Return a copy of `mask` with every enclosed pocket selected.

    Pixels that cannot be reached from the canvas border by walking unselected
    pixels are inside the shape, so a path that circles back on itself (a
    self-crossing lasso, or a polygon whose vertices enclose a second loop)
    cannot leave holes in the middle of the selection.
    """
    width, height = int(width), int(height)
    source = bytearray(mask)
    if width <= 0 or height <= 0 or len(source) != width * height:
        return source
    outside = bytearray(width * height)
    stack = []
    for x in range(width):
        for y in (0, height - 1):
            index = y * width + x
            if not source[index] and not outside[index]:
                outside[index] = 1
                stack.append(index)
    for y in range(height):
        for x in (0, width - 1):
            index = y * width + x
            if not source[index] and not outside[index]:
                outside[index] = 1
                stack.append(index)
    while stack:
        index = stack.pop()
        y, x = divmod(index, width)
        if x > 0 and not source[index - 1] and not outside[index - 1]:
            outside[index - 1] = 1
            stack.append(index - 1)
        if x + 1 < width and not source[index + 1] and not outside[index + 1]:
            outside[index + 1] = 1
            stack.append(index + 1)
        if y > 0 and not source[index - width] \
                and not outside[index - width]:
            outside[index - width] = 1
            stack.append(index - width)
        if y + 1 < height and not source[index + width] \
                and not outside[index + width]:
            outside[index + width] = 1
            stack.append(index + width)
    for index in range(width * height):
        if not outside[index]:
            source[index] = 255
    return source


def selection_polygon_mask(width, height, points):
    """Even-odd scanline fill of a polygon, with its outline included.

    Enclosed pockets are filled afterwards, so a path that crosses itself
    still yields a solid byte selection instead of one with holes in it.
    """
    mask = bytearray(width * height)
    pts = [(int(px), int(py)) for px, py in points]
    if len(pts) < 2:
        if pts:
            x, y = pts[0]
            if 0 <= x < width and 0 <= y < height:
                mask[y * width + x] = 255
        return mask
    if len(pts) == 2:
        for x, y in _line_points(pts[0][0], pts[0][1], pts[1][0], pts[1][1]):
            if 0 <= x < width and 0 <= y < height:
                mask[y * width + x] = 255
        return mask

    ys = [p[1] for p in pts]
    ymin = clamp(min(ys), 0, height - 1)
    ymax = clamp(max(ys), 0, height - 1)
    count = len(pts)
    for y in range(ymin, ymax + 1):
        py = y + 0.5
        crossings = []
        for i in range(count):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % count]
            if ay == by:
                continue
            if (ay <= py < by) or (by <= py < ay):
                t = (py - ay) / float(by - ay)
                crossings.append(ax + t * (bx - ax))
        crossings.sort()
        for i in range(0, len(crossings) - 1, 2):
            xs = int(math.ceil(crossings[i] - 0.5))
            xe = int(math.floor(crossings[i + 1] - 0.5))
            xs = clamp(xs, 0, width - 1)
            xe = clamp(xe, 0, width - 1)
            if xe >= xs:
                base = y * width
                mask[base + xs:base + xe + 1] = b"\xff" * (xe - xs + 1)
    # include the outline so thin polygons still select their edges
    for i in range(count):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % count]
        for x, y in _line_points(ax, ay, bx, by):
            if 0 <= x < width and 0 <= y < height:
                mask[y * width + x] = 255
    return fill_holes(mask, width, height)


def clean_path(points, tolerance=0.5):
    """Tidy a hand-drawn path: drop repeats and points on straight runs.

    A slow drag emits the same cell many times and hundreds of points that
    only differ by a pixel. Keeping the bends removes that noise without
    changing the shape the mask takes.
    """
    cleaned = []
    for x, y in points:
        point = (int(x), int(y))
        if cleaned and cleaned[-1] == point:
            continue
        cleaned.append(point)
    if len(cleaned) < 3:
        return cleaned
    out = [cleaned[0]]
    for index in range(1, len(cleaned) - 1):
        ax, ay = out[-1]
        bx, by = cleaned[index]
        cx, cy = cleaned[index + 1]
        dx = cx - ax
        dy = cy - ay
        length = math.hypot(dx, dy)
        if not length:
            out.append((bx, by))
            continue
        distance = abs((bx - ax) * dy - (by - ay) * dx) / length
        if distance >= tolerance:
            out.append((bx, by))
    out.append(cleaned[-1])
    return out


def pick_related(bitmap, x, y, tolerance=0, ignore_alpha=False, contiguous=True):
    """Select pixel data related to the pixel at (x, y).

    `contiguous` gives a magic wand (4-connected flood), otherwise every
    related pixel in the image is selected. `tolerance` compares each channel;
    `ignore_alpha` compares RGB only.
    """
    width, height = bitmap.width, bitmap.height
    mask = bytearray(width * height)
    if x < 0 or y < 0 or x >= width or y >= height:
        return mask
    data = bitmap.data
    seed = bitmap.rgba(x, y)
    target = seed[:3] if ignore_alpha else seed
    tol = int(tolerance or 0)

    def related(i):
        base = i * 4
        if ignore_alpha:
            return (abs(data[base] - target[0]) <= tol
                    and abs(data[base + 1] - target[1]) <= tol
                    and abs(data[base + 2] - target[2]) <= tol)
        return (abs(data[base] - target[0]) <= tol
                and abs(data[base + 1] - target[1]) <= tol
                and abs(data[base + 2] - target[2]) <= tol
                and abs(data[base + 3] - target[3]) <= tol)

    if not contiguous:
        for i in range(width * height):
            if related(i):
                mask[i] = 255
        return mask

    from collections import deque

    queue = deque()
    start = y * width + x
    mask[start] = 255
    queue.append(start)
    while queue:
        i = queue.popleft()
        cx = i % width
        cy = i // width
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            j = ny * width + nx
            if mask[j]:
                continue
            if related(j):
                mask[j] = 255
                queue.append(j)
    return mask


# --------------------------------------------------------------------- edits
def _iter_selected(mask, width, height):
    if mask is None:
        return range(width * height)
    return (i for i in range(width * height) if mask[i])


def paint_brush(bitmap, mask, x, y, size, color):
    """Paint a square brush of `size` pixels, clipped by the selection."""
    size = max(1, int(size))
    half = size // 2
    x0 = x - half
    y0 = y - half
    count = 0
    for py in range(y0, y0 + size):
        if py < 0 or py >= bitmap.height:
            continue
        row = py * bitmap.width
        for px in range(x0, x0 + size):
            if px < 0 or px >= bitmap.width:
                continue
            i = row + px
            if mask is not None and mask[i] == 0:
                continue
            b = i * 4
            bitmap.data[b] = color[0]
            bitmap.data[b + 1] = color[1]
            bitmap.data[b + 2] = color[2]
            bitmap.data[b + 3] = color[3]
            count += 1
    return count


def paint_line(bitmap, mask, x0, y0, x1, y1, size, color):
    count = 0
    for x, y in _line_points(int(x0), int(y0), int(x1), int(y1)):
        count += paint_brush(bitmap, mask, x, y, size, color)
    return count


def flood_fill(bitmap, mask, x, y, color, tolerance=0, contiguous=True):
    """Fill related pixel data with `color`, restricted to the selection."""
    width, height = bitmap.width, bitmap.height
    if x < 0 or y < 0 or x >= width or y >= height:
        return 0
    if mask is not None and mask[y * width + x] == 0:
        return 0
    seed = bitmap.rgba(x, y)
    tol = int(tolerance or 0)

    def related(i):
        base = i * 4
        return (abs(bitmap.data[base] - seed[0]) <= tol
                and abs(bitmap.data[base + 1] - seed[1]) <= tol
                and abs(bitmap.data[base + 2] - seed[2]) <= tol
                and abs(bitmap.data[base + 3] - seed[3]) <= tol)

    start = y * width + x
    filled = 0

    def put(i):
        base = i * 4
        bitmap.data[base] = color[0]
        bitmap.data[base + 1] = color[1]
        bitmap.data[base + 2] = color[2]
        bitmap.data[base + 3] = color[3]

    if not contiguous:
        for i in range(width * height):
            if mask is not None and mask[i] == 0:
                continue
            if related(i):
                put(i)
                filled += 1
        return filled

    if seed[:3] == color[:3] and seed[3] == color[3]:
        return 0
    from collections import deque

    seen = bytearray(width * height)
    queue = deque([start])
    seen[start] = 1
    while queue:
        i = queue.popleft()
        put(i)
        filled += 1
        cx = i % width
        cy = i // width
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            j = ny * width + nx
            if seen[j]:
                continue
            if mask is not None and mask[j] == 0:
                continue
            if related(j):
                seen[j] = 1
                queue.append(j)
    return filled


def apply_op(bitmap, mask, operand, op, strength=255, wrap=False,
             include_alpha=False):
    """Combine every selected pixel with `operand` using a bitwise op."""
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    changed = 0
    for i in _iter_selected(mask, width, height):
        base = i * 4
        data[base] = combine(data[base], operand[0], op, strength, wrap)
        data[base + 1] = combine(data[base + 1], operand[1], op, strength, wrap)
        data[base + 2] = combine(data[base + 2], operand[2], op, strength, wrap)
        if include_alpha:
            data[base + 3] = combine(data[base + 3], operand[3], op, strength, wrap)
        changed += 1
    return changed


def apply_map(bitmap, mask, mapping, tolerance=0):
    """Recolor selected pixels using a {(r,g,b): (r,g,b)} map."""
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    keys = list(mapping.items())
    changed = 0
    for i in _iter_selected(mask, width, height):
        base = i * 4
        color = (data[base], data[base + 1], data[base + 2])
        dest = mapping.get(color)
        if dest is None and tolerance > 0:
            for old, new in keys:
                if matches(color, old, tolerance):
                    dest = new
                    break
        if dest is not None:
            data[base] = dest[0]
            data[base + 1] = dest[1]
            data[base + 2] = dest[2]
            changed += 1
    return changed


def apply_hue(bitmap, mask, degrees):
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    changed = 0
    for i in _iter_selected(mask, width, height):
        base = i * 4
        r, g, b = hue_shift(data[base], data[base + 1], data[base + 2], degrees)
        data[base], data[base + 1], data[base + 2] = r, g, b
        changed += 1
    return changed


def unique_colors(bitmap, mask=None):
    """Distinct RGB colors in the selection, sorted by luminance."""
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    seen = {}
    for i in _iter_selected(mask, width, height):
        base = i * 4
        key = (data[base], data[base + 1], data[base + 2])
        if key not in seen:
            seen[key] = luminance(*key)
    return sorted(seen, key=lambda c: (seen[c], c))


def apply_ramp(bitmap, mask, shift):
    """Rotate the palette of the selection along its luminance ramp."""
    colors = unique_colors(bitmap, mask)
    if not colors:
        return 0
    return apply_map(bitmap, mask, rotate_ramp(colors, shift))


def clear_region(bitmap, mask):
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    count = 0
    for i in _iter_selected(mask, width, height):
        base = i * 4
        data[base:base + 4] = b"\x00\x00\x00\x00"
        count += 1
    return count


def extract_selection(bitmap, mask, bounds):
    """Copy the selected pixels into a bounds-sized bitmap.

    Pixels outside the mask stay transparent, so the piece keeps its shape
    and its transparency while it is moved or scaled.
    """
    left, top, width, height = bounds
    piece = Bitmap(width, height)
    full = bitmap.width
    for y in range(height):
        src_row = (top + y) * full + left
        dst_row = y * width
        for x in range(width):
            if mask is not None and not mask[src_row + x]:
                continue
            i = (src_row + x) * 4
            j = (dst_row + x) * 4
            piece.data[j:j + 4] = bitmap.data[i:i + 4]
    return piece


def stamp_bitmap(bitmap, piece, origin):
    """Paste a bitmap over another, skipping its transparent pixels."""
    ox, oy = origin
    width, height = bitmap.width, bitmap.height
    for y in range(piece.height):
        ty = oy + y
        if ty < 0 or ty >= height:
            continue
        src_row = y * piece.width
        for x in range(piece.width):
            tx = ox + x
            if tx < 0 or tx >= width:
                continue
            i = (src_row + x) * 4
            if not piece.data[i + 3]:
                continue
            d = (ty * width + tx) * 4
            bitmap.data[d:d + 4] = piece.data[i:i + 4]


def piece_mask(piece, origin, width, height):
    """A full-canvas selection mask covering a stamped piece."""
    mask = bytearray(width * height)
    ox, oy = origin
    for y in range(piece.height):
        ty = oy + y
        if ty < 0 or ty >= height:
            continue
        row = ty * width
        src_row = y * piece.width
        for x in range(piece.width):
            tx = ox + x
            if 0 <= tx < width and piece.data[(src_row + x) * 4 + 3]:
                mask[row + tx] = 255
    return mask


def invert_colors(bitmap, mask):
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    count = 0
    for i in _iter_selected(mask, width, height):
        base = i * 4
        data[base] = 255 - data[base]
        data[base + 1] = 255 - data[base + 1]
        data[base + 2] = 255 - data[base + 2]
        count += 1
    return count


def place_block(bitmap, mask, block, mode="stamp", origin=(0, 0), mirror=False):
    """Draw a Block into the bitmap, honouring the selection mask.

    Selections are byte masks, so `.` (SKIP) pixels leave the artwork alone,
    exactly as the hex block declares.
    """
    rect = (0, 0, bitmap.width, bitmap.height)
    blit(bitmap.data, rect, block, mode, origin, mirror, mask)
    return block.width * block.height


def format_dump(bitmap, mask=None, max_rows=4096, per_row=8):
    """Hex dump of selected pixel data: offset then RGBA bytes."""
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    lines = []
    row = []
    row_offset = 0
    for i in _iter_selected(mask, width, height):
        base = i * 4
        if not row:
            row_offset = base
        row.append("%02X%02X%02X%02X"
                   % (data[base], data[base + 1], data[base + 2], data[base + 3]))
        if len(row) >= per_row:
            lines.append("%08X  %s" % (row_offset, " ".join(row)))
            row = []
            if len(lines) >= max_rows:
                lines.append("... truncated ...")
                return "\n".join(lines)
    if row:
        lines.append("%08X  %s" % (row_offset, " ".join(row)))
    return "\n".join(lines)


def selected_bytes(bitmap, mask=None):
    """The selected pixel data as raw RGBA bytes, in image order."""
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    out = bytearray()
    for i in _iter_selected(mask, width, height):
        base = i * 4
        out += data[base:base + 4]
    return out


def block_from_bitmap(bitmap, rect=None, skip_transparent=True):
    """Render part of a bitmap as hex block text."""
    if rect is None:
        rect = (0, 0, bitmap.width, bitmap.height)
    part = bitmap.rect_rgba(*rect)
    return format_block(part.data, part.width, part.height, skip_transparent)


# ------------------------------------------------------------------------ undo
class UndoStack(object):
    """Swap-based undo over whole document states under a byte budget.

    A state is a dict of pixels plus canvas properties, so undoing a canvas
    resize or a color-mode change restores the previous size and metadata too.
    """

    def __init__(self, budget=96 * 1024 * 1024):
        self.budget = int(budget)
        self._undo = []
        self._redo = []
        self._bytes = 0

    def _size(self, *states):
        total = 0
        for state in states:
            layers = state.get("layers")
            if layers:
                total += sum(len(entry["data"]) for entry in layers)
            else:
                total += len(state["data"])
        return total

    def clear(self):
        self._undo = []
        self._redo = []
        self._bytes = 0

    def push(self, before, after):
        if before == after:
            return False
        self._undo.append((before, after))
        self._bytes += self._size(before, after)
        while self._bytes > self.budget and len(self._undo) > 1:
            old_before, old_after = self._undo.pop(0)
            self._bytes -= self._size(old_before, old_after)
        self._redo = []
        return True

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self, document):
        if not self._undo:
            return False
        before, after = self._undo.pop()
        self._bytes -= self._size(before, after)
        self._redo.append((document.state(), after))
        document.restore(before)
        return True

    def redo(self, document):
        if not self._redo:
            return False
        current, after = self._redo.pop()
        self._undo.append((current, after))
        self._bytes += self._size(current, after)
        document.restore(after)
        return True


class Document(object):
    """Layers, canvas properties, selection, undo history and path."""

    def __init__(self, width, height, data=None, path=None, ppi=72.0,
                 color_mode="RGB", alpha=True):
        self.layers = [Layer(width, height, data=data)]
        self.layer_index = 0
        self.groups = []
        self.selection = Selection(width, height)
        self.undo = UndoStack()
        self.path = path
        self.display_format = "png"
        # Set by every edit that pushes undo history and cleared by a save, so
        # the window can warn before throwing unsaved work away.
        self.dirty = False
        self.matte = MATTE_WHITE
        self.ppi = float(ppi)
        self.color_mode = color_mode
        self.alpha = bool(alpha)
        # Working layout for this canvas. When set, every committed edit is
        # reduced to these channel widths; None leaves 8-bit pixels untouched.
        # Packing and order travel with the document in a .h2p project.
        self.working_widths = None
        self.working_packing = "packed"
        self.working_order = "RGB"

    @property
    def layer_count(self):
        return len(self.layers)

    @property
    def active_layer(self):
        return self.layers[self.layer_index]

    @property
    def bitmap(self):
        """The active layer's pixels: every tool edits this one layer."""
        return self.layers[self.layer_index].bitmap

    @bitmap.setter
    def bitmap(self, bitmap):
        self.layers[self.layer_index].bitmap = bitmap

    @property
    def width(self):
        return self.bitmap.width

    @property
    def height(self):
        return self.bitmap.height

    def mask(self):
        return self.selection.mask_or_full()

    def set_working_widths(self, widths, quantize=True):
        """Set the canvas working limit, optionally reducing existing pixels."""
        if widths is None:
            self.working_widths = None
            return 0
        from . import bitdepth
        self.working_widths = bitdepth.channel_widths(widths)
        return self.limit_pixels() if quantize else 0

    def working_layout(self):
        """The saved working layout, or None when no limit is set."""
        if not self.working_widths:
            return None
        return {"raw_channels": tuple(self.working_widths),
                "raw_packing": self.working_packing,
                "raw_order": self.working_order}

    def set_working_layout(self, working, quantize=True):
        """Set widths, packing and order, optionally reducing pixels."""
        if working is None:
            self.working_widths = None
            self.working_packing = "packed"
            self.working_order = "RGB"
            return 0
        if not isinstance(working, dict):
            raise HexError("working layout must be a mapping")
        from . import bitdepth
        widths = bitdepth.channel_widths(working.get("raw_channels"))
        packing = bitdepth.check_packing(working.get("raw_packing"))
        order = bitdepth.check_order(working.get("raw_order"))
        self.working_widths = widths
        self.working_packing = packing
        self.working_order = order
        return self.limit_pixels() if quantize else 0

    def limit_color(self, color):
        """A colour clamped to this canvas's working widths."""
        if not self.working_widths:
            return tuple(color)
        from . import bitdepth
        return bitdepth.limit_color(color, self.working_widths)

    def limit_pixels(self, rect=None):
        """Reduce every layer (or one rectangle) to the working widths."""
        if not self.working_widths:
            return 0
        from . import bitdepth
        count = 0
        if rect is None:
            for layer in self.layers:
                bitmap = layer.bitmap
                limited = bitdepth.limit_rgba(bitmap.data, self.working_widths)
                if limited != bitmap.data:
                    bitmap.data[:] = limited
                count += bitmap.width * bitmap.height
            return count
        left, top, width, height = [int(value) for value in rect]
        right = min(self.width, left + width)
        bottom = min(self.height, top + height)
        left, top = max(0, left), max(0, top)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return 0
        for layer in self.layers:
            bitmap = layer.bitmap
            for y in range(top, bottom):
                start = (y * bitmap.width + left) * 4
                end = start + width * 4
                limited = bitdepth.limit_rgba(bitmap.data[start:end],
                                              self.working_widths)
                if limited != bitmap.data[start:end]:
                    bitmap.data[start:end] = limited
            count += width * height
        return count

    def flatten(self, rect=None):
        """Composite the visible layers, or one rectangle, into a Bitmap."""
        if (self.layer_count == 1 and self.layers[0].visible
                and self.layers[0].alpha >= 255 and not self.layers[0].group):
            bitmap = self.layers[0].bitmap
            return bitmap.rect_rgba(*rect) if rect else bitmap.copy()
        return composite_layers(self.layers, rect, (self.width, self.height),
                                self.group_map())

    def add_layer(self, name=None, op="none", blend="none", gate="none"):
        """Insert an empty layer above the active one and select it."""
        name = name or "Layer %d" % (self.layer_count + 1)
        layer = Layer(self.width, self.height, name, op, blend=blend,
                      gate=gate)
        self.layer_index += 1
        self.layers.insert(self.layer_index, layer)
        return layer

    def duplicate_layer(self, index=None):
        index = self.layer_index if index is None else int(index)
        layer = self.layers[index].copy()
        layer.name = "%s copy" % layer.name
        self.layers.insert(index + 1, layer)
        self.layer_index = index + 1
        return layer

    def remove_layer(self, index=None):
        if self.layer_count <= 1:
            return False
        index = self.layer_index if index is None else int(index)
        del self.layers[index]
        self.layer_index = min(index, self.layer_count - 1)
        self.prune_groups()
        return True

    def prune_groups(self):
        """Forget group entries that no longer have any members."""
        names = set(layer.group for layer in self.layers if layer.group)
        self.groups = [group for group in self.groups if group.name in names]

    # ------------------------------------------------------------ groups
    def group_map(self):
        """Group name to LayerGroup, as composite_layers expects it."""
        return {group.name: group for group in self.groups}

    def group(self, name):
        for group in self.groups:
            if group.name == name:
                return group
        return None

    def ensure_group(self, name=None):
        """Return a group, creating it (with a fresh name) when needed."""
        if name:
            found = self.group(name)
            if found is not None:
                return found
            group = LayerGroup(str(name))
            self.groups.append(group)
            return group
        number = len(self.groups) + 1
        name = "Group %d" % number
        while self.group(name) is not None:
            number += 1
            name = "Group %d" % number
        return self.ensure_group(name)

    def group_layer(self, index=None, name=None):
        """Put a layer into a group, creating the group when it is new."""
        index = self.layer_index if index is None else int(index)
        if index < 0 or index >= self.layer_count:
            return None
        group = self.ensure_group(name)
        self.layers[index].group = group.name
        return group

    def group_members(self, name):
        """Indices of the layers in a group, bottom first."""
        return [index for index, layer in enumerate(self.layers)
                if layer.group == name]

    def rename_group(self, old, new):
        """Rename a group and the layer tags that point at it."""
        group = self.group(old)
        new = str(new or "").strip()
        if group is None or not new or new == old or self.group(new):
            return False
        group.name = new
        for layer in self.layers:
            if layer.group == old:
                layer.group = new
        return True

    def rename_layer(self, index=None, name=None):
        index = self.layer_index if index is None else int(index)
        name = str(name or "").strip()
        if index < 0 or index >= self.layer_count or not name:
            return False
        self.layers[index].name = name
        return True

    def remove_group(self, name, merge=False):
        """Drop a group; its layers stay, ungrouped (or merged into one)."""
        if merge:
            return self.merge_group(name)
        group = self.group(name)
        if group is None:
            return 0
        count = 0
        for layer in self.layers:
            if layer.group == name:
                layer.group = ""
                count += 1
        self.groups.remove(group)
        return count

    # ------------------------------------------------------------- merging
    def merge_down(self, index=None):
        """Combine a layer into the one below, using its op and opacity."""
        index = self.layer_index if index is None else int(index)
        if index <= 0 or index >= self.layer_count:
            return False
        below = self.layers[index - 1]
        above = self.layers[index]
        merged = composite_layers([below, above], None,
                                  (self.width, self.height), self.group_map())
        below.bitmap = merged
        # The opacity is baked into the pixels now, so clear it to avoid
        # scaling the merged result a second time.
        below.alpha = 255
        del self.layers[index]
        self.layer_index = index - 1
        return True

    def merge_group(self, name):
        """Flatten a whole group into one layer where its lowest member was."""
        group = self.group(name)
        members = self.group_members(name)
        if group is None or not members:
            return False
        layers = [self.layers[index] for index in members]
        merged = composite_layers(layers, None, (self.width, self.height),
                                  {name: group})
        result = Layer(self.width, self.height, group.name,
                       layers[0].op, True, bytes(merged.data),
                       blend=layers[0].blend, gate=layers[0].gate)
        for index in reversed(members):
            del self.layers[index]
        self.layers.insert(members[0], result)
        self.layer_index = clamp(members[0], 0, self.layer_count - 1)
        self.groups.remove(group)
        return True

    def move_layer(self, delta, index=None):
        index = self.layer_index if index is None else int(index)
        target = index + int(delta)
        if target < 0 or target >= self.layer_count or target == index:
            return False
        self.layers[index], self.layers[target] = (self.layers[target],
                                                   self.layers[index])
        self.layer_index = target
        return True

    def move_layer_to(self, target, index=None, group=None):
        """Move a layer to another slot and, when given, into another group.

        `group` of "" takes the layer out of its group and None leaves the
        membership alone. The active layer stays active even when the move
        crosses over it.
        """
        index = self.layer_index if index is None else int(index)
        target = int(target)
        if (index < 0 or index >= self.layer_count
                or target < 0 or target >= self.layer_count):
            return False
        layer = self.layers[index]
        regroup = group is not None and str(group) != layer.group
        if target == index and not regroup:
            return False
        active = self.active_layer
        if target != index:
            self.layers.pop(index)
            self.layers.insert(target, layer)
            self.layer_index = self.layers.index(active)
        if regroup:
            name = str(group or "")
            if name:
                self.ensure_group(name)
            layer.group = name
            self.prune_groups()
        return True

    def reorder_layer(self, target, index=None):
        """Move a layer to another slot in the stack."""
        return self.move_layer_to(target, index)

    def transform_all(self, func):
        """Apply a bitmap transform (flip, rotate, resize) to every layer."""
        for layer in self.layers:
            layer.bitmap = func(layer.bitmap)

    def state(self):
        """Everything an undo step has to restore, including the canvas size."""
        return {
            "data": bytes(self.bitmap.data),
            "width": self.width,
            "height": self.height,
            "ppi": self.ppi,
            "color_mode": self.color_mode,
            "alpha": self.alpha,
            "layers": [layer.to_state() for layer in self.layers],
            "groups": [group.to_state() for group in self.groups],
            "layer_index": self.layer_index,
        }

    def restore(self, state):
        width, height = state["width"], state["height"]
        if state.get("layers"):
            self.layers = [Layer.from_state(entry, width, height)
                           for entry in state["layers"]]
            self.layer_index = clamp(int(state.get("layer_index", 0)), 0,
                                     len(self.layers) - 1)
        else:
            self.layers = [Layer(width, height, data=state["data"])]
            self.layer_index = 0
        self.groups = [LayerGroup.from_state(entry)
                       for entry in state.get("groups") or []]
        known = set(group.name for group in self.groups)
        for layer in self.layers:
            if layer.group and layer.group not in known:
                self.groups.append(LayerGroup(layer.group))
                known.add(layer.group)
        self.selection = Selection(width, height)
        self.ppi = state["ppi"]
        self.color_mode = state["color_mode"]
        self.alpha = state["alpha"]
        # Undo can revive a pre-limit snapshot; the working limit still wins.
        self.limit_pixels()

    def begin(self):
        return self.state()

    def commit(self, before):
        # A working layout is a hard limit on the canvas, so catch any edit
        # path that writes bytes directly before the state enters history.
        self.limit_pixels()
        self.dirty = True
        return self.undo.push(before, self.state())

    def undo_last(self):
        changed = self.undo.undo(self)
        if changed:
            self.dirty = True
        return changed

    def redo_last(self):
        changed = self.undo.redo(self)
        if changed:
            self.dirty = True
        return changed

    def replace_bitmap(self, bitmap, keep_selection=False):
        self.layers = [Layer(bitmap.width, bitmap.height, data=bitmap.data)]
        self.groups = []
        self.layer_index = 0
        if not keep_selection:
            self.selection = Selection(bitmap.width, bitmap.height)
