"""The bare-necessity art filters: tone, colour and edge.

Every filter edits the selected pixels in place (the whole canvas when there is
no selection), leaves alpha alone and returns how many pixels it touched.

* `brightness` shifts RGB by a percentage of full scale.
* `contrast` pushes RGB away from mid grey (or towards it, when negative).
* `grayscale` replaces RGB with perceived luminance.
* `posterize` snaps RGB to a small number of even levels.
* `threshold` turns RGB into black or white at a luminance cut-off.
* `saturate` blends RGB towards grey, or away from it.
* `hue_shift` rotates every hue around the colour wheel.
* `noise` adds stable per-pixel grain, so the same image always looks the same.
* `sepia` maps the image onto a warm brown ramp.
* `edges` keeps the Sobel edge strength as grey.

Pure Python, no tkinter, so the results are unit-testable.
"""

import colorsys

from .model import clamp


def luminance(color):
    """Perceived brightness of an RGB(A) pixel, 0-255."""
    return int(0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2] + 0.5)


def _indices(bitmap, mask):
    count = bitmap.width * bitmap.height
    if mask is None:
        return range(count)
    return (index for index in range(count) if mask[index])


def brightness(bitmap, mask=None, amount=0):
    """Shift RGB by `amount` percent of full scale, clamped."""
    shift = int(round(clamp(float(amount), -100.0, 100.0) * 255.0 / 100.0))
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        for channel in range(3):
            data[base + channel] = clamp(data[base + channel] + shift, 0, 255)
        changed += 1
    return changed


def contrast(bitmap, mask=None, amount=0):
    """Scale RGB around mid grey; -100 flattens, 100 doubles the spread."""
    factor = 1.0 + clamp(float(amount), -100.0, 100.0) / 100.0
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        for channel in range(3):
            value = 128 + (data[base + channel] - 128) * factor
            data[base + channel] = clamp(int(round(value)), 0, 255)
        changed += 1
    return changed


def grayscale(bitmap, mask=None):
    """Replace RGB with perceived brightness."""
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        level = luminance((data[base], data[base + 1], data[base + 2]))
        data[base] = level
        data[base + 1] = level
        data[base + 2] = level
        changed += 1
    return changed


def posterize(bitmap, mask=None, levels=4):
    """Snap RGB to `levels` even steps between black and white."""
    levels = max(2, min(32, int(levels)))
    slope = 255.0 / (levels - 1)
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        for channel in range(3):
            step = int(round(data[base + channel] / slope))
            data[base + channel] = clamp(int(round(step * slope)), 0, 255)
        changed += 1
    return changed


def threshold(bitmap, mask=None, level=128):
    """Turn RGB into black or white at a luminance cut-off."""
    level = clamp(int(level), 0, 255)
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        value = 255 if luminance((data[base], data[base + 1],
                                  data[base + 2])) >= level else 0
        data[base] = value
        data[base + 1] = value
        data[base + 2] = value
        changed += 1
    return changed


def saturate(bitmap, mask=None, amount=0):
    """Blend RGB towards grey (negative) or away from it (positive)."""
    factor = 1.0 + clamp(float(amount), -100.0, 100.0) / 100.0
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        level = luminance((data[base], data[base + 1], data[base + 2]))
        for channel in range(3):
            value = level + (data[base + channel] - level) * factor
            data[base + channel] = clamp(int(round(value)), 0, 255)
        changed += 1
    return changed


def hue_shift(bitmap, mask=None, degrees=0):
    """Rotate every hue by `degrees` around the colour wheel."""
    rotation = (float(degrees) % 360.0) / 360.0
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        hue, saturation, value = colorsys.rgb_to_hsv(
            data[base] / 255.0, data[base + 1] / 255.0, data[base + 2] / 255.0)
        red, green, blue = colorsys.hsv_to_rgb((hue + rotation) % 1.0,
                                               saturation, value)
        data[base] = clamp(int(round(red * 255.0)), 0, 255)
        data[base + 1] = clamp(int(round(green * 255.0)), 0, 255)
        data[base + 2] = clamp(int(round(blue * 255.0)), 0, 255)
        changed += 1
    return changed


def _hash01(x, y, salt):
    value = (int(x) * 374761393 + int(y) * 668265263 + int(salt) * 69069)
    value &= 0xFFFFFFFF
    value = ((value ^ (value >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((value ^ (value >> 16)) & 0xFFFFFF) / float(0x1000000)


def noise(bitmap, mask=None, amount=10, seed=0):
    """Add grain: shift each pixel's brightness by up to `amount` percent."""
    spread = int(round(clamp(float(amount), 0.0, 100.0) / 100.0 * 255.0))
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        y, x = divmod(index, bitmap.width)
        offset = int(round((_hash01(x, y, seed) - 0.5) * 2.0 * spread))
        base = index * 4
        for channel in range(3):
            data[base + channel] = clamp(data[base + channel] + offset, 0, 255)
        changed += 1
    return changed


def sepia(bitmap, mask=None):
    """Map RGB onto the classic warm brown ramp, keeping alpha."""
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        red = data[base]
        green = data[base + 1]
        blue = data[base + 2]
        data[base] = clamp(int(round(0.393 * red + 0.769 * green
                                     + 0.189 * blue)), 0, 255)
        data[base + 1] = clamp(int(round(0.349 * red + 0.686 * green
                                         + 0.168 * blue)), 0, 255)
        data[base + 2] = clamp(int(round(0.272 * red + 0.534 * green
                                         + 0.131 * blue)), 0, 255)
        changed += 1
    return changed


def edges(bitmap, mask=None):
    """Draw the Sobel edge strength of the image as grey."""
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    source = [luminance((data[index * 4], data[index * 4 + 1],
                         data[index * 4 + 2]))
              for index in range(width * height)]

    def level(x, y):
        return source[min(height - 1, max(0, y)) * width
                      + min(width - 1, max(0, x))]

    result = list(data)
    changed = 0
    for index in _indices(bitmap, mask):
        y, x = divmod(index, width)
        gx = (level(x + 1, y - 1) + 2 * level(x + 1, y)
              + level(x + 1, y + 1)) - (level(x - 1, y - 1)
                                        + 2 * level(x - 1, y)
                                        + level(x - 1, y + 1))
        gy = (level(x - 1, y + 1) + 2 * level(x, y + 1)
              + level(x + 1, y + 1)) - (level(x - 1, y - 1)
                                        + 2 * level(x, y - 1)
                                        + level(x + 1, y - 1))
        magnitude = clamp(int(round((abs(gx) + abs(gy)) / 8.0)), 0, 255)
        base = index * 4
        result[base] = magnitude
        result[base + 1] = magnitude
        result[base + 2] = magnitude
        changed += 1
    data[:] = result
    return changed
