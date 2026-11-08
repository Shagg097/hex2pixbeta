"""Palettes: ready-made pixel-art ramps and Aseprite-compatible .gpl files.

Colors are RGBA tuples, one per entry. The GPL writer follows the GIMP palette
format that Aseprite reads and writes, so palettes move between the two.
"""

from .model import HexError, parse_hex

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
}


def preset_colors(name):
    """The RGBA colors of a built-in palette."""
    return [parse_hex(value) for value in PALETTES[name]]


def parse_gpl(text):
    """Parse GPL text into (name, colors). Raises HexError when unusable."""
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
