"""Palettes: presets, 256-colour files, GPL/JASC palettes and quantization.

Colors are RGBA tuples, one per entry. The GPL writer follows the GIMP palette
format that Aseprite reads and writes; ``.256`` files are plain hex lists and
``.pal`` files use the JASC format. A palette holds at most 256 colours.
"""

import os
import re

from .model import HexError, clamp, parse_hex

MAX_PALETTE_COLORS = 256
PALETTE_FILETYPES = (("256-colour palette", "*.256"),
                     ("GIMP palette", "*.gpl"),
                     ("JASC palette", "*.pal"),
                     ("All files", "*.*"))


def cube_256():
    """A 6x6x6 colour cube plus a 40-step gray ramp: 256 colours."""
    colors = []
    levels = (0, 51, 102, 153, 204, 255)
    for red in levels:
        for green in levels:
            for blue in levels:
                colors.append((red, green, blue, 255))
    for step in range(1, 41):
        value = int(round(step * 255 / 41.0))
        colors.append((value, value, value, 255))
    return colors


PALETTES = {
    "PICO-8": (
        "000000", "1D2B53", "7E2553", "008751", "AB5236", "5F574F",
        "C2C3C7", "FFF1E8", "FF004D", "FFA300", "FFEC27", "00E436",
        "29ADFF", "83769C", "FF77A8", "FFCCAA",
    ),
    "DB16": (
        "140C1C", "442434", "30346D", "4E4A4E", "854C30", "346524",
        "D04648", "757161", "597DCE", "D27D2C", "8595A1", "6DAA2C",
        "D2AA99", "6DC2CA", "DAD45E", "DEEED6",
    ),
    "Grayscale": tuple("%02X%02X%02X" % (n, n, n) for n in range(0, 256, 17)),
    "256 Cube": tuple("%02X%02X%02X" % color[:3] for color in cube_256()),
}


def preset_colors(name):
    """The RGBA colors of a built-in palette."""
    return [parse_hex(value) for value in PALETTES[name]]


def make_palette(name, count=16, fill="black", color=(255, 255, 255, 255)):
    """A fresh palette of `count` colors, filled in one of four ways."""
    count = max(1, min(MAX_PALETTE_COLORS, int(count)))
    fill = str(fill or "black").lower()
    base = tuple(color)
    if len(base) == 3:
        base = base + (255,)
    colors = []
    for index in range(count):
        if fill == "grayscale":
            level = int(round(index * 255 / float(max(1, count - 1))))
            colors.append((level, level, level, 255))
        elif fill == "white":
            colors.append((255, 255, 255, 255))
        elif fill in ("color", "foreground", "fg"):
            colors.append(base[:4])
        else:
            colors.append((0, 0, 0, 255))
    return str(name or "Untitled palette").strip(), colors


# ------------------------------------------------------------------ GPL files
def parse_gpl(text):
    """Parse GPL text into (name, colors, columns). Raises HexError."""
    name = "Imported palette"
    columns = 8
    colors = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("gimp palette"):
            continue
        if low.startswith("name:"):
            name = line.split(":", 1)[1].strip() or name
            continue
        if low.startswith("columns:"):
            try:
                columns = int(line.split(":", 1)[1].strip())
            except ValueError:
                columns = 8
            continue
        parts = line.replace("\t", " ").split()
        if len(parts) < 3:
            continue
        try:
            red, green, blue = (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue
        if not (0 <= red <= 255 and 0 <= green <= 255 and 0 <= blue <= 255):
            continue
        colors.append((red, green, blue, 255))
    if not colors:
        raise HexError("no colors found in that palette file")
    return name, colors, max(1, columns)


def format_gpl(name, colors, columns=8):
    """Render colors as GPL text Aseprite can open."""
    lines = ["GIMP Palette", "Name: %s" % (name or "hex2pix palette"),
             "Columns: %d" % max(1, int(columns)), "#"]
    for color in colors:
        lines.append("%3d %3d %3d\t#%02X%02X%02X"
                     % (color[0], color[1], color[2],
                        color[0], color[1], color[2]))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------- 256-colour files
_HEX_LINE = re.compile(r"(?<![0-9A-Fa-f])(?:#)?"
                       r"([0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})"
                       r"(?![0-9A-Fa-f])")
_NAME_LINE = re.compile(r"^#?\s*name\s*[:=]\s*(.+)$", re.IGNORECASE)


def parse_hex_palette(text):
    """Parse a .256 file: one hex colour per line, comments allowed.

    Lines may be plain ``RRGGBB``, ``#RRGGBB``, ``RRGGBBAA``, or carry an
    index/name around the colour; every 6/8 digit hex token is read. A
    ``# Name:`` or ``Name:`` line names the palette.
    """
    name = "Imported palette"
    colors = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        named = _NAME_LINE.match(line)
        if named:
            name = named.group(1).strip() or name
            continue
        for token in _HEX_LINE.findall(line):
            colors.append(parse_hex("#" + token))
    if not colors:
        raise HexError("no colors found in that palette file")
    if len(colors) > MAX_PALETTE_COLORS:
        raise HexError("a palette holds at most %d colours"
                       % MAX_PALETTE_COLORS)
    return name, colors


def format_hex_palette(name, colors):
    """Render colors as a .256 file: a name comment, then hex lines."""
    lines = ["# Name: %s" % (name or "hex2pix palette"),
             "# hex2pix 256-colour palette: one RRGGBB colour per line", ""]
    for color in colors:
        lines.append("%02X%02X%02X" % color[:3])
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------- JASC files
def parse_jasc_pal(text):
    """Parse a JASC-PAL palette into (name, colors)."""
    lines = [line.strip() for line in str(text or "").splitlines()
             if line.strip()]
    if not lines or lines[0].upper() != "JASC-PAL":
        raise HexError("not a JASC palette file")
    try:
        count = int(lines[2])
    except (IndexError, ValueError):
        count = len(lines) - 3
    colors = []
    for line in lines[3:3 + count]:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            red, green, blue = (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue
        colors.append((clamp(red, 0, 255), clamp(green, 0, 255),
                       clamp(blue, 0, 255), 255))
    if not colors:
        raise HexError("no colors found in that palette file")
    if len(colors) > MAX_PALETTE_COLORS:
        raise HexError("a palette holds at most %d colours"
                       % MAX_PALETTE_COLORS)
    return "Imported palette", colors


def format_jasc_pal(colors):
    """Render colors as a JASC-PAL file."""
    lines = ["JASC-PAL", "0100", str(len(colors))]
    for color in colors:
        lines.append("%d %d %d" % (color[0], color[1], color[2]))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ dispatch
def parse_palette(text, path=""):
    """Read a palette file by extension or header; (name, colors, columns)."""
    extension = os.path.splitext(str(path))[1].lower()
    stripped = str(text or "").lstrip()
    if extension == ".gpl" or stripped.lower().startswith("gimp palette"):
        return parse_gpl(text)
    if extension == ".pal" or stripped.upper().startswith("JASC-PAL"):
        name, colors = parse_jasc_pal(text)
        return name, colors, 8
    name, colors = parse_hex_palette(text)
    return name, colors, 8


def format_palette(path, name, colors):
    """Render a palette for `path`: .gpl, .pal, or .256 by default."""
    extension = os.path.splitext(str(path))[1].lower()
    if extension == ".gpl":
        return format_gpl(name, colors)
    if extension == ".pal":
        return format_jasc_pal(colors)
    return format_hex_palette(name, colors)


# ------------------------------------------------------------- quantization
def nearest_color(color, colors):
    """The palette entry closest to `color`, weighted like the eye sees it."""
    if not colors:
        raise HexError("the palette is empty")
    best = colors[0]
    best_distance = None
    for entry in colors:
        red = color[0] - entry[0]
        green = color[1] - entry[1]
        blue = color[2] - entry[2]
        distance = 2 * red * red + 4 * green * green + 3 * blue * blue
        if best_distance is None or distance < best_distance:
            best = entry
            best_distance = distance
    return tuple(best)


def quantize_bitmap(bitmap, colors, dither=False, mask=None):
    """Map every opaque pixel of `bitmap` to the nearest palette colour.

    `mask` limits the pixels like a selection. Alpha is preserved. With
    `dither`, Floyd-Steinberg error diffusion spreads the difference between
    the pixel and its palette entry to the neighbours not yet visited.
    """
    if not colors:
        raise HexError("the palette is empty")
    palette = [tuple(entry) for entry in colors]
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    cache = {}

    def nearest(red, green, blue):
        key = (red, green, blue)
        found = cache.get(key)
        if found is None:
            found = nearest_color(key + (255,), palette)[:3]
            cache[key] = found
        return found

    painted = 0
    if not dither:
        for y in range(height):
            row = y * width
            for x in range(width):
                index = row + x
                if mask is not None and not mask[index]:
                    continue
                base = index * 4
                if data[base + 3] == 0:
                    continue
                red, green, blue = nearest(data[base], data[base + 1],
                                           data[base + 2])
                data[base] = red
                data[base + 1] = green
                data[base + 2] = blue
                painted += 1
        return painted

    row_error = [0.0] * (width * 3)
    next_error = [0.0] * (width * 3)
    for y in range(height):
        row = y * width
        for x in range(width):
            index = row + x
            if mask is not None and not mask[index]:
                continue
            base = index * 4
            if data[base + 3] == 0:
                continue
            red = clamp(data[base] + row_error[x * 3], 0, 255)
            green = clamp(data[base + 1] + row_error[x * 3 + 1], 0, 255)
            blue = clamp(data[base + 2] + row_error[x * 3 + 2], 0, 255)
            found = nearest(int(red + 0.5), int(green + 0.5),
                            int(blue + 0.5))
            data[base], data[base + 1], data[base + 2] = found
            error = (red - found[0], green - found[1], blue - found[2])
            if x + 1 < width:
                for channel in range(3):
                    row_error[(x + 1) * 3 + channel] += \
                        error[channel] * 7 / 16.0
            if y + 1 < height:
                if x > 0:
                    for channel in range(3):
                        next_error[(x - 1) * 3 + channel] += \
                            error[channel] * 3 / 16.0
                for channel in range(3):
                    next_error[x * 3 + channel] += error[channel] * 5 / 16.0
                if x + 1 < width:
                    for channel in range(3):
                        next_error[(x + 1) * 3 + channel] += \
                            error[channel] * 1 / 16.0
            painted += 1
        row_error, next_error = next_error, [0.0] * (width * 3)
    return painted
