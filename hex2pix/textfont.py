"""System font discovery and rasterization for the Text tool.

The built-in 5x7 font stays the default. When another family is chosen, this
module finds the matching face installed on the machine and uses FreeType
(through Pillow) to turn the text into the same solid pixel mask the bitmap
font uses. TrueType, OpenType, collections, Windows bitmap fonts and the
FreeType text formats are all tried; a family that cannot be loaded simply
does not appear in the catalog.
"""

from __future__ import annotations

import os
import struct
import sys
from collections import namedtuple
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, features

BUILTIN_FONT = "Pixel 5x7"
CUSTOM_PREFIX = "Custom: "
FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc", ".fon", ".fnt",
                   ".pfb", ".pfa", ".bdf", ".pcf")
FONT_FILETYPES = [
    ("Font files", "*.ttf *.otf *.ttc *.otc *.fon *.fnt *.pfb *.pfa "
                   "*.bdf *.pcf"),
    ("All files", "*.*"),
]
_BITMAP_EXTENSIONS = (".bdf", ".pcf")
_PROBE_SIZES = (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32)
_STYLE_WORDS = frozenset((
    "black", "bold", "book", "condensed", "demibold", "extrabold",
    "extracondensed", "extralight", "heavy", "italic", "light", "medium",
    "narrow", "oblique", "regular", "semibold", "semicond", "semiconden",
    "semicondensed", "semilight", "thin", "ultralight",
))
_CHARSET_WORDS = frozenset(("baltic", "ce", "cyr", "cyrillic", "greek",
                            "tur", "turkish"))

FontFace = namedtuple("FontFace", "family style path index kind")
RenderedText = namedtuple("RenderedText", "points width height")


def _layout_engine():
    """Use RAQM when Pillow was built with it, for complex-script shaping."""
    try:
        if features.check("raqm"):
            return ImageFont.Layout.RAQM
    except (AttributeError, ValueError):
        pass
    return ImageFont.Layout.BASIC


def _open_truetype(path, size, index=0):
    # FreeTypeFont is the same loader as ImageFont.truetype, without that
    # helper's fallback search through the whole Windows font directory
    # whenever a legacy face cannot be opened at the requested size.
    kwargs = {"index": int(index)}
    try:
        kwargs["layout_engine"] = _layout_engine()
        return ImageFont.FreeTypeFont(path, int(size), **kwargs)
    except TypeError:
        kwargs.pop("layout_engine", None)
        return ImageFont.FreeTypeFont(path, int(size), **kwargs)


def _open_bitmap(path):
    return ImageFont.load(path)


def _font_name(font, path):
    try:
        family, style = font.getname()
    except (AttributeError, OSError, ValueError):
        family, style = "", ""
    family = str(family or "").strip().lstrip("@")
    style = str(style or "Regular").strip()
    if not family:
        family = os.path.splitext(os.path.basename(path))[0]
    return family, style


@lru_cache(maxsize=512)
def _is_symbol_face(path, index=0):
    """True when a font has a Windows symbol cmap instead of Unicode.

    Wingdings, Webdings and Symbol map their pictures to U+F020-U+F0FF in a
    platform-3/symbol cmap. FreeType treats that table as unmapped and draws
    .notdef for ordinary text, so these faces need the GDI symbol path.
    """
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
            if len(header) != 12:
                return False
            if header[:4] == b"ttcf":
                count = struct.unpack_from(">I", header, 8)[0]
                if not 0 < count <= 256 or not 0 <= index < count:
                    return False
                offsets = handle.read(4 * count)
                if len(offsets) != 4 * count:
                    return False
                base = struct.unpack_from(">I", offsets, 4 * index)[0]
            else:
                base = 0
            handle.seek(base)
            table_header = handle.read(12)
            if len(table_header) != 12:
                return False
            count = struct.unpack_from(">H", table_header, 4)[0]
            if not 0 < count <= 512:
                return False
            records = handle.read(16 * count)
            if len(records) != 16 * count:
                return False
            cmap = None
            for offset in range(0, len(records), 16):
                if records[offset:offset + 4] == b"cmap":
                    cmap = struct.unpack_from(">I", records, offset + 8)[0]
                    break
            if cmap is None:
                return False
            handle.seek(cmap)
            cmap_header = handle.read(4)
            if len(cmap_header) != 4:
                return False
            count = struct.unpack_from(">H", cmap_header, 2)[0]
            if not 0 < count <= 64:
                return False
            entries = handle.read(8 * count)
            if len(entries) != 8 * count:
                return False
            symbol = False
            for offset in range(0, len(entries), 8):
                platform, encoding = struct.unpack_from(">HH", entries, offset)
                if platform == 0 or (platform == 3 and encoding in (1, 10)):
                    return False
                if platform == 3 and encoding == 0:
                    symbol = True
            return symbol
    except (OSError, struct.error, ValueError):
        return False


def _font_dirs():
    folders = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR") or r"C:\Windows"
        local = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
        folders.extend([
            os.path.join(windir, "Fonts"),
            os.path.join(local, "Microsoft", "Windows", "Fonts"),
        ])
    elif sys.platform == "darwin":
        folders.extend([
            "/System/Library/Fonts",
            "/Library/Fonts",
            os.path.join(os.path.expanduser("~"), "Library", "Fonts"),
        ])
    else:
        home = os.path.expanduser("~")
        folders.extend([
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.join(home, ".fonts"),
            os.path.join(home, ".local", "share", "fonts"),
        ])
    return [folder for folder in folders if folder and os.path.isdir(folder)]


def _registry_font_paths():
    """Fonts registered outside the normal font folders, on Windows."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    result = []
    local = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    keys = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts",
         os.environ.get("WINDIR") or r"C:\Windows"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts",
         local),
    )
    for root, subkey, fallback in keys:
        try:
            handle = winreg.OpenKey(root, subkey, 0, winreg.KEY_READ)
        except OSError:
            continue
        index = 0
        try:
            while True:
                try:
                    _name, value, _kind = winreg.EnumValue(handle, index)
                except OSError:
                    break
                index += 1
                if not isinstance(value, str) or not value.strip():
                    continue
                path = value.strip()
                if not os.path.isabs(path):
                    path = os.path.join(fallback, "Fonts", path)
                result.append(path)
        finally:
            try:
                winreg.CloseKey(handle)
            except OSError:
                pass
    return result


@lru_cache(maxsize=1)
def _font_paths():
    paths = []
    seen = set()
    for folder in _font_dirs():
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if os.path.splitext(name)[1].lower() not in FONT_EXTENSIONS:
                    continue
                path = os.path.abspath(os.path.join(root, name))
                key = path.casefold()
                if key not in seen:
                    seen.add(key)
                    paths.append(path)
    for path in _registry_font_paths():
        if os.path.splitext(path)[1].lower() not in FONT_EXTENSIONS:
            continue
        path = os.path.abspath(path)
        if os.path.isfile(path):
            key = path.casefold()
            if key not in seen:
                seen.add(key)
                paths.append(path)
    paths.sort(key=lambda item: item.casefold())
    return tuple(paths)


@lru_cache(maxsize=512)
def _font_face_count(path):
    """Number of faces in a TrueType/OpenType collection, else one."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
        if len(header) == 12 and header[:4] == b"ttcf":
            count = struct.unpack_from(">I", header, 8)[0]
            if count:
                return min(count, 64)
    except (OSError, struct.error, ValueError):
        pass
    return 1


@lru_cache(maxsize=512)
def faces_from_file(path):
    """Every usable face in one font file, including collection members."""
    path = os.path.abspath(str(path or ""))
    if not path or not os.path.isfile(path):
        return ()
    extension = os.path.splitext(path)[1].lower()
    if extension in _BITMAP_EXTENSIONS:
        try:
            font = _open_bitmap(path)
            family, style = _font_name(font, path)
        except (OSError, ValueError):
            return ()
        return (FontFace(family, style, path, 0, "bitmap"),)

    faces = []
    seen = set()
    for index in range(_font_face_count(path)):
        font = None
        try:
            font = _open_truetype(path, 12, index)
        except (OSError, ValueError):
            for size in _PROBE_SIZES:
                try:
                    font = _open_truetype(path, size, index)
                except (OSError, ValueError):
                    continue
                break
        if font is None:
            break
        family, style = _font_name(font, path)
        key = (family.casefold(), style.casefold(), index)
        if key not in seen:
            seen.add(key)
            faces.append(FontFace(family, style, path, index, "truetype"))
    return tuple(faces)


@lru_cache(maxsize=1)
def system_faces():
    """All faces found in the operating system's font locations."""
    faces = []
    seen = set()
    for path in _font_paths():
        for face in faces_from_file(path):
            key = (face.family.casefold(), face.style.casefold(),
                   face.path.casefold(), face.index)
            if key not in seen:
                seen.add(key)
                faces.append(face)
    return tuple(faces)


@lru_cache(maxsize=1)
def family_names():
    """Sorted, non-vertical family and named-weight names from the catalog."""
    names = set()
    for face in system_faces():
        family = str(face.family or "").strip()
        if not family or family.startswith("@"):
            continue
        names.add(family)
        style = str(face.style or "").strip()
        if style and style.casefold() not in ("regular", "normal") \
                and style.casefold() not in _CHARSET_WORDS:
            names.add(family + " " + style)
    return tuple(sorted(names, key=str.casefold))


def custom_label(path, family=None):
    """The dropdown label for a font loaded from an explicit file."""
    name = str(family or "").strip().lstrip("@")
    if not name:
        name = os.path.splitext(os.path.basename(str(path or "")))[0]
    return CUSTOM_PREFIX + (name or "Font file")


def _style_flags(style):
    text = str(style or "").casefold()
    bold = any(word in text for word in
               ("bold", "black", "heavy", "demi", "semibold"))
    italic = "italic" in text or "oblique" in text
    return bold, italic


def _split_alias(family):
    """Split a Tk family alias into its base family and style words."""
    words = str(family or "").strip().split()
    extra = []
    known = _STYLE_WORDS | _CHARSET_WORDS
    while len(words) > 1:
        word = words[-1].casefold().replace("-", "").replace(" ", "")
        if word not in known:
            break
        extra.insert(0, words.pop())
    return " ".join(words), extra


def _style_distance(style, bold, italic, extra=()):
    text = str(style or "").casefold()
    extra_words = [str(word).casefold() for word in extra]
    want_bold = bool(bold) or any(word in _STYLE_WORDS
                                  and ("bold" in word or word in
                                       ("black", "heavy"))
                                  for word in extra_words)
    want_italic = bool(italic) or any(word in ("italic", "oblique")
                                      for word in extra_words)
    face_bold, face_italic = _style_flags(style)
    penalty = abs(int(face_bold) - int(want_bold))
    penalty += abs(int(face_italic) - int(want_italic))
    for word in extra_words:
        if word in _CHARSET_WORDS:
            continue
        if word not in text:
            penalty += 1
    if not extra and "regular" not in text:
        penalty += 1
    return (penalty, 0 if face_bold == want_bold else 1,
            0 if face_italic == want_italic else 1)


def face_for(family, bold=False, italic=False, file_path=""):
    """The best matching face for a family or a Tk-style family alias."""
    faces = faces_from_file(file_path) if file_path else system_faces()
    if not faces:
        return None
    wanted = str(family or "").strip().casefold()
    matches = [face for face in faces if face.family.casefold() == wanted]
    extra = []
    if not matches and not file_path:
        base, extra = _split_alias(family)
        matches = [face for face in faces
                   if face.family.casefold() == base.casefold()]
    if not matches and file_path:
        matches = list(faces)
    if not matches:
        return None
    return min(matches, key=lambda face: _style_distance(
        face.style, bold, italic, extra))


@lru_cache(maxsize=512)
def _valid_sizes(path, index):
    """Sizes accepted by a fixed-size font (legacy FON/FNT faces)."""
    sizes = []
    for size in range(1, 129):
        try:
            _open_truetype(path, size, index)
        except (OSError, ValueError):
            continue
        sizes.append(size)
    return tuple(sizes)


_VARIATION_ALIASES = {
    "semicond": "SemiCondensed",
    "semiconden": "SemiCondensed",
}


def _apply_variation(font, extra):
    """Select a named instance on a variable font, when it has one."""
    if not extra:
        return
    try:
        names = [name.decode("utf-8") if isinstance(name, bytes) else str(name)
                 for name in font.get_variation_names()]
    except (AttributeError, OSError, ValueError):
        return
    words = [_VARIATION_ALIASES.get(str(word).casefold(), str(word))
             for word in extra]
    candidates = [" ".join(words)] + words
    for candidate in candidates:
        for name in names:
            if name.casefold() == candidate.casefold():
                try:
                    font.set_variation_by_name(name)
                except (AttributeError, OSError, ValueError):
                    pass
                return


def open_face(face, size):
    """Open a face at `size`; fixed-size fonts fall back to their nearest size."""
    size = max(1, int(size))
    if face.kind == "bitmap":
        return _open_bitmap(face.path), size
    try:
        return _open_truetype(face.path, size, face.index), size
    except (OSError, ValueError):
        sizes = _valid_sizes(face.path, face.index)
        if not sizes:
            raise
        actual = min(sizes, key=lambda value: abs(value - size))
        return _open_truetype(face.path, actual, face.index), actual


@lru_cache(maxsize=128)
def _font_charset(family, bold=False, italic=False):
    """The Windows charset a family reports, or None when it cannot tell."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class TextMetric(ctypes.Structure):
        _fields_ = [
            ("tmHeight", wintypes.LONG), ("tmAscent", wintypes.LONG),
            ("tmDescent", wintypes.LONG),
            ("tmInternalLeading", wintypes.LONG),
            ("tmExternalLeading", wintypes.LONG),
            ("tmAveCharWidth", wintypes.LONG),
            ("tmMaxCharWidth", wintypes.LONG), ("tmWeight", wintypes.LONG),
            ("tmOverhang", wintypes.LONG),
            ("tmDigitizedAspectX", wintypes.LONG),
            ("tmDigitizedAspectY", wintypes.LONG),
            ("tmFirstChar", wintypes.WCHAR), ("tmLastChar", wintypes.WCHAR),
            ("tmDefaultChar", wintypes.WCHAR),
            ("tmBreakChar", wintypes.WCHAR),
            ("tmItalic", wintypes.BYTE), ("tmUnderlined", wintypes.BYTE),
            ("tmStruckOut", wintypes.BYTE),
            ("tmPitchAndFamily", wintypes.BYTE),
            ("tmCharSet", wintypes.BYTE),
        ]

    gdi32 = ctypes.windll.gdi32
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateFontW.restype = wintypes.HANDLE
    gdi32.CreateFontW.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, ctypes.c_wchar_p]
    gdi32.GetTextMetricsW.argtypes = [wintypes.HDC,
                                      ctypes.POINTER(TextMetric)]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]

    dc = font = old = None
    try:
        dc = gdi32.CreateCompatibleDC(None)
        if not dc:
            return None
        font = gdi32.CreateFontW(
            -12, 0, 0, 0, 700 if bold else 400, 1 if italic else 0,
            0, 0, 0, 0, 0, 0, 0, str(family))
        if not font:
            return None
        old = gdi32.SelectObject(dc, font)
        metric = TextMetric()
        if not gdi32.GetTextMetricsW(dc, ctypes.byref(metric)):
            return None
        return int(metric.tmCharSet)
    except (AttributeError, OSError, ValueError):
        return None
    finally:
        if dc and old:
            gdi32.SelectObject(dc, old)
        if font:
            gdi32.DeleteObject(font)
        if dc:
            gdi32.DeleteDC(dc)


def _glyph_image(font, char):
    """One character rendered like render_text does, cropped to its box."""
    try:
        box = font.getbbox(char)
    except (AttributeError, OSError, ValueError):
        return None
    if not box:
        return None
    left, top, right, bottom = [int(round(value)) for value in box]
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    image = Image.new("L", (width, height), 0)
    try:
        ImageDraw.Draw(image).text((-left, -top), char, font=font, fill=255)
    except (OSError, ValueError):
        return None
    if image.getbbox() is None:
        return None
    return image


def _mask_is_notdef(mask):
    """True when a glyph is a plain filled or outlined rectangle.

    FreeType draws .notdef for symbol-font ASCII as a solid bar in some fonts
    and a hollow box in others; either shape means the character was not
    really mapped.
    """
    box = mask.getbbox()
    if box is None:
        return False
    mask = mask.crop(box)
    width, height = [int(value) for value in mask.size]
    if width < 3 or height < 3:
        return False
    data = mask.tobytes()
    if len(data) != width * height:
        return False
    inset = 2 if min(width, height) >= 5 else 1
    solid = True
    outer = True
    inner = False
    for y in range(height):
        row = data[y * width:(y + 1) * width]
        for x in range(width):
            ink = bool(row[x])
            if not ink:
                solid = False
            if x in (0, width - 1) or y in (0, height - 1):
                if not ink:
                    outer = False
            elif (x >= inset and x < width - inset
                  and y >= inset and y < height - inset):
                if ink:
                    inner = True
    return solid or (outer and not inner)


_UNDEFINED_GLYPH = "\U0010FFFD"


def _glyph_crop(font, char):
    """One rendered character cropped to its ink, or None when it is blank."""
    image = _glyph_image(font, char)
    if image is None:
        return None
    box = image.getbbox()
    if box is None:
        return None
    return image.crop(box)


def _glyphs_are_notdef(font, text):
    """True when distinct characters all draw the same hollow notdef box.

    Symbol fonts such as Wingdings and Webdings map ASCII to a private-use
    range that FreeType does not follow, so it draws .notdef for each letter.
    """
    chars = []
    for char in str(text or ""):
        if char not in chars:
            chars.append(char)
        if len(chars) == 3:
            break
    images = [_glyph_crop(font, char) for char in chars]
    images = [image for image in images if image is not None]
    if not images:
        return False
    reference = _glyph_crop(font, _UNDEFINED_GLYPH)
    if reference is not None:
        signature = (reference.size, reference.tobytes())
        if all((image.size, image.tobytes()) == signature
               for image in images):
            return True
    if len(images) == 1:
        return _mask_is_notdef(images[0])
    if all(_mask_is_notdef(image) for image in images):
        return True
    first = (images[0].size, images[0].tobytes())
    return all((image.size, image.tobytes()) == first for image in images[1:])


def _symbol_bytes(text):
    """Translate symbol-font text to the byte codes GDI expects."""
    data = bytearray()
    for char in str(text or ""):
        code = ord(char)
        if 0xF000 <= code <= 0xF0FF:
            code -= 0xF000
        if 0 <= code <= 0xFF:
            data.append(code)
        else:
            data.append(ord("?"))
    return bytes(data)


def _gdi_render(text, family, size, bold=False, italic=False, symbol=False):
    """Render a Windows font family through GDI, including legacy FON aliases."""
    if sys.platform != "win32" or not text:
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader),
                    ("bmiColors", wintypes.DWORD * 3)]

    gdi32 = ctypes.windll.gdi32
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateFontW.restype = wintypes.HANDLE
    gdi32.CreateFontW.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, ctypes.c_wchar_p]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetTextExtentPoint32A.restype = wintypes.BOOL
    gdi32.GetTextExtentPoint32A.argtypes = [
        wintypes.HDC, ctypes.c_char_p, ctypes.c_int,
        ctypes.POINTER(wintypes.SIZE)]
    gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL
    gdi32.GetTextExtentPoint32W.argtypes = [
        wintypes.HDC, ctypes.c_wchar_p, ctypes.c_int,
        ctypes.POINTER(wintypes.SIZE)]
    gdi32.CreateDIBSection.restype = wintypes.HANDLE
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BitmapInfo), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
    gdi32.TextOutA.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                               ctypes.c_char_p, ctypes.c_int]
    gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                               ctypes.c_wchar_p, ctypes.c_int]
    gdi32.GdiFlush.argtypes = []

    text = str(text or "")
    raw_text = _symbol_bytes(text) if symbol else text
    height = max(1, int(size))
    weight = 700 if bold else 400
    italic_flag = 1 if italic else 0
    dc = None
    font = None
    old = None
    bitmap = None
    bits = ctypes.c_void_p()
    try:
        dc = gdi32.CreateCompatibleDC(None)
        if not dc:
            return None
        font = gdi32.CreateFontW(
            -height, 0, 0, 0, weight, italic_flag, 0, 0, 2 if symbol else 0,
            0, 0, 2, 0, str(family))
        if not font:
            return None
        old = gdi32.SelectObject(dc, font)
        extent = wintypes.SIZE()
        if symbol:
            extent_ok = gdi32.GetTextExtentPoint32A(
                dc, raw_text, len(raw_text), ctypes.byref(extent))
        else:
            extent_ok = gdi32.GetTextExtentPoint32W(
                dc, raw_text, len(raw_text), ctypes.byref(extent))
        if not extent_ok:
            return None
        width, text_height = int(extent.cx), int(extent.cy)
        if width <= 0 or text_height <= 0:
            return RenderedText((), 0, 0)
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -text_height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        bitmap = gdi32.CreateDIBSection(
            dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits:
            return None
        ctypes.memset(bits, 0, width * text_height * 4)
        selected = gdi32.SelectObject(dc, bitmap)
        gdi32.SetBkMode(dc, 1)
        gdi32.SetTextColor(dc, 0x00FFFFFF)
        if symbol:
            gdi32.TextOutA(dc, 0, 0, raw_text, len(raw_text))
        else:
            gdi32.TextOutW(dc, 0, 0, raw_text, len(raw_text))
        gdi32.GdiFlush()
        gdi32.SelectObject(dc, selected)
        raw = ctypes.string_at(bits, width * text_height * 4)
        points = tuple(
            (index % width, index // width)
            for index in range(width * text_height)
            if raw[index * 4] or raw[index * 4 + 1] or raw[index * 4 + 2])
    except (AttributeError, OSError, ValueError):
        return None
    finally:
        if dc and old:
            gdi32.SelectObject(dc, old)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if font:
            gdi32.DeleteObject(font)
        if dc:
            gdi32.DeleteDC(dc)
    if not points:
        return RenderedText((), 0, 0)
    return RenderedText(points, width, text_height)


def render_text(text, family, size, bold=False, italic=False, file_path=""):
    """Rasterize one line to pixel points, or None when the face is missing.

    `size` is the requested em height in document pixels. Text is drawn
    without antialiased colour; every pixel with any coverage becomes ink,
    which keeps stamps crisp while retaining the font's actual outlines.
    """
    face = face_for(family, bold, italic, file_path)
    if face is None:
        if not file_path:
            return _gdi_render(text, family, size, bold, italic)
        return None
    try:
        font, actual = open_face(face, size)
    except (OSError, ValueError):
        return None
    if not file_path:
        base, extra = _split_alias(family)
        if extra and face.family.casefold() == base.casefold():
            _apply_variation(font, extra)
    text = str(text or "")
    if not text:
        return RenderedText((), 0, 0)
    try:
        box = font.getbbox(text)
    except (AttributeError, OSError, ValueError):
        return None
    if not box:
        return RenderedText((), 0, 0)
    left, top, right, bottom = [int(round(value)) for value in box]
    width = max(0, right - left)
    height = max(0, bottom - top)
    if not width or not height:
        return RenderedText((), 0, 0)
    image = Image.new("L", (width, height), 0)
    try:
        ImageDraw.Draw(image).text((-left, -top), text, font=font, fill=255)
    except (OSError, ValueError):
        return None
    if actual != max(1, int(size)):
        scale = max(1, int(size)) / float(actual)
        image = image.resize((max(1, int(round(width * scale))),
                              max(1, int(round(height * scale)))),
                             Image.NEAREST)
    width, height = image.size
    data = image.tobytes()
    points = tuple((index % width, index // width)
                   for index, value in enumerate(data) if value)
    # FreeType cannot follow a symbol font's ASCII mapping and draws the
    # .notdef box instead; GDI can, and also rescues legacy faces whose only
    # glyphs FreeType cannot draw. Prefer its result when the signs are there.
    if sys.platform == "win32" and not file_path:
        symbol = (_is_symbol_face(face.path, face.index)
                  or _font_charset(face.family, bold, italic) == 2)
        if (symbol or not points or _glyphs_are_notdef(font, text)):
            fallback = _gdi_render(text, face.family, size, bold, italic,
                                   symbol=symbol)
            if fallback is not None and fallback.points:
                return fallback
    if not points:
        return RenderedText((), 0, 0)
    return RenderedText(points, width, height)


def clear_cache():
    """Forget discovered files and faces, e.g. after installing a font."""
    _font_paths.cache_clear()
    _font_face_count.cache_clear()
    _is_symbol_face.cache_clear()
    faces_from_file.cache_clear()
    system_faces.cache_clear()
    family_names.cache_clear()
    _valid_sizes.cache_clear()
