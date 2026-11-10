"""File formats for hex2pix.

BMP and PPM are written by hand so the byte layout is exactly what the format
says (24-bit and 32-bit BMP, 24-bit binary PPM); everything else goes through
Pillow. Reading prefers the hand-written BMP parser so a file this app wrote
round-trips with its alpha intact, even though some viewers read 32-bit
BI_RGB BMPs as 24-bit + padding.
"""

import json
import math
import os
import struct
import zipfile

from PIL import Image

from . import bitdepth
from .model import (MATTE_WHITE, SKIP, Bitmap, Document, HexError, Layer,
                    LayerGroup, block_from_bitmap, clamp, parse_block)

FORMATS = (
    {"key": "png", "label": "PNG (8-bit RGBA)", "ext": ".png", "alpha": True},
    {"key": "bmp24", "label": "BMP 24-bit (flattened)", "ext": ".bmp", "alpha": False},
    {"key": "bmp32", "label": "BMP 32-bit (alpha)", "ext": ".bmp", "alpha": True},
    {"key": "tga", "label": "TGA 32-bit", "ext": ".tga", "alpha": True},
    {"key": "gif", "label": "GIF (256 colors)", "ext": ".gif", "alpha": False},
    {"key": "ico", "label": "ICO (multi-size)", "ext": ".ico", "alpha": True},
    {"key": "tif", "label": "TIFF RGBA", "ext": ".tif", "alpha": True},
    {"key": "webp", "label": "WEBP lossless", "ext": ".webp", "alpha": True},
    {"key": "jpg", "label": "JPEG (flattened, quality)", "ext": ".jpg", "alpha": False},
    {"key": "ppm", "label": "PPM 24-bit", "ext": ".ppm", "alpha": False},
    {"key": "raw", "label": "Raw pixel bits (1-32 bpp, headers)",
     "ext": ".raw", "alpha": True},
    {"key": "hex", "label": "Hex text (.txt)", "ext": ".txt", "alpha": False},
)
FORMAT_BY_KEY = {item["key"]: item for item in FORMATS}
FORMAT_BY_LABEL = {item["label"]: item for item in FORMATS}
FORMAT_LABELS = [item["label"] for item in FORMATS]

OPEN_FILETYPES = [
    ("Images and projects", "*.h2p *.png *.bmp *.gif *.tga *.ico *.tif "
                           "*.tiff *.webp *.jpg *.jpeg *.ppm *.pgm *.pbm"),
    ("hex2pix project", "*.h2p"),
    ("Hex text", "*.txt *.hex"),
    ("All files", "*.*"),
]

PROJECT_EXT = ".h2p"
PROJECT_FORMAT = "hex2pix-project"
PROJECT_VERSION = 1
PROJECT_FILETYPES = [("hex2pix project", "*.h2p"), ("All files", "*.*")]
SAVE_FILETYPES = ([("hex2pix project", "*.h2p")]
                  + [(item["label"], "*" + item["ext"]) for item in FORMATS]
                  + [("All files", "*.*")])

# A scaled export larger than this would take a very long time to build.
MAX_EXPORT_PIXELS = 64 * 1024 * 1024


def flatten(data, width, height, matte=MATTE_WHITE):
    """RGB bytes with any transparency blended over `matte`."""
    out = bytearray(width * height * 3)
    mr, mg, mb = matte
    for i in range(width * height):
        base = i * 4
        alpha = data[base + 3]
        target = i * 3
        if alpha == 255:
            out[target] = data[base]
            out[target + 1] = data[base + 1]
            out[target + 2] = data[base + 2]
        elif alpha == 0:
            out[target] = mr
            out[target + 1] = mg
            out[target + 2] = mb
        else:
            inv = 255 - alpha
            out[target] = (data[base] * alpha + mr * inv) // 255
            out[target + 1] = (data[base + 1] * alpha + mg * inv) // 255
            out[target + 2] = (data[base + 2] * alpha + mb * inv) // 255
    return out


def trim_alpha(bitmap):
    """Crop fully transparent rows and columns away from a bitmap.

    A bitmap with no opaque pixel at all is returned unchanged, so exporting an
    empty canvas still writes a full-size image instead of a 1x1 one.
    """
    width, height = bitmap.width, bitmap.height
    data = bitmap.data
    left, top = width, height
    right = bottom = -1
    for y in range(height):
        row = y * width
        for x in range(width):
            if data[(row + x) * 4 + 3]:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
    if right < left or bottom < top:
        return bitmap.copy()
    return bitmap.rect_rgba(left, top, right - left + 1, bottom - top + 1)


def flatten_bitmap(bitmap, matte=MATTE_WHITE):
    """Copy with transparency blended onto `matte` and every alpha byte 255."""
    rgb = flatten(bitmap.data, bitmap.width, bitmap.height, matte)
    out = Bitmap(bitmap.width, bitmap.height)
    data = out.data
    for index in range(bitmap.width * bitmap.height):
        data[index * 4] = rgb[index * 3]
        data[index * 4 + 1] = rgb[index * 3 + 1]
        data[index * 4 + 2] = rgb[index * 3 + 2]
        data[index * 4 + 3] = 255
    return out


def scale_bitmap(bitmap, factor, resample="nearest"):
    """Resample a bitmap by `factor` (nearest or bilinear)."""
    try:
        factor = float(factor)
    except (TypeError, ValueError):
        raise HexError("scale must be a number")
    if factor <= 0:
        raise HexError("scale must be greater than zero")
    width = max(1, int(math.floor(bitmap.width * factor + 0.5)))
    height = max(1, int(math.floor(bitmap.height * factor + 0.5)))
    if width * height > MAX_EXPORT_PIXELS:
        raise HexError("a %dx%d export is too large; use a smaller scale"
                       % (width, height))
    if (width, height) == (bitmap.width, bitmap.height):
        return bitmap.copy()
    from .canvas import scale_image
    return scale_image(bitmap, width, height,
                       "bilinear" if str(resample).lower() == "bilinear"
                       else "nearest")


def prepare_export(bitmap, scale=1, resample="nearest", trim=False,
                   keep_alpha=True, matte=MATTE_WHITE):
    """Build the bitmap an export writes: trimmed, scaled and flattened."""
    out = trim_alpha(bitmap) if trim else bitmap
    out = scale_bitmap(out, scale, resample)
    if not keep_alpha:
        out = flatten_bitmap(out, matte)
    return out


def to_pil(bitmap, matte=MATTE_WHITE, flatten_alpha=False):
    if flatten_alpha:
        return Image.frombytes(
            "RGB", (bitmap.width, bitmap.height),
            bytes(flatten(bitmap.data, bitmap.width, bitmap.height, matte)))
    return Image.frombytes(
        "RGBA", (bitmap.width, bitmap.height), bytes(bitmap.data))


def from_pil(image):
    image = image.convert("RGBA")
    return Bitmap(image.width, image.height, image.tobytes())


# --------------------------------------------------------------------- reading
def read_bmp_info(path):
    """Parse an uncompressed 24- or 32-bit BMP into (Bitmap, ppi)."""
    with open(path, "rb") as handle:
        raw = handle.read()
    if len(raw) < 54 or raw[:2] != b"BM":
        raise HexError("not a BMP file")
    data_offset = struct.unpack_from("<I", raw, 10)[0]
    header_size = struct.unpack_from("<I", raw, 14)[0]
    if header_size < 40:
        raise HexError("unsupported BMP header")
    width, height = struct.unpack_from("<ii", raw, 18)
    bpp = struct.unpack_from("<H", raw, 28)[0]
    compression = struct.unpack_from("<I", raw, 30)[0]
    if compression != 0:
        raise HexError("compressed BMP files are not supported")
    if bpp not in (24, 32):
        raise HexError("%d-bit BMP files are not supported" % bpp)
    top_down = height < 0
    height = abs(height)
    if width <= 0 or height <= 0:
        raise HexError("empty BMP")
    stride = ((width * bpp // 8) + 3) // 4 * 4
    out = bytearray(width * height * 4)
    for y in range(height):
        src_y = y if top_down else (height - 1 - y)
        row = data_offset + src_y * stride
        dst = y * width * 4
        if bpp == 24:
            for x in range(width):
                s = row + x * 3
                d = dst + x * 4
                out[d] = raw[s + 2]
                out[d + 1] = raw[s + 1]
                out[d + 2] = raw[s]
                out[d + 3] = 255
        else:
            for x in range(width):
                s = row + x * 4
                d = dst + x * 4
                out[d] = raw[s + 2]
                out[d + 1] = raw[s + 1]
                out[d + 2] = raw[s]
                out[d + 3] = raw[s + 3]
    pixels_per_meter = struct.unpack_from("<i", raw, 38)[0]
    ppi = pixels_per_meter / 39.3700787 if pixels_per_meter > 0 else 72.0
    return Bitmap(width, height, out), ppi


def read_bmp(path):
    """Parse an uncompressed 24- or 32-bit BMP into a Bitmap."""
    return read_bmp_info(path)[0]


def load(path):
    """Load any supported file into a Bitmap."""
    path = str(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".hex"):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return bitmap_from_block(parse_block(handle.read()))
    if ext == ".bmp":
        try:
            return read_bmp(path)
        except HexError:
            pass
    try:
        with Image.open(path) as image:
            image.load()
            return from_pil(image)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        raise HexError("could not open %s: %s" % (os.path.basename(path), exc))


def load_document(path):
    """Load a file into (Bitmap, ppi), reading resolution when it is stored."""
    path = str(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".hex"):
        return load(path), 72.0
    if ext == ".bmp":
        try:
            return read_bmp_info(path)
        except HexError:
            pass
    bitmap = load(path)
    ppi = 72.0
    try:
        with Image.open(path) as image:
            dpi = image.info.get("dpi")
            if dpi and dpi[0]:
                ppi = float(dpi[0])
    except Exception:  # noqa: BLE001 - resolution is optional metadata
        pass
    return bitmap, ppi


def bitmap_from_block(block):
    bitmap = Bitmap(max(1, block.width), max(1, block.height))
    for y, row in enumerate(block.rows):
        for x, pixel in enumerate(row):
            if pixel is SKIP:
                continue
            bitmap.set_rgba(x, y, pixel)
    return bitmap


# --------------------------------------------------------------------- writing
def _bmp_header(width, height, bpp, image_size, ppi=72.0):
    stride = ((width * bpp // 8) + 3) // 4 * 4
    file_size = 54 + stride * height
    header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 54)
    pixels_per_meter = int(round(float(ppi or 72.0) / 0.0254))
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, bpp, 0,
                       image_size, pixels_per_meter, pixels_per_meter, 0, 0)
    return header + info, stride


def save_bmp24(bitmap, path, matte=MATTE_WHITE, ppi=72.0):
    """Write a 24 bits-per-pixel BMP with transparency blended over matte."""
    width, height = bitmap.width, bitmap.height
    rgb = flatten(bitmap.data, width, height, matte)
    stride = ((width * 3) + 3) // 4 * 4
    pad = stride - width * 3
    header, _ = _bmp_header(width, height, 24, stride * height, ppi)
    rows = []
    for y in range(height - 1, -1, -1):
        base = y * width * 3
        row = bytearray(stride)
        for x in range(width):
            s = base + x * 3
            d = x * 3
            row[d] = rgb[s + 2]
            row[d + 1] = rgb[s + 1]
            row[d + 2] = rgb[s]
        rows.append(bytes(row[:width * 3]) + b"\x00" * pad)
    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(b"".join(rows))
    return width, height


def save_bmp32(bitmap, path, ppi=72.0):
    """Write a 32 bits-per-pixel BMP keeping the alpha bytes."""
    width, height = bitmap.width, bitmap.height
    stride = width * 4
    header, _ = _bmp_header(width, height, 32, stride * height, ppi)
    data = bitmap.data
    rows = []
    for y in range(height - 1, -1, -1):
        base = y * width * 4
        row = bytearray(stride)
        for x in range(width):
            s = base + x * 4
            d = x * 4
            row[d] = data[s + 2]
            row[d + 1] = data[s + 1]
            row[d + 2] = data[s]
            row[d + 3] = data[s + 3]
        rows.append(bytes(row))
    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(b"".join(rows))
    return width, height


def save_ppm(bitmap, path, matte=MATTE_WHITE):
    """Write a 24-bit binary PPM (P6)."""
    width, height = bitmap.width, bitmap.height
    rgb = flatten(bitmap.data, width, height, matte)
    with open(path, "wb") as handle:
        handle.write(b"P6\n%d %d\n255\n" % (width, height))
        handle.write(bytes(rgb))
    return width, height


def save_hex_text(bitmap, path):
    text = block_from_bitmap(bitmap) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return bitmap.width, bitmap.height


def _ensure_dir(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)


def _raw_samples(bitmap, mode, depth, channels, matte, order="RGB"):
    """Samples, palette, bits per pixel and alpha width for a raw export."""
    mode = str(mode or "rgb").lower()
    if mode.startswith("index"):
        try:
            depth = int(depth)
        except (TypeError, ValueError):
            raise HexError("indexed bit depth must be a whole number")
        if depth < 1 or depth > bitdepth.MAX_INDEX_BITS:
            raise HexError("indexed bit depth must be 1-%d"
                           % bitdepth.MAX_INDEX_BITS)
        rgb = flatten(bitmap.data, bitmap.width, bitmap.height, matte)
        indices, palette = bitdepth.quantize_rgb(
            rgb, bitmap.width, bitmap.height, depth)
        return indices, palette, depth, 0
    if mode != "rgb":
        raise HexError("unknown raw colour mode: %s" % mode)
    widths = bitdepth.channel_widths(channels)
    if widths[3]:
        samples = bitdepth.sample_rgba(bitmap.data, widths, order)
    else:
        rgb = flatten(bitmap.data, bitmap.width, bitmap.height, matte)
        samples = bitdepth.sample_rgb(rgb, widths[:3], order)
    return samples, None, sum(widths), widths[3]


def _palette_bytes(palette, with_alpha=False, pad_bmp=False):
    """BMP/TGA colour table bytes (BGR or BGRA) for a quantized palette."""
    out = bytearray()
    for entry in palette or ():
        red, green, blue = [int(value) for value in entry[:3]]
        out.extend((blue, green, red))
        if with_alpha:
            out.append(int(entry[3]) if len(entry) > 3 else 255)
        elif pad_bmp:
            out.append(0)
    return bytes(out)


def _bmp_pixel(sample, alpha):
    """BGR(A) bytes for one 8-bit-per-channel sample."""
    if alpha:
        return bytes(((sample >> 8) & 0xFF, (sample >> 16) & 0xFF,
                      (sample >> 24) & 0xFF, sample & 0xFF))
    return bytes((sample & 0xFF, (sample >> 8) & 0xFF, (sample >> 16) & 0xFF))


def _ordered_masks(widths, order="RGB"):
    """BMP colour masks for channels laid out in `order`, most significant
    first, with the alpha mask left empty the way the BMP writers expect."""
    widths = [int(value) for value in widths[:3]]
    order = bitdepth.check_order(order)[:3]
    shift = sum(widths)
    shifts = {}
    for letter in order:
        shift -= widths["RGB".index(letter)]
        shifts[letter] = shift
    masks = []
    for letter in "RGB":
        width = widths["RGB".index(letter)]
        masks.append(((1 << width) - 1) << shifts[letter])
    return tuple(masks) + (0,)


def _bmp_layout(mode, depth, channels=None, total=None, order="RGB"):
    """Pixel depth, compression and masks BMP will store.

    BMP only defines 1, 4, 8, 16, 24 and 32 bits per pixel, so the requested
    channel widths are mapped onto the nearest of those. At 16 bits the masks
    carry the channel order, so any RGB permutation survives; 24/32-bit BMP
    has a fixed BGR(A) byte order.
    """
    if str(mode or "rgb").lower().startswith("index"):
        bits = max(1, min(bitdepth.MAX_INDEX_BITS, int(depth or 8)))
        palette_bits = 1 if bits <= 1 else 4 if bits <= 4 else 8
        return palette_bits, 0, (0, 0, 0, 0)
    widths = bitdepth.channel_widths(channels) if channels else ()
    total = sum(widths) if widths else int(total or 0)
    if total <= 16:
        target = ((5, 5, 5, 0) if tuple(widths[:3]) == (5, 5, 5)
                  else (5, 6, 5, 0))
        return 16, 3, _ordered_masks(target[:3], order)
    if total <= 24:
        return 24, 0, (0x00FF0000, 0x0000FF00, 0x000000FF, 0)
    return 32, 0, (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)


def _bmp_custom_header(width, height, bits, image_size, ppi, header,
                       palette_count, masks, compression):
    """A BITMAPINFOHEADER/V4/V5 for a legal BMP pixel depth."""
    header = bitdepth.check_header(header)
    if header not in ("bmp", "bmpv4", "bmpv5"):
        raise HexError("not a BMP header: %s" % header)
    header_size = {"bmp": 40, "bmpv4": 108, "bmpv5": 124}[header]
    if header in ("bmpv4", "bmpv5") and bits == 32 and masks[3]:
        compression = 3
    pixels_per_meter = int(round(float(ppi or 72.0) / 0.0254))
    info = struct.pack("<IiiHHIIiiII", header_size, width, height, 1, bits,
                       compression, image_size, pixels_per_meter,
                       pixels_per_meter, palette_count, 0)
    if header in ("bmpv4", "bmpv5"):
        info += struct.pack("<IIII", *masks)
        info += struct.pack("<I", 0)          # LCS_CALIBRATED_RGB
        info += b"\x00" * 36                   # endpoints
        info += b"\x00" * 12                   # gamma red/green/blue
    elif compression == 3:
        # BI_BITFIELDS puts three colour masks after a plain header.
        info += struct.pack("<III", *tuple(masks)[:3])
    if header == "bmpv5":
        info += struct.pack("<IIII", 4, 0, 0, 0)  # LCS_GM_IMAGES
    expected = header_size + (12 if header == "bmp"
                              and compression == 3 else 0)
    if len(info) != expected:
        raise HexError("internal BMP header size mismatch")
    return info


def _bmp_custom(bitmap, mode, depth, channels, packing, matte, ppi,
                header, order="RGB"):
    """Wrap the artwork in a BMP file that ordinary readers can open.

    BMP only defines 1, 4, 8, 16, 24 and 32 bits per pixel, so the requested
    channel widths are stored in the nearest legal form: RGB totals up to 16
    bits become 16 bpp with the standard 5:5:5 or 5:6:5 masks, larger totals
    become 24 or 32 bpp, and indexed depths move up to the next colour-table
    depth. Rows are byte-aligned, padded to four bytes and stored bottom-up,
    as BMP requires.
    """
    width, height = bitmap.width, bitmap.height
    container, compression, masks = _bmp_layout(mode, depth, channels,
                                                order=order)
    if str(mode or "rgb").lower().startswith("index"):
        samples, palette, _bits, _alpha = _raw_samples(
            bitmap, mode, depth, channels, matte)
        rows = [bitdepth.pack_samples(samples[y * width:(y + 1) * width],
                                      container, "packed")
                for y in range(height)]
        palette_data = _palette_bytes(palette, pad_bmp=True)
        palette_data += b"\x00" * ((1 << container) * 4
                                   - len(palette_data))
        palette_count = 1 << container
    else:
        widths = bitdepth.channel_widths(channels)
        rgb = flatten(bitmap.data, width, height, matte)
        palette_data = b""
        palette_count = 0
        if container == 16:
            target = ((5, 5, 5, 0) if tuple(widths[:3]) == (5, 5, 5)
                      else (5, 6, 5, 0))
            samples = bitdepth.sample_rgb(rgb, target[:3], order)
            rows = [b"".join(struct.pack("<H", sample) for sample in
                             samples[y * width:(y + 1) * width])
                    for y in range(height)]
        else:
            keep_alpha = container == 32 and bool(widths[3])
            if keep_alpha:
                samples = bitdepth.sample_rgba(bitmap.data, (8, 8, 8, 8))
            else:
                samples = bitdepth.sample_rgb(rgb, (8, 8, 8))
            rows = [b"".join(_bmp_pixel(sample, keep_alpha)
                             for sample in samples[y * width:(y + 1) * width])
                    for y in range(height)]
    row_bytes = (width * container + 7) // 8
    stride = (row_bytes + 3) // 4 * 4
    payload = b"".join(row + b"\x00" * (stride - len(row))
                       for row in reversed(rows))
    info = _bmp_custom_header(
        width, height, container, stride * height, ppi, header,
        palette_count, masks, compression)
    offset = 14 + len(info) + len(palette_data)
    file_size = offset + len(payload)
    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, offset)
    return file_header + info + palette_data + payload


def _tga_custom(bitmap, mode, depth, channels, packing, matte, header):
    """Wrap the artwork in a TGA 2.0 file that readers can open.

    TGA stores 8-bit colour-mapped, 24 bpp BGR or 32 bpp BGRA pixels, so
    indexed artwork becomes 8-bit indices with a 24-bit colour map, and direct
    colour is written at 24 bpp, or 32 bpp when an alpha channel is in use.
    """
    width, height = bitmap.width, bitmap.height
    if str(mode or "rgb").lower().startswith("index"):
        samples, palette, bits, _alpha = _raw_samples(
            bitmap, mode, depth, channels, matte)
        cmap_length = 1 << max(1, min(bitdepth.MAX_INDEX_BITS, bits))
        palette_data = _palette_bytes(palette)
        palette_data += b"\x00" * (cmap_length * 3 - len(palette_data))
        payload = bytes(int(sample) & 0xFF for sample in samples)
        header_bytes = struct.pack(
            "<BBBHHBHHHHBB", 0, 1, 1, 0, cmap_length, 24, 0, 0,
            width, height, 8, 0x20)
        return header_bytes + palette_data + payload
    widths = bitdepth.channel_widths(channels)
    alpha = bool(widths[3])
    if alpha:
        samples = bitdepth.sample_rgba(bitmap.data, (8, 8, 8, 8))
    else:
        samples = bitdepth.sample_rgb(
            flatten(bitmap.data, width, height, matte), (8, 8, 8))
    payload = b"".join(_bmp_pixel(sample, alpha) for sample in samples)
    header_bytes = struct.pack(
        "<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, width, height,
        32 if alpha else 24, 0x20 | (8 if alpha else 0))
    return header_bytes + payload


def save_raw(bitmap, path, mode="rgb", depth=8, channels=(8, 8, 8, 0),
             packing="packed", matte=MATTE_WHITE, header="raw", ppi=72.0,
             order="RGB"):
    """Write pixel bits at 1-32 bits per pixel, optionally with a header.

    `mode` is "indexed" (1-8 bits per pixel, adaptive palette) or "rgb"
    (`channels` are the red, green, blue and alpha widths). `packing` is
    "packed" for a dense bit stream or "aligned" for whole bytes per pixel.
    `order` puts the channels into the sample in any RGB permutation (BGR and
    so on), `header` is "raw" for headerless data, one of the BMP variants,
    or "tga". A 16-bit BMP carries the order in its colour masks; 24/32-bit
    BMP and TGA fix their byte order to BGR(A), and indexed output stores
    palette indices, so `order` does not reach those files.
    """
    packing = bitdepth.check_packing(packing)
    header = bitdepth.check_header(header)
    if header == "raw":
        samples, _palette, bits, _alpha_bits = _raw_samples(
            bitmap, mode, depth, channels, matte, order)
        data = bitdepth.pack_samples(samples, bits, packing)
    elif header in ("bmp", "bmpv4", "bmpv5"):
        data = _bmp_custom(bitmap, mode, depth, channels, packing, matte,
                           ppi, header, order)
    else:
        data = _tga_custom(bitmap, mode, depth, channels, packing, matte,
                           header)
    _ensure_dir(path)
    with open(path, "wb") as handle:
        handle.write(data)
    return len(data)


def raw_size(width, height, bits, packing="packed", header="raw",
             palette_count=0, mode="rgb", depth=None, channels=None):
    """Bytes a raw export will take, including its header.

    `bits` is the requested pixel depth; `mode`, `depth` and `channels` let
    the headers report the legal container they actually write.
    """
    header = bitdepth.check_header(header)
    bits = int(bits)
    indexed = str(mode or "rgb").lower().startswith("index")
    if header == "raw":
        return bitdepth.byte_size(width, height, bits, packing)
    if header in ("bmp", "bmpv4", "bmpv5"):
        container, compression, _masks = _bmp_layout(mode, depth or bits,
                                                     channels, total=bits)
        row_bytes = (int(width) * container + 7) // 8
        stride = (row_bytes + 3) // 4 * 4
        info_size = {"bmp": 40, "bmpv4": 108, "bmpv5": 124}[header]
        if header == "bmp" and compression == 3:
            info_size += 12
        palette_bytes = (1 << container) * 4 if indexed else 0
        return 14 + info_size + palette_bytes + stride * int(height)
    if indexed:
        entries = 1 << max(1, min(bitdepth.MAX_INDEX_BITS,
                                 int(depth or bits)))
        return 18 + entries * 3 + int(width) * int(height)
    widths = bitdepth.channel_widths(channels or (8, 8, 8, 0))
    return 18 + int(width) * int(height) * (4 if widths[3] else 3)


def _save_pillow(image, path, image_format, dpi=None, **kwargs):
    """Save with a dpi pair when the format accepts it, otherwise without."""
    if dpi:
        try:
            image.save(path, image_format, dpi=(float(dpi), float(dpi)),
                       **kwargs)
            return
        except (TypeError, ValueError):
            pass
    image.save(path, image_format, **kwargs)


def save(bitmap, path, key="png", matte=MATTE_WHITE, quality=92, ppi=72.0,
         keep_alpha=True, gif_colors=256, png_compress=6,
         raw_mode="rgb", raw_depth=8, raw_channels=(8, 8, 8, 0),
         raw_packing="packed", raw_header="raw", raw_order="RGB"):
    """Write `bitmap` to `path` in the given format. Returns bytes written.

    `keep_alpha` flattens transparency onto `matte` when false, `gif_colors`
    caps the adaptive GIF palette and `png_compress` is Pillow's 0-9 deflate
    level. Formats that cannot store alpha always flatten. The `raw_*` options
    pick the layout and optional header of the custom-bit-depth format.
    """
    path = str(path)
    key = (key or "png").lower()
    if key not in FORMAT_BY_KEY:
        raise HexError("unknown format: %s" % key)
    _ensure_dir(path)
    if not keep_alpha and key not in ("bmp24", "ppm", "hex", "jpg", "gif"):
        bitmap = flatten_bitmap(bitmap, matte)
    if key == "bmp24":
        save_bmp24(bitmap, path, matte, ppi)
    elif key == "bmp32":
        save_bmp32(bitmap, path, ppi)
    elif key == "ppm":
        save_ppm(bitmap, path, matte)
    elif key == "hex":
        save_hex_text(bitmap, path)
    elif key == "raw":
        save_raw(bitmap, path, raw_mode, raw_depth, raw_channels,
                 raw_packing, matte, raw_header, ppi, raw_order)
    elif key == "jpg":
        _save_pillow(to_pil(bitmap, matte, flatten_alpha=True), path, "JPEG",
                     dpi=ppi, quality=int(quality), subsampling=0)
    elif key == "gif":
        colors = max(2, min(256, int(gif_colors or 256)))
        image = to_pil(bitmap, matte, flatten_alpha=True)
        image.convert("P", palette=Image.ADAPTIVE, colors=colors).save(
            path, "GIF")
    elif key == "webp":
        _save_pillow(to_pil(bitmap), path, "WEBP", dpi=ppi, lossless=True,
                     quality=100)
    elif key == "ico":
        # ICO entries are square, so pad onto a transparent square canvas
        # instead of letting the encoder resample the artwork.
        image = to_pil(bitmap)
        side = max(bitmap.width, bitmap.height)
        target = 256 if side > 256 else side
        if side != target:
            image = image.resize(
                (max(1, bitmap.width * target // side),
                 max(1, bitmap.height * target // side)), Image.NEAREST)
        square = Image.new("RGBA", (target, target), (0, 0, 0, 0))
        square.paste(image, (0, 0))
        square.save(path, "ICO", sizes=[(target, target)])
    elif key == "tga":
        to_pil(bitmap).save(path, "TGA")
    elif key == "tif":
        _save_pillow(to_pil(bitmap), path, "TIFF", dpi=ppi)
    else:  # png
        level = max(0, min(9, int(png_compress)))
        image = (to_pil(bitmap) if keep_alpha
                 else to_pil(bitmap, matte, flatten_alpha=True))
        _save_pillow(image, path, "PNG", dpi=ppi, optimize=False,
                     compress_level=level)
    return os.path.getsize(path)


def format_for_path(path):
    """Best-guess format key from a file extension."""
    ext = os.path.splitext(str(path))[1].lower()
    for item in FORMATS:
        if item["ext"] == ext:
            return item["key"]
    return "png"


# ------------------------------------------------------------------- projects
class ProjectFile(object):
    """A loaded .h2p project: the document plus the editor's session extras."""

    __slots__ = ("document", "extras")

    def __init__(self, document, extras=None):
        self.document = document
        self.extras = dict(extras or {})


def is_project_path(path):
    """True when `path` names a hex2pix project container."""
    return os.path.splitext(str(path or ""))[1].lower() == PROJECT_EXT


def save_project(path, document, extras=None):
    """Write the whole document as a .h2p zip and return its size in bytes.

    `project.json` holds the canvas properties, the layer list and any session
    extras (palette, pattern, colours, view); each layer's RGBA bytes live in
    its own `layers/NNN.rgba` entry and the byte selection in
    `selection.mask`, so layers stay separate on disk instead of being
    flattened.
    """
    path = str(path)
    _ensure_dir(path)
    entries = []
    layers = []
    for index, layer in enumerate(document.layers):
        name = "layers/%03d.rgba" % index
        layers.append({
            "name": layer.name,
            "op": layer.op,
            "gate": layer.gate,
            "blend": layer.blend,
            "visible": bool(layer.visible),
            "alpha": int(layer.alpha),
            "group": layer.group,
            "data": name,
        })
        entries.append((name, bytes(layer.bitmap.data)))
    header = {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "width": int(document.width),
        "height": int(document.height),
        "ppi": float(document.ppi),
        "color_mode": document.color_mode,
        "alpha": bool(document.alpha),
        "working": document.working_layout(),
        "matte": list(document.matte),
        "display_format": document.display_format,
        "active_layer": int(document.layer_index),
        "layers": layers,
        "groups": [group.to_state() for group in document.groups],
        "selection_active": bool(document.selection.active),
        "selection": "selection.mask" if document.selection.active else None,
        "extras": dict(extras or {}),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json",
                         json.dumps(header, indent=2, sort_keys=True))
        for name, data in entries:
            archive.writestr(name, data)
        if document.selection.active:
            archive.writestr("selection.mask", bytes(document.selection.mask))
    return os.path.getsize(path)


def load_project(path):
    """Read a .h2p project into a ProjectFile, layers kept separate."""
    path = str(path)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            try:
                raw_header = archive.read("project.json")
            except KeyError:
                raise HexError("not a hex2pix project: no project.json")
            try:
                header = json.loads(raw_header.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise HexError("damaged project header: %s" % exc)
            if header.get("format") != PROJECT_FORMAT:
                raise HexError("not a hex2pix project file")
            version = int(header.get("version", 0))
            if version > PROJECT_VERSION:
                raise HexError("this project needs a newer hex2pix "
                               "(format version %d)" % version)
            try:
                width = int(header["width"])
                height = int(header["height"])
            except (KeyError, TypeError, ValueError):
                raise HexError("project has no canvas size")
            if width <= 0 or height <= 0:
                raise HexError("project has no canvas size")
            layers = []
            for index, entry in enumerate(header.get("layers") or []):
                name = entry.get("data") or "layers/%03d.rgba" % index
                try:
                    data = archive.read(name)
                except KeyError:
                    raise HexError("project is missing layer data for %r"
                                   % (entry.get("name"),))
                state = dict(entry)
                state["data"] = data
                # Layer.from_state moves older single `op` names into the
                # right option when a project has no separate blend.
                layers.append(Layer.from_state(
                    state, width, height))
            if not layers:
                raise HexError("project has no layers")
            document = Document(width, height, path=path,
                                ppi=float(header.get("ppi", 72.0)),
                                color_mode=header.get("color_mode", "RGB"),
                                alpha=header.get("alpha", True))
            document.layers = layers
            document.groups = [LayerGroup.from_state(entry)
                               for entry in header.get("groups") or []
                               if isinstance(entry, dict)]
            known = set(group.name for group in document.groups)
            for layer in layers:
                if layer.group and layer.group not in known:
                    document.groups.append(LayerGroup(layer.group))
                    known.add(layer.group)
            document.layer_index = clamp(int(header.get("active_layer", 0)), 0,
                                         len(layers) - 1)
            document.display_format = str(header.get("display_format", "png"))
            matte = header.get("matte")
            if isinstance(matte, (list, tuple)) and len(matte) == 3:
                document.matte = tuple(clamp(int(v), 0, 255) for v in matte)
            working = header.get("working")
            if working is not None:
                if not isinstance(working, dict):
                    raise HexError("project has an invalid working layout")
                try:
                    document.set_working_layout(working, quantize=True)
                except HexError as exc:
                    raise HexError("project has an invalid working layout: %s"
                                   % exc)
            if header.get("selection_active"):
                try:
                    mask = archive.read(header.get("selection")
                                        or "selection.mask")
                except KeyError:
                    mask = b""
                if len(mask) == width * height:
                    document.selection.restore(mask)
    except zipfile.BadZipFile:
        raise HexError("not a hex2pix project: %s" % os.path.basename(path))
    except OSError as exc:
        raise HexError("could not read the project: %s" % exc)
    return ProjectFile(document, header.get("extras"))

