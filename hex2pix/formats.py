"""File formats for hex2pix.

BMP and PPM are written by hand so the byte layout is exactly what the format
says (24-bit and 32-bit BMP, 24-bit binary PPM); everything else goes through
Pillow. Reading prefers the hand-written BMP parser so a file this app wrote
round-trips with its alpha intact, even though some viewers read 32-bit
BI_RGB BMPs as 24-bit + padding.
"""

import os
import struct

from PIL import Image

from .model import (MATTE_WHITE, SKIP, Bitmap, HexError, block_from_bitmap,
                    parse_block)

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
    {"key": "hex", "label": "Hex text (.txt)", "ext": ".txt", "alpha": False},
)
FORMAT_BY_KEY = {item["key"]: item for item in FORMATS}
FORMAT_BY_LABEL = {item["label"]: item for item in FORMATS}
FORMAT_LABELS = [item["label"] for item in FORMATS]

OPEN_FILETYPES = [
    ("Images", "*.png *.bmp *.gif *.tga *.ico *.tif *.tiff *.webp *.jpg "
               "*.jpeg *.ppm *.pgm *.pbm"),
    ("Hex text", "*.txt *.hex"),
    ("All files", "*.*"),
]


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


def save(bitmap, path, key="png", matte=MATTE_WHITE, quality=92, ppi=72.0):
    """Write `bitmap` to `path` in the given format. Returns bytes written."""
    path = str(path)
    key = (key or "png").lower()
    if key not in FORMAT_BY_KEY:
        raise HexError("unknown format: %s" % key)
    _ensure_dir(path)
    if key == "bmp24":
        save_bmp24(bitmap, path, matte, ppi)
    elif key == "bmp32":
        save_bmp32(bitmap, path, ppi)
    elif key == "ppm":
        save_ppm(bitmap, path, matte)
    elif key == "hex":
        save_hex_text(bitmap, path)
    elif key == "jpg":
        _save_pillow(to_pil(bitmap, matte, flatten_alpha=True), path, "JPEG",
                     dpi=ppi, quality=int(quality), subsampling=0)
    elif key == "gif":
        image = to_pil(bitmap, matte, flatten_alpha=True)
        image.convert("P", palette=Image.ADAPTIVE, colors=256).save(path, "GIF")
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
        _save_pillow(to_pil(bitmap), path, "PNG", dpi=ppi, optimize=False)
    return os.path.getsize(path)


def format_for_path(path):
    """Best-guess format key from a file extension."""
    ext = os.path.splitext(str(path))[1].lower()
    for item in FORMATS:
        if item["ext"] == ext:
            return item["key"]
    return "png"
