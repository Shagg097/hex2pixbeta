"""Packing pixels at bit depths other than 8 bits per channel.

The editor keeps pixels as RGBA bytes, but masks, small displays and odd file
formats want fewer bits: 1-bit masks, 2:2:2 colour, 4:4:4 colour across two
bytes, and so on. These helpers turn channel values into fixed-width samples
and run them into a byte stream.

Channels are concatenated from most to least significant in R, G, B, A order,
and each channel keeps the top `n` bits of its 8-bit value (a channel wider
than 8 bits is shifted up, leaving zero low bits).

Two layouts are available:

* ``packed`` - samples follow each other bit by bit, most significant bit
  first, with no padding at all. A 12-bit pixel is one and a half bytes, so
  two pixels fill three bytes: ``rrrrgggg bbbbrrrr ggggbbbb``. A 6-bit pixel
  fills four pixels in three bytes: ``rrggbbrr ggbbrrgg bbrrggbb``.
* ``aligned`` - every sample starts on a byte boundary and is written
  big-endian into the smallest whole number of bytes, with unused high bits
  zero. A 12-bit pixel takes two bytes (``0000rrrr ggggbbbb``) and a 6-bit
  pixel takes one (``00rrggbb``).

The last byte of a packed stream is padded with zero bits at the low end.
"""

from PIL import Image

from .model import HexError

MAX_BITS = 32
MAX_CHANNEL_BITS = 16
MAX_INDEX_BITS = 8

RAW_MODES = ("Indexed", "RGB(A)")
RAW_PACKINGS = ("Packed", "Aligned")

# Label, (red, green, blue, alpha) bit widths. The alpha channel is optional.
CHANNEL_PRESETS = (
    ("1:1:1 (3 bpp)", (1, 1, 1, 0)),
    ("2:2:2 (6 bpp)", (2, 2, 2, 0)),
    ("3:3:2 (8 bpp)", (3, 3, 2, 0)),
    ("4:4:4 (12 bpp)", (4, 4, 4, 0)),
    ("5:5:5 (15 bpp)", (5, 5, 5, 0)),
    ("5:6:5 (16 bpp)", (5, 6, 5, 0)),
    ("6:6:6 (18 bpp)", (6, 6, 6, 0)),
    ("8:8:8 (24 bpp)", (8, 8, 8, 0)),
    ("10:10:10 (30 bpp)", (10, 10, 10, 0)),
    ("8:8:8:8 (32 bpp)", (8, 8, 8, 8)),
)


def check_packing(packing):
    """Return the canonical packing name or raise HexError."""
    key = str(packing or "packed").lower()
    if key not in ("packed", "aligned"):
        raise HexError("bit packing must be packed or aligned")
    return key


def channel_widths(channels):
    """Validate (red, green, blue, alpha) widths adding up to 1-32 bits."""
    try:
        widths = [int(value) for value in channels]
    except (TypeError, ValueError):
        raise HexError("channel widths must be whole numbers")
    if len(widths) != 4:
        raise HexError("give red, green, blue and alpha widths")
    for width in widths:
        if width < 0 or width > MAX_CHANNEL_BITS:
            raise HexError("each channel needs 0-%d bits" % MAX_CHANNEL_BITS)
    total = sum(widths)
    if total < 1 or total > MAX_BITS:
        raise HexError("channels must total 1-%d bits" % MAX_BITS)
    if not any(widths[:3]):
        raise HexError("give red, green or blue at least one bit")
    return tuple(widths)


def channel_value(value, bits):
    """The top `bits` bits of an 8-bit channel value (or shifted up)."""
    bits = int(bits)
    if bits <= 0:
        return 0
    value = int(value) & 0xFF
    if bits == 8:
        return value
    if bits < 8:
        return value >> (8 - bits)
    return value << (bits - 8)


def sample_rgb(rgb, widths):
    """One integer per RGB triple, channels packed R then G then B."""
    red, green, blue = [int(value) for value in widths[:3]]
    samples = []
    append = samples.append
    for index in range(0, len(rgb), 3):
        value = channel_value(rgb[index], red)
        value = (value << green) | channel_value(rgb[index + 1], green)
        value = (value << blue) | channel_value(rgb[index + 2], blue)
        append(value)
    return samples


def sample_rgba(rgba, widths):
    """One integer per RGBA quad, channels packed R, G, B then A."""
    red, green, blue, alpha = [int(value) for value in widths]
    samples = []
    append = samples.append
    for index in range(0, len(rgba), 4):
        value = channel_value(rgba[index], red)
        value = (value << green) | channel_value(rgba[index + 1], green)
        value = (value << blue) | channel_value(rgba[index + 2], blue)
        value = (value << alpha) | channel_value(rgba[index + 3], alpha)
        append(value)
    return samples


def pack_samples(samples, bits, packing="packed"):
    """Pack integer samples of `bits` width into bytes."""
    try:
        bits = int(bits)
    except (TypeError, ValueError):
        raise HexError("bits per pixel must be a whole number")
    if bits < 1 or bits > MAX_BITS:
        raise HexError("bits per pixel must be 1-%d" % MAX_BITS)
    packing = check_packing(packing)
    mask = (1 << bits) - 1
    out = bytearray()
    if packing == "aligned":
        width = (bits + 7) // 8
        for sample in samples:
            value = int(sample) & mask
            for shift in range((width - 1) * 8, -1, -8):
                out.append((value >> shift) & 0xFF)
        return bytes(out)
    accumulator = 0
    held = 0
    for sample in samples:
        accumulator = (accumulator << bits) | (int(sample) & mask)
        held += bits
        while held >= 8:
            held -= 8
            out.append((accumulator >> held) & 0xFF)
            accumulator &= (1 << held) - 1
    if held:
        out.append((accumulator << (8 - held)) & 0xFF)
    return bytes(out)


def unpack_samples(data, bits, count=None, packing="packed"):
    """Read samples back out of a byte stream (the inverse of pack_samples)."""
    bits = int(bits)
    if bits < 1 or bits > MAX_BITS:
        raise HexError("bits per pixel must be 1-%d" % MAX_BITS)
    packing = check_packing(packing)
    data = bytes(data)
    if packing == "aligned":
        width = (bits + 7) // 8
        if count is None:
            if len(data) % width:
                raise HexError("aligned stream ends in the middle of a sample")
            count = len(data) // width
        if count * width != len(data):
            raise HexError("stream does not hold %d samples" % count)
        return [int.from_bytes(data[index * width:(index + 1) * width], "big")
                for index in range(count)]
    if count is None:
        count = len(data) * 8 // bits
    if count * bits > len(data) * 8:
        raise HexError("stream is too short for %d samples" % count)
    out = []
    accumulator = 0
    held = 0
    position = 0
    mask = (1 << bits) - 1
    for _index in range(count):
        while held < bits:
            accumulator = (accumulator << 8) | data[position]
            position += 1
            held += 8
        held -= bits
        out.append((accumulator >> held) & mask)
        accumulator &= (1 << held) - 1
    return out


def quantize_rgb(rgb, width, height, depth):
    """Reduce RGB bytes to palette indices plus the adaptive palette.

    The palette holds at most 2**depth entries, so 1 bit gives black and
    white, 4 bits gives sixteen colours and 8 bits gives 256. Dithering is off:
    equal colours stay equal instead of being scattered across neighbouring
    entries.
    """
    try:
        depth = int(depth)
    except (TypeError, ValueError):
        raise HexError("indexed bit depth must be a whole number")
    if depth < 1 or depth > MAX_INDEX_BITS:
        raise HexError("indexed bit depth must be 1-%d" % MAX_INDEX_BITS)
    colors = 1 << depth
    image = Image.frombytes("RGB", (int(width), int(height)), bytes(rgb))
    methods = getattr(Image, "Quantize", None)
    method = getattr(methods, "MEDIANCUT", 0) if methods else 0
    dithers = getattr(Image, "Dither", None)
    dither = getattr(dithers, "NONE", 0) if dithers else 0
    reduced = image.quantize(colors=colors, method=method, dither=dither)
    raw_palette = reduced.getpalette() or []
    palette = [tuple(raw_palette[index:index + 3])
               for index in range(0, min(len(raw_palette), colors * 3), 3)]
    return bytes(reduced.tobytes()), palette


def byte_size(width, height, bits, packing="packed"):
    """Bytes a raw stream of this size will occupy."""
    bits = int(bits)
    if check_packing(packing) == "aligned":
        return int(width) * int(height) * ((bits + 7) // 8)
    return (int(width) * int(height) * bits + 7) // 8
