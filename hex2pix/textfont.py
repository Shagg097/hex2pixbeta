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
    kwargs = {"index": int(index)}
    try:
        kwargs["layout_engine"] = _layout_engine()
        return ImageFont.truetype(path, int(size), **kwargs)
    except TypeError:
        kwargs.pop("layout_engine", None)
        return ImageFont.truetype(path, int(size), **kwargs)


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
    for index in range(64):
        font = None
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


def family_names():
    """Sorted, non-vertical family names from the installed font catalog."""
    names = set()
    for face in system_faces():
        if face.family and not face.family.startswith("@"):
            names.add(face.family)
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


def _style_distance(style, bold, italic):
    face_bold, face_italic = _style_flags(style)
    return (abs(int(face_bold) - int(bool(bold))),
            abs(int(face_italic) - int(bool(italic))),
            0 if "regular" in str(style or "").casefold() else 1)


def face_for(family, bold=False, italic=False, file_path=""):
    """The best matching face for a family and requested style."""
    faces = faces_from_file(file_path) if file_path else system_faces()
    if not faces:
        return None
    wanted = str(family or "").strip().casefold()
    matches = [face for face in faces if face.family.casefold() == wanted]
    if not matches and file_path:
        matches = list(faces)
    if not matches:
        return None
    return min(matches, key=lambda face: _style_distance(face.style, bold,
                                                         italic))


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


def render_text(text, family, size, bold=False, italic=False, file_path=""):
    """Rasterize one line to pixel points, or None when the face is missing.

    `size` is the requested em height in document pixels. Text is drawn
    without antialiased colour; every pixel with any coverage becomes ink,
    which keeps stamps crisp while retaining the font's actual outlines.
    """
    face = face_for(family, bold, italic, file_path)
    if face is None:
        return None
    try:
        font, actual = open_face(face, size)
    except (OSError, ValueError):
        return None
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
    if not points:
        return RenderedText((), 0, 0)
    return RenderedText(points, width, height)


def clear_cache():
    """Forget discovered files and faces, e.g. after installing a font."""
    _font_paths.cache_clear()
    faces_from_file.cache_clear()
    system_faces.cache_clear()
    _valid_sizes.cache_clear()
