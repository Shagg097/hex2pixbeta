"""Packing pixels at bit depths other than 8 bits per channel.

The editor keeps pixels as RGBA bytes, but masks, small displays and odd file
formats want fewer bits: 1-bit masks, 2:2:2 colour, 4:4:4 colour across two
bytes, and so on. These helpers turn channel values into fixed-width samples
and run them into a byte stream.

Channels are concatenated from most to least significant in the order you
choose (RGB by default, or BGR and the other permutations), and each channel
keeps the top `n` bits of its 8-bit value (a channel wider than 8 bits is
shifted up, leaving zero low bits).

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

# Channel order inside a sample, most significant first. Alpha, when it has a
# width, goes at the end of the RGBA variants.
CHANNEL_ORDERS = ("RGB", "RBG", "GRB", "GBR", "BRG", "BGR")

# Header wrappers for the custom-bit-depth stream. "raw" is headerless; the
# BMP variants differ only in the size of their info header, and TGA writes a
# TGA 2.0 header with an optional colour map.
RAW_HEADERS = (
    ("Raw (none)", "raw"),
    ("BMP", "bmp"),
    ("BMP V4", "bmpv4"),
    ("BMP V5", "bmpv5"),
    ("TGA", "tga"),
)
RAW_HEADER_KEYS = tuple(key for _label, key in RAW_HEADERS)
RAW_HEADER_LABELS = {key: label for label, key in RAW_HEADERS}
RAW_HEADER_BY_LABEL = {label: key for label, key in RAW_HEADERS}
RAW_HEADER_EXT = {"raw": ".raw", "bmp": ".bmp", "bmpv4": ".bmp",
                  "bmpv5": ".bmp", "tga": ".tga"}


def check_header(header):
    """Return the canonical header key or raise HexError."""
    value = str(header or "raw")
    key = value.lower()
    for label, candidate in RAW_HEADERS:
        if value.lower() == label.lower():
            return candidate
    if key not in RAW_HEADER_KEYS:
        raise HexError("unknown raw header: %s" % header)
    return key


def header_key(value):
    """Best-effort header key for UI values without raising."""
    try:
        return check_header(value)
    except HexError:
        return "raw"


def header_label(key):
    """Human label for a header key."""
    return RAW_HEADER_LABELS.get(header_key(key), RAW_HEADER_LABELS["raw"])


def header_ext(key):
    """File extension for a header key."""
    return RAW_HEADER_EXT[header_key(key)]


def preset_label(widths):
    """The channel preset matching `widths`, or Custom."""
    packed = tuple(int(value) for value in widths)
    for label, preset in CHANNEL_PRESETS:
        if preset == packed:
            return label
    return "Custom"


def widths_for_total(total, widths=(8, 8, 8, 0)):
    """(r, g, b, a) widths that add up to `total` bits per pixel.

    Alpha stays in use when the current layout has it (or at 32 bpp) and the
    rest is split as evenly as possible, with the spare bits going to green
    first, then red, then blue. That reproduces the usual layouts: 8 bpp is
    3:3:2, 16 bpp is 5:6:5 and 32 bpp is 8:8:8:8.
    """
    try:
        total = int(total)
    except (TypeError, ValueError):
        raise HexError("bits per pixel must be a whole number")
    if total < 1 or total > MAX_BITS:
        raise HexError("bits per pixel must be 1-%d" % MAX_BITS)
    try:
        current = [int(value) for value in widths]
    except (TypeError, ValueError):
        current = [8, 8, 8, 0]
    while len(current) < 4:
        current.append(0)
    alpha = 0
    rest = total
    if total > 3 and (current[3] > 0 or total >= MAX_BITS):
        alpha = max(1, min(MAX_CHANNEL_BITS, total // 4))
        rest = total - alpha
    channels = [rest // 3, rest // 3, rest // 3]
    for index in (1, 0, 2)[:rest % 3]:
        channels[index] += 1
    return tuple(channels + [alpha])


def channel_masks(widths):
    """R/G/B/A bit masks for a most-significant-bit-first sample layout."""
    masks = [0, 0, 0, 0]
    shift = 0
    for index in range(3, -1, -1):
        width = int(widths[index])
        if width:
            masks[index] = ((1 << width) - 1) << shift
        shift += width
    return tuple(masks)

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

# Named byte layouts: label, (r, g, b, a) widths, packing. The label spells
# out the bytes so a 4:4:4 image can be recognised as either the padded
# 16-bit form or the 12-bit packed stream.
BIT_LAYOUTS = (
    ("xxxxrrrr ggggbbbb - 4:4:4 padded, 12 bpp (2 bytes)", (4, 4, 4, 0),
     "aligned"),
    ("rrrrgggg bbbbrrrr ggggbbbb - 4:4:4 packed, 12 bpp", (4, 4, 4, 0),
     "packed"),
    ("rrrrgggg bbbbaaaa - 4:4:4:4, 16 bpp (2 bytes)", (4, 4, 4, 4),
     "aligned"),
    ("rrrrrrrr gggggggg bbbbbbbb aaaaaaaa - 8:8:8:8, 32 bpp (4 bytes)",
     (8, 8, 8, 8), "aligned"),
    ("rrrgggbb - 3:3:2, 8 bpp (1 byte)", (3, 3, 2, 0), "aligned"),
    ("rrrrrggg gggbbbbb - 5:6:5, 16 bpp (2 bytes)", (5, 6, 5, 0),
     "aligned"),
)


def layout_label(widths, packing="packed"):
    """The named byte layout matching `widths` and `packing`, or Custom."""
    widths = tuple(int(value) for value in widths)
    packing = check_packing(packing)
    for label, preset, preset_packing in BIT_LAYOUTS:
        if preset == widths and preset_packing == packing:
            return label
    return "Custom"


def layout_for(label):
    """(widths, packing) for a named layout label, or None."""
    for name, widths, packing in BIT_LAYOUTS:
        if name == label:
            return widths, packing
    return None


def check_packing(packing):
    """Return the canonical packing name or raise HexError."""
    key = str(packing or "packed").lower()
    if key not in ("packed", "aligned"):
        raise HexError("bit packing must be packed or aligned")
    return key


def check_order(order):
    """Return a canonical channel order such as RGB, BGR or RGBA."""
    text = str(order or "RGB").upper()
    if len(text) not in (3, 4) or sorted(text) not in (["B", "G", "R"],
                                                       ["A", "B", "G", "R"]):
        raise HexError("channel order must use R, G and B once each")
    return text


def order_label(order):
    """Best-effort channel order for UI values without raising."""
    try:
        return check_order(order)
    except HexError:
        return "RGB"


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


def sample_rgb(rgb, widths, order="RGB"):
    """One integer per RGB triple, channels packed most significant first."""
    order = check_order(order)[:3]
    widths = [int(value) for value in widths[:3]]
    plan = [(widths["RGB".index(letter)], "RGB".index(letter))
            for letter in order]
    samples = []
    append = samples.append
    for index in range(0, len(rgb), 3):
        value = 0
        for width, channel in plan:
            value = (value << width) | channel_value(rgb[index + channel],
                                                     width)
        append(value)
    return samples


def sample_rgba(rgba, widths, order="RGBA"):
    """One integer per RGBA quad, channels packed most significant first."""
    order = check_order(order)
    if "A" not in order:
        order += "A"
    widths = [int(value) for value in widths]
    plan = [(widths["RGBA".index(letter)], "RGBA".index(letter))
            for letter in order]
    samples = []
    append = samples.append
    for index in range(0, len(rgba), 4):
        value = 0
        for width, channel in plan:
            value = (value << width) | channel_value(rgba[index + channel],
                                                     width)
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


def quantize_rgba(rgba, widths):
    """Reduce RGBA bytes to the channel widths, keeping the top bits.

    A zero colour width clears that channel and a zero alpha width makes the
    pixels opaque. Channels narrower than 8 bits are truncated with their low
    bits zeroed, which is the value the packers read.
    """
    widths = [int(value) for value in widths]
    if len(widths) != 4:
        raise HexError("give red, green, blue and alpha widths")
    out = bytearray(rgba)
    for index in range(0, len(out) - 3, 4):
        for channel in range(3):
            bits = widths[channel]
            if bits <= 0:
                out[index + channel] = 0
            elif bits < 8:
                out[index + channel] = (
                    (out[index + channel] >> (8 - bits)) << (8 - bits))
        bits = widths[3]
        if bits <= 0:
            out[index + 3] = 255
        elif bits < 8:
            out[index + 3] = ((out[index + 3] >> (8 - bits))
                              << (8 - bits))
    return bytes(out)


def _limit_table(bits):
    """A byte translation table that keeps the top `bits` of each value."""
    if bits <= 0:
        return bytes(256)
    if bits >= 8:
        return bytes(range(256))
    shift = 8 - bits
    return bytes((value >> shift) << shift for value in range(256))


def limit_color(color, widths):
    """One RGBA colour reduced to the working channel widths.

    Unlike `quantize_rgba`, a zero alpha width leaves alpha alone. The working
    layout is a drawing limit, not an export matte, so transparent pixels and
    the eraser must keep working when the layout has no alpha channel.
    """
    widths = [int(value) for value in widths]
    if len(widths) != 4:
        raise HexError("give red, green, blue and alpha widths")
    out = []
    for channel in range(3):
        bits = widths[channel]
        value = int(color[channel])
        if bits <= 0:
            out.append(0)
        elif bits < 8:
            out.append((value >> (8 - bits)) << (8 - bits))
        else:
            out.append(value)
    bits = widths[3]
    value = int(color[3])
    if bits <= 0:
        out.append(value)
    elif bits < 8:
        out.append((value >> (8 - bits)) << (8 - bits))
    else:
        out.append(value)
    return tuple(out)


def limit_rgba(rgba, widths):
    """Reduce an RGBA byte stream to the working widths, in place semantics.

    RGB channels with no bits become zero. Alpha is only touched when the
    layout gives it a width, so the eraser still clears pixels under a 4:4:4
    layout. Byte translation keeps a whole-canvas limit fast.
    """
    widths = [int(value) for value in widths]
    if len(widths) != 4:
        raise HexError("give red, green, blue and alpha widths")
    out = bytearray(rgba)
    for channel in range(3):
        bits = widths[channel]
        if bits >= 8:
            continue
        out[channel::4] = out[channel::4].translate(_limit_table(bits))
    bits = widths[3]
    if 0 < bits < 8:
        out[3::4] = out[3::4].translate(_limit_table(bits))
    return bytes(out)


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
