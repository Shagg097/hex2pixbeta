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

# How a layer combines with the layers below it. "normal" is plain
# source-over, the default: the layer covers what is underneath. The rest
# combine per channel with the layers below. Unlike the byte operations above
# there are no bitwise modes here.
LAYER_OPS = ("normal", "add", "subtract", "multiply", "divide")
LAYER_OP_LABELS = {
    "normal": "Normal",
    "add": "Add",
    "subtract": "Subtract",
    "multiply": "Multiply",
    "divide": "Divide",
}

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
    else:
        raise HexError("unknown operation: %s" % op)
    raw = raw % 256 if wrap else clamp(raw, 0, 255)
    return clamp(round_half_up(a + (raw - a) * (strength / 255.0)), 0, 255)


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


def rotate_piece(bitmap, degrees):
    """Rotate a bitmap clockwise by any angle, keeping the whole piece.

    Nearest-neighbour sampling around the centre; the result is the bounding
    box of the rotated rectangle, so nothing is cropped and quarter turns are
    exact.
    """
    angle = float(degrees) % 360.0
    if not angle:
        return bitmap.copy()
    radians = math.radians(angle)
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    w, h = bitmap.width, bitmap.height
    half_w, half_h = w / 2.0, h / 2.0
    xs, ys = [], []
    for x, y in ((0, 0), (w, 0), (0, h), (w, h)):
        dx, dy = x - half_w, y - half_h
        xs.append(dx * cos_a - dy * sin_a)
        ys.append(dx * sin_a + dy * cos_a)
    out_w = max(1, int(round(max(xs) - min(xs))))
    out_h = max(1, int(round(max(ys) - min(ys))))
    out = Bitmap(out_w, out_h)
    back = math.radians(-angle)
    cos_b, sin_b = math.cos(back), math.sin(back)
    cx_out, cy_out = out_w / 2.0, out_h / 2.0
    src = bitmap.data
    dst = out.data
    for oy in range(out_h):
        dy = oy + 0.5 - cy_out
        for ox in range(out_w):
            dx = ox + 0.5 - cx_out
            sx = half_w + dx * cos_b - dy * sin_b
            sy = half_h + dx * sin_b + dy * cos_b
            ix, iy = int(math.floor(sx)), int(math.floor(sy))
            if 0 <= ix < w and 0 <= iy < h:
                si = (iy * w + ix) * 4
                di = (oy * out_w + ox) * 4
                dst[di:di + 4] = src[si:si + 4]
    return out


# --------------------------------------------------------------------- layers
class Layer(object):
    """One bitmap in a document's layer stack.

    `op` is how the layer combines with the layers below it, and its alpha is
    the strength of that operation. `alpha` is the layer's own opacity, which
    scales that strength for every pixel. `Document.layers[0]` is the bottom
    layer.
    """

    __slots__ = ("bitmap", "name", "op", "visible", "alpha")

    def __init__(self, width, height, name="Layer 1", op="normal",
                 visible=True, data=None, alpha=255):
        self.bitmap = Bitmap(width, height, data)
        self.name = str(name)
        self.op = op if op in LAYER_OPS else "normal"
        self.visible = bool(visible)
        self.alpha = clamp(int(alpha), 0, 255)

    def __repr__(self):
        return "Layer(%r, %s)" % (self.name, self.op)

    def copy(self):
        return Layer(self.bitmap.width, self.bitmap.height, self.name, self.op,
                     self.visible, bytes(self.bitmap.data), self.alpha)

    def to_state(self):
        return {"name": self.name, "op": self.op, "visible": self.visible,
                "alpha": self.alpha, "data": bytes(self.bitmap.data)}

    @classmethod
    def from_state(cls, state, width, height):
        return cls(width, height, state.get("name", "Layer"),
                   state.get("op", "normal"), state.get("visible", True),
                   state.get("data"), state.get("alpha", 255))


def composite_layers(layers, rect=None, frame=None):
    """Flatten the visible layers into one Bitmap.

    `frame` is the full canvas size and `rect` limits the result to one
    rectangle, which is what the canvas view composites while painting. Each
    layer combines with the stack below it using its own operation, with its
    alpha as the strength, so transparent pixels leave the result unchanged.
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
        opacity = clamp(int(layer.alpha), 0, 255)
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
                dst[d] = combine(dst[d], src[s], op, above)
                dst[d + 1] = combine(dst[d + 1], src[s + 1], op, above)
                dst[d + 2] = combine(dst[d + 2], src[s + 2], op, above)
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
    mask = bytearray(width * height)
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    xa = clamp(xa, 0, width - 1)
    xb = clamp(xb, 0, width - 1)
    ya = clamp(ya, 0, height - 1)
    yb = clamp(yb, 0, height - 1)
    for y in range(ya, yb + 1):
        start = y * width + xa
        mask[start:start + (xb - xa + 1)] = b"\xff" * (xb - xa + 1)
    return mask


def selection_ellipse_mask(width, height, x0, y0, x1, y1):
    mask = bytearray(width * height)
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    xa = clamp(xa, 0, width - 1)
    xb = clamp(xb, 0, width - 1)
    ya = clamp(ya, 0, height - 1)
    yb = clamp(yb, 0, height - 1)
    cx = (xa + xb + 1) / 2.0
    cy = (ya + yb + 1) / 2.0
    rx = max(0.5, (xb - xa + 1) / 2.0)
    ry = max(0.5, (yb - ya + 1) / 2.0)
    for y in range(ya, yb + 1):
        dy = (y + 0.5 - cy) / ry
        base = y * width
        for x in range(xa, xb + 1):
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
        self.selection = Selection(width, height)
        self.undo = UndoStack()
        self.path = path
        self.display_format = "png"
        self.matte = MATTE_WHITE
        self.ppi = float(ppi)
        self.color_mode = color_mode
        self.alpha = bool(alpha)

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

    def flatten(self, rect=None):
        """Composite the visible layers, or one rectangle, into a Bitmap."""
        if (self.layer_count == 1 and self.layers[0].visible
                and self.layers[0].alpha >= 255):
            bitmap = self.layers[0].bitmap
            return bitmap.rect_rgba(*rect) if rect else bitmap.copy()
        return composite_layers(self.layers, rect, (self.width, self.height))

    def add_layer(self, name=None, op="normal"):
        """Insert an empty layer above the active one and select it."""
        name = name or "Layer %d" % (self.layer_count + 1)
        layer = Layer(self.width, self.height, name, op)
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

    def reorder_layer(self, target, index=None):
        """Move a layer to another slot in the stack."""
        index = self.layer_index if index is None else int(index)
        target = int(target)
        if (index == target or index < 0 or index >= self.layer_count
                or target < 0 or target >= self.layer_count):
            return False
        layer = self.layers.pop(index)
        self.layers.insert(target, layer)
        if index == self.layer_index:
            self.layer_index = target
        return True

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
        self.selection = Selection(width, height)
        self.ppi = state["ppi"]
        self.color_mode = state["color_mode"]
        self.alpha = state["alpha"]

    def begin(self):
        return self.state()

    def commit(self, before):
        return self.undo.push(before, self.state())

    def undo_last(self):
        return self.undo.undo(self)

    def redo_last(self):
        return self.undo.redo(self)

    def replace_bitmap(self, bitmap, keep_selection=False):
        self.layers = [Layer(bitmap.width, bitmap.height, data=bitmap.data)]
        self.layer_index = 0
        if not keep_selection:
            self.selection = Selection(bitmap.width, bitmap.height)
