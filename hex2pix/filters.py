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
* `invert` turns RGB into its negative.
* `solarize` inverts the pixels above a luminance cut-off.
* `gamma` bends the midtones without clipping either end.
* `auto_levels` stretches each channel over the selected tone range.
* `blur` averages a square neighbourhood (box blur).
* `sharpen` pushes pixels away from their 3x3 average.
* `pixelate` flattens size x size blocks to their average colour.
* `mix_filter` runs any filter and blends the result with the original.

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


def invert(bitmap, mask=None):
    """Turn every channel into its negative, keeping alpha."""
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        data[base] = 255 - data[base]
        data[base + 1] = 255 - data[base + 1]
        data[base + 2] = 255 - data[base + 2]
        changed += 1
    return changed


def solarize(bitmap, mask=None, level=128):
    """Invert the pixels brighter than `level`, like darkroom solarisation."""
    level = clamp(int(level), 0, 255)
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        if luminance((data[base], data[base + 1], data[base + 2])) >= level:
            data[base] = 255 - data[base]
            data[base + 1] = 255 - data[base + 1]
            data[base + 2] = 255 - data[base + 2]
        changed += 1
    return changed


def gamma(bitmap, mask=None, percent=100):
    """Bend the midtones: below 100 darkens, above 100 lightens."""
    percent = clamp(float(percent), 10.0, 300.0)
    exponent = 100.0 / percent
    table = [clamp(int(round((value / 255.0) ** exponent * 255.0)), 0, 255)
             for value in range(256)]
    data = bitmap.data
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        data[base] = table[data[base]]
        data[base + 1] = table[data[base + 1]]
        data[base + 2] = table[data[base + 2]]
        changed += 1
    return changed


def auto_levels(bitmap, mask=None):
    """Stretch each channel over the selected tone range to 0-255."""
    indices = list(_indices(bitmap, mask))
    if not indices:
        return 0
    data = bitmap.data
    tables = []
    for channel in range(3):
        values = [data[index * 4 + channel] for index in indices]
        low, high = min(values), max(values)
        if high <= low:
            tables.append(None)
            continue
        scale = 255.0 / (high - low)
        tables.append([clamp(int(round((value - low) * scale)), 0, 255)
                       for value in range(256)])
    for index in indices:
        base = index * 4
        for channel in range(3):
            table = tables[channel]
            if table is not None:
                data[base + channel] = table[data[base + channel]]
    return len(indices)


def _plane(bitmap, channel):
    """One channel of the bitmap as a flat row-major list."""
    data = bitmap.data
    return [data[index * 4 + channel]
            for index in range(bitmap.width * bitmap.height)]


def _box_blur_plane(source, width, height, radius):
    """Separable box blur over a flat plane, clamping at the edges."""
    radius = max(1, int(radius))
    window = radius * 2 + 1
    temporary = [0] * (width * height)
    for y in range(height):
        row = y * width
        total = 0
        for x in range(-radius, radius + 1):
            total += source[row + min(width - 1, max(0, x))]
        for x in range(width):
            temporary[row + x] = total // window
            total += (source[row + min(width - 1, x + radius + 1)]
                      - source[row + min(width - 1, max(0, x - radius))])
    result = [0] * (width * height)
    for x in range(width):
        total = 0
        for y in range(-radius, radius + 1):
            total += temporary[min(height - 1, max(0, y)) * width + x]
        for y in range(height):
            result[y * width + x] = total // window
            total += (temporary[min(height - 1, y + radius + 1) * width + x]
                      - temporary[min(height - 1, max(0, y - radius)) * width
                                  + x])
    return result


def blur(bitmap, mask=None, radius=1):
    """Box-blur RGB over a square neighbourhood, keeping alpha."""
    radius = clamp(int(radius), 1, 32)
    planes = [_box_blur_plane(_plane(bitmap, channel), bitmap.width,
                              bitmap.height, radius)
              for channel in range(3)]
    data = bitmap.data
    result = list(data)
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        result[base] = planes[0][index]
        result[base + 1] = planes[1][index]
        result[base + 2] = planes[2][index]
        changed += 1
    data[:] = result
    return changed


def sharpen(bitmap, mask=None, amount=50):
    """Unsharp mask: push each pixel away from its 3x3 average."""
    factor = clamp(float(amount), 0.0, 100.0) / 100.0 * 2.0
    width, height = bitmap.width, bitmap.height
    means = [_box_blur_plane(_plane(bitmap, channel), width, height, 1)
             for channel in range(3)]
    data = bitmap.data
    result = list(data)
    changed = 0
    for index in _indices(bitmap, mask):
        base = index * 4
        for channel in range(3):
            original = data[base + channel]
            value = original + (original - means[channel][index]) * factor
            result[base + channel] = clamp(int(round(value)), 0, 255)
        changed += 1
    data[:] = result
    return changed


def pixelate(bitmap, mask=None, size=4):
    """Flatten each size x size block to its average colour."""
    size = max(1, int(size))
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    blocks = {}
    for block_y in range(0, height, size):
        for block_x in range(0, width, size):
            total = [0, 0, 0]
            count = 0
            for y in range(block_y, min(height, block_y + size)):
                row = y * width
                for x in range(block_x, min(width, block_x + size)):
                    offset = (row + x) * 4
                    total[0] += data[offset]
                    total[1] += data[offset + 1]
                    total[2] += data[offset + 2]
                    count += 1
            blocks[(block_x, block_y)] = [value // count for value in total]
    result = list(data)
    changed = 0
    for index in _indices(bitmap, mask):
        y, x = divmod(index, width)
        colour = blocks[(x - x % size, y - y % size)]
        base = index * 4
        result[base] = colour[0]
        result[base + 1] = colour[1]
        result[base + 2] = colour[2]
        changed += 1
    data[:] = result
    return changed


def mix_filter(bitmap, mask, function, strength=100, *args):
    """Run `function` and blend its result over the original pixels.

    `strength` is a percentage: 100 applies the filter fully, 50 keeps half
    of the original colour, and 0 leaves the pixels untouched.
    """
    strength = clamp(float(strength), 0.0, 100.0)
    if strength <= 0.0:
        return 0
    if strength >= 100.0:
        return function(bitmap, mask, *args)
    original = list(bitmap.data)
    changed = function(bitmap, mask, *args)
    if not changed:
        return 0
    factor = strength / 100.0
    data = bitmap.data
    for index in _indices(bitmap, mask):
        base = index * 4
        for channel in range(3):
            value = original[base + channel] + (
                data[base + channel] - original[base + channel]) * factor
            data[base + channel] = clamp(int(round(value)), 0, 255)
    return changed
