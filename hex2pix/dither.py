"""Dithering a bitmap down to two colours.

The reducers, all selection aware and all writing only the two colours they are
given:

* ordered Bayer matrices (2x2, 4x4, 8x8) for stable, tileable dot patterns;
* Floyd-Steinberg error diffusion for organic mixing, optionally with a
  serpentine scan;
* a custom pattern block whose bright cells become light pixels, so any pixel
  art (checkerboard, hatching, a logo) can act as the dither matrix. Pattern
  cells that are `.` (SKIP) are left untouched;
* white noise, for grainy shading without a grid;
* horizontal lines, for scanline shading.

Every reducer takes the same three options: `bias` shifts the light/dark cut
(negative keeps more light pixels), `strength` blends the dithered colour back
towards the pixel it replaced and `scale` spaces out the pattern and line
modes.

`apply_gradient_dither` runs the same machinery over a linear gradient, which
is what the Gradient tool's Dither option uses.

Pure Python, no tkinter, so the behaviour is unit-testable.
"""

from .model import SKIP, HexError, clamp

DITHER_MODES = ("off", "bayer2", "bayer4", "bayer8", "floyd", "pattern",
                "noise", "lines")

DITHER_LABELS = {
    "Off": "off",
    "Bayer 2x2": "bayer2",
    "Bayer 4x4": "bayer4",
    "Bayer 8x8": "bayer8",
    "Floyd-Steinberg": "floyd",
    "Pattern": "pattern",
    "Noise": "noise",
    "Lines": "lines",
}

_BAYER_SIZES = {"bayer2": 2, "bayer4": 4, "bayer8": 8}

# Quadrant extra values of the recursive Bayer construction:
# top-left, top-right, bottom-left, bottom-right.
_BAYER_EXTRA = (0, 2, 3, 1)


def bayer_matrix(size=4):
    """The `size` x `size` Bayer matrix, built from the 2x2 base."""
    size = max(2, min(8, int(size)))
    if size <= 2:
        return ((0, 2), (3, 1))
    half = bayer_matrix(size // 2)
    n = len(half)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            quadrant = (0 if y < n else 2) + (0 if x < n else 1)
            row.append(4 * half[y % n][x % n] + _BAYER_EXTRA[quadrant])
        rows.append(tuple(row))
    return tuple(rows)


def bayer_threshold(x, y, size=4):
    """A threshold in (0, 1) for one pixel of a Bayer matrix."""
    matrix = bayer_matrix(size)
    n = len(matrix)
    return (matrix[int(y) % n][int(x) % n] + 0.5) / float(n * n)


def luminance(color):
    """Perceived brightness of an RGBA pixel, 0-255."""
    return int(0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2] + 0.5)


def hash01(x, y, salt=0):
    """A stable 0-1 hash of a pixel position, used for noise."""
    value = (int(x) * 374761393 + int(y) * 668265263 + int(salt) * 69069)
    value &= 0xFFFFFFFF
    value = ((value ^ (value >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((value ^ (value >> 16)) & 0xFFFFFF) / float(0x1000000)


def noise_threshold(x, y):
    """White-noise threshold for one pixel."""
    return hash01(x, y, 7)


def line_threshold(y, scale=1):
    """A two-level threshold that bands every `scale` rows."""
    scale = max(1, min(16, int(scale)))
    return 0.25 if (int(y) // scale) % 2 == 0 else 0.75


def pattern_threshold(block, x, y, scale=1):
    """The threshold a custom pattern cell asks for at (x, y).

    Bright cells (yellow, white) mean "light pixel here", so the threshold is
    inverted: a white cell has threshold 0 and always picks the light colour.
    Returns None for a `.` cell, which means leave the pixel alone.
    """
    if block is None or block.width < 1 or block.height < 1:
        return 0.5
    scale = max(1, min(16, int(scale)))
    cell = block.rows[(int(y) // scale) % block.height][
        (int(x) // scale) % block.width]
    if cell is SKIP:
        return None
    return 1.0 - luminance(cell) / 255.0


def _settings(bias, strength, scale):
    """Turn the percent options into the values the loops use."""
    shift = clamp(float(bias), -100.0, 100.0) / 100.0 * 0.5
    amount = clamp(float(strength), 0.0, 100.0) / 100.0
    scale = max(1, min(16, int(scale)))
    return shift, amount, scale


def _mix(original, target, amount):
    """Blend a dithered colour back towards the pixel it replaces."""
    if amount >= 1.0:
        return target
    return tuple(int(round(original[index]
                           + (target[index] - original[index]) * amount))
                 for index in range(4))


def _threshold_for(mode, pattern, x, y, scale, size):
    if mode == "pattern":
        return pattern_threshold(pattern, x, y, scale)
    if mode == "noise":
        return noise_threshold(x, y)
    if mode == "lines":
        return line_threshold(y, scale)
    return bayer_threshold(x, y, size)


def dither_bitmap(bitmap, mask, light, dark, mode="bayer4", pattern=None,
                  scale=1, bias=0.0, strength=100, serpentine=False):
    """Replace each selected pixel with `light` or `dark`; returns the count."""
    mode = mode if mode in DITHER_MODES else "bayer4"
    if mode == "off":
        return 0
    shift, amount, scale = _settings(bias, strength, scale)
    if mode == "floyd":
        return _floyd_bitmap(bitmap, mask, light, dark, shift, amount,
                             serpentine)
    size = _BAYER_SIZES.get(mode, 4)
    width, height = bitmap.width, bitmap.height
    painted = 0
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if mask is not None and mask[index] == 0:
                continue
            threshold = _threshold_for(mode, pattern, x, y, scale, size)
            if threshold is None:
                continue
            original = bitmap.rgba(x, y)
            value = clamp(luminance(original) / 255.0 - shift, 0.0, 1.0)
            chosen = light if value >= threshold else dark
            bitmap.set_rgba(x, y, _mix(original, chosen, amount))
            painted += 1
    return painted


def pattern_dither(bitmap, mask, light, dark, block, scale=1, bias=0.0,
                   strength=100):
    """Dither with a custom pattern block as the threshold matrix."""
    return dither_bitmap(bitmap, mask, light, dark, "pattern", block, scale,
                         bias, strength)


def _floyd_bitmap(bitmap, mask, light, dark, shift=0.0, amount=1.0,
                  serpentine=False):
    """Floyd-Steinberg error diffusion over luminance."""
    width, height = bitmap.width, bitmap.height
    errors = [0.0] * (width + 2)
    painted = 0
    for y in range(height):
        reverse = serpentine and y % 2 == 1
        step = -1 if reverse else 1
        following = [0.0] * (width + 2)
        columns = range(width - 1, -1, -1) if reverse else range(width)
        for x in columns:
            index = y * width + x
            if mask is not None and mask[index] == 0:
                continue
            original = bitmap.rgba(x, y)
            value = clamp(luminance(original) / 255.0 - shift
                          + errors[x + 1], 0.0, 1.0)
            is_light = value >= 0.5
            bitmap.set_rgba(x, y, _mix(original,
                                       light if is_light else dark, amount))
            painted += 1
            error = value - (1.0 if is_light else 0.0)
            errors[x + 1 + step] += error * 7 / 16.0
            following[x + 1 - step] += error * 3 / 16.0
            following[x + 1] += error * 5 / 16.0
            following[x + 1 + step] += error * 1 / 16.0
        errors = following
    return painted


def _gradient_step(x, y, x0, y0, dx, dy, denominator):
    """How much of the start colour this pixel should keep, 0-1."""
    t = ((x - x0) * dx + (y - y0) * dy) / float(denominator)
    return 1.0 - clamp(t, 0.0, 1.0)


def apply_gradient_dither(bitmap, mask, x0, y0, x1, y1, start, end,
                          mode="bayer4", pattern=None, scale=1, bias=0.0,
                          strength=100, serpentine=False):
    """Dither a linear gradient between its two colours.

    The gradient's position becomes the brightness that gets dithered, so
    `start` is dense at the start of the stroke and `end` at the far end.
    """
    dx = int(x1) - int(x0)
    dy = int(y1) - int(y0)
    denominator = dx * dx + dy * dy
    if denominator == 0:
        raise HexError("drag the gradient tool to set a direction")
    mode = mode if mode in DITHER_MODES else "bayer4"
    if mode == "off":
        mode = "bayer4"
    shift, amount, scale = _settings(bias, strength, scale)
    width, height = bitmap.width, bitmap.height
    if mode == "floyd":
        return _floyd_gradient(bitmap, mask, x0, y0, dx, dy, denominator,
                               start, end, shift, amount, serpentine)
    size = _BAYER_SIZES.get(mode, 4)
    painted = 0
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if mask is not None and mask[index] == 0:
                continue
            threshold = _threshold_for(mode, pattern, x, y, scale, size)
            if threshold is None:
                threshold = 0.5
            original = bitmap.rgba(x, y)
            value = clamp(_gradient_step(x, y, x0, y0, dx, dy, denominator)
                          - shift, 0.0, 1.0)
            chosen = start if value >= threshold else end
            bitmap.set_rgba(x, y, _mix(original, chosen, amount))
            painted += 1
    return painted


def _floyd_gradient(bitmap, mask, x0, y0, dx, dy, denominator, start, end,
                    shift=0.0, amount=1.0, serpentine=False):
    width, height = bitmap.width, bitmap.height
    errors = [0.0] * (width + 2)
    painted = 0
    for y in range(height):
        reverse = serpentine and y % 2 == 1
        step = -1 if reverse else 1
        following = [0.0] * (width + 2)
        columns = range(width - 1, -1, -1) if reverse else range(width)
        for x in columns:
            index = y * width + x
            if mask is not None and mask[index] == 0:
                continue
            original = bitmap.rgba(x, y)
            value = clamp(_gradient_step(x, y, x0, y0, dx, dy, denominator)
                          - shift + errors[x + 1], 0.0, 1.0)
            is_light = value >= 0.5
            bitmap.set_rgba(x, y, _mix(original,
                                       start if is_light else end, amount))
            painted += 1
            error = value - (1.0 if is_light else 0.0)
            errors[x + 1 + step] += error * 7 / 16.0
            following[x + 1 - step] += error * 3 / 16.0
            following[x + 1] += error * 5 / 16.0
            following[x + 1 + step] += error * 1 / 16.0
        errors = following
    return painted
