"""Canvas properties and geometry: pad/crop, resample, color mode, alpha.

The canvas spec is what the New Canvas and Canvas Properties dialogs collect:
pixel size, resolution, color model, alpha and (when editing) how to reach the
new size. This module turns that spec into pixel operations, with no tkinter.
"""

import math

from .model import MATTE_WHITE, Bitmap, HexError, clamp, luminance, round_half_up

ANCHOR_CODES = ("nw", "n", "ne", "w", "center", "e", "sw", "s", "se")
ANCHOR_LABELS = {
    "nw": "\u2196", "n": "\u2191", "ne": "\u2197",
    "w": "\u2190", "center": "\u00b7", "e": "\u2192",
    "sw": "\u2199", "s": "\u2193", "se": "\u2198",
}
COLOR_MODES = ("RGB", "Grayscale")
RESAMPLERS = ("nearest", "bilinear")
UNITS = ("px", "mm", "in")
MAX_SIDE = 8192


def normalize_mode(mode):
    text = str(mode or "RGB").strip().lower()
    if text.startswith("gray"):
        return "Grayscale"
    return "RGB"


class CanvasSpec(object):
    """The settings that define a canvas."""

    __slots__ = ("width", "height", "ppi", "color_mode", "alpha", "fill",
                 "resize_mode", "anchor", "resample")

    def __init__(self, width, height, ppi=72.0, color_mode="RGB", alpha=True,
                 fill=(0, 0, 0, 0), resize_mode="canvas", anchor="center",
                 resample="nearest"):
        self.width = int(width)
        self.height = int(height)
        self.ppi = float(ppi)
        self.color_mode = normalize_mode(color_mode)
        self.alpha = bool(alpha)
        self.fill = tuple(fill)
        self.resize_mode = "scale" if str(resize_mode).lower() == "scale" else "canvas"
        self.anchor = anchor if anchor in ANCHOR_CODES else "center"
        self.resample = "bilinear" if str(resample).lower() == "bilinear" else "nearest"

    def __repr__(self):
        return ("CanvasSpec(%dx%d @ %gppi %s%s)"
                % (self.width, self.height, self.ppi, self.color_mode,
                   "+alpha" if self.alpha else ""))


def physical_size(width, height, ppi):
    """(mm_w, mm_h, in_w, in_h) for a pixel size at a resolution."""
    ppi = float(ppi) if ppi else 72.0
    mm_w = width / ppi * 25.4
    mm_h = height / ppi * 25.4
    return mm_w, mm_h, mm_w / 25.4, mm_h / 25.4


def px_per_unit(unit, ppi):
    unit = unit or "px"
    if unit == "mm":
        return float(ppi) / 25.4
    if unit == "in":
        return float(ppi)
    return 1.0


def offset_for(old_w, old_h, new_w, new_h, anchor="center"):
    """Top-left shift when a canvas changes size, for one of nine anchors."""
    ax = 0.0 if anchor in ("nw", "w", "sw") else (
        1.0 if anchor in ("ne", "e", "se") else 0.5)
    ay = 0.0 if anchor in ("nw", "n", "ne") else (
        1.0 if anchor in ("sw", "s", "se") else 0.5)
    return (int(round((new_w - old_w) * ax)), int(round((new_h - old_h) * ay)))


def resize_canvas(bitmap, width, height, anchor="center", fill=(0, 0, 0, 0)):
    """Pad or crop a bitmap without resampling its pixels."""
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise HexError("canvas must be at least 1x1")
    out = Bitmap(width, height)
    fill = tuple(fill)
    if fill != (0, 0, 0, 0):
        out.fill(fill)
    dx, dy = offset_for(bitmap.width, bitmap.height, width, height, anchor)
    x0 = max(0, -dx)
    y0 = max(0, -dy)
    x1 = min(bitmap.width, width - dx)
    y1 = min(bitmap.height, height - dy)
    if x1 <= x0 or y1 <= y0:
        return out
    span = (x1 - x0) * 4
    for y in range(y0, y1):
        src = (y * bitmap.width + x0) * 4
        dst = ((y + dy) * width + (x0 + dx)) * 4
        out.data[dst:dst + span] = bitmap.data[src:src + span]
    return out


def _bilinear(bitmap, fx, fy):
    """Premultiplied bilinear sample, so transparent pixels do not bleed."""
    width, height = bitmap.width, bitmap.height
    fx = clamp(fx, 0.0, width - 1.0)
    fy = clamp(fy, 0.0, height - 1.0)
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    tx = fx - x0
    ty = fy - y0
    x1 = min(width - 1, x0 + 1)
    y1 = min(height - 1, y0 + 1)
    data = bitmap.data

    def sample(x, y):
        i = (y * width + x) * 4
        alpha = data[i + 3]
        return (data[i] * alpha, data[i + 1] * alpha,
                data[i + 2] * alpha, alpha)

    c00 = sample(x0, y0)
    c10 = sample(x1, y0)
    c01 = sample(x0, y1)
    c11 = sample(x1, y1)
    w00 = (1.0 - tx) * (1.0 - ty)
    w10 = tx * (1.0 - ty)
    w01 = (1.0 - tx) * ty
    w11 = tx * ty
    alpha = c00[3] * w00 + c10[3] * w10 + c01[3] * w01 + c11[3] * w11
    if alpha <= 0.0001:
        return (0, 0, 0, 0)
    red = (c00[0] * w00 + c10[0] * w10 + c01[0] * w01 + c11[0] * w11) / alpha
    green = (c00[1] * w00 + c10[1] * w10 + c01[1] * w01 + c11[1] * w11) / alpha
    blue = (c00[2] * w00 + c10[2] * w10 + c01[2] * w01 + c11[2] * w11) / alpha
    return (clamp(round_half_up(red), 0, 255),
            clamp(round_half_up(green), 0, 255),
            clamp(round_half_up(blue), 0, 255),
            clamp(round_half_up(alpha), 0, 255))


def scale_image(bitmap, width, height, resample="nearest"):
    """Resample a bitmap to a new pixel size."""
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise HexError("image must be at least 1x1")
    out = Bitmap(width, height)
    src_w, src_h = bitmap.width, bitmap.height
    data = bitmap.data
    resample = "bilinear" if str(resample).lower() == "bilinear" else "nearest"
    for y in range(height):
        base = y * width * 4
        if resample == "nearest":
            sy = min(src_h - 1, int((y + 0.5) * src_h / height))
            row = sy * src_w
            for x in range(width):
                sx = min(src_w - 1, int((x + 0.5) * src_w / width))
                src = (row + sx) * 4
                dst = base + x * 4
                out.data[dst:dst + 4] = data[src:src + 4]
        else:
            fy = (y + 0.5) * src_h / height - 0.5
            for x in range(width):
                fx = (x + 0.5) * src_w / width - 0.5
                dst = base + x * 4
                out.data[dst:dst + 4] = bytes(_bilinear(bitmap, fx, fy))
    return out


def convert_mode(bitmap, mode):
    """Convert pixels to RGB or Grayscale in place. Returns pixels touched."""
    mode = normalize_mode(mode)
    if mode == "RGB":
        return 0
    data = bitmap.data
    count = 0
    for i in range(0, len(data), 4):
        value = clamp(round_half_up(luminance(data[i], data[i + 1], data[i + 2])),
                      0, 255)
        data[i] = value
        data[i + 1] = value
        data[i + 2] = value
        count += 1
    return count


def flatten_alpha(bitmap, matte=MATTE_WHITE):
    """Blend transparency onto `matte` and make every pixel opaque."""
    mr, mg, mb = matte
    data = bitmap.data
    for i in range(0, len(data), 4):
        alpha = data[i + 3]
        if alpha == 255:
            continue
        if alpha == 0:
            data[i] = mr
            data[i + 1] = mg
            data[i + 2] = mb
        else:
            inv = 255 - alpha
            data[i] = (data[i] * alpha + mr * inv) // 255
            data[i + 1] = (data[i + 1] * alpha + mg * inv) // 255
            data[i + 2] = (data[i + 2] * alpha + mb * inv) // 255
        data[i + 3] = 255
    return bitmap


def apply_spec_to_document(document, spec, matte=MATTE_WHITE,
                           working_widths=None):
    """Reshape a document's canvas to `spec` in one undoable change.

    `working_widths` also sets and enforces the canvas working limit in the
    same undo step, so Canvas Properties can change both at once.
    """
    before = document.begin()
    old_size = (document.width, document.height)
    resized = (spec.width, spec.height) != old_size

    def reshape(bitmap):
        if spec.color_mode != document.color_mode:
            convert_mode(bitmap, spec.color_mode)
        if not spec.alpha and document.alpha:
            flatten_alpha(bitmap, matte)
        if not resized:
            return bitmap
        if spec.resize_mode == "scale":
            return scale_image(bitmap, spec.width, spec.height, spec.resample)
        if spec.alpha:
            fill = (0, 0, 0, 0)
        else:
            fill = (matte[0], matte[1], matte[2], 255)
        return resize_canvas(bitmap, spec.width, spec.height, spec.anchor,
                             fill)

    document.transform_all(reshape)
    if resized:
        document.selection = type(document.selection)(spec.width, spec.height)
    document.ppi = spec.ppi
    document.color_mode = spec.color_mode
    document.alpha = spec.alpha
    if working_widths is not None:
        document.set_working_widths(working_widths, quantize=True)
    document.commit(before)
    return document
