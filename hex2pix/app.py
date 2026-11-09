"""hex2pix: a keyboard-first hex pixel editor with byte selections.

The window is a canvas, a tool palette, and a notebook of hex panels (block
placement, byte operations, recolor, byte dump, export). Everything edits the
`Document` from `hex2pix.model`; this module only draws and routes input.
"""

import colorsys
import io
import json
import math
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from tkinter import (colorchooser, filedialog, font as tkfont, messagebox,
                     simpledialog, ttk)

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from . import __version__, dither, filters, formats, icons
from .integrations import (GITHUB_REPOSITORY, SettingsStore, UpdateError,
                           apply_update, check_for_update, download_update,
                           is_folder_build)
from .macro import Macro, Recorder
from .palette import (MAX_PALETTE_COLORS, PALETTE_FILETYPES, PALETTES,
                      format_palette, make_palette, parse_palette,
                      preset_colors, quantize_bitmap)
from .pattern import PatternSpec, apply_pattern, block_from_grid
from .bitmapfont import text_size
from .effects import (blur_area, blur_stroke, content_mask, contour_mask,
                      smear_area, spray_area, shuffle_area, shuffle_stroke,
                      spray_stroke)
from .shapes import (brush_offsets, custom_brush, custom_brush_from_text,
                     custom_brush_text, gradient_fill, line_points,
                     mirror_points, paint_curve, paint_dab, paint_mask,
                     paint_shape, paint_stroke, set_custom_brush, shape_color,
                     stamp_pattern, stamp_text)
from .canvas import (ANCHOR_CODES, ANCHOR_LABELS, MAX_SIDE, UNITS, CanvasSpec,
                     apply_spec_to_document, physical_size, px_per_unit,
                     scale_image)
from .model import (
    LAYER_OPS, LAYER_OP_LABELS, OPS, OP_LABELS, Bitmap, Document, HexError,
    apply_hue, apply_map, apply_op,
    apply_ramp, block_from_bitmap, clear_region, extract_selection,
    flip_horizontal, flip_vertical, flood_fill, format_dump, hex_str,
    clean_path, invert_colors, paint_brush, paint_line, parse_block,
    parse_hex, parse_hex_map, pick_related, piece_mask, place_block, rotate90,
    RotationDrag, rotate_piece, scale_box, selected_bytes,
    selection_ellipse_mask,
    selection_polygon_mask,
    selection_rect_mask, stamp_bitmap, transform_block,
)

TOOLS = (
    ("pencil", "Pencil", "p", "Paint with the foreground color"),
    ("eraser", "Eraser", "e", "Erase to transparency"),
    ("spray", "Spray can", "a", "Spray the foreground color; hold to build up"),
    ("picker", "Picker", "i", "Pick the color under the cursor"),
    ("fill", "Fill", "f", "Flood fill related pixel data"),
    ("line", "Line", "n", "Drag a straight line, or Enter twice"),
    ("curve", "Curve", "c", "Drag a curved line; Bend sets the arc"),
    ("shape_rect", "Rectangle", "r", "Drag a rectangle, outline or filled"),
    ("shape_ellipse", "Ellipse", "u", "Drag an ellipse, outline or filled"),
    ("shape_triangle", "Triangle", "",
     "Drag a triangle, outline or filled"),
    ("shape_diamond", "Diamond", "", "Drag a diamond, outline or filled"),
    ("shape_star", "Star", "", "Drag a star, outline or filled"),
    ("shape_arrow", "Arrow", "", "Drag an arrow, outline or filled"),
    ("gradient", "Gradient", "v", "Drag a linear, radial or palette gradient"),
    ("pattern", "Line pattern", "l", "Fill lines with the pattern, row by row"),
    ("structure", "Structure fill", "k", "Fill with a mirrored structural pattern"),
    ("contour", "Contour", "g", "Outline the selection, or the artwork"),
    ("blur", "Blur", "b", "Soften the pixels under the brush"),
    ("shuffle", "Shuffle", "j", "Mix, scatter or noise the pixels"),
    ("text", "Text", "x", "Click to stamp the Text option in the fg color"),
    ("rect", "Rect select", "m", "Select a rectangle"),
    ("ellipse", "Ellipse select", "o", "Select an ellipse"),
    ("polygon", "Polygon select", "y", "Click vertices, Enter closes"),
    ("lasso", "Lasso select", "q", "Freehand byte selection"),
    ("wand", "Wand", "w", "Select contiguous related pixel data"),
    ("similar", "Similar", "s", "Select all related pixel data"),
    ("transform", "Move / Scale", "t",
     "Drag a selection to move it, Shift to scale, Ctrl to rotate"),
    ("hand", "Hand", "h", "Drag to pan; the wheel zooms at the pointer"),
)
TOOL_BY_KEY = {key: name for name, _, key, _ in TOOLS if key}
TOOL_TIPS = {name: tip for name, _, _, tip in TOOLS}
HEX_CHARS = set("0123456789abcdef")

SELECTION_TOOLS = ("rect", "ellipse", "polygon", "lasso", "wand",
                   "similar", "transform")
PAINT_TOOLS = ("pencil", "eraser")
SHAPE_TOOLS = ("line", "curve", "shape_rect", "shape_ellipse",
               "shape_triangle", "shape_diamond", "shape_star",
               "shape_arrow")

# The palette is a grid of dropdown folders; related tools share a button.
TOOL_GROUPS = (
    ("brush", "Brush", ("pencil", "eraser", "spray", "blur", "shuffle")),
    ("shape", "Shapes", ("line", "curve", "shape_rect", "shape_ellipse",
                         "shape_triangle", "shape_diamond", "shape_star",
                         "shape_arrow")),
    ("fill", "Fill", ("fill", "gradient", "pattern", "structure",
                      "contour")),
    ("select", "Select", ("rect", "ellipse", "polygon", "lasso", "wand",
                          "similar")),
    ("transform", "Transform", ("transform", "hand")),
    ("utility", "Text and pick", ("picker", "text")),
)

# Dockers hidden on startup; the View menu shows them again.
DEFAULT_HIDDEN_DOCKERS = ("block", "bytes", "dump", "export")

EXPORT_SOURCES = ("Whole image", "Current layer", "Selection",
                  "Each visible layer")


def _layer_export_path(path, name, index, ext):
    """A unique file path for one layer exported beside `path`."""
    stem, current = os.path.splitext(path)
    if current.lower() != ext:
        stem = path
    safe = "".join(char if char.isalnum() or char in "-_" else "-"
                   for char in name).strip("-")
    safe = safe or "layer"
    return "%s_%02d_%s%s" % (stem, index, safe, ext)

MAX_PIXELS = 12000000
MIN_ZOOM = 1e-9           # keeps Tk's coordinate maths finite on huge canvases
ZOOM_OUT_FACTOR = 50000.0  # furthest zoom out: 1 : (longer side x this)
MAX_ZOOM = 64.0
ROTATE_SNAP = 15.0
SYMMETRY_LABELS = {"": "Off", "x": "X", "y": "Y", "xy": "XY"}
SYMMETRY_VALUES = {"Off": "", "X": "x", "Y": "y", "XY": "xy"}
SEL_MODE_LABELS = {"replace": "Replace", "add": "Add",
                   "subtract": "Subtract", "intersect": "Intersect"}
SEL_MODE_VALUES = {"Replace": "replace", "Add": "add",
                   "Subtract": "subtract", "Intersect": "intersect"}


def min_zoom_for(width, height):
    """The furthest zoom out: the longer side shrinks to 1/50,000 of a pixel."""
    span = max(1, int(width), int(height))
    return max(MIN_ZOOM, 1.0 / (span * ZOOM_OUT_FACTOR))


def _bounded(value, low, high, default):
    """An int from a widget variable, clamped to its range."""
    try:
        value = int(value)
    except (tk.TclError, TypeError, ValueError):
        return default
    return max(low, min(high, value))


class _KeyEvent(object):
    """A key press, either from tkinter or rebuilt from a recorded step."""

    __slots__ = ("keysym", "char", "state")

    def __init__(self, keysym, char="", state=0):
        self.keysym = keysym
        self.char = char
        self.state = int(state)


class _ToolTip(object):
    """A hover label, since the tool palette is icon-only."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def show(self, event=None):
        if self.tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() + 6
            y = self.widget.winfo_rooty() - 2
            tip = tk.Toplevel(self.widget)
        except tk.TclError:
            return
        tip.wm_overrideredirect(True)
        tip.wm_geometry("+%d+%d" % (x, y))
        tk.Label(tip, text=self.text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, font=("Segoe UI", 8),
                 padx=4, pady=1).pack()
        self.tip = tip

    def hide(self, event=None):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


HOME_BG = "#1b1b1f"
HOME_PANEL = "#26262b"
HOME_PANEL_ALT = "#303038"
HOME_ACCENT = "#3daee9"
HOME_TEXT = "#e9e9ef"
HOME_MUTED = "#9aa0aa"
HOME_BORDER = "#34343c"

THEMES = {
    "dark": {
        "bg": "#1b1e24",
        "panel": "#232830",
        "card": "#262c35",
        "border": "#3a414d",
        "fg": "#e6e9ef",
        "muted": "#9aa3b2",
        "field": "#2b313b",
        "field_fg": "#e6e9ef",
        "button": "#2d333d",
        "button_active": "#3a424e",
        "accent": "#2f8fd0",
        "accent_fg": "#ffffff",
        "tab": "#232830",
        "tab_selected": "#2f3540",
        "status_bg": "#14171c",
        "status_fg": "#e7ebf2",
        "status_hint": "#8f98a6",
        "canvas_bg": "#20242b",
        "canvas_border": "#39404d",
        "heading": "#e6e9ef",
        "home_bg": "#1b1b1f",
        "home_panel": "#26262b",
        "home_panel_alt": "#303038",
        "home_text": "#e9e9ef",
        "home_muted": "#9aa0aa",
        "home_border": "#34343c",
        "home_accent_fg": "#101014",
    },
    "light": {
        "bg": "#e9ecf2",
        "panel": "#eff2f7",
        "card": "#ffffff",
        "border": "#d9dde5",
        "fg": "#20242b",
        "muted": "#6b7280",
        "field": "#ffffff",
        "field_fg": "#20242b",
        "button": "#eef1f6",
        "button_active": "#e0e6ef",
        "accent": "#2f8fd0",
        "accent_fg": "#ffffff",
        "tab": "#e4e8ef",
        "tab_selected": "#ffffff",
        "status_bg": "#2b2f37",
        "status_fg": "#e7ebf2",
        "status_hint": "#8f98a6",
        "canvas_bg": "#20242b",
        "canvas_border": "#39404d",
        "heading": "#2f3540",
        "home_bg": "#f4f6fa",
        "home_panel": "#ffffff",
        "home_panel_alt": "#e6ebf3",
        "home_text": "#20242b",
        "home_muted": "#5b6472",
        "home_border": "#d5dbe5",
        "home_accent_fg": "#101014",
    },
}

HOME_PRESETS = (
    (16, 16, "icon"),
    (32, 32, "icon"),
    (64, 64, "sprite"),
    (128, 128, "sprite"),
    (256, 256, "tile"),
    (512, 512, "tile"),
    (1280, 720, "HD"),
    (1920, 1080, "Full HD"),
)


def default_recent_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"),
                                                     ".config")
    return os.path.join(base, "hex2pix", "recent.json")


def manual_path():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md")
    return path if os.path.isfile(path) else None


class RecentStore(object):
    """Recently opened documents, persisted as JSON in the user config dir."""

    def __init__(self, path=None, limit=10):
        self.path = str(path or default_recent_path())
        self.limit = max(1, int(limit))
        self.items = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return []
        entries = data.get("recent", []) if isinstance(data, dict) else []
        items = []
        for entry in entries:
            if not isinstance(entry, str):
                continue
            path = os.path.abspath(entry)
            if os.path.isfile(path) and path not in items:
                items.append(path)
            if len(items) >= self.limit:
                break
        return items

    def add(self, path):
        if not path:
            return
        path = os.path.abspath(str(path))
        if path in self.items:
            self.items.remove(path)
        self.items.insert(0, path)
        del self.items[self.limit:]
        self._save()

    def clear(self):
        self.items = []
        self._save()

    def _save(self):
        try:
            folder = os.path.dirname(self.path)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump({"recent": self.items}, handle, indent=2)
        except OSError:
            pass


class HomeView(tk.Frame):
    """Start page modelled on Krita's: create, recent documents, links."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=HOME_BG)
        self.app = app
        self._build()

    # ------------------------------------------------------------- building
    def _section(self, parent, text):
        tk.Label(parent, text=text.upper(), bg=HOME_BG, fg=HOME_MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")

    def _button(self, parent, text, command, accent=False):
        if accent:
            background, foreground = HOME_ACCENT, "#101014"
        else:
            background, foreground = HOME_PANEL, HOME_TEXT
        return tk.Button(parent, text=text, command=command, bg=background,
                         fg=foreground, activebackground=HOME_PANEL_ALT,
                         activeforeground=HOME_TEXT, relief="flat", bd=0,
                         padx=12, pady=6, cursor="hand2",
                         font=("Segoe UI", 9))

    def _link(self, parent, text, command):
        link = tk.Label(parent, text=text, bg=HOME_PANEL, fg=HOME_ACCENT,
                        font=("Segoe UI", 8), cursor="hand2")
        link.bind("<Button-1>", lambda event: command())
        return link

    def _build(self):
        header = tk.Frame(self, bg=HOME_BG)
        header.pack(fill="x", padx=36, pady=(26, 10))
        tk.Label(header, text="hex2pix", bg=HOME_BG, fg=HOME_TEXT,
                 font=("Segoe UI", 26, "bold")).pack(anchor="w")
        tk.Label(header,
                 text="A hex-first pixel editor: type codes, select bytes, "
                      "place blocks.",
                 bg=HOME_BG, fg=HOME_MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 0))
        tk.Frame(self, bg=HOME_ACCENT, height=3).pack(fill="x", padx=36)

        columns = tk.Frame(self, bg=HOME_BG)
        columns.pack(fill="both", expand=True, padx=36, pady=16)

        left = tk.Frame(columns, bg=HOME_BG)
        left.pack(side="left", fill="both", expand=True)
        self._section(left, "Start a new canvas")
        grid = tk.Frame(left, bg=HOME_BG)
        grid.pack(anchor="w", pady=(8, 8))
        for index, (width, height, caption) in enumerate(HOME_PRESETS):
            tk.Button(
                grid, text="%d \u00d7 %d\n%s" % (width, height, caption),
                bg=HOME_PANEL, fg=HOME_TEXT, activebackground=HOME_ACCENT,
                activeforeground="#101014", relief="flat", bd=0, width=12,
                height=2, justify="left", anchor="w", cursor="hand2",
                font=("Segoe UI", 9),
                command=lambda w=width, h=height: self.app.start_preset(w, h)
            ).grid(row=index // 4, column=index % 4, padx=(0, 8), pady=(0, 8),
                   sticky="ew")
        for column in range(4):
            grid.columnconfigure(column, weight=1)

        actions = tk.Frame(left, bg=HOME_BG)
        actions.pack(anchor="w", pady=(2, 0))
        self._button(actions, "Custom canvas...", self.app.new_document,
                     accent=True).pack(side="left", padx=(0, 8))
        self._button(actions, "Open image...", self.app.open_document).pack(
            side="left", padx=(0, 8))
        self._button(actions, "Continue", self.app.show_editor).pack(side="left")

        tk.Label(left,
                 text="Presets start at 72 ppi on a transparent canvas. "
                      "Custom canvas... opens the full dialog with units, "
                      "resolution, colour model and alpha.",
                 bg=HOME_BG, fg=HOME_MUTED, font=("Segoe UI", 8),
                 wraplength=520, justify="left").pack(anchor="w", pady=(12, 0))

        right = tk.Frame(columns, bg=HOME_BG, width=420)
        right.pack(side="right", fill="y", padx=(24, 0))
        right.pack_propagate(False)
        self._section(right, "Recent documents")
        list_frame = tk.Frame(right, bg=HOME_BORDER)
        list_frame.pack(fill="both", expand=True, pady=(8, 6))
        self.listbox = tk.Listbox(
            list_frame, bg=HOME_PANEL, fg=HOME_TEXT,
            selectbackground=HOME_ACCENT, selectforeground="#101014",
            activestyle="none", relief="flat", bd=0, highlightthickness=0,
            font=("Segoe UI", 9), height=10, exportselection=False)
        scroll = ttk.Scrollbar(list_frame, orient="vertical",
                               command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", self.open_selected)
        self.listbox.bind("<Return>", self.open_selected)

        self.empty_label = tk.Label(right, text="No recent documents yet.",
                                    bg=HOME_BG, fg=HOME_MUTED,
                                    font=("Segoe UI", 9))
        buttons = tk.Frame(right, bg=HOME_BG)
        buttons.pack(fill="x")
        self._button(buttons, "Open selected", self.open_selected).pack(
            side="left", padx=(0, 6))
        self._button(buttons, "Clear list", self.clear_recent).pack(side="left")

        footer = tk.Frame(self, bg=HOME_PANEL)
        footer.pack(fill="x", side="bottom")
        tk.Label(footer, text="hex2pix %s" % __version__, bg=HOME_PANEL,
                 fg=HOME_MUTED, font=("Segoe UI", 8)).pack(side="left",
                                                          padx=12, pady=6)
        self._link(footer, "Manual", self.open_manual).pack(side="right", padx=8)
        self._link(footer, "Byte selections",
                   self.app.show_selection_help).pack(side="right", padx=8)
        self._link(footer, "Keyboard reference",
                   self.app.show_help).pack(side="right", padx=8)

        self.refresh_recent()

    def _theme_lookup(self):
        """Map either theme's home colours onto the current palette."""
        colours = self.app.colors
        pairs = (("home_bg", HOME_BG), ("home_panel", HOME_PANEL),
                 ("home_panel_alt", HOME_PANEL_ALT),
                 ("accent", HOME_ACCENT), ("home_text", HOME_TEXT),
                 ("home_muted", HOME_MUTED), ("home_border", HOME_BORDER),
                 ("home_accent_fg", "#101014"))
        lookup = {}
        for key, original in pairs:
            target = colours.get(key, original).lower()
            lookup[original.lower()] = target
            for theme in THEMES.values():
                value = theme.get(key)
                if value:
                    lookup[value.lower()] = target
        return lookup

    def apply_theme(self):
        """Recolour the start page in place when the editor theme changes."""
        lookup = self._theme_lookup()
        try:
            self.configure(bg=self.app.colors["home_bg"])
        except tk.TclError:
            return
        stack = [self]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            for option in ("bg", "fg", "activebackground",
                           "activeforeground", "selectbackground",
                           "selectforeground", "highlightbackground"):
                try:
                    value = str(widget.cget(option)).lower()
                except tk.TclError:
                    continue
                replacement = lookup.get(value)
                if replacement and replacement != value:
                    try:
                        widget.configure(**{option: replacement})
                    except tk.TclError:
                        pass

    # -------------------------------------------------------------- actions
    def refresh_recent(self):
        self.listbox.delete(0, "end")
        items = self.app.recent_files.items
        for path in items:
            self.listbox.insert("end", "  %s    %s"
                                % (os.path.basename(path),
                                   os.path.dirname(path)))
        if items:
            self.empty_label.pack_forget()
        else:
            self.empty_label.pack(anchor="w", pady=(0, 4))

    def open_selected(self, event=None):
        selection = self.listbox.curselection()
        items = self.app.recent_files.items
        if not selection or selection[0] >= len(items):
            return "break"
        self.app.load_path(items[selection[0]])
        return "break"

    def clear_recent(self):
        self.app.recent_files.clear()
        self.refresh_recent()

    def open_manual(self):
        path = manual_path()
        if not path:
            self.app.report_error("The manual was not found next to the app.")
            return
        try:
            os.startfile(path)  # noqa: S606 - open the README in its viewer
        except (AttributeError, OSError):
            self.app.report_error("Manual: %s" % path)


class ColorWheel(tk.Canvas):
    """Hue ring around a saturation/value square.

    Left-click sets the foreground colour and right-click the background;
    alpha stays with the app's existing swatches.
    """

    def __init__(self, parent, size=140, command=None, **kwargs):
        super().__init__(parent, width=size, height=size, bd=0,
                         highlightthickness=0, bg="#ffffff", **kwargs)
        self.size = int(size)
        self.command = command
        self.outer = self.size / 2.0 - 2
        self.inner = self.outer * 0.64
        self.square = int(self.inner * 1.414) - 4
        self.left = int((self.size - self.square) / 2.0)
        self.top = int((self.size - self.square) / 2.0)
        self._hue = 0.92
        self._sat = 1.0
        self._val = 1.0
        self._ring_photo = None
        self._square_photo = None
        self._render_ring()
        self.create_image(0, 0, anchor="nw", image=self._ring_photo,
                          tags="ring")
        self._square_item = self.create_image(self.left, self.top,
                                               anchor="nw", tags="square")
        self._render_square()
        self._ring_dot = self.create_oval(0, 0, 0, 0, outline="#ffffff",
                                          width=2, tags="marker")
        self._square_dot = self.create_oval(0, 0, 0, 0, outline="#ffffff",
                                            width=2, tags="marker")
        self.bind("<Button-1>", lambda event: self._pick(event, False))
        self.bind("<B1-Motion>", lambda event: self._pick(event, False))
        self.bind("<Button-3>", lambda event: self._pick(event, True))
        self.bind("<B3-Motion>", lambda event: self._pick(event, True))
        self.set_color((255, 0, 128, 255))

    def _render_ring(self):
        scale = 4
        big = self.size * scale
        image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        box = [0, 0, big - 1, big - 1]
        for step in range(360):
            red, green, blue = colorsys.hsv_to_rgb(step / 360.0, 1.0, 1.0)
            draw.pieslice(box, step, step + 1,
                          fill=(int(red * 255), int(green * 255),
                                int(blue * 255), 255))
        mask = Image.new("L", (big, big), 0)
        mask_draw = ImageDraw.Draw(mask)
        center = big / 2.0
        outer = self.outer * scale
        inner = self.inner * scale
        mask_draw.ellipse([center - outer, center - outer,
                           center + outer, center + outer], fill=255)
        mask_draw.ellipse([center - inner, center - inner,
                           center + inner, center + inner], fill=0)
        image.putalpha(mask)
        image = image.resize((self.size, self.size), Image.LANCZOS)
        self._ring_photo = ImageTk.PhotoImage(image)

    def _render_square(self):
        side = max(4, self.square)
        scale = 2
        big = side * scale
        image = Image.new("RGBA", (big, big), (0, 0, 0, 255))
        pixels = image.load()
        for y in range(big):
            value = 1.0 - y / float(big - 1)
            for x in range(big):
                saturation = x / float(big - 1)
                red, green, blue = colorsys.hsv_to_rgb(self._hue,
                                                       saturation, value)
                pixels[x, y] = (int(red * 255 + 0.5),
                                int(green * 255 + 0.5),
                                int(blue * 255 + 0.5), 255)
        image = image.resize((side, side), Image.LANCZOS)
        self._square_photo = ImageTk.PhotoImage(image)
        self.itemconfig(self._square_item, image=self._square_photo)

    def set_color(self, color):
        red, green, blue = [channel / 255.0 for channel in color[:3]]
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        if saturation > 0.001:
            self._hue = hue
        self._sat = saturation
        self._val = value
        self._render_square()
        self._place_markers()

    def _place_markers(self):
        angle = self._hue * 2.0 * math.pi
        radius = (self.outer + self.inner) / 2.0
        center = self.size / 2.0
        dot_x = center + math.cos(angle) * radius
        dot_y = center + math.sin(angle) * radius
        self.coords(self._ring_dot, dot_x - 5, dot_y - 5,
                    dot_x + 5, dot_y + 5)
        square_x = self.left + self._sat * self.square
        square_y = self.top + (1.0 - self._val) * self.square
        self.coords(self._square_dot, square_x - 5, square_y - 5,
                    square_x + 5, square_y + 5)
        self.tag_raise("marker")

    def _pick(self, event, secondary):
        center = self.size / 2.0
        distance = math.hypot(event.x - center, event.y - center)
        changed = False
        if self.inner <= distance <= self.outer:
            self._hue = (math.atan2(event.y - center, event.x - center)
                         / (2.0 * math.pi)) % 1.0
            if self._sat < 0.05:
                self._sat = 1.0
            self._render_square()
            changed = True
        elif (self.left <= event.x < self.left + self.square
              and self.top <= event.y < self.top + self.square):
            self._sat = max(0.0, min(1.0, (event.x - self.left)
                                     / float(self.square)))
            self._val = max(0.0, min(1.0, 1.0 - (event.y - self.top)
                                     / float(self.square)))
            changed = True
        if not changed:
            return
        self._place_markers()
        if self.command is not None:
            red, green, blue = colorsys.hsv_to_rgb(self._hue, self._sat,
                                                   self._val)
            self.command((int(red * 255 + 0.5), int(green * 255 + 0.5),
                          int(blue * 255 + 0.5), 255), secondary)


class PaletteDialog(tk.Toplevel):
    """Ask for a new palette's name, size and starting fill."""

    FILLS = ("Black", "White", "Foreground", "Grayscale ramp")

    def __init__(self, parent, fg=(255, 255, 255, 255),
                 name="Untitled palette"):
        super().__init__(parent)
        self.title("New palette")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.fg = tuple(fg)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Name").grid(row=0, column=0, sticky="w",
                                          pady=2)
        self.name_var = tk.StringVar(value=name)
        self.name_entry = ttk.Entry(body, textvariable=self.name_var,
                                    width=24)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(body, text="Colors").grid(row=1, column=0, sticky="w",
                                            pady=2)
        self.count_var = tk.IntVar(value=16)
        ttk.Spinbox(body, from_=1, to=MAX_PALETTE_COLORS, width=6,
                    textvariable=self.count_var).grid(row=1, column=1,
                                                       sticky="w", pady=2)
        ttk.Label(body, text="Start with").grid(row=2, column=0, sticky="w",
                                                pady=2)
        self.fill_var = tk.StringVar(value=self.FILLS[0])
        ttk.Combobox(body, values=self.FILLS, state="readonly", width=14,
                     textvariable=self.fill_var).grid(row=2, column=1,
                                                      sticky="w", pady=2)
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(
            side="right")
        ttk.Button(buttons, text="Create", command=self.ok).pack(
            side="right", padx=4)
        self.bind("<Return>", lambda event: self.ok())
        self.bind("<Escape>", lambda event: self.cancel())
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.name_entry.focus_set()

    def build(self):
        name = self.name_var.get().strip() or "Untitled palette"
        try:
            count = int(self.count_var.get())
        except (TypeError, ValueError, tk.TclError):
            count = 16
        fill = {"black": "black", "white": "white",
                "foreground": "color",
                "grayscale ramp": "grayscale"}.get(
                    self.fill_var.get().lower(), "black")
        return make_palette(name, count, fill, self.fg)

    def ok(self):
        self.result = self.build()
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class DocumentTab(object):
    """One canvas in the tab strip, with where its view was left."""

    def __init__(self, document):
        self.doc = document
        self.cursor = (0, 0)
        self.zoom = 8
        self.xview = 0.0
        self.yview = 0.0


BRUSH_TIPS = ("Square", "Circle", "Custom")
BRUSH_TIP_SIZE = 9


class BrushShapeDialog(tk.Toplevel):
    """Draw the footprint of a custom brush tip."""

    def __init__(self, parent, rows=None):
        tk.Toplevel.__init__(self, parent)
        self.result = None
        self.size = BRUSH_TIP_SIZE
        rows = [list(row) for row in (rows or [])]
        self.cells = [[1 if y < len(rows) and x < len(rows[y]) and rows[y][x]
                       else 0 for x in range(self.size)]
                      for y in range(self.size)]
        self.title("Custom brush tip")
        self.transient(parent)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Click cells to shape the brush tip; an "
                             "empty tip is ignored.").pack(anchor="w",
                                                           pady=(0, 8))
        grid = ttk.Frame(body)
        grid.pack()
        self.buttons = []
        for y in range(self.size):
            row = []
            for x in range(self.size):
                button = tk.Button(grid, width=2, height=1, bd=0,
                                   bg="#20242b", fg="#cfd6e4",
                                   activebackground="#3daee9",
                                   command=lambda x=x, y=y: self.toggle(x, y))
                button.grid(row=y, column=x, padx=1, pady=1)
                row.append(button)
            self.buttons.append(row)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Clear", command=self.clear).pack(side="left")
        ttk.Button(actions, text="Fill", command=self.fill).pack(side="left",
                                                                  padx=6)
        ttk.Button(actions, text="Cancel",
                   command=self.cancel).pack(side="right")
        ttk.Button(actions, text="OK",
                   command=self.accept).pack(side="right", padx=6)
        self.bind("<Escape>", lambda event: self.cancel())
        self.paint()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def paint(self):
        for y, row in enumerate(self.buttons):
            for x, button in enumerate(row):
                button.configure(bg="#d8dee9" if self.cells[y][x]
                                 else "#20242b")

    def toggle(self, x, y):
        self.cells[y][x] = 0 if self.cells[y][x] else 1
        self.paint()

    def clear(self):
        self.cells = [[0] * self.size for _ in range(self.size)]
        self.paint()

    def fill(self):
        self.cells = [[1] * self.size for _ in range(self.size)]
        self.paint()

    def accept(self):
        if not any(cell for row in self.cells for cell in row):
            return
        self.result = [list(row) for row in self.cells]
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class DropDialog(tk.Toplevel):
    """Ask what to do with image files dropped onto the window."""

    def __init__(self, parent, paths):
        tk.Toplevel.__init__(self, parent)
        self.result = None
        self.title("Add dropped files")
        self.transient(parent)
        self.resizable(False, False)
        names = [os.path.basename(str(path)) for path in paths]
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += " and %d more" % (len(names) - 3)
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=shown, wraplength=380).pack(anchor="w")
        ttk.Label(body, text="Insert %s as a layer on this canvas, or open "
                             "%s in %s tab%s?"
                  % ("it" if len(names) == 1 else "them",
                     "it" if len(names) == 1 else "each",
                     "a" if len(names) == 1 else "new",
                     "" if len(names) == 1 else "s")).pack(
            anchor="w", pady=(6, 10))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Insert as layer",
                   command=lambda: self.choose("layer")).pack(side="left")
        ttk.Button(buttons, text="Open as canvas",
                   command=lambda: self.choose("canvas")).pack(side="left",
                                                              padx=6)
        ttk.Button(buttons, text="Cancel",
                   command=self.cancel).pack(side="right")
        self.bind("<Escape>", lambda event: self.cancel())
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def choose(self, result):
        self.result = result
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


WM_DROPFILES = 0x0233
GWL_WNDPROC = -4


def _dropped_file_paths(shell32, ctypes, hdrop):
    """Read the file list out of a WM_DROPFILES handle."""
    from ctypes import wintypes
    handle = wintypes.HANDLE(hdrop)
    count = int(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0))
    paths = []
    for index in range(count):
        length = int(shell32.DragQueryFileW(handle, index, None, 0))
        buffer = ctypes.create_unicode_buffer(length + 1)
        shell32.DragQueryFileW(handle, index, buffer, length + 1)
        if buffer.value:
            paths.append(buffer.value)
    return paths


def enable_file_drops(window, callback):
    """Let Explorer drop files onto `window`; True when the hook is installed.

    Tk 8.6 has no drag and drop of its own on Windows, so this registers the
    window with the shell and reads WM_DROPFILES in a subclassed window
    procedure. That procedure must not call into Tk: a drop arrives while
    Explorer is still inside its own drag loop, and touching the interpreter
    from there crashes the program. It only stores the paths, and a timer
    hands them to `callback` once control is back in Tk's event loop. Every
    failure path returns False, because the editor works without drops.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        if hasattr(user32, "SetWindowLongPtrW"):
            set_long = user32.SetWindowLongPtrW
        else:
            # 32-bit Windows has no ...PtrW; the plain call is the same size.
            set_long = user32.SetWindowLongW
        set_long.restype = ctypes.c_void_p
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                           wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPARAM]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                           wintypes.LPWSTR, wintypes.UINT]
        shell32.DragFinish.argtypes = [wintypes.HANDLE]
        prototype = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                       wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM)
        client = int(window.winfo_id())
        parent = user32.GetParent(wintypes.HWND(client))
        targets = [client]
        if parent and int(parent) != client:
            targets.append(int(parent))
        queue = []
        keep = []
        hooks = []
        stopped = []
        job = [None]

        def install(target):
            # The previous procedure belongs to this window alone.
            previous = {}

            def handler(hwnd, message, wparam, lparam):
                try:
                    if message == WM_DROPFILES:
                        try:
                            paths = _dropped_file_paths(shell32, ctypes,
                                                        wparam)
                            if paths:
                                # Only Python state here; see the docstring.
                                queue.append(paths)
                        finally:
                            try:
                                shell32.DragFinish(wintypes.HANDLE(wparam))
                            except Exception:  # noqa: BLE001
                                pass
                        return 0
                    old = previous.get("proc")
                    if old is None:
                        return 0
                    return user32.CallWindowProcW(old, hwnd, message,
                                                  wparam, lparam)
                except Exception:  # noqa: BLE001 - never raise in a wndproc
                    return 0

            shell32.DragAcceptFiles(wintypes.HWND(target), True)
            procedure = prototype(handler)
            old = set_long(wintypes.HWND(target), GWL_WNDPROC,
                           ctypes.cast(procedure, ctypes.c_void_p))
            if not old:
                return False
            previous["proc"] = ctypes.c_void_p(old)
            keep.append(procedure)
            hooks.append((target, old))
            return True

        if not any(install(target) for target in targets):
            return False
        # A collected thunk would crash Tk, so keep the callbacks alive here.
        window._drop_procedures = keep

        def drain():
            """Hand the queued drops to the editor, from Tk's event loop."""
            job[0] = None
            while queue:
                paths = queue.pop(0)
                try:
                    callback(paths)
                except Exception:  # noqa: BLE001 - a bad drop must not
                    pass          # take the whole editor down
            if stopped:
                return
            try:
                if not window.winfo_exists():
                    return
                job[0] = window.after(60, drain)
            except tk.TclError:
                pass

        def restore(event=None):
            """Put Tk's own procedure back before the window goes away."""
            if event is not None and event.widget is not window:
                return
            stopped.append(True)
            if job[0] is not None:
                try:
                    window.after_cancel(job[0])
                except tk.TclError:
                    pass
                job[0] = None
            while hooks:
                target, old = hooks.pop()
                try:
                    set_long(wintypes.HWND(target), GWL_WNDPROC,
                             ctypes.c_void_p(old))
                except Exception:  # noqa: BLE001
                    pass

        try:
            window.bind("<Destroy>", restore, add="+")
        except tk.TclError:
            pass
        drain()
        return True
    except Exception:  # noqa: BLE001 - drag and drop is a convenience
        return False


class hex2pixApp(tk.Tk):
    """The editor window."""

    def __init__(self, document=None, recent_path=None, show_home=True,
                 settings_path=None):
        super().__init__()
        self.settings = SettingsStore(settings_path)
        self._load_custom_brush()
        self._update_results = queue.Queue()
        self._update_thread = None
        self._update_busy = False
        self._closing = False
        self._destroyed = False
        self.doc = document or Document(64, 64)
        self.tabs = [DocumentTab(self.doc)]
        self.tab_index = 0
        self._tab_reused = False
        self.drop_enabled = False
        self.blur_last = None
        self.recent_files = RecentStore(recent_path)
        self.home_visible = False
        self.fg = (255, 255, 255, 255)
        self.bg = (255, 0, 128, 255)
        self.tool = "pencil"
        self.brush = 1
        self.zoom = 8
        self.grid = True
        self.view_mode = "image"
        self.hex_font = 9
        self.hex_font_obj = tkfont.Font(family="Consolas", size=self.hex_font)
        self.hex_cell = (72, 18)
        self.hex_pad = 3
        self.view_mode_var = tk.StringVar(value="image")
        self._update_hex_metrics()
        self.tolerance = 0
        self.cursor = (0, 0)
        self.typed = ""
        self.sel_mode = "replace"
        self.sel_backup = None
        self.drag_anchor = None
        self.poly_points = []
        self.lasso_points = []
        self.last_paint = None
        self.stroke_before = None
        self.erase_drag = False
        self.transform_before = None
        self.transform_base = None
        self.transform_piece = None
        self.transform_mask = None
        self.transform_box = None
        self.transform_origin_box = None
        self.transform_grab = None
        self.transform_fixed = None
        self.transform_mode = "move"
        self.transform_angle = 0.0
        self.transform_rotation = None
        self.layer_alpha_before = None
        self.layer_alpha_dirty = False
        self._layer_alpha_syncing = False
        self.recent = []
        self.silent = False
        self._photo = None
        self._checker = None
        self._checker_key = None
        self._checker_tile = None
        self._dirty_cells = set()
        self._hex_incremental_ok = False
        self._hex_geom = None
        self._hex_items = {}
        self._hex_cursor = None
        self._pan = None
        self._redraw_pending = False
        self.kb_anchor = None
        self.kb_before = None
        self.recorder = Recorder()
        self.playing = False
        self._dispatching = False
        self.play_steps = []
        self.play_index = 0
        self._play_job = None
        self._play_interval = 45
        self.pattern_size = 8
        self.pattern_cells = [[None] * self.pattern_size
                              for _ in range(self.pattern_size)]
        self.pattern_cell_px = 15
        self.symmetry = ""
        self.shape_anchor = None
        self.shape_base = None
        self.shape_before = None
        self.gradient_anchor = None
        self.gradient_before = None
        self.palette = preset_colors("PICO-8")
        self.palette_name = "PICO-8"
        self.palette_selected = None
        self.palette_chips = []
        self.fullscreen = False
        self._saved_geometry = None
        self.dark_mode = True
        self.colors = THEMES["dark"]
        self.update_auto_var = tk.BooleanVar(
            value=bool(self.settings.get("check_updates")))
        self.docker_vars = {}
        self.docker_tabs = {}
        self.docker_labels = {}
        self._heading_widgets = []
        self._muted_labels = []

        self.title("hex2pix")
        self._set_window_icon()
        self.geometry("1200x780")
        self.minsize(940, 620)
        self.configure(bg=self.colors["bg"])
        self._polish_style()

        self._build_menus()
        self.status = tk.StringVar(value="")
        self.status_bar = tk.Frame(self, bg=self.colors["status_bg"])
        self.status_bar.pack(side="bottom", fill="x")
        self.status_rule = tk.Frame(self, bg=self.colors["border"], height=1)
        self.status_rule.pack(side="bottom", fill="x")
        self.status_label = tk.Label(self.status_bar, textvariable=self.status,
                                     anchor="w",
                                     bg=self.colors["status_bg"],
                                     fg=self.colors["status_fg"], padx=12,
                                     pady=5, font=("Segoe UI", 9))
        self.status_label.pack(side="left", fill="x", expand=True)
        self.status_hint = tk.Label(
            self.status_bar,
            text="Wheel zoom  \u00b7  Shift+wheel pan  \u00b7  F1 keys",
            bg=self.colors["status_bg"], fg=self.colors["status_hint"],
            padx=12, font=("Segoe UI", 8))
        self.status_hint.pack(side="right")

        self.container = ttk.Frame(self)
        self.container.pack(side="top", fill="both", expand=True)
        self.home = HomeView(self.container, self)
        self.editor = ttk.Frame(self.container)
        self._build_left(self.editor)
        self._build_right(self.editor)
        self._build_canvas(self.editor)
        self._populate_docker_menu()
        self.apply_theme()

        self.bind_all("<KeyPress>", self.on_key)
        self.view.focus_set()
        self.set_fg(self.fg)
        self._set_default_export_path()
        self.refresh_status()
        self.refresh_bytes()
        self.redraw()
        if show_home:
            self.show_home()
        else:
            self.show_editor()
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        self.drop_enabled = enable_file_drops(self, self.handle_dropped_paths)
        self.after(500, self._start_integrations)

    # ------------------------------------------------------------------ layout
    def _polish_style(self):
        """Use the themeable clam base and give widgets consistent padding."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self._style_ttk(style)

    @staticmethod
    def _style_configure(style, name, **options):
        try:
            style.configure(name, **options)
        except tk.TclError:
            pass

    @staticmethod
    def _style_map(style, name, **options):
        try:
            style.map(name, **options)
        except tk.TclError:
            pass

    def _style_ttk(self, style):
        """Paint every ttk widget in the current light or dark palette."""
        colours = self.colors
        configure = self._style_configure
        configure(style, "TFrame", background=colours["bg"])
        configure(style, "TLabel", background=colours["bg"],
                  foreground=colours["fg"])
        configure(style, "TButton", background=colours["button"],
                  foreground=colours["fg"], bordercolor=colours["border"],
                  lightcolor=colours["border"],
                  darkcolor=colours["border"],
                  focuscolor=colours["accent"], relief="flat",
                  padding=(6, 3))
        self._style_map(
            style, "TButton",
            background=[("active", colours["button_active"]),
                        ("pressed", colours["button_active"])],
            foreground=[("disabled", colours["muted"])])
        configure(style, "TEntry", fieldbackground=colours["field"],
                  foreground=colours["field_fg"],
                  insertcolor=colours["field_fg"],
                  bordercolor=colours["border"],
                  lightcolor=colours["border"],
                  darkcolor=colours["border"], padding=2)
        configure(style, "TCombobox", fieldbackground=colours["field"],
                  background=colours["button"],
                  foreground=colours["field_fg"], arrowcolor=colours["fg"],
                  bordercolor=colours["border"],
                  lightcolor=colours["border"],
                  darkcolor=colours["border"], padding=1)
        self._style_map(
            style, "TCombobox",
            fieldbackground=[("readonly", colours["field"])],
            foreground=[("readonly", colours["field_fg"])],
            selectbackground=[("readonly", colours["field"])],
            selectforeground=[("readonly", colours["field_fg"])],
            arrowcolor=[("active", colours["accent"])])
        configure(style, "TSpinbox", fieldbackground=colours["field"],
                  background=colours["button"],
                  foreground=colours["field_fg"], arrowcolor=colours["fg"],
                  bordercolor=colours["border"], padding=1)
        configure(style, "TCheckbutton", background=colours["bg"],
                  foreground=colours["fg"],
                  indicatorbackground=colours["field"],
                  indicatorforeground=colours["accent"],
                  focuscolor=colours["accent"])
        self._style_map(style, "TCheckbutton",
                        background=[("active", colours["bg"])])
        configure(style, "TRadiobutton", background=colours["bg"],
                  foreground=colours["fg"],
                  indicatorbackground=colours["field"],
                  indicatorforeground=colours["accent"],
                  focuscolor=colours["accent"])
        self._style_map(style, "TRadiobutton",
                        background=[("active", colours["bg"])])
        configure(style, "TScale", background=colours["bg"],
                  troughcolor=colours["field"])
        configure(style, "TNotebook", background=colours["bg"],
                  borderwidth=0)
        configure(style, "TNotebook.Tab", background=colours["panel"],
                  foreground=colours["muted"], font=("Segoe UI", 8),
                  padding=(8, 6),
                  bordercolor=colours["border"],
                  lightcolor=colours["panel"],
                  darkcolor=colours["panel"])
        self._style_map(
            style, "TNotebook.Tab",
            background=[("selected", colours["tab_selected"])],
            foreground=[("selected", colours["fg"])])
        configure(style, "TSeparator", background=colours["border"])
        configure(style, "TScrollbar", background=colours["button"],
                  troughcolor=colours["bg"], arrowcolor=colours["fg"],
                  bordercolor=colours["bg"])
        self._style_map(style, "TScrollbar",
                        background=[("active", colours["button_active"])])
        for option, value in (
                ("*TCombobox*Listbox.background", colours["field"]),
                ("*TCombobox*Listbox.foreground", colours["field_fg"]),
                ("*TCombobox*Listbox.selectBackground", colours["accent"]),
                ("*TCombobox*Listbox.selectForeground",
                 colours["accent_fg"])):
            try:
                self.option_add(option, value)
            except tk.TclError:
                pass

    def _style_menus(self, menu):
        """Recursively repaint tk menus; Windows accepts these options too."""
        colours = self.colors
        try:
            menu.configure(bg=colours["panel"], fg=colours["fg"],
                           activebackground=colours["accent"],
                           activeforeground=colours["accent_fg"],
                           selectcolor=colours["accent"],
                           disabledforeground=colours["muted"], bd=0,
                           relief="flat")
            end = menu.index("end")
        except tk.TclError:
            return
        if end is None:
            return
        for index in range(end + 1):
            try:
                if menu.type(index) == "cascade":
                    sub = menu.nametowidget(menu.entrycget(index, "menu"))
                    self._style_menus(sub)
            except tk.TclError:
                continue

    def _populate_docker_menu(self):
        """Add one View checkbutton per right-side notebook tab."""
        if getattr(self, "notebook", None) is None:
            return
        self.view_menu.add_separator()
        for tab in self.notebook.tabs():
            label = str(self.notebook.tab(tab, "text"))
            key = label.lower()
            visible = key not in DEFAULT_HIDDEN_DOCKERS
            var = tk.BooleanVar(value=visible)
            self.docker_vars[key] = var
            self.docker_tabs[key] = tab
            self.docker_labels[key] = label
            self.view_menu.add_checkbutton(
                label=label, variable=var,
                command=lambda k=key: self.toggle_docker(k))
            if not visible:
                self.set_docker_visible(key, False)
        self._select_first_visible_docker()
        self._style_menus(self.menubar)

    def _select_first_visible_docker(self):
        """Keep the notebook on a tab that is actually shown."""
        for tab in self.notebook.tabs():
            if str(self.notebook.tab(tab, "state")) != "hidden":
                self.notebook.select(tab)
                return

    def set_docker_visible(self, key, visible):
        """Show or hide one right-side docker without reordering the tabs."""
        tab = self.docker_tabs.get(key)
        if tab is None:
            return
        try:
            self.notebook.tab(tab, state="normal" if visible else "hidden")
        except tk.TclError:
            if visible:
                self.notebook.add(tab, text=self.docker_labels.get(key, key))
            else:
                self.notebook.hide(tab)

    def toggle_docker(self, key):
        """Show or hide a right-side docker without reordering the tabs."""
        var = self.docker_vars.get(key)
        if var is None:
            return
        visible = bool(var.get())
        self.set_docker_visible(key, visible)
        if not visible:
            self._select_first_visible_docker()

    def toggle_dark_mode(self):
        self.dark_mode = bool(self.dark_var.get())
        self.colors = THEMES["dark" if self.dark_mode else "light"]
        self.apply_theme()

    def apply_theme(self):
        """Repaint ttk, tk widgets, menus and the start page for the theme."""
        colours = self.colors
        self.configure(bg=colours["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self._style_ttk(style)
        if getattr(self, "menubar", None) is not None:
            self._style_menus(self.menubar)
        if getattr(self, "left_canvas", None) is not None:
            self.left_canvas.configure(bg=colours["bg"],
                                       highlightbackground=colours["border"])
        if getattr(self, "left_rule", None) is not None:
            self.left_rule.configure(bg=colours["border"])
        if getattr(self, "right_card", None) is not None:
            self.right_card.configure(bg=colours["bg"],
                                      highlightbackground=colours["border"])
        if getattr(self, "wheel_card", None) is not None:
            self.wheel_card.configure(bg=colours["card"],
                                      highlightbackground=colours["border"])
        if getattr(self, "status_rule", None) is not None:
            self.status_rule.configure(bg=colours["border"])
        for name in ("fg_swatch", "bg_swatch"):
            swatch = getattr(self, name, None)
            if swatch is not None:
                swatch.configure(highlightbackground=colours["border"])
        if getattr(self, "tool_card", None) is not None:
            self.tool_card.configure(bg=colours["card"],
                                     highlightbackground=colours["border"])
            self.tool_grid.configure(bg=colours["card"])
        if getattr(self, "palette_frame", None) is not None:
            self.palette_frame.configure(bg=colours["card"],
                                         highlightbackground=colours["border"])
            self.palette_canvas.configure(bg=colours["card"])
            self.palette_grid.configure(bg=colours["card"])
        if getattr(self, "palette_count_label", None) is not None:
            self.palette_count_label.configure(foreground=colours["muted"])
        if getattr(self, "wheel", None) is not None:
            self.wheel.configure(bg=colours["card"])
        if getattr(self, "status_bar", None) is not None:
            self.status_bar.configure(bg=colours["status_bg"])
            self.status_label.configure(bg=colours["status_bg"],
                                        fg=colours["status_fg"])
            self.status_hint.configure(bg=colours["status_bg"],
                                       fg=colours["status_hint"])
        if getattr(self, "options_canvas", None) is not None:
            self.options_canvas.configure(bg=colours["bg"])
        if getattr(self, "view", None) is not None:
            self.view.configure(bg=colours["canvas_bg"],
                                highlightbackground=colours["canvas_border"])
        for bar, label in getattr(self, "_heading_widgets", ()):
            bar.configure(bg=colours["accent"])
            label.configure(foreground=colours["heading"])
        for label in getattr(self, "_muted_labels", ()):
            label.configure(foreground=colours["muted"])
        for name in ("block_text", "map_text", "pattern_text", "dump_text"):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                widget.configure(bg=colours["field"], fg=colours["field_fg"],
                                 insertbackground=colours["field_fg"],
                                 selectbackground=colours["accent"],
                                 selectforeground=colours["accent_fg"],
                                 highlightbackground=colours["border"])
            except tk.TclError:
                pass
        if getattr(self, "layer_list", None) is not None:
            try:
                self.layer_list.configure(
                    bg=colours["field"], fg=colours["field_fg"],
                    selectbackground=colours["accent"],
                    selectforeground=colours["accent_fg"],
                    highlightbackground=colours["border"])
            except tk.TclError:
                pass
        if getattr(self, "home", None) is not None:
            self.home.apply_theme()
        if getattr(self, "tool_buttons", None):
            self._reload_tool_icons()
            self.refresh_tool_buttons()

    def _build_menus(self):
        bar = tk.Menu(self, tearoff=0)
        self.menubar = bar

        file_menu = tk.Menu(bar, tearoff=0)
        file_menu.add_command(label="Home", command=self.show_home)
        file_menu.add_separator()
        file_menu.add_command(label="New canvas...", accelerator="Ctrl+N",
                              command=self.new_document)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O",
                              command=self.open_document)
        file_menu.add_command(label="Open project...",
                              command=self.open_project)
        file_menu.add_command(label="Save", accelerator="Ctrl+S",
                              command=self.save_document)
        file_menu.add_command(label="Save As...", command=self.save_document_as)
        file_menu.add_command(label="Save project as...",
                              command=self.save_project_as)
        file_menu.add_separator()
        file_menu.add_command(label="New tab", accelerator="Ctrl+T",
                              command=self.new_document)
        file_menu.add_command(label="Close tab", accelerator="Ctrl+W",
                              command=self.close_tab)
        file_menu.add_separator()
        file_menu.add_command(label="Export As...", accelerator="Ctrl+E",
                              command=self.focus_export)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.request_close)
        bar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(bar, tearoff=0)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z",
                              command=self.undo)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Y",
                              command=self.redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="Copy block text", accelerator="Ctrl+C",
                              command=self.copy_block)
        edit_menu.add_command(label="Paste block text", accelerator="Ctrl+V",
                              command=self.paste_block)
        edit_menu.add_separator()
        edit_menu.add_command(label="Invert colors",
                              command=self.invert_colors_action)
        edit_menu.add_command(label="Clear selection", accelerator="Delete",
                              command=self.clear_selection)
        bar.add_cascade(label="Edit", menu=edit_menu)

        select_menu = tk.Menu(bar, tearoff=0)
        select_menu.add_command(label="Select all", accelerator="Ctrl+A",
                                command=self.select_all)
        select_menu.add_command(label="Deselect", accelerator="Ctrl+D",
                                command=self.deselect)
        select_menu.add_command(label="Invert selection",
                                command=self.invert_selection)
        select_menu.add_separator()
        for name, label, key, tip in TOOLS:
            if name in SELECTION_TOOLS:
                select_menu.add_command(
                    label=label, command=lambda n=name: self.set_tool(n))
        bar.add_cascade(label="Select", menu=select_menu)

        image_menu = tk.Menu(bar, tearoff=0)
        image_menu.add_command(label="Flip horizontal",
                               command=lambda: self.flip_image(True))
        image_menu.add_command(label="Flip vertical",
                               command=lambda: self.flip_image(False))
        image_menu.add_command(label="Rotate 90 clockwise",
                               command=lambda: self.rotate_image(True))
        image_menu.add_command(label="Rotate 90 counter-clockwise",
                               command=lambda: self.rotate_image(False))
        image_menu.add_separator()
        image_menu.add_command(label="Map to palette",
                               command=lambda: self.apply_palette(False))
        image_menu.add_command(label="Map to palette (dithered)",
                               command=lambda: self.apply_palette(True))
        image_menu.add_separator()
        image_menu.add_command(label="Canvas properties...",
                               command=self.canvas_properties)
        image_menu.add_command(label="Scale image...",
                               command=lambda: self.canvas_properties("scale"))
        image_menu.add_command(label="Resize canvas...",
                               command=lambda: self.canvas_properties("canvas"))
        bar.add_cascade(label="Image", menu=image_menu)

        layer_menu = tk.Menu(bar, tearoff=0)
        layer_menu.add_command(label="New layer", accelerator="Ctrl+Shift+N",
                               command=self.new_layer)
        layer_menu.add_command(label="Duplicate layer", accelerator="Alt+C",
                               command=self.duplicate_layer)
        layer_menu.add_command(label="Delete layer", accelerator="Alt+Delete",
                               command=self.delete_layer)
        layer_menu.add_separator()
        layer_menu.add_command(label="Raise layer", accelerator="Alt+Up",
                               command=lambda: self.move_layer(1))
        layer_menu.add_command(label="Lower layer", accelerator="Alt+Down",
                               command=lambda: self.move_layer(-1))
        layer_menu.add_separator()
        for op in LAYER_OPS:
            layer_menu.add_command(
                label="Operation: %s" % LAYER_OP_LABELS[op],
                command=lambda name=op: self.set_layer_op(name))
        bar.add_cascade(label="Layer", menu=layer_menu)

        view_menu = tk.Menu(bar, tearoff=0)
        view_menu.add_radiobutton(label="Pixel view", value="image",
                                  variable=self.view_mode_var,
                                  command=self.set_view_mode)
        view_menu.add_radiobutton(label="Hex code view", value="hex",
                                  variable=self.view_mode_var,
                                  command=self.set_view_mode)
        view_menu.add_separator()
        view_menu.add_command(label="Zoom in", accelerator="]",
                              command=lambda: self.zoom_step(1))
        view_menu.add_command(label="Zoom out", accelerator="[",
                              command=lambda: self.zoom_step(-1))
        view_menu.add_command(label="Zoom to fit", command=self.zoom_fit)
        view_menu.add_separator()
        self.grid_var = tk.BooleanVar(value=self.grid)
        view_menu.add_checkbutton(label="Pixel grid", variable=self.grid_var,
                                  command=self.toggle_grid)
        view_menu.add_separator()
        view_menu.add_command(label="Fullscreen", accelerator="F11",
                              command=self.toggle_fullscreen)
        view_menu.add_separator()
        self.dark_var = tk.BooleanVar(value=self.dark_mode)
        view_menu.add_checkbutton(label="Dark mode", variable=self.dark_var,
                                  command=self.toggle_dark_mode)
        self.view_menu = view_menu
        bar.add_cascade(label="View", menu=view_menu)

        macro_menu = tk.Menu(bar, tearoff=0)
        macro_menu.add_command(label="Record / stop recording",
                               accelerator="F9",
                               command=self.toggle_recording)
        macro_menu.add_command(label="Play recorded keys", accelerator="F5",
                               command=self.play_macro)
        macro_menu.add_command(label="Play repeatedly...",
                               command=self.play_macro_repeated)
        macro_menu.add_separator()
        macro_menu.add_command(label="Save macro...", command=self.save_macro)
        macro_menu.add_command(label="Load macro...", command=self.load_macro)
        macro_menu.add_command(label="Clear recording", command=self.clear_macro)
        bar.add_cascade(label="Macro", menu=macro_menu)

        help_menu = tk.Menu(bar, tearoff=0)
        help_menu.add_command(label="Keyboard reference",
                              command=self.show_help)
        help_menu.add_command(label="Byte selection help",
                              command=self.show_selection_help)
        help_menu.add_separator()
        help_menu.add_command(label="Check for updates...",
                              command=lambda: self.check_for_updates(True))
        help_menu.add_checkbutton(
            label="Check for updates automatically",
            variable=self.update_auto_var,
            command=self._update_auto_changed)
        bar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=bar)

    def _build_left(self, parent):
        holder = ttk.Frame(parent)
        holder.pack(side="left", fill="y")
        self.left_rule = tk.Frame(parent, bg=self.colors["border"], width=1)
        self.left_rule.pack(side="left", fill="y")
        background = (ttk.Style(self).lookup("TFrame", "background")
                      or "#f0f0f0")
        self.left_canvas = tk.Canvas(holder, highlightthickness=0, bd=0,
                                     width=184, height=420, bg=background)
        bar = ttk.Scrollbar(holder, orient="vertical",
                            command=self.left_canvas.yview)
        self.left_canvas.configure(yscrollcommand=bar.set)
        self.left_canvas.pack(side="left", fill="y")
        bar.pack(side="right", fill="y")
        left = ttk.Frame(self.left_canvas, padding=(12, 12, 12, 14))
        self.left_inner = left
        self.left_window = self.left_canvas.create_window((0, 0), window=left,
                                                          anchor="nw")
        left.bind("<Configure>",
                  lambda event: self._update_left_scrollregion())

        self._heading(left, "Tools")
        self.tool_var = tk.StringVar(value=self.tool)
        self.tool_buttons = {}
        self.tool_icons = {}
        self.tool_groups = []
        self._tool_tips = []
        for name, _, _, _ in TOOLS:
            photo = self._load_tool_icon(name)
            if photo is not None:
                self.tool_icons[name] = photo
        card = tk.Frame(left, bg=self.colors["card"], highlightthickness=1,
                        highlightbackground=self.colors["border"], bd=0,
                        padx=4, pady=4)
        card.pack(anchor="w", pady=(2, 0))
        self.tool_card = card
        grid = tk.Frame(card, bg=self.colors["card"])
        grid.pack(anchor="w")
        self.tool_grid = grid
        for index, (group_id, label, names) in enumerate(TOOL_GROUPS):
            group = self._build_tool_group(grid, group_id, label, names)
            group["button"].grid(row=index // 3, column=index % 3,
                                 padx=2, pady=2)
            self.tool_groups.append(group)
            for name in names:
                self.tool_buttons[name] = group["button"]
        hint = ttk.Label(left,
                         text="Hover a tool for its name; Shift+letter "
                              "picks it",
                         foreground=self.colors["muted"], wraplength=190)
        hint.pack(anchor="w", pady=(2, 0))
        self._muted_labels.append(hint)
        self.refresh_tool_buttons()

        ttk.Separator(left).pack(fill="x", pady=6)

        self._heading(left, "Colors")
        swatches = ttk.Frame(left)
        swatches.pack(anchor="w", pady=2)
        self.fg_swatch = tk.Canvas(swatches, width=42, height=42,
                                   highlightthickness=1,
                                   highlightbackground=self.colors["border"])
        self.fg_swatch.pack(side="left")
        self.fg_swatch.bind("<Button-1>", lambda e: self.ask_color("fg"))
        ttk.Label(swatches, text=" ").pack(side="left")
        self.bg_swatch = tk.Canvas(swatches, width=42, height=42,
                                   highlightthickness=1,
                                   highlightbackground=self.colors["border"])
        self.bg_swatch.pack(side="left")
        self.bg_swatch.bind("<Button-1>", lambda e: self.ask_color("bg"))

        self.wheel_card = tk.Frame(left, bg=self.colors["card"],
                                   highlightthickness=1,
                                   highlightbackground=self.colors["border"],
                                   bd=0, padx=4, pady=4)
        self.wheel_card.pack(anchor="w", pady=(4, 2))
        self.wheel = ColorWheel(self.wheel_card, size=140,
                                command=self.wheel_color)
        self.wheel.configure(bg=self.colors["card"])
        self.wheel.pack()

        self.hex_entry = tk.StringVar(value=hex_str(*self.fg))
        entry = ttk.Entry(left, textvariable=self.hex_entry, width=12)
        entry.pack(anchor="w", pady=(4, 2))
        entry.bind("<Return>", lambda e: self.apply_hex_entry())
        ttk.Button(left, text="Set color", command=self.apply_hex_entry).pack(
            fill="x")
        ttk.Button(left, text="Swap FG/BG (space)", command=self.swap_colors).pack(
            fill="x", pady=(2, 0))

        alpha_row = ttk.Frame(left)
        alpha_row.pack(fill="x", pady=(4, 0))
        ttk.Label(alpha_row, text="Alpha").pack(side="left")
        self.alpha_var = tk.IntVar(value=self.fg[3])
        alpha = ttk.Spinbox(alpha_row, from_=0, to=255, width=5,
                            textvariable=self.alpha_var,
                            command=self.apply_alpha)
        alpha.pack(side="right")
        alpha.bind("<Return>", lambda e: self.apply_alpha())

        ttk.Separator(left).pack(fill="x", pady=6)
        brush_row = ttk.Frame(left)
        brush_row.pack(fill="x")
        ttk.Label(brush_row, text="Brush").pack(side="left")
        self.brush_var = tk.IntVar(value=self.brush)
        brush = ttk.Spinbox(brush_row, from_=1, to=64, width=5,
                            textvariable=self.brush_var,
                            command=self.apply_brush)
        brush.pack(side="right")
        brush.bind("<Return>", lambda e: self.apply_brush())

        tip_row = ttk.Frame(left)
        tip_row.pack(fill="x", pady=(2, 0))
        ttk.Label(tip_row, text="Tip").pack(side="left")
        self.brush_shape_var = tk.StringVar(value="Square")
        self.brush_pattern_var = tk.BooleanVar(value=False)
        tip_combo = ttk.Combobox(tip_row, values=BRUSH_TIPS, state="readonly",
                                 width=8, textvariable=self.brush_shape_var)
        tip_combo.pack(side="right")
        tip_combo.bind("<<ComboboxSelected>>",
                       lambda event: self.tip_chosen())

        sym_row = ttk.Frame(left)
        sym_row.pack(fill="x", pady=(2, 0))
        ttk.Label(sym_row, text="Mirror").pack(side="left")
        self.symmetry_var = tk.StringVar(value="")
        for value, label in (("xy", "XY"), ("y", "Y"), ("x", "X"),
                             ("", "Off")):
            ttk.Radiobutton(sym_row, text=label, value=value, width=3,
                            variable=self.symmetry_var,
                            command=self.apply_symmetry).pack(side="right")

        self.shape_filled = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Filled shapes",
                        variable=self.shape_filled).pack(anchor="w")
        self.shape_bg_var = tk.BooleanVar(value=False)

        zrow = ttk.Frame(left)
        zrow.pack(fill="x", pady=(2, 0))
        ttk.Label(zrow, text="Zoom").pack(side="left")
        ttk.Button(zrow, text="+", width=2,
                   command=lambda: self.zoom_step(1)).pack(side="right")
        ttk.Button(zrow, text="\u2212", width=2,
                   command=lambda: self.zoom_step(-1)).pack(side="right",
                                                           padx=2)
        self.zoom_var = tk.StringVar(value=self.zoom_label())
        ttk.Label(zrow, textvariable=self.zoom_var).pack(side="right")
        ttk.Button(left, text="Zoom to fit", command=self.zoom_fit).pack(
            fill="x", pady=(2, 0))

        view_row = ttk.Frame(left)
        view_row.pack(fill="x", pady=(4, 0))
        ttk.Label(view_row, text="View").pack(side="left")
        ttk.Radiobutton(view_row, text="Pixel", value="image",
                        variable=self.view_mode_var,
                        command=self.set_view_mode).pack(side="right")
        ttk.Radiobutton(view_row, text="Hex", value="hex",
                        variable=self.view_mode_var,
                        command=self.set_view_mode).pack(side="right")

        troll = ttk.Frame(left)
        troll.pack(fill="x", pady=(2, 0))
        ttk.Label(troll, text="Tolerance").pack(side="left")
        self.tol_var = tk.IntVar(value=self.tolerance)
        ttk.Spinbox(troll, from_=0, to=255, width=5,
                    textvariable=self.tol_var,
                    command=self.apply_tolerance).pack(side="right")

        ttk.Separator(left).pack(fill="x", pady=6)
        self._heading(left, "Recent")
        self.recent_frame = ttk.Frame(left)
        self.recent_frame.pack(anchor="w", pady=2)

        ttk.Separator(left).pack(fill="x", pady=6)
        self._heading(left, "Palette")
        self.palette_var = tk.StringVar(value="PICO-8")
        palette_combo = ttk.Combobox(left, values=list(PALETTES) + ["Custom"],
                                     state="readonly", width=14,
                                     textvariable=self.palette_var)
        palette_combo.pack(fill="x")
        palette_combo.bind("<<ComboboxSelected>>",
                           lambda event: self.load_preset_palette())
        self.palette_frame = tk.Frame(left, bg=self.colors["card"],
                                      highlightthickness=1,
                                      highlightbackground=self.colors["border"])
        self.palette_frame.pack(anchor="w", pady=(2, 0), fill="x")
        self.palette_canvas = tk.Canvas(self.palette_frame, width=170,
                                        height=92,
                                        bg=self.colors["card"],
                                        highlightthickness=0, bd=0)
        palette_scroll = ttk.Scrollbar(self.palette_frame, orient="vertical",
                                       command=self.palette_canvas.yview)
        self.palette_canvas.configure(yscrollcommand=palette_scroll.set)
        self.palette_canvas.pack(side="left", fill="both", expand=True)
        palette_scroll.pack(side="right", fill="y")
        self.palette_grid = tk.Frame(self.palette_canvas,
                                     bg=self.colors["card"])
        self.palette_canvas.create_window((0, 0), window=self.palette_grid,
                                          anchor="nw")
        self.palette_grid.bind("<Configure>",
                               lambda event: self._update_palette_scroll())
        self.palette_count_var = tk.StringVar(value="")
        self.palette_count_label = ttk.Label(
            left, textvariable=self.palette_count_var,
            foreground=self.colors["muted"])
        self.palette_count_label.pack(anchor="w")
        palette_buttons = ttk.Frame(left)
        palette_buttons.pack(fill="x", pady=(2, 0))
        ttk.Button(palette_buttons, text="New...", width=6,
                   command=self.new_palette).pack(side="left")
        ttk.Button(palette_buttons, text="Set", width=5,
                   command=self.set_palette_color).pack(side="left", padx=2)
        ttk.Button(palette_buttons, text="Del", width=5,
                   command=self.delete_palette_color).pack(side="left")
        palette_buttons2 = ttk.Frame(left)
        palette_buttons2.pack(fill="x", pady=(2, 0))
        ttk.Button(palette_buttons2, text="+ FG", width=6,
                   command=self.add_palette_color).pack(side="left")
        ttk.Button(palette_buttons2, text="Save", width=6,
                   command=self.save_palette).pack(side="left", padx=2)
        ttk.Button(palette_buttons2, text="Load", width=6,
                   command=self.load_palette).pack(side="left")
        palette_buttons3 = ttk.Frame(left)
        palette_buttons3.pack(fill="x", pady=(2, 0))
        ttk.Button(palette_buttons3, text="Map to palette",
                   command=lambda: self.apply_palette(False)).pack(fill="x")
        ttk.Button(palette_buttons3, text="Map + dither",
                   command=lambda: self.apply_palette(True)).pack(
            fill="x", pady=(2, 0))
        self.draw_palette()

        ttk.Separator(left).pack(fill="x", pady=6)
        self._heading(left, "Macro")
        self.macro_info = tk.StringVar(value="no recording")
        ttk.Label(left, textvariable=self.macro_info, foreground="#ffb86b",
                  wraplength=190).pack(anchor="w", pady=(0, 2))
        self.record_button = ttk.Button(left, text="Record keys (F9)",
                                        command=self.toggle_recording)
        self.record_button.pack(fill="x")
        ttk.Button(left, text="Play keys (F5)", command=self.play_macro).pack(
            fill="x", pady=(2, 0))
        self.refresh_macro_status()

        self.sel_info = tk.StringVar(value="no selection")
        ttk.Label(left, textvariable=self.sel_info, wraplength=190,
                  foreground="#8ab4f8").pack(anchor="w", pady=(10, 0))
        sel_buttons = ttk.Frame(left)
        sel_buttons.pack(fill="x", pady=(2, 0))
        ttk.Button(sel_buttons, text="Select all",
                   command=self.select_all).pack(side="left", fill="x",
                                                 expand=True)
        ttk.Button(sel_buttons, text="Deselect",
                   command=self.deselect).pack(side="left", fill="x",
                                               expand=True, padx=(2, 0))
        self._update_left_scrollregion()

    def _heading(self, parent, text):
        """A small accent bar plus a bold heading, used down the sidebar."""
        row = ttk.Frame(parent)
        row.pack(anchor="w", fill="x", pady=(2, 2))
        bar = tk.Frame(row, bg=self.colors["accent"], width=3, height=14)
        bar.pack(side="left")
        label = ttk.Label(row, text=text, font=("Segoe UI", 9, "bold"),
                          foreground=self.colors["heading"])
        label.pack(side="left", padx=(6, 0))
        self._heading_widgets.append((bar, label))
        return row

    def _build_tool_group(self, parent, group_id, label, names):
        """A dropdown folder button; its icon shows the active tool."""
        button = tk.Menubutton(
            parent, image=self.tool_icons.get(names[0]),
            width=icons.BUTTON_SIZE, height=icons.BUTTON_SIZE, bd=1,
            padx=1, pady=1, relief="raised", background="#ffffff",
            activebackground="#eef4fb", highlightthickness=2,
            highlightbackground="#dbe0e8", indicatoron=False,
            compound="right", text="\u25be", foreground="#5b6270",
            font=("Segoe UI", 7), cursor="hand2")
        menu = tk.Menu(button, tearoff=0)
        button.configure(menu=menu)
        group = {"id": group_id, "label": label, "button": button,
                 "menu": menu, "tools": tuple(names), "default": names[0]}
        self._fill_tool_menu(group)
        labels = dict((name, text) for name, text, _, _ in TOOLS)
        self._tool_tips.append(_ToolTip(
            button, "%s tools\n%s" % (label, ", ".join(labels[name]
                                                       for name in names))))
        return group

    def _fill_tool_menu(self, group):
        """(Re)build a folder's menu so it carries the current icons."""
        menu = group["menu"]
        menu.delete(0, "end")
        labels = dict((name, text) for name, text, _, _ in TOOLS)
        keys = dict((name, key) for name, _, key, _ in TOOLS)
        for name in group["tools"]:
            text = labels[name]
            key = keys.get(name, "")
            if key:
                text += "     Shift+" + key.upper()
            menu.add_command(label=text, image=self.tool_icons.get(name),
                             compound="left",
                             command=lambda tool=name: self.set_tool(tool))

    def _load_tool_icon(self, name):
        path = icons.icon_path(name)
        if not path:
            return None
        try:
            with Image.open(path) as image:
                art = image.convert("RGBA").resize(
                    (icons.BUTTON_ICON_SIZE, icons.BUTTON_ICON_SIZE),
                    Image.NEAREST)
        except (OSError, ValueError):
            return None
        return ImageTk.PhotoImage(art)

    def _set_window_icon(self):
        """Put the H2P logo in the title bar and on the taskbar."""
        path = icons.logo_path()
        if not path:
            return
        try:
            with Image.open(path) as image:
                art = image.convert("RGBA")
            self._logo_photo = ImageTk.PhotoImage(art)
            self.iconphoto(True, self._logo_photo)
        except (OSError, ValueError, tk.TclError):
            self._logo_photo = None

    def _reload_tool_icons(self):
        """Rebuild the palette artwork after a theme switch."""
        for name, _, _, _ in TOOLS:
            photo = self._load_tool_icon(name)
            if photo is not None:
                self.tool_icons[name] = photo
        for group in getattr(self, "tool_groups", []):
            self._fill_tool_menu(group)
        self.refresh_tool_buttons()

    def refresh_tool_buttons(self):
        """Show each folder's current tool and highlight the active one."""
        for group in getattr(self, "tool_groups", []):
            active = self.tool in group["tools"]
            name = self.tool if active else group["default"]
            try:
                group["button"].configure(
                    image=self.tool_icons.get(name),
                    relief="sunken" if active else "raised",
                    background="#bcdcff" if active else "#ffffff",
                    activebackground="#a8d2ff" if active else "#eef4fb",
                    highlightbackground=("#2f8fd0" if active
                                         else "#dbe0e8"))
            except tk.TclError:
                return

    def _update_left_scrollregion(self):
        """Keep the tool column a fixed rail, scrolling any extra height."""
        width = 212
        if str(self.left_canvas.cget("width")) != str(width):
            self.left_canvas.configure(width=width)
            self.left_canvas.itemconfigure(self.left_window, width=width)
        box = self.left_canvas.bbox("all")
        if box:
            self.left_canvas.configure(scrollregion=box)

    def _build_right(self, parent):
        right = ttk.Frame(parent, padding=(8, 8))
        right.pack(side="right", fill="y")
        self.right_card = tk.Frame(right, bg=self.colors["bg"],
                                   highlightthickness=1,
                                   highlightbackground=self.colors["border"],
                                   bd=0, padx=6, pady=6)
        self.right_card.pack(fill="both", expand=True)
        self.notebook = ttk.Notebook(self.right_card)
        self.notebook.pack(fill="both", expand=True)

        # ---- block placement
        block = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(block, text="Block")
        ttk.Label(block, text="Hex pixel data (rows, ';' or '|', '.' skips)"
                  ).pack(anchor="w")
        self.block_text = tk.Text(block, width=34, height=8, undo=True,
                                  font=("Consolas", 9))
        self.block_text.pack(fill="x", pady=(2, 4))
        self.block_text.insert("1.0", "FF0000 00FF00 0000FF\n00FFFF . FFFF00")

        opts = ttk.Frame(block)
        opts.pack(fill="x")
        ttk.Label(opts, text="Mode").grid(row=0, column=0, sticky="w")
        self.block_mode = ttk.Combobox(opts, values=("Stamp", "Tile", "Stretch"),
                                       state="readonly", width=9)
        self.block_mode.current(0)
        self.block_mode.grid(row=0, column=1, sticky="w", padx=2)
        ttk.Label(opts, text="Scale").grid(row=0, column=2, sticky="w", padx=(6, 0))
        self.block_scale = tk.IntVar(value=1)
        ttk.Spinbox(opts, from_=1, to=32, width=4,
                    textvariable=self.block_scale).grid(row=0, column=3, padx=2)
        ttk.Label(opts, text="Rotate").grid(row=1, column=0, sticky="w")
        self.block_rotate = ttk.Combobox(opts, values=("0", "90", "180", "270"),
                                         state="readonly", width=5)
        self.block_rotate.current(0)
        self.block_rotate.grid(row=1, column=1, sticky="w", padx=2, pady=2)
        self.flip_x = tk.BooleanVar(value=False)
        self.flip_y = tk.BooleanVar(value=False)
        self.mirror_tiles = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Flip X", variable=self.flip_x).grid(
            row=1, column=2, sticky="w")
        ttk.Checkbutton(opts, text="Flip Y", variable=self.flip_y).grid(
            row=1, column=3, sticky="w")
        ttk.Checkbutton(opts, text="Mirror tiles", variable=self.mirror_tiles
                        ).grid(row=2, column=0, columnspan=2, sticky="w")

        for label, anchor in (("Place at selection", "selection"),
                              ("Place at center", "center"),
                              ("Place at cursor", "cursor")):
            ttk.Button(block, text=label,
                       command=lambda a=anchor: self.place_block(a)).pack(
                fill="x", pady=1)
        ttk.Separator(block).pack(fill="x", pady=4)
        ttk.Button(block, text="Load from selection",
                   command=self.load_block_from_selection).pack(fill="x", pady=1)
        ttk.Button(block, text="Load from file...",
                   command=self.load_block_from_file).pack(fill="x", pady=1)
        ttk.Button(block, text="Copy block to clipboard",
                   command=self.copy_block).pack(fill="x", pady=1)

        # ---- layers
        layers = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(layers, text="Layers")
        ttk.Label(layers, text="Top layer first").pack(anchor="w")
        list_frame = ttk.Frame(layers)
        list_frame.pack(fill="both", expand=True, pady=2)
        self.layer_list = tk.Listbox(list_frame, height=7, exportselection=False,
                                     font=("Consolas", 9), activestyle="none")
        scroll = ttk.Scrollbar(list_frame, orient="vertical",
                               command=self.layer_list.yview)
        self.layer_list.configure(yscrollcommand=scroll.set)
        self.layer_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.layer_list.bind("<<ListboxSelect>>", self.on_layer_select)
        self.layer_list.bind("<Double-Button-1>", self.on_layer_toggle)
        self.layer_list.bind("<ButtonPress-1>", self.begin_layer_drag)
        self.layer_list.bind("<ButtonRelease-1>", self.end_layer_drag)

        op_row = ttk.Frame(layers)
        op_row.pack(fill="x", pady=(2, 0))
        ttk.Label(op_row, text="Operation").pack(side="left")
        self.layer_op_var = tk.StringVar(value=LAYER_OP_LABELS["normal"])
        op_box = ttk.Combobox(op_row, textvariable=self.layer_op_var,
                              values=[LAYER_OP_LABELS[o] for o in LAYER_OPS],
                              state="readonly", width=10)
        op_box.pack(side="right")
        op_box.bind("<<ComboboxSelected>>", self.on_layer_op)

        self.layer_visible_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(layers, text="Visible",
                        variable=self.layer_visible_var,
                        command=self.toggle_layer_visible).pack(anchor="w",
                                                               pady=(2, 0))

        alpha_row = ttk.Frame(layers)
        alpha_row.pack(fill="x", pady=(4, 0))
        ttk.Label(alpha_row, text="Opacity").pack(side="left")
        self.layer_alpha_label = ttk.Label(alpha_row, text="100%")
        self.layer_alpha_label.pack(side="right")
        self.layer_alpha_var = tk.DoubleVar(value=100.0)
        self.layer_alpha_scale = ttk.Scale(layers, from_=0.0, to=100.0,
                                          variable=self.layer_alpha_var,
                                          command=self.on_layer_alpha)
        self.layer_alpha_scale.pack(fill="x")
        self.layer_alpha_scale.bind("<ButtonPress-1>", self.begin_layer_alpha)
        self.layer_alpha_scale.bind("<ButtonRelease-1>", self.end_layer_alpha)

        buttons = ttk.Frame(layers)
        buttons.pack(fill="x", pady=(4, 0))
        ttk.Button(buttons, text="New", width=6,
                   command=self.new_layer).pack(side="left")
        ttk.Button(buttons, text="Copy", width=6,
                   command=self.duplicate_layer).pack(side="left", padx=2)
        ttk.Button(buttons, text="Delete", width=6,
                   command=self.delete_layer).pack(side="left")
        move_row = ttk.Frame(layers)
        move_row.pack(fill="x", pady=(2, 0))
        ttk.Button(move_row, text="Raise",
                   command=lambda: self.move_layer(1)).pack(side="left",
                                                            fill="x",
                                                            expand=True)
        ttk.Button(move_row, text="Lower",
                   command=lambda: self.move_layer(-1)).pack(side="left",
                                                             fill="x",
                                                             expand=True,
                                                             padx=(2, 0))
        self.layer_info = tk.StringVar(value="")
        ttk.Label(layers, textvariable=self.layer_info, foreground="#8ab4f8",
                  wraplength=280).pack(anchor="w", pady=(6, 0))
        ttk.Label(layers,
                  text="A layer combines with the layers below it using its "
                       "operation; Normal simply covers what is underneath. "
                       "Drag a row to restack it, double-click to show or "
                       "hide. Alt+N/C/Delete adds, copies or removes a layer "
                       "and Alt+Up/Down restacks it. Both views show the "
                       "result; the dump shows the active layer.",
                  foreground="#777", wraplength=280,
                  justify="left").pack(anchor="w", pady=(2, 0))
        self.refresh_layers()

        # ---- byte operations
        ops = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(ops, text="Bytes")
        ttk.Label(ops, text="Operand hex").pack(anchor="w")
        self.operand = tk.StringVar(value="FF00FF")
        ttk.Entry(ops, textvariable=self.operand, width=14).pack(anchor="w")
        self.op_var = tk.StringVar(value=OP_LABELS["add"])
        ttk.Combobox(ops, values=[OP_LABELS[o] for o in OPS],
                     textvariable=self.op_var, state="readonly",
                     width=12).pack(anchor="w", pady=2)

        strength_row = ttk.Frame(ops)
        strength_row.pack(fill="x", pady=(2, 0))
        ttk.Label(strength_row, text="Strength").pack(side="left")
        self.strength_label = ttk.Label(strength_row, text="255")
        self.strength_label.pack(side="right")
        self.strength = tk.IntVar(value=255)
        scale = ttk.Scale(ops, from_=0, to=255, variable=self.strength,
                          command=lambda v: self.strength_label.config(
                              text=str(int(float(v)))))
        scale.pack(fill="x")

        self.wrap = tk.BooleanVar(value=False)
        self.alpha_ops = tk.BooleanVar(value=False)
        ttk.Checkbutton(ops, text="Wrap overflow (else clamp)",
                        variable=self.wrap).pack(anchor="w")
        ttk.Checkbutton(ops, text="Include alpha channel",
                        variable=self.alpha_ops).pack(anchor="w")
        ttk.Button(ops, text="Apply to selection",
                   command=self.apply_operation).pack(fill="x", pady=(4, 2))
        ttk.Separator(ops).pack(fill="x", pady=4)

        ttk.Label(ops, text="Recolor map (OLD=NEW per line)").pack(anchor="w")
        self.map_text = tk.Text(ops, width=32, height=4, font=("Consolas", 9))
        self.map_text.pack(fill="x")
        self.map_text.insert("1.0", "FF0000=00FF00\n0000FF=FF00FF")
        ttk.Button(ops, text="Apply map", command=self.apply_map).pack(
            fill="x", pady=1)
        hue_row = ttk.Frame(ops)
        hue_row.pack(fill="x", pady=(4, 0))
        ttk.Label(hue_row, text="Hue shift").pack(side="left")
        self.hue_var = tk.IntVar(value=30)
        ttk.Spinbox(hue_row, from_=-180, to=180, width=5,
                    textvariable=self.hue_var).pack(side="right")
        ttk.Button(ops, text="Shift hue of selection",
                   command=self.apply_hue_action).pack(fill="x", pady=1)
        ramp_row = ttk.Frame(ops)
        ramp_row.pack(fill="x", pady=(2, 0))
        ttk.Label(ramp_row, text="Ramp rotate").pack(side="left")
        self.ramp_var = tk.IntVar(value=1)
        ttk.Spinbox(ramp_row, from_=-16, to=16, width=5,
                    textvariable=self.ramp_var).pack(side="right")
        ttk.Button(ramp_row, text="Rotate palette",
                   command=self.apply_ramp_action).pack(fill="x", pady=1)

        # ---- byte dump
        dump = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(dump, text="Dump")
        self.dump_info = tk.StringVar(value="")
        ttk.Label(dump, textvariable=self.dump_info, wraplength=280,
                  foreground="#8ab4f8").pack(anchor="w")
        dump_row = ttk.Frame(dump)
        dump_row.pack(fill="both", expand=True, pady=2)
        self.dump_text = tk.Text(dump_row, width=36, height=18, wrap="none",
                                 font=("Consolas", 8), state="disabled")
        scroll = ttk.Scrollbar(dump_row, orient="vertical",
                               command=self.dump_text.yview)
        self.dump_text.configure(yscrollcommand=scroll.set)
        self.dump_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Button(dump, text="Refresh", command=self.refresh_bytes).pack(
            fill="x", pady=1)
        ttk.Button(dump, text="Copy dump", command=self.copy_dump).pack(fill="x")
        ttk.Button(dump, text="Save bytes as hex text...",
                   command=self.save_bytes).pack(fill="x", pady=(1, 0))

        # ---- export
        export = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(export, text="Export")
        self.export_tab = export
        ttk.Label(export, text="Save project").pack(anchor="w")
        self.project_path_var = tk.StringVar(value="not saved yet")
        ttk.Label(export, textvariable=self.project_path_var, wraplength=266,
                  foreground="#7ddc7d").pack(anchor="w")
        project_row = ttk.Frame(export)
        project_row.pack(fill="x", pady=(2, 4))
        ttk.Button(project_row, text="Save", command=self.save_document).pack(
            side="left", expand=True, fill="x")
        ttk.Button(project_row, text="Save as...",
                   command=self.save_document_as).pack(
            side="left", expand=True, fill="x")
        ttk.Separator(export).pack(fill="x", pady=4)

        ttk.Label(export, text="Image format").pack(anchor="w")
        self.format_var = tk.StringVar(value=formats.FORMAT_LABELS[0])
        format_box = ttk.Combobox(export, values=formats.FORMAT_LABELS,
                                  textvariable=self.format_var,
                                  state="readonly")
        format_box.pack(fill="x", pady=(0, 4))
        format_box.bind("<<ComboboxSelected>>", self._format_changed)

        ttk.Label(export, text="Source").pack(anchor="w")
        self.export_source = tk.StringVar(value=EXPORT_SOURCES[0])
        source_box = ttk.Combobox(export, values=EXPORT_SOURCES,
                                  textvariable=self.export_source,
                                  state="readonly")
        source_box.pack(fill="x", pady=(0, 4))
        source_box.bind("<<ComboboxSelected>>", self._update_export_hint)

        self.export_trim = tk.BooleanVar(value=False)
        ttk.Checkbutton(export, text="Trim transparent edges",
                        variable=self.export_trim,
                        command=self._update_export_hint).pack(anchor="w")

        scale_row = ttk.Frame(export)
        scale_row.pack(fill="x", pady=2)
        ttk.Label(scale_row, text="Scale").pack(side="left")
        self.export_scale = tk.IntVar(value=1)
        scale_spin = ttk.Spinbox(scale_row, from_=1, to=32, width=4,
                                 textvariable=self.export_scale,
                                 command=self._update_export_hint)
        scale_spin.pack(side="left", padx=(4, 2))
        scale_spin.bind("<KeyRelease>", self._update_export_hint)
        ttk.Label(scale_row, text="x").pack(side="left")
        self.export_resample = tk.StringVar(value="Nearest")
        ttk.Combobox(scale_row, values=("Nearest", "Bilinear"), width=9,
                     state="readonly",
                     textvariable=self.export_resample).pack(side="right")

        ppi_row = ttk.Frame(export)
        ppi_row.pack(fill="x", pady=2)
        ttk.Label(ppi_row, text="Resolution").pack(side="left")
        self.export_ppi = tk.IntVar(value=72)
        ttk.Spinbox(ppi_row, from_=1, to=2400, width=6,
                    textvariable=self.export_ppi).pack(side="right")
        ttk.Label(ppi_row, text="PPI").pack(side="right", padx=2)

        alpha_row = ttk.Frame(export)
        alpha_row.pack(fill="x", pady=2)
        self.export_alpha = tk.BooleanVar(value=True)
        self.alpha_check = ttk.Checkbutton(
            alpha_row, text="Keep transparency", variable=self.export_alpha,
            command=self._update_export_options)
        self.alpha_check.pack(side="left")
        self.matte_var = tk.StringVar(value="FFFFFF")
        self.matte_entry = ttk.Entry(alpha_row, textvariable=self.matte_var,
                                     width=8)
        self.matte_entry.pack(side="right")
        ttk.Label(alpha_row, text="Matte").pack(side="right", padx=2)

        quality_row = ttk.Frame(export)
        quality_row.pack(fill="x", pady=(4, 0))
        ttk.Label(quality_row, text="JPEG quality").pack(side="left")
        self.quality_var = tk.IntVar(value=92)
        self.quality_spin = ttk.Spinbox(quality_row, from_=1, to=100, width=5,
                                        textvariable=self.quality_var)
        self.quality_spin.pack(side="right")

        gif_row = ttk.Frame(export)
        gif_row.pack(fill="x", pady=2)
        ttk.Label(gif_row, text="GIF colours").pack(side="left")
        self.gif_colors_var = tk.IntVar(value=256)
        self.gif_spin = ttk.Spinbox(gif_row, from_=2, to=256, increment=2,
                                   width=5,
                                   textvariable=self.gif_colors_var)
        self.gif_spin.pack(side="right")

        png_row = ttk.Frame(export)
        png_row.pack(fill="x", pady=2)
        ttk.Label(png_row, text="PNG level").pack(side="left")
        self.png_compress_var = tk.IntVar(value=6)
        self.png_spin = ttk.Spinbox(png_row, from_=0, to=9, width=5,
                                    textvariable=self.png_compress_var)
        self.png_spin.pack(side="right")

        ttk.Label(export, text="Path").pack(anchor="w", pady=(4, 0))
        self.export_path = tk.StringVar(value="")
        ttk.Entry(export, textvariable=self.export_path).pack(fill="x")
        ttk.Button(export, text="Browse...", command=self.browse_export).pack(
            fill="x", pady=1)
        ttk.Button(export, text="Export image", command=self.export_now).pack(
            fill="x", pady=(4, 2))
        self.export_hint = tk.StringVar(value="")
        ttk.Label(export, textvariable=self.export_hint, wraplength=270,
                  foreground="#9fb3c8").pack(anchor="w")
        self.export_info = tk.StringVar(value="")
        ttk.Label(export, textvariable=self.export_info, wraplength=280,
                  foreground="#7ddc7d").pack(anchor="w")
        self._update_export_options()

        # ---- pattern fill
        pattern = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(pattern, text="Pattern")
        self.pattern_mode = tk.StringVar(value="line")
        ttk.Radiobutton(pattern, text="Line by line", value="line",
                        variable=self.pattern_mode).pack(anchor="w")
        ttk.Radiobutton(pattern, text="Structural (mirrored tiles)",
                        value="structure",
                        variable=self.pattern_mode).pack(anchor="w")

        ttk.Label(pattern, text="Pattern pixels (click paints, right-click skips)"
                  ).pack(anchor="w", pady=(6, 2))
        self.pattern_view = tk.Canvas(
            pattern, width=self.pattern_size * self.pattern_cell_px,
            height=self.pattern_size * self.pattern_cell_px,
            bg="#232326", highlightthickness=1, highlightbackground="#555")
        self.pattern_view.pack(anchor="w")
        self.pattern_view.bind("<Button-1>", self.on_pattern_click)
        self.pattern_view.bind(
            "<Button-3>", lambda event: self.on_pattern_click(event, True))

        size_row = ttk.Frame(pattern)
        size_row.pack(fill="x", pady=(4, 0))
        ttk.Label(size_row, text="Grid").pack(side="left")
        self.pattern_size_var = tk.IntVar(value=self.pattern_size)
        spin = ttk.Spinbox(size_row, from_=1, to=16, width=4,
                           textvariable=self.pattern_size_var,
                           command=self.apply_pattern_size)
        spin.pack(side="right")
        spin.bind("<Return>", lambda event: self.apply_pattern_size())

        ttk.Label(pattern, text="Hex rows ('.' skips)").pack(anchor="w",
                                                             pady=(6, 0))
        self.pattern_text = tk.Text(pattern, width=32, height=3,
                                    font=("Consolas", 9), undo=True)
        self.pattern_text.pack(fill="x")
        text_row = ttk.Frame(pattern)
        text_row.pack(fill="x", pady=1)
        ttk.Button(text_row, text="Text -> grid",
                   command=self.pattern_grid_from_text).pack(side="left")
        ttk.Button(text_row, text="Grid -> text",
                   command=self.pattern_text_from_grid).pack(side="left",
                                                             padx=4)

        opt = ttk.Frame(pattern)
        opt.pack(fill="x", pady=(6, 0))
        ttk.Label(opt, text="Scale").grid(row=0, column=0, sticky="w")
        self.pattern_scale = tk.IntVar(value=1)
        ttk.Spinbox(opt, from_=1, to=16, width=4,
                    textvariable=self.pattern_scale).grid(row=0, column=1)
        ttk.Label(opt, text="Shift").grid(row=0, column=2, sticky="w",
                                           padx=(6, 0))
        self.pattern_shift = tk.IntVar(value=0)
        ttk.Spinbox(opt, from_=-16, to=16, width=4,
                    textvariable=self.pattern_shift).grid(row=0, column=3)
        ttk.Label(opt, text="Super").grid(row=1, column=0, sticky="w", pady=2)
        self.pattern_sw = tk.IntVar(value=2)
        ttk.Spinbox(opt, from_=1, to=8, width=4,
                    textvariable=self.pattern_sw).grid(row=1, column=1)
        self.pattern_sh = tk.IntVar(value=2)
        ttk.Spinbox(opt, from_=1, to=8, width=4,
                    textvariable=self.pattern_sh).grid(row=1, column=3)
        self.pattern_mirror = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Mirror tiles",
                        variable=self.pattern_mirror).grid(
            row=2, column=0, columnspan=4, sticky="w")

        ttk.Button(pattern, text="Fill selection (or canvas)",
                   command=self.apply_pattern_fill).pack(fill="x", pady=(6, 1))
        ttk.Button(pattern, text="Pattern from selection",
                   command=self.pattern_from_selection).pack(fill="x", pady=1)
        ttk.Button(pattern, text="Clear pattern",
                   command=self.clear_pattern).pack(fill="x", pady=1)
        self.pattern_info = tk.StringVar(value="")
        ttk.Label(pattern, textvariable=self.pattern_info,
                  foreground="#7ddc7d", wraplength=280).pack(anchor="w",
                                                             pady=(4, 0))
        self.draw_pattern_grid()
        self._build_dither()

    def _build_dither(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Dither")
        self.dither_mode_var = tk.StringVar(value="Bayer 4x4")
        self.dither_bias_var = tk.IntVar(value=0)
        self.dither_strength_var = tk.IntVar(value=100)
        self.dither_scale_var = tk.IntVar(value=1)
        self.dither_serpentine_var = tk.BooleanVar(value=False)

        block = ttk.LabelFrame(tab, text="Dither to FG and BG", padding=6)
        block.pack(fill="x")
        block.columnconfigure(1, weight=1)
        ttk.Label(block, text="Mode").grid(row=0, column=0, sticky="w")
        ttk.Combobox(block, values=tuple(dither.DITHER_LABELS),
                     state="readonly",
                     textvariable=self.dither_mode_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=(0, 4))
        self._dither_spin(block, 1, "Bias", "%", self.dither_bias_var, -100,
                          100)
        self._dither_spin(block, 2, "Strength", "%",
                          self.dither_strength_var, 0, 100)
        self._dither_spin(block, 3, "Scale", "x", self.dither_scale_var, 1,
                          16)
        ttk.Checkbutton(block, text="Serpentine scan (Floyd-Steinberg)",
                        variable=self.dither_serpentine_var).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(2, 0))
        ttk.Button(block, text="Apply dither",
                   command=self.apply_dither).grid(
            row=5, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(block, text="FG replaces light pixels, BG dark ones.\n"
                  "Scale spaces out Pattern and Lines; the Gradient tool "
                  "uses these settings too.", foreground="#8ab7f8",
                  wraplength=260).grid(row=6, column=0, columnspan=4,
                                       sticky="w", pady=(4, 0))
        self._build_filters(tab)

    def _dither_spin(self, parent, row, label, unit, var, low, high):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w",
                                           pady=1)
        ttk.Spinbox(parent, from_=low, to=high, width=6,
                    textvariable=var).grid(row=row, column=1, sticky="w")
        ttk.Label(parent, text=unit).grid(row=row, column=2, sticky="w",
                                          padx=(2, 0))

    def _build_filters(self, tab):
        block = ttk.LabelFrame(tab, text="Filters", padding=6)
        block.pack(fill="x", pady=(8, 0))
        block.columnconfigure(1, weight=1)
        ttk.Button(block, text="Grayscale",
                   command=self.apply_grayscale).grid(
            row=0, column=0, columnspan=4, sticky="ew", pady=1)
        self.posterize_var = tk.IntVar(value=4)
        row = self._filter_row(block, 1, "Posterize", "levels",
                               self.posterize_var, 2, 32,
                               self.apply_posterize)
        self.filter_threshold_var = tk.IntVar(value=128)
        row = self._filter_row(block, row, "Threshold", "level",
                               self.filter_threshold_var, 0, 255,
                               self.apply_threshold)
        self.filter_brightness_var = tk.IntVar(value=25)
        row = self._filter_row(block, row, "Brightness", "%",
                               self.filter_brightness_var, -100, 100,
                               self.apply_brightness)
        self.filter_contrast_var = tk.IntVar(value=25)
        row = self._filter_row(block, row, "Contrast", "%",
                               self.filter_contrast_var, -100, 100,
                               self.apply_contrast)
        self.filter_saturation_var = tk.IntVar(value=30)
        row = self._filter_row(block, row, "Saturation", "%",
                               self.filter_saturation_var, -100, 100,
                               self.apply_saturation)
        self.filter_hue_var = tk.IntVar(value=30)
        row = self._filter_row(block, row, "Hue shift", "deg",
                               self.filter_hue_var, -180, 180,
                               self.apply_hue)
        self.filter_noise_var = tk.IntVar(value=12)
        row = self._filter_row(block, row, "Noise", "%",
                               self.filter_noise_var, 0, 100,
                               self.apply_noise)
        pair = ttk.Frame(block)
        pair.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(3, 0))
        pair.columnconfigure(0, weight=1)
        pair.columnconfigure(1, weight=1)
        ttk.Button(pair, text="Sepia", command=self.apply_sepia).grid(
            row=0, column=0, sticky="ew", padx=(0, 2))
        ttk.Button(pair, text="Find edges", command=self.apply_edges).grid(
            row=0, column=1, sticky="ew")

    def _filter_row(self, parent, row, label, unit, var, low, high, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        ttk.Spinbox(parent, from_=low, to=high, width=6,
                    textvariable=var).grid(row=row, column=1, sticky="w")
        ttk.Label(parent, text=unit).grid(row=row, column=2, sticky="w",
                                          padx=(2, 0))
        ttk.Button(parent, text="Apply", width=6,
                   command=command).grid(row=row, column=3, sticky="e",
                                         padx=(6, 0))
        return row + 1

    def _build_canvas(self, parent):
        frame = ttk.Frame(parent, padding=(8, 8))
        frame.pack(side="left", fill="both", expand=True)
        frame.rowconfigure(3, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tab_bar = ttk.Frame(frame)
        self.tab_bar.grid(row=0, column=0, columnspan=2, sticky="ew",
                          pady=(0, 4))
        self.options_bar = ttk.Frame(frame)
        self.options_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.options_canvas = tk.Canvas(self.options_bar, height=48,
                                        bg=self.colors["bg"],
                                        highlightthickness=0, bd=0)
        self.options_canvas.pack(side="top", fill="x", expand=True)
        self.tool_options = ttk.Frame(self.options_canvas, padding=(10, 8))
        self.options_window = self.options_canvas.create_window(
            (0, 0), window=self.tool_options, anchor="nw")
        self.options_scroll = ttk.Scrollbar(
            self.options_bar, orient="horizontal",
            command=self.options_canvas.xview)
        self.options_canvas.configure(xscrollcommand=self.options_scroll.set)
        self.options_canvas.bind("<Configure>",
                                 lambda event: self._sync_options_scroll())
        self.tool_options.bind("<Configure>",
                               lambda event: self._sync_options_scroll())
        self.options_rule = ttk.Separator(frame, orient="horizontal")
        self.options_rule.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.sel_mode_var = tk.StringVar(
            value=SEL_MODE_LABELS.get(self.sel_mode, "Replace"))
        self.pick_target_var = tk.StringVar(value="FG")
        self.ignore_alpha_var = tk.BooleanVar(value=False)
        self.lasso_smooth_var = tk.BooleanVar(value=True)
        self.gradient_reverse_var = tk.BooleanVar(value=False)
        self.gradient_dither_var = tk.StringVar(value="Off")
        self.gradient_radial_var = tk.BooleanVar(value=False)
        self.gradient_palette_var = tk.BooleanVar(value=False)
        self.gradient_spread_var = tk.StringVar(value="Clamp")
        self.symmetry_option_var = tk.StringVar(
            value=SYMMETRY_LABELS.get(self.symmetry, "Off"))
        self.rotate_snap = int(ROTATE_SNAP)
        self.rotate_snap_var = tk.IntVar(value=int(ROTATE_SNAP))
        self.blur_size_var = tk.IntVar(value=4)
        self.blur_strength_var = tk.IntVar(value=60)
        self.blur_smear_var = tk.IntVar(value=0)
        self.shuffle_size_var = tk.IntVar(value=4)
        self.shuffle_mode_var = tk.StringVar(value="Shuffle")
        self.shuffle_amount_var = tk.IntVar(value=40)
        self.shuffle_blur_var = tk.IntVar(value=0)
        self.text_value = tk.StringVar(value="HEX")
        self.text_scale_var = tk.IntVar(value=2)
        self.text_spacing_var = tk.IntVar(value=1)
        self.spray_size_var = tk.IntVar(value=8)
        self.spray_flow_var = tk.IntVar(value=25)
        self.curve_bend_var = tk.IntVar(value=30)
        self.curve_vectors_var = tk.IntVar(value=1)
        self.curve_direction_var = tk.StringVar(value="Left")
        self.contour_width_var = tk.IntVar(value=1)
        self.contour_side_var = tk.StringVar(value="Outside")
        self.contour_connect_var = tk.StringVar(value="4")
        self.spray_blur_var = tk.DoubleVar(value=0.0)
        self.spray_blur_text = tk.StringVar(value="0%")
        self.spray_multi_var = tk.BooleanVar(value=False)
        self._spray_job = None
        self.blur_source = None
        self.tool_option_widgets = {}
        self._refresh_tool_options()
        self.view = tk.Canvas(frame, bg="#20242b", highlightthickness=1,
                              highlightbackground="#39404d", bd=0)
        vbar = ttk.Scrollbar(frame, orient="vertical", command=self.scroll_y)
        hbar = ttk.Scrollbar(frame, orient="horizontal", command=self.scroll_x)
        self.view.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.view.grid(row=3, column=0, sticky="nsew")
        vbar.grid(row=3, column=1, sticky="ns")
        hbar.grid(row=4, column=0, sticky="ew")
        self.view_image = self.view.create_image(0, 0, anchor="nw")

        self.view.bind("<Button-1>", lambda e: self.on_press(e, False))
        self.view.bind("<Button-3>", lambda e: self.on_press(e, True))
        self.view.bind("<B1-Motion>", self.on_drag)
        self.view.bind("<B3-Motion>", self.on_drag)
        self.view.bind("<ButtonRelease-1>", self.on_release)
        self.view.bind("<ButtonRelease-3>", self.on_release)
        self.view.bind("<Double-Button-1>", self.on_double)
        self.view.bind("<Motion>", self.on_motion)
        self.view.bind("<MouseWheel>", self.on_wheel)
        self.view.bind("<Button-2>", self.on_middle)
        self.view.bind("<B2-Motion>", self.on_middle_drag)
        self.view.bind("<ButtonRelease-2>", self.on_middle_release)
        self.view.bind("<Double-Button-2>", self.on_middle_double)
        self.view.bind("<Configure>", lambda event: self._on_view_configure())
        self.refresh_tabs()

    # ------------------------------------------------------------ tool options
    def _option_label(self, text, padx=(10, 2)):
        label = ttk.Label(self.tool_options, text=text)
        label.pack(side="left", padx=padx)
        return label

    def _option_check(self, text, var, command=None, padx=(10, 0)):
        check = ttk.Checkbutton(self.tool_options, text=text, variable=var,
                                command=command)
        check.pack(side="left", padx=padx)
        return check

    def _option_spin(self, var, low, high, width=4, command=None):
        spin = ttk.Spinbox(self.tool_options, from_=low, to=high, width=width,
                           textvariable=var, command=command)
        spin.pack(side="left")
        if command is not None:
            spin.bind("<Return>", lambda event: command())
        return spin

    def _option_combo(self, var, values, width=9, command=None):
        combo = ttk.Combobox(self.tool_options, values=values,
                             state="readonly", width=width,
                             textvariable=var)
        combo.pack(side="left")
        if command is not None:
            combo.bind("<<ComboboxSelected>>", lambda event: command())
        return combo

    def _option_entry(self, var, width=14):
        entry = ttk.Entry(self.tool_options, textvariable=var, width=width)
        entry.pack(side="left")
        return entry

    def _option_button(self, text, command, width=None):
        button = ttk.Button(self.tool_options, text=text, command=command,
                            width=width)
        button.pack(side="left", padx=(4, 0))
        return button

    def _refresh_tool_options(self):
        """Give the bar the options that belong to the active tool."""
        if not hasattr(self, "tool_options"):
            return
        for child in self.tool_options.winfo_children():
            child.destroy()
        ttk.Label(self.tool_options, text="Tool options",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        labels = dict((name, label) for name, label, _, _ in TOOLS)
        name = labels.get(self.tool, self.tool)
        tk.Label(self.tool_options, text=name, bg="#3daee9", fg="#0e1116",
                 font=("Segoe UI", 8, "bold"), padx=6, pady=1).pack(
            side="left", padx=(6, 2))
        builder = getattr(self, "_options_" + self.tool, None)
        if builder is not None:
            builder()
        self.tool_option_widgets = {
            "tool": self.tool,
            "name": name,
            "children": tuple(self.tool_options.winfo_children()),
        }
        self.options_canvas.xview_moveto(0)
        self._options_reset = True
        self.after_idle(self._sync_options_scroll)

    def _sync_options_scroll(self):
        """Show a horizontal scrollbar only when the option row overflows."""
        if not hasattr(self, "options_scroll"):
            return
        try:
            content = max(1, self.tool_options.winfo_reqwidth())
            view = max(1, self.options_canvas.winfo_width())
            height = max(1, self.tool_options.winfo_reqheight())
        except tk.TclError:
            return
        if content > view + 1:
            if not self.options_scroll.winfo_ismapped():
                self.options_scroll.pack(side="bottom", fill="x")
            self.options_canvas.configure(scrollregion=(0, 0, content, height))
        else:
            if self.options_scroll.winfo_ismapped():
                self.options_scroll.pack_forget()
            self.options_canvas.configure(scrollregion=(0, 0, view, height))
        if getattr(self, "_options_reset", False):
            self.options_canvas.xview_moveto(0)
            self._options_reset = False

    def _options_brush(self):
        """Size, tip and mirror: shared by the freehand painting tools."""
        self._option_label("Size")
        self._option_spin(self.brush_var, 1, 64, command=self.apply_brush)
        self._option_label("Tip")
        self._option_combo(self.brush_shape_var, BRUSH_TIPS, 8,
                           command=self.tip_chosen)
        self._option_button("Edit…", self.edit_brush_shape, width=5)
        self._option_label("Mirror")
        self.symmetry_option_var.set(SYMMETRY_LABELS.get(self.symmetry,
                                                         "Off"))
        self._option_combo(self.symmetry_option_var,
                           ("Off", "X", "Y", "XY"), 5,
                           command=self.apply_symmetry_option)

    def _options_pencil(self):
        self._options_brush()
        self._option_check("Pattern tip", self.brush_pattern_var)

    def _options_eraser(self):
        self._options_brush()

    def _options_picker(self):
        self._option_label("Pick into")
        self._option_combo(self.pick_target_var, ("FG", "BG"), 4)

    def _options_fill(self):
        self._option_label("Tolerance")
        self._option_spin(self.tol_var, 0, 255, command=self.apply_tolerance)

    def _options_line(self):
        self._option_label("Width")
        self._option_spin(self.brush_var, 1, 64, command=self.apply_brush)
        self._option_label("Tip")
        self._option_combo(self.brush_shape_var, BRUSH_TIPS, 8,
                           command=self.tip_chosen)
        self._option_check("Background", self.shape_bg_var)

    def _options_shape(self):
        self._option_check("Fill", self.shape_filled)
        self._option_check("Background", self.shape_bg_var)
        self._option_label("Width")
        self._option_spin(self.brush_var, 1, 64, command=self.apply_brush)
        self._option_label("Tip")
        self._option_combo(self.brush_shape_var, BRUSH_TIPS, 8,
                           command=self.tip_chosen)

    def _options_shape_rect(self):
        self._options_shape()

    def _options_shape_ellipse(self):
        self._options_shape()

    def _options_shape_triangle(self):
        self._options_shape()

    def _options_shape_diamond(self):
        self._options_shape()

    def _options_shape_star(self):
        self._options_shape()

    def _options_shape_arrow(self):
        self._options_shape()

    def _options_gradient(self):
        self._option_check("Radial", self.gradient_radial_var,
                           command=self._sync_gradient_options)
        self._option_check("Whole palette", self.gradient_palette_var,
                           command=self._sync_gradient_options)
        self._option_check("Reverse (BG to FG)", self.gradient_reverse_var)
        self._option_label("Spread")
        self._option_combo(self.gradient_spread_var,
                           ("Clamp", "Repeat", "Reflect"), 8,
                           command=self._sync_gradient_options)
        self._option_label("Dither")
        self.gradient_dither_combo = self._option_combo(
            self.gradient_dither_var, tuple(dither.DITHER_LABELS), 15)
        self._option_label("Dither Bias / Strength / Scale: Dither tab",
                           (10, 2))
        self._sync_gradient_options()

    def _sync_gradient_options(self):
        """Dithering is defined over one axis, so smooth ramps disable it."""
        combo = getattr(self, "gradient_dither_combo", None)
        if combo is None:
            return
        smooth = (self.gradient_palette_var.get()
                  or self.gradient_radial_var.get()
                  or self.gradient_spread_var.get() != "Clamp")
        combo.configure(state="disabled" if smooth else "readonly")

    def _options_pattern(self):
        self._option_label("Grid")
        self._option_spin(self.pattern_size_var, 1, 16,
                          command=self.apply_pattern_size)
        self._option_label("Scale")
        self._option_spin(self.pattern_scale, 1, 16)
        self._option_label("Shift")
        self._option_spin(self.pattern_shift, -16, 16)

    def _options_structure(self):
        self._option_label("Grid")
        self._option_spin(self.pattern_size_var, 1, 16,
                          command=self.apply_pattern_size)
        self._option_check("Mirror tiles", self.pattern_mirror)

    def _options_blur(self):
        self._option_label("Size")
        self._option_spin(self.blur_size_var, 1, 64)
        self._option_label("Tip")
        self._option_combo(self.brush_shape_var, BRUSH_TIPS, 8,
                           command=self.tip_chosen)
        self._option_label("Strength")
        self._option_spin(self.blur_strength_var, 0, 100)
        self._option_label("%", (2, 2))
        self._option_label("Smear")
        self._option_spin(self.blur_smear_var, 0, 100)
        self._option_label("%", (2, 2))
        self._option_label("Mirror")
        self._option_combo(self.symmetry_option_var,
                           ("Off", "X", "Y", "XY"), 5,
                           command=self.apply_symmetry_option)

    def _options_shuffle(self):
        self._option_label("Size")
        self._option_spin(self.shuffle_size_var, 1, 64)
        self._option_label("Tip")
        self._option_combo(self.brush_shape_var, BRUSH_TIPS, 8,
                           command=self.tip_chosen)
        self._option_label("Mode")
        self._option_combo(self.shuffle_mode_var,
                           ("Shuffle", "Scatter", "Noise"), 8)
        self._option_label("Amount")
        self._option_spin(self.shuffle_amount_var, 0, 100)
        self._option_label("%", (2, 2))
        self._option_label("Blur")
        self._option_spin(self.shuffle_blur_var, 0, 100)
        self._option_label("%", (2, 2))

    def _options_text(self):
        self._option_label("Text")
        self._option_entry(self.text_value, 16)
        self._option_label("Scale")
        self._option_spin(self.text_scale_var, 1, 8, width=3)
        self._option_label("Spacing")
        self._option_spin(self.text_spacing_var, 0, 8, width=3)
        self._option_label("Click to stamp in the foreground color", (16, 2))

    def _options_spray(self):
        self._option_label("Size")
        self._option_spin(self.spray_size_var, 1, 64)
        self._option_label("Flow")
        self._option_spin(self.spray_flow_var, 1, 100)
        self._option_label("%", (2, 2))
        self._option_label("Tip")
        self._option_combo(self.brush_shape_var, BRUSH_TIPS, 8,
                           command=self.tip_chosen)
        self._option_label("Blur")
        ttk.Scale(self.tool_options, from_=0, to=100, length=90,
                  variable=self.spray_blur_var,
                  command=self.spray_blur_changed).pack(side="left")
        ttk.Label(self.tool_options, textvariable=self.spray_blur_text,
                  width=4).pack(side="left", padx=(2, 0))
        self._option_check("Multicolor", self.spray_multi_var)

    def _options_curve(self):
        self._option_label("Width")
        self._option_spin(self.brush_var, 1, 64, command=self.apply_brush)
        self._option_label("Tip")
        self._option_combo(self.brush_shape_var, BRUSH_TIPS, 8,
                           command=self.tip_chosen)
        self._option_label("Bend")
        self._option_spin(self.curve_bend_var, -100, 100, width=4)
        self._option_label("Vectors")
        self._option_spin(self.curve_vectors_var, 1, 8, width=3)
        self._option_label("Side")
        self._option_combo(self.curve_direction_var,
                           ("Left", "Right", "Alternate"), 10)
        self._option_check("Background", self.shape_bg_var)

    def _options_contour(self):
        self._option_label("Width")
        self._option_spin(self.contour_width_var, 1, 8)
        self._option_label("Side")
        self._option_combo(self.contour_side_var, ("Outside", "Inside"), 8)
        self._option_label("Connect")
        self._option_combo(self.contour_connect_var, ("4", "8"), 3)
        self._option_label("Outlines the selection, else the artwork",
                           (14, 2))

    def _options_selection_mode(self):
        self._option_label("Mode")
        self._option_combo(self.sel_mode_var,
                           ("Replace", "Add", "Subtract", "Intersect"), 9,
                           command=self.apply_sel_mode_option)

    def _options_rect(self):
        self._options_selection_mode()

    def _options_ellipse(self):
        self._options_selection_mode()

    def _options_polygon(self):
        self._options_selection_mode()

    def _options_lasso(self):
        self._options_selection_mode()
        self._option_check("Smooth path", self.lasso_smooth_var)

    def _options_wand(self):
        self._option_label("Tolerance")
        self._option_spin(self.tol_var, 0, 255, command=self.apply_tolerance)
        self._option_check("Ignore alpha", self.ignore_alpha_var)

    def _options_similar(self):
        self._options_wand()

    def _options_transform(self):
        self.rotate_snap_var.set(self.rotate_snap)
        self._option_label("Rotate snap")
        self._option_spin(self.rotate_snap_var, 1, 90, width=3,
                          command=self.apply_rotate_snap)
        self._option_label("deg")
        self._option_label("Drag moves, Shift scales, Ctrl rotates", (18, 2))

    def _options_hand(self):
        self._option_label("Zoom")
        self._option_button("\u2212", lambda: self.zoom_step(-1), width=2)
        self._option_button("+", lambda: self.zoom_step(1), width=2)
        self._option_button("Fit", self.zoom_fit)
        ttk.Label(self.tool_options, textvariable=self.zoom_var).pack(
            side="left", padx=(8, 0))

    def apply_symmetry_option(self):
        self.symmetry_var.set(
            SYMMETRY_VALUES.get(self.symmetry_option_var.get(), ""))
        self.apply_symmetry()

    def apply_sel_mode_option(self):
        self.sel_mode = SEL_MODE_VALUES.get(self.sel_mode_var.get(),
                                            "replace")

    def apply_rotate_snap(self):
        try:
            snap = int(self.rotate_snap_var.get())
        except (tk.TclError, ValueError):
            return
        self.rotate_snap = max(1, min(90, snap))

    # --------------------------------------------------------------- rendering
    def checker(self, width, height):
        key = (width, height)
        if self._checker_key != key:
            tile = Image.new("RGBA", (8, 8), (58, 58, 64, 255))
            draw = ImageDraw.Draw(tile)
            draw.rectangle([0, 0, 3, 3], fill=(76, 76, 84, 255))
            draw.rectangle([4, 4, 7, 7], fill=(76, 76, 84, 255))
            image = Image.new("RGBA", (width, height))
            for y in range(0, height, 8):
                for x in range(0, width, 8):
                    image.paste(tile, (x, y))
            self._checker = image
            self._checker_key = key
        return self._checker

    def redraw(self):
        if self.view_mode == "hex":
            self.redraw_hex()
        else:
            self.redraw_image()

    def redraw_image(self):
        """Draw only the visible part of the canvas.

        A large sprite at high zoom can be tens of millions of pixels, and
        compositing, scaling and converting all of it for every dab made
        painting crawl. Cropping to the viewport keeps a paint near 30 ms
        whatever the canvas size.
        """
        self.view.delete("hex")
        width, height = self.doc.width, self.doc.height
        zoom = max(self.min_zoom(), float(self.zoom))
        full_w = max(1, int(round(width * zoom)))
        full_h = max(1, int(round(height * zoom)))
        self._fit_scrollregion(full_w, full_h)

        view = self.view
        left = int(view.canvasx(0))
        top = int(view.canvasy(0))
        ox = max(0.0, min(max(0.0, full_w - zoom),
                          math.floor(left / zoom) * zoom))
        oy = max(0.0, min(max(0.0, full_h - zoom),
                          math.floor(top / zoom) * zoom))
        bx = max(0, min(width - 1, int(round(ox / zoom))))
        by = max(0, min(height - 1, int(round(oy / zoom))))
        cols = int(math.ceil(max(1, view.winfo_width()) / zoom)) + 3
        rows = int(math.ceil(max(1, view.winfo_height()) / zoom)) + 3
        bw = max(1, min(width - bx, cols))
        bh = max(1, min(height - by, rows))
        cw = max(1, int(round(bw * zoom)))
        ch = max(1, int(round(bh * zoom)))

        part = self.doc.flatten((bx, by, bw, bh))
        stride = bw * 4
        lines = [bytes(part.data[row * stride:(row + 1) * stride])
                 for row in range(bh)]
        base = Image.frombytes("RGBA", (bw, bh), b"".join(lines))
        image = Image.alpha_composite(self.checker_plate(bx, by, bw, bh),
                                      base)
        if zoom != 1:
            image = image.resize((cw, ch), Image.NEAREST)
        if self.doc.selection.active:
            image = self.selection_overlay(image, width, height, zoom,
                                           (bx, by, bw, bh))
        if self.grid and zoom >= 6:
            image = self.grid_overlay(image, cw, ch, zoom)
        image = self.polygon_overlay(image, zoom, (ox, oy))
        image = self.keyboard_overlay(image, zoom, (ox, oy))
        image = self.cursor_overlay(image, zoom, (ox, oy))
        photo = getattr(self, "_photo", None)
        if photo is None or photo.width() != cw or photo.height() != ch:
            self._photo = ImageTk.PhotoImage(image)
        else:
            photo.paste(image)
        # Switching from hex clears the item, so re-attach it on every draw.
        self.view.itemconfig(self.view_image, image=self._photo)
        self.view.coords(self.view_image, ox, oy)

    def _fit_scrollregion(self, width, height):
        """Centre the canvas and leave room to drag the camera anywhere."""
        view = self.view
        view_w = max(1, view.winfo_width())
        view_h = max(1, view.winfo_height())
        pad_x = max(0, (view_w - width) // 2)
        pad_y = max(0, (view_h - height) // 2)
        margin_x = max(48, view_w // 3)
        margin_y = max(48, view_h // 3)
        view.configure(scrollregion=(-pad_x - margin_x, -pad_y - margin_y,
                                     max(width, view_w) - pad_x + margin_x,
                                     max(height, view_h) - pad_y + margin_y))

    def checker_plate(self, x, y, width, height):
        """A checkerboard patch for the visible region only."""
        if 8 * self.zoom < 1:
            # Checks smaller than a screen pixel are noise, and a zoomed-out
            # canvas covers the whole sprite: one flat plate beats a paste
            # every 8 pixels of it.
            return Image.new("RGBA", (width, height), (58, 58, 64, 255))
        if self._checker_tile is None:
            tile = Image.new("RGBA", (8, 8), (58, 58, 64, 255))
            draw = ImageDraw.Draw(tile)
            draw.rectangle([0, 0, 3, 3], fill=(76, 76, 84, 255))
            draw.rectangle([4, 4, 7, 7], fill=(76, 76, 84, 255))
            self._checker_tile = tile
        plate = Image.new("RGBA", (width, height))
        start_x = x - (x % 8)
        start_y = y - (y % 8)
        for py in range(start_y, y + height, 8):
            for px in range(start_x, x + width, 8):
                plate.paste(self._checker_tile, (px - x, py - y))
        return plate

    def _update_hex_metrics(self):
        self.hex_font_obj.configure(size=self.hex_font)
        text_width = self.hex_font_obj.measure("0" * 8)
        line_height = self.hex_font_obj.metrics("linespace")
        pad = max(3, self.hex_font // 3)
        self.hex_pad = pad
        self.hex_cell = (pad * 2 + text_width, line_height + pad * 2)

    def cell_size(self):
        if self.view_mode == "hex":
            return self.hex_cell
        return (self.zoom, self.zoom)

    @staticmethod
    def _readable(color):
        """The pixel color, lightened just enough to stay legible on dark."""
        red, green, blue = color[0], color[1], color[2]
        level = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        if level >= 110:
            return "#%02x%02x%02x" % (red, green, blue)
        if level <= 1:
            return "#7d7d8c"
        scale = 110.0 / level
        return "#%02x%02x%02x" % (min(255, int(red * scale) + 30),
                                  min(255, int(green * scale) + 30),
                                  min(255, int(blue * scale) + 30))

    def hex_pixel(self, x, y):
        """The color a hex cell shows: the result once layers are stacked."""
        if self.doc.layer_count > 1:
            return self.doc.flatten((x, y, 1, 1)).rgba(0, 0)
        return self.doc.bitmap.rgba(x, y)

    def _hex_cell_style(self, col, row):
        color = self.hex_pixel(col, row)
        selection = self.doc.selection
        if selection.active and selection.get(col, row):
            background = "#1c3d48"
        else:
            background = "#101014"
        if color[3] == 0:
            return background, "......", "#5f5f6d"
        if color[3] == 255:
            text = "%02X%02X%02X" % (color[0], color[1], color[2])
        else:
            text = "%02X%02X%02X%02X" % color
        return background, text, self._readable(color)

    def redraw_hex(self):
        """Draw the canvas as a grid of hex codes, each in its own color.

        Only the visible cells are drawn, so the view stays usable on large
        canvases without building a giant bitmap.
        """
        view = self.view
        view.delete("hex")
        view.itemconfig(self.view_image, image="")
        bitmap = self.doc.bitmap
        cols, rows = bitmap.width, bitmap.height
        cell_w, cell_h = self.hex_cell
        pad = self.hex_pad
        self._fit_scrollregion(cols * cell_w, rows * cell_h)
        left = view.canvasx(0)
        top = view.canvasy(0)
        right = left + max(1, view.winfo_width())
        bottom = top + max(1, view.winfo_height())
        first_col = max(0, min(cols - 1, int(left // cell_w)))
        first_row = max(0, min(rows - 1, int(top // cell_h)))
        last_col = max(first_col, min(cols - 1, int((right - 1) // cell_w)))
        last_row = max(first_row, min(rows - 1, int((bottom - 1) // cell_h)))
        font = ("Consolas", self.hex_font)
        text_x = pad
        middle = cell_h // 2
        items = {}
        for row in range(first_row, last_row + 1):
            y = row * cell_h
            for col in range(first_col, last_col + 1):
                x = col * cell_w
                background, text, text_color = self._hex_cell_style(col, row)
                rect = view.create_rectangle(x, y, x + cell_w, y + cell_h,
                                             fill=background, width=0,
                                             tags="hex")
                label = view.create_text(x + text_x, y + middle, text=text,
                                         fill=text_color, font=font,
                                         anchor="w", tags="hex")
                items[(col, row)] = (rect, label)
        self._hex_items = items
        self._hex_geom = (cell_w, cell_h, first_col, first_row, last_col,
                          last_row)
        self._hex_cursor = None
        self._dirty_cells = set()
        cursor_x, cursor_y = self.cursor
        if first_col <= cursor_x <= last_col and first_row <= cursor_y <= last_row:
            self._hex_cursor = view.create_rectangle(
                cursor_x * cell_w, cursor_y * cell_h,
                cursor_x * cell_w + cell_w, cursor_y * cell_h + cell_h,
                outline="#ff40c0", width=2, tags="hex")
        if self.kb_anchor is not None:
            anchor_x, anchor_y = self.kb_anchor
            view.create_rectangle(anchor_x * cell_w, anchor_y * cell_h,
                                  anchor_x * cell_w + cell_w,
                                  anchor_y * cell_h + cell_h,
                                  outline="#ffec00", dash=(2, 2),
                                  width=1, tags="hex")
        if self.tool == "lasso" and len(self.lasso_points) > 1:
            lasso_coords = []
            for point_x, point_y in self.lasso_points:
                lasso_coords.extend([point_x * cell_w + cell_w / 2.0,
                                     point_y * cell_h + cell_h / 2.0])
            lasso_coords.extend(lasso_coords[:2])
            view.create_line(*lasso_coords, fill="#ffec00", width=2,
                             tags="hex")
        if self.poly_points and self.tool == "polygon":
            coords = []
            for point_x, point_y in self.poly_points:
                coords.extend([point_x * cell_w + cell_w / 2.0,
                               point_y * cell_h + cell_h / 2.0])
            if len(coords) >= 4:
                view.create_line(*coords, fill="#ffec00", width=2, tags="hex")
            for index in range(0, len(coords), 2):
                view.create_rectangle(coords[index] - 3, coords[index + 1] - 3,
                                      coords[index] + 3, coords[index + 1] + 3,
                                      outline="#ffec00", tags="hex")

    def request_redraw(self):
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self._flush_redraw)

    def _mark_dirty(self, x0, y0, x1, y1, half=None):
        """Record the hex cells touched by a stroke, mirrors included."""
        if self.view_mode != "hex":
            return
        if half is None:
            half = max(0, self.brush // 2)
        points = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        for px, py in list(points):
            points.extend(mirror_points(px, py, self.doc.width,
                                        self.doc.height, self.symmetry))
        left = max(0, min(p[0] for p in points) - half - 1)
        right = min(self.doc.width - 1, max(p[0] for p in points) + half + 1)
        top = max(0, min(p[1] for p in points) - half - 1)
        bottom = min(self.doc.height - 1, max(p[1] for p in points) + half + 1)
        if self._dirty_cells is None:
            self._dirty_cells = set()
        for row in range(top, bottom + 1):
            for col in range(left, right + 1):
                self._dirty_cells.add((col, row))
        self._hex_incremental_ok = True

    def _flush_redraw(self):
        self._redraw_pending = False
        if (self.view_mode == "hex" and self._hex_incremental_ok
                and self._hex_geom is not None and self.kb_anchor is None
                and not self.poly_points
                and len(self._dirty_cells or ()) <= 400):
            self._update_hex_incremental()
            return
        self._dirty_cells = set()
        self._hex_incremental_ok = False
        self.redraw()

    def _update_hex_incremental(self):
        """Repaint only the cells a stroke touched, plus the cursor."""
        geom = self._hex_geom
        if geom is None or (geom[0], geom[1]) != self.hex_cell:
            self._hex_incremental_ok = False
            self.redraw()
            return
        cell_w, cell_h = geom[0], geom[1]
        view = self.view
        for col, row in list(self._dirty_cells or ()):
            ids = self._hex_items.get((col, row))
            if ids is None:
                continue
            rect, label = ids
            background, text, color = self._hex_cell_style(col, row)
            view.itemconfigure(rect, fill=background)
            view.itemconfigure(label, text=text, fill=color)
        self._dirty_cells = set()
        if self._hex_cursor is not None:
            view.delete(self._hex_cursor)
            self._hex_cursor = None
        x, y = self.cursor
        if (geom[2] <= x <= geom[4] and geom[3] <= y <= geom[5]):
            self._hex_cursor = view.create_rectangle(
                x * cell_w, y * cell_h, x * cell_w + cell_w,
                y * cell_h + cell_h, outline="#ff40c0", width=2,
                tags="hex")
        self._hex_incremental_ok = False

    def selection_overlay(self, image, width, height, zoom, box=None):
        if box is None:
            box = (0, 0, width, height)
        x0, y0, bw, bh = box
        mask = Image.frombytes("L", (width, height),
                               bytes(self.doc.selection.mask))
        # One pixel of halo keeps the crop border from looking like an edge.
        hx0, hy0 = max(0, x0 - 1), max(0, y0 - 1)
        hx1, hy1 = min(width, x0 + bw + 1), min(height, y0 + bh + 1)
        halo = mask.crop((hx0, hy0, hx1, hy1))
        if zoom != 1:
            halo = halo.resize((max(1, int(round((hx1 - hx0) * zoom))),
                                max(1, int(round((hy1 - hy0) * zoom)))),
                               Image.NEAREST)
        halo = halo.point(lambda v: 255 if v > 0 else 0)
        trim = (int(round((x0 - hx0) * zoom)),
                int(round((y0 - hy0) * zoom)),
                int(round((x0 - hx0 + bw) * zoom)),
                int(round((y0 - hy0 + bh) * zoom)))
        solid = halo.crop(trim)
        if solid.size != image.size:
            # Fractional zoom rounds the crop up to a pixel away from the
            # drawn image, and composite() needs an exact match.
            solid = solid.resize(image.size, Image.NEAREST)
        tint = Image.new("RGBA", image.size, (0, 190, 255, 70))
        image = Image.composite(Image.alpha_composite(image, tint), image, solid)
        edges = halo.filter(ImageFilter.FIND_EDGES).crop(trim).point(
            lambda v: 255 if v > 0 else 0)
        if edges.size != image.size:
            edges = edges.resize(image.size, Image.NEAREST)
        accent = Image.new("RGBA", image.size, (0, 235, 255, 235))
        return Image.composite(accent, image, edges)

    def grid_overlay(self, image, width, height, zoom):
        draw = ImageDraw.Draw(image)
        color = (255, 255, 255, 36)
        step = max(1.0, float(zoom))
        x = 0.0
        while x <= width:
            draw.line([(x, 0), (x, height)], fill=color)
            x += step
        y = 0.0
        while y <= height:
            draw.line([(0, y), (width, y)], fill=color)
            y += step
        return image

    def polygon_overlay(self, image, zoom, origin=(0, 0)):
        if self.tool == "lasso" and self.lasso_points:
            draw = ImageDraw.Draw(image)
            ox, oy = origin
            points = [(px * zoom + zoom / 2.0 - ox,
                       py * zoom + zoom / 2.0 - oy)
                      for px, py in self.lasso_points]
            if len(points) > 1:
                draw.line(points + [points[0]], fill=(255, 236, 0, 255),
                          width=1)
            return image
        if not self.poly_points or self.tool != "polygon":
            return image
        draw = ImageDraw.Draw(image)
        ox, oy = origin
        points = [(px * zoom + zoom / 2.0 - ox, py * zoom + zoom / 2.0 - oy)
                  for px, py in self.poly_points]
        color = (255, 236, 0, 255)
        if len(points) > 1:
            draw.line(points, fill=color, width=1)
        for px, py in points:
            draw.rectangle([px - 2, py - 2, px + 2, py + 2], outline=color)
        return image

    def keyboard_overlay(self, image, zoom, origin=(0, 0)):
        """Dashed rectangle from the keyboard selection anchor to the cursor."""
        if self.kb_anchor is None:
            return image
        draw = ImageDraw.Draw(image)
        ox, oy = origin
        anchor_x, anchor_y = self.kb_anchor
        x, y = self.cursor
        left = min(anchor_x, x) * zoom - ox
        top = min(anchor_y, y) * zoom - oy
        right = max(left, (max(anchor_x, x) + 1) * zoom - 1 - ox)
        bottom = max(top, (max(anchor_y, y) + 1) * zoom - 1 - oy)
        draw.rectangle([int(round(left)), int(round(top)),
                        int(round(right)), int(round(bottom))],
                       outline=(255, 236, 0, 255))
        return image

    def cursor_overlay(self, image, zoom, origin=(0, 0)):
        draw = ImageDraw.Draw(image)
        ox, oy = origin
        x, y = self.cursor
        half = self.brush // 2
        size = max(1.0, self.brush * zoom)
        left = (x - half) * zoom - ox
        top = (y - half) * zoom - oy
        right = left + size - 1
        bottom = top + size - 1
        draw.rectangle([int(round(left)), int(round(top)),
                        max(int(round(left)), int(round(right))),
                        max(int(round(top)), int(round(bottom)))],
                       outline=(255, 64, 192, 255))
        return image

    # ---------------------------------------------------------------- geometry
    def pixel_at(self, event):
        """The pixel under the pointer, which may lie outside the canvas.

        Shapes and selections use this so a drag can run past the edge and
        still describe the geometry of the whole drag; tools that act on a
        single pixel use `clamped` instead.
        """
        cell_w, cell_h = self.cell_size()
        x = int(math.floor(self.view.canvasx(event.x) / cell_w))
        y = int(math.floor(self.view.canvasy(event.y) / cell_h))
        return x, y

    def clamped(self, event):
        x, y = self.pixel_at(event)
        x = max(0, min(self.doc.width - 1, x))
        y = max(0, min(self.doc.height - 1, y))
        return x, y

    def mode_from_event(self, event):
        state = event.state
        if state & 0x0004:
            return "subtract"
        if state & 0x0001:
            return "add"
        if state & 0x20000:
            return "intersect"
        return self.default_sel_mode()

    def default_sel_mode(self):
        try:
            return SEL_MODE_VALUES.get(self.sel_mode_var.get(), "replace")
        except AttributeError:
            return "replace"

    # ------------------------------------------------------------------ input
    def on_press(self, event, secondary=False):
        self.view.focus_set()
        self._hex_incremental_ok = False
        x, y = self.clamped(event)
        free_x, free_y = self.pixel_at(event)
        self.cursor = (x, y)
        self.sel_mode = "subtract" if secondary else self.mode_from_event(event)
        tool = self.tool
        if tool in PAINT_TOOLS:
            self.erase_drag = bool(secondary)
            self.begin_stroke()
            self.paint_at(x, y, "eraser" if secondary else tool)
            self.last_paint = (x, y)
        elif tool == "spray":
            self.begin_stroke()
            self.spray_at(x, y)
            self.last_paint = (x, y)
            self.start_spray()
        elif tool == "picker":
            self.pick_at(x, y)
        elif tool == "fill":
            self.fill_at(x, y)
        elif tool == "pattern":
            self.pattern_at(x, y, "line")
        elif tool == "structure":
            self.pattern_at(x, y, "structure")
        elif tool == "blur":
            self.begin_stroke()
            self.blur_source = self.doc.bitmap.copy()
            self.blur_at(x, y)
            self.last_paint = (x, y)
        elif tool == "shuffle":
            self.begin_stroke()
            self.shuffle_at(x, y)
            self.last_paint = (x, y)
        elif tool == "text":
            before = self.doc.begin()
            if self.stamp_text_at(x, y):
                self.doc.commit(before)
        elif tool == "contour":
            self.contour_at()
        elif tool in SHAPE_TOOLS:
            self.begin_shape(free_x, free_y)
        elif tool == "gradient":
            self.begin_gradient(x, y)
        elif tool in ("rect", "ellipse", "lasso"):
            self.sel_backup = self.doc.selection.snapshot()
            self.drag_anchor = (free_x, free_y)
            self.lasso_points = [(free_x, free_y)]
        elif tool == "polygon":
            if not self.poly_points:
                self.sel_backup = self.doc.selection.snapshot()
            if (not self.poly_points
                    or self.poly_points[-1] != (free_x, free_y)):
                self.poly_points.append((free_x, free_y))
        elif tool in ("wand", "similar"):
            self.sel_backup = self.doc.selection.snapshot()
            self.wand_at(x, y, tool == "wand")
        elif tool == "transform" and not secondary:
            state = int(getattr(event, "state", 0) or 0)
            if state & 0x0001:
                mode = "scale"
            elif state & 0x0004:
                mode = "rotate"
            else:
                mode = "move"
            self.begin_transform(x, y, mode)
        elif tool == "hand":
            self.begin_pan(event)
        self.request_redraw()
        self.refresh_status()

    def on_drag(self, event):
        x, y = self.clamped(event)
        free_x, free_y = self.pixel_at(event)
        state = int(getattr(event, "state", 0) or 0)
        self.cursor = (x, y)
        self._hex_incremental_ok = False
        tool = self.tool
        if tool in PAINT_TOOLS and self.stroke_before is not None:
            action = "eraser" if self.erase_drag else tool
            previous = self.last_paint
            if previous:
                if action == "pencil" and self.brush_pattern():
                    self.pattern_stroke(previous[0], previous[1], x, y)
                else:
                    paint_stroke(self.doc.bitmap, self.doc.mask(),
                                 previous[0], previous[1], x, y, self.brush,
                                 (0, 0, 0, 0) if action == "eraser"
                                 else self.fg, self.brush_shape(),
                                 self.symmetry)
            else:
                self.paint_at(x, y, action)
            if previous:
                self._mark_dirty(previous[0], previous[1], x, y)
            self.last_paint = (x, y)
        elif tool in ("blur", "shuffle") and self.stroke_before is not None:
            previous = self.last_paint or (x, y)
            if tool == "blur":
                if self.blur_source is None:
                    self.blur_source = self.doc.bitmap.copy()
                blur_stroke(self.doc.bitmap, self.doc.mask(),
                            previous[0], previous[1], x, y,
                            self.blur_radius(), self.blur_strength(),
                            self.brush_shape(), self.symmetry,
                            self.blur_source, self.blur_smear())
                half = self.blur_radius() // 2 + 1
            else:
                shuffle_stroke(self.doc.bitmap, self.doc.mask(),
                               previous[0], previous[1], x, y,
                               self.shuffle_radius(), self.shuffle_mode(),
                               self.shuffle_amount(), self.brush_shape(),
                               self.symmetry, blur=self.shuffle_blur())
                half = self.shuffle_radius() // 2 + 1
            self._mark_dirty(previous[0], previous[1], x, y, half)
            self.last_paint = (x, y)
        elif tool == "spray" and self.stroke_before is not None:
            previous = self.last_paint or (x, y)
            spray_stroke(self.doc.bitmap, self.doc.mask(),
                         previous[0], previous[1], x, y,
                         self.spray_size(), self.spray_flow(),
                         self.fg, self.brush_shape(), self.symmetry,
                         **self.spray_options())
            self._mark_dirty(previous[0], previous[1], x, y,
                             self.spray_size() // 2 + 1)
            self.last_paint = (x, y)
        elif tool in SHAPE_TOOLS and self.shape_anchor is not None:
            self.apply_shape(free_x, free_y, bool(state & 0x0001))
        elif tool in ("rect", "ellipse") and self.drag_anchor:
            self.apply_drag_selection(free_x, free_y)
        elif tool == "lasso":
            if (not self.lasso_points
                    or self.lasso_points[-1] != (free_x, free_y)):
                self.lasso_points.append((free_x, free_y))
        elif tool == "hand" and self._pan is not None:
            self.on_middle_drag(event)
        elif tool == "transform" and self.transform_piece is not None:
            state = int(getattr(event, "state", 0) or 0)
            if state & 0x0001:
                mode = "scale"
            elif state & 0x0004:
                mode = "rotate"
            else:
                mode = self.transform_mode or "move"
            self.apply_transform(x, y, mode)
        self.request_redraw()
        self.refresh_status()

    def on_release(self, event):
        self.cursor = self.clamped(event)
        self.release_point = self.pixel_at(event)
        self.on_release_common()
        self.release_point = None
        self.redraw()

    def on_release_common(self):
        self.stop_spray()
        if self.shape_anchor is not None:
            self.finish_shape()
        if self.gradient_anchor is not None:
            self.apply_gradient()
        if self.transform_base is not None:
            self.finish_transform()
        if self.stroke_before is not None:
            self.doc.commit(self.stroke_before)
            self.stroke_before = None
            self.last_paint = None
            self.blur_source = None
            self.blur_last = None
        if self.tool in ("rect", "ellipse") and self.drag_anchor:
            # The pointer may have been released past the canvas edge, so the
            # final marquee comes from the unclamped position.
            self.apply_drag_selection(*self.release_point or self.cursor)
            self.drag_anchor = None
        if self.tool == "lasso" and len(self.lasso_points) >= 2:
            points = self.lasso_points
            if not hasattr(self, "lasso_smooth_var") \
                    or self.lasso_smooth_var.get():
                points = clean_path(self.lasso_points)
            mask = selection_polygon_mask(self.doc.width, self.doc.height,
                                          points)
            self.combine_selection(mask)
            self.lasso_points = []
        if self.tool == "hand":
            self._pan = None
        self.drag_anchor = None
        self.refresh_bytes()
        self.refresh_status()

    def on_double(self, event):
        if self.tool == "polygon":
            self.finish_polygon()
        elif self.tool == "hand":
            self.zoom_fit()

    def on_motion(self, event):
        x, y = self.clamped(event)
        moved = (x, y) != self.cursor
        self.cursor = (x, y)
        self.refresh_status()
        if moved:
            self._hex_incremental_ok = (
                self.stroke_before is None and self.shape_anchor is None
                and self.gradient_anchor is None and self.drag_anchor is None
                and self.transform_base is None and not self.poly_points)
            self.request_redraw()

    def on_wheel(self, event):
        self.cursor = self.clamped(event)
        state = int(getattr(event, "state", 0) or 0)
        direction = 1 if int(getattr(event, "delta", 0) or 0) > 0 else -1
        if state & 0x0001:
            self.view.xview_scroll(-direction * 3, "units")
            self.request_redraw()
            return
        if state & 0x0004 and self.view_mode == "image":
            self.nudge_brush(direction)
            return
        self.zoom_step(direction, (event.x, event.y))

    def nudge_brush(self, direction):
        """Grow or shrink the brush from Ctrl+wheel without leaving the canvas."""
        self.brush_var.set(max(1, min(64, int(self.brush) + direction)))
        self.apply_brush()

    def scroll_x(self, *args):
        self.view.xview(*args)
        self.request_redraw()

    def scroll_y(self, *args):
        self.view.yview(*args)
        self.request_redraw()

    def set_view_mode(self, mode=None):
        if mode is None:
            mode = self.view_mode_var.get()
        self.view_mode = "hex" if mode == "hex" else "image"
        self.view_mode_var.set(self.view_mode)
        self.view.delete("hex")
        self.view.itemconfig(self.view_image, image="")
        self.refresh_status()
        self.redraw()
        if self.view_mode == "image":
            self.center_document()
        else:
            self.center_on_cursor()

    def toggle_view(self):
        self.set_view_mode("image" if self.view_mode == "hex" else "hex")

    def zoom_step(self, direction, anchor=None):
        if self.view_mode == "hex":
            self.hex_font = max(6, min(22, self.hex_font + int(direction)))
            self._update_hex_metrics()
            self.refresh_status()
            self.redraw()
            self.center_on_cursor()
            return
        if direction > 0:
            self.set_zoom(self.zoom * 1.25, anchor)
        else:
            self.set_zoom(self.zoom / 1.25, anchor)

    def on_middle(self, event):
        self.begin_pan(event)

    def begin_pan(self, event):
        """Start a pan drag; the Hand tool and the middle button share it."""
        self._pan = (event.x, event.y, self.view.xview()[0],
                     self.view.yview()[0])

    def on_middle_drag(self, event):
        if not self._pan:
            return
        px, py, fx, fy = self._pan
        box = self._scroll_box()
        span_x = max(1.0, box[2] - box[0])
        span_y = max(1.0, box[3] - box[1])
        self.view.xview_moveto(max(0.0, min(1.0,
                                            fx - (event.x - px) / span_x)))
        self.view.yview_moveto(max(0.0, min(1.0,
                                            fy - (event.y - py) / span_y)))
        self.request_redraw()

    def on_middle_release(self, event):
        self._pan = None

    def on_middle_double(self, event=None):
        self._pan = None
        self.zoom_fit()

    # ------------------------------------------------------------------- tools
    def set_tool(self, name):
        if name == "jumble":       # the tool's name before it became Shuffle
            name = "shuffle"
        elif name == "airbrush":   # ... and before it became Spray can
            name = "spray"
        self.stop_spray()
        self.tool = name
        self.tool_var.set(name)
        if name == "transform" and not self.doc.selection.bounds():
            # Move / Scale works on the whole layer until a selection exists.
            self.doc.selection.select_all()
            if hasattr(self, "sel_info"):
                self.refresh_bytes()
        self.refresh_tool_buttons()
        self.poly_points = []
        self.lasso_points = []
        self.blur_source = None
        self.blur_last = None
        self._refresh_tool_options()
        if hasattr(self, "view"):
            cursors = {"hand": "fleur", "transform": "fleur",
                       "picker": "hand2", "text": "xterm"}
            self.view.configure(cursor=cursors.get(name, "crosshair"))
        self.status_tool()
        self.redraw()

    def status_tool(self):
        self.refresh_status()

    def begin_stroke(self):
        self.stroke_before = self.doc.begin()
        self.last_paint = None
        self.blur_last = None

    def end_stroke(self):
        if self.stroke_before is not None:
            self.doc.commit(self.stroke_before)
            self.stroke_before = None
            self.last_paint = None

    def paint_at(self, x, y, tool, color=None):
        if tool == "pencil" and self.brush_pattern():
            painted = self.pattern_dab(x, y)
            self._mark_dirty(x, y, x, y, self.pattern_size // 2 + 1)
            return painted
        color = color if color is not None else self.fg
        if tool == "eraser":
            color = (0, 0, 0, 0)
        paint_dab(self.doc.bitmap, self.doc.mask(), x, y, self.brush, color,
                  self.brush_shape(), self.symmetry)
        self._mark_dirty(x, y, x, y)

    def blur_at(self, x, y):
        """Soften the brush footprint, reading from the stroke snapshot."""
        if self.blur_source is None:
            self.blur_source = self.doc.bitmap.copy()
        painted = blur_area(self.doc.bitmap, self.doc.mask(), x, y,
                            self.blur_radius(), self.blur_strength(),
                            self.brush_shape(), self.symmetry,
                            self.blur_source)
        smear = self.blur_smear()
        if smear and self.blur_last is not None:
            # Smear drags colour along the stroke, so it needs the pixel the
            # brush has just left behind.
            dx = x - self.blur_last[0]
            dy = y - self.blur_last[1]
            if dx or dy:
                painted += smear_area(self.doc.bitmap, self.doc.mask(), x, y,
                                      self.blur_radius(), smear,
                                      self.brush_shape(), self.symmetry,
                                      dx, dy)
        self.blur_last = (x, y)
        self._mark_dirty(x, y, x, y, self.blur_radius() // 2 + 1)
        return painted

    def shuffle_at(self, x, y):
        painted = shuffle_area(self.doc.bitmap, self.doc.mask(), x, y,
                               self.shuffle_radius(), self.shuffle_mode(),
                               self.shuffle_amount(), self.brush_shape(),
                               self.symmetry, blur=self.shuffle_blur())
        self._mark_dirty(x, y, x, y, self.shuffle_radius() // 2 + 1)
        return painted

    def pattern_dab(self, x, y):
        """Stamp the Pattern grid as a tip, centred on (x, y)."""
        return stamp_pattern(self.doc.bitmap, self.doc.mask(), x, y,
                             self.pattern_cells, self.symmetry)

    def pattern_stroke(self, x0, y0, x1, y1):
        """Stamp the pattern tip along a pencil drag."""
        painted = 0
        for x, y in line_points(x0, y0, x1, y1):
            painted += self.pattern_dab(x, y)
        return painted

    def stamp_text_at(self, x, y):
        """Stamp the Text option with its top-left corner at (x, y)."""
        text = self.text_value.get()
        if not text:
            return 0
        painted = stamp_text(self.doc.bitmap, self.doc.mask(), x, y, text,
                             self.fg, self.text_scale(), self.text_spacing(),
                             self.symmetry)
        width, height = text_size(text, self.text_scale(), self.text_spacing())
        self._mark_dirty(x, y, x + max(0, width - 1), y + max(0, height - 1))
        self.refresh_bytes()
        return painted

    def spray_at(self, x, y):
        """One spray pass over the brush footprint."""
        painted = spray_area(self.doc.bitmap, self.doc.mask(), x, y,
                             self.spray_size(), self.spray_flow(),
                             self.fg, self.brush_shape(), self.symmetry,
                             **self.spray_options())
        self._mark_dirty(x, y, x, y, self.spray_size() // 2 + 1)
        self.request_redraw()
        return painted

    def start_spray(self):
        if self._spray_job is None:
            self._spray_job = self.after(60, self._spray_tick)

    def _spray_tick(self):
        self._spray_job = None
        if self.stroke_before is None or self.tool != "spray":
            return
        self.spray_at(self.cursor[0], self.cursor[1])
        self._spray_job = self.after(60, self._spray_tick)

    def stop_spray(self):
        if getattr(self, "_spray_job", None) is None:
            return
        try:
            self.after_cancel(self._spray_job)
        except tk.TclError:
            pass
        self._spray_job = None

    def contour_at(self):
        """Outline the selection, or the artwork when nothing is selected.

        The ring is painted as-is, even the part outside a selection: the
        selection is what gets outlined, so clipping to it would hide the
        result.
        """
        selection = self.doc.selection
        region = selection.mask if selection.active else None
        solid = content_mask(self.doc.bitmap, region)
        outline = contour_mask(solid, self.doc.width, self.doc.height,
                               self.contour_width(), self.contour_side(),
                               self.contour_connectivity())
        before = self.doc.begin()
        painted = paint_mask(self.doc.bitmap, None, outline, self.fg)
        if painted:
            self.doc.commit(before)
        self._mark_dirty(0, 0, self.doc.width - 1, self.doc.height - 1)
        self.refresh_bytes()
        self.request_redraw()
        return painted

    def pick_at(self, x, y):
        color = self.doc.bitmap.rgba(x, y)
        if (getattr(self, "pick_target_var", None) is not None
                and self.pick_target_var.get() == "BG"):
            self.bg = color
            self.draw_swatches()
            self.refresh_status()
            return
        self.set_fg(color)
        self.hex_entry.set(hex_str(*color))

    def fill_at(self, x, y):
        before = self.doc.begin()
        flood_fill(self.doc.bitmap, self.doc.mask(), x, y, self.fg,
                   self.tolerance)
        self.doc.commit(before)
        self.refresh_bytes()

    def wand_at(self, x, y, contiguous):
        ignore_alpha = (bool(self.ignore_alpha_var.get())
                        if hasattr(self, "ignore_alpha_var") else False)
        mask = pick_related(self.doc.bitmap, x, y, self.tolerance,
                            ignore_alpha=ignore_alpha, contiguous=contiguous)
        self.combine_selection(mask)

    def apply_drag_selection(self, x, y):
        ax, ay = self.drag_anchor or (x, y)
        if self.tool == "ellipse":
            mask = selection_ellipse_mask(self.doc.width, self.doc.height,
                                          ax, ay, x, y)
        else:
            mask = selection_rect_mask(self.doc.width, self.doc.height,
                                       ax, ay, x, y)
        self.combine_selection(mask)

    def combine_selection(self, mask):
        if self.sel_backup is not None:
            self.doc.selection.restore(self.sel_backup)
        self.doc.selection.apply_mask(mask, self.sel_mode)
        self.refresh_status()
        self.refresh_bytes()

    def finish_polygon(self):
        if len(self.poly_points) >= 2:
            mask = selection_polygon_mask(self.doc.width, self.doc.height,
                                          self.poly_points)
            if self.sel_backup is None:
                self.sel_backup = self.doc.selection.snapshot()
            self.combine_selection(mask)
        self.poly_points = []
        self.sel_backup = None
        self.redraw()

    # ---------------------------------------------------------- move / scale
    def begin_transform(self, x, y, mode="move"):
        """Pick the selected pixels up: drag moves, Shift scales, Ctrl rotates."""
        if self.transform_base is not None:
            return
        if mode is True:
            mode = "scale"
        elif mode is False:
            mode = "move"
        bounds = self.doc.selection.bounds()
        if not bounds:
            # Nothing selected: the whole layer is the piece.
            self.doc.selection.select_all()
            bounds = self.doc.selection.bounds()
            if not bounds:
                return
        left, top, width, height = bounds
        if mode == "move" and not self.doc.selection.get(x, y):
            self.report_error("Drag from inside the selection to move it, "
                              "Shift to scale it, Ctrl to rotate it.")
            return
        self.transform_before = self.doc.begin()
        self.transform_base = self.doc.bitmap.copy()
        self.transform_mask = bytes(self.doc.selection.mask)
        self.transform_piece = extract_selection(
            self.doc.bitmap, self.transform_mask, bounds)
        self.transform_origin_box = bounds
        self.transform_box = bounds
        self.transform_grab = (x, y)
        self.transform_mode = mode
        self.transform_angle = 0.0
        self.transform_rotation = RotationDrag(
            (left + width / 2.0, top + height / 2.0), (x, y))
        near_left = abs(x - left) <= abs(x - (left + width - 1))
        near_top = abs(y - top) <= abs(y - (top + height - 1))
        self.transform_fixed = (left + width - 1 if near_left else left,
                                top + height - 1 if near_top else top)
        self.refresh_status()

    def apply_transform(self, x, y, mode=None):
        """Rebuild the layer with the piece moved, scaled or rotated."""
        if self.transform_piece is None or self.transform_base is None:
            return
        if mode is True:
            mode = "scale"
        elif mode is False:
            mode = "move"
        if mode is None:
            mode = self.transform_mode or "move"
        piece = self.transform_piece
        left, top, width, height = self.transform_origin_box
        if mode == "scale":
            nx, ny, new_w, new_h = scale_box(
                self.transform_origin_box, self.transform_fixed,
                self.transform_grab, (x, y),
                (self.doc.width, self.doc.height))
            piece = scale_image(piece, new_w, new_h)
        elif mode == "rotate":
            snap = max(1, int(getattr(self, "rotate_snap", ROTATE_SNAP)))
            angle = self.transform_rotation.update((x, y), snap)
            self.transform_angle = angle
            piece = rotate_piece(piece, angle)
            centre_x, centre_y = self.transform_rotation.centre
            nx = int(round(centre_x - piece.width / 2.0))
            ny = int(round(centre_y - piece.height / 2.0))
        else:
            grab_x, grab_y = self.transform_grab
            nx = left + (x - grab_x)
            ny = top + (y - grab_y)
        self.doc.bitmap = self.transform_base.copy()
        clear_region(self.doc.bitmap, self.transform_mask)
        stamp_bitmap(self.doc.bitmap, piece, (nx, ny))
        mask = piece_mask(piece, (nx, ny), self.doc.width, self.doc.height)
        self.doc.selection.apply_mask(mask, "replace")
        self.transform_box = (nx, ny, piece.width, piece.height)
        self._hex_incremental_ok = False
        self.refresh_bytes()
        self.refresh_status()
        self.request_redraw()

    def finish_transform(self):
        if self.transform_base is None:
            return
        if self.transform_box != self.transform_origin_box:
            self.doc.commit(self.transform_before)
        self.transform_before = None
        self.transform_base = None
        self.transform_piece = None
        self.transform_mask = None
        self.transform_box = None
        self.transform_origin_box = None
        self.transform_grab = None
        self.transform_fixed = None
        self.transform_mode = "move"
        self.transform_angle = 0.0
        self.transform_rotation = None
        self.refresh_bytes()
        self.refresh_status()

    def cancel_transform(self):
        """Escape while moving: put the pixels back where they were."""
        if self.transform_base is None:
            return
        self.doc.bitmap = self.transform_base
        if self.transform_mask is not None:
            self.doc.selection.apply_mask(self.transform_mask, "replace")
        self.transform_before = None
        self.transform_base = None
        self.transform_piece = None
        self.transform_mask = None
        self.transform_box = None
        self.transform_origin_box = None
        self.transform_grab = None
        self.transform_fixed = None
        self.transform_mode = "move"
        self.transform_angle = 0.0
        self.transform_rotation = None
        self.refresh_bytes()
        self.refresh_status()

    def move_selection_to(self, x, y):
        """Enter with the move tool: put the selection's corner at the cursor."""
        bounds = self.doc.selection.bounds()
        if not bounds:
            # Nothing selected: Enter moves the whole layer.
            self.doc.selection.select_all()
            bounds = self.doc.selection.bounds()
            if not bounds:
                return
        piece = extract_selection(self.doc.bitmap, self.doc.selection.mask,
                                  bounds)
        before = self.doc.begin()
        clear_region(self.doc.bitmap, self.doc.selection.mask)
        stamp_bitmap(self.doc.bitmap, piece, (x, y))
        mask = piece_mask(piece, (x, y), self.doc.width, self.doc.height)
        self.doc.selection.apply_mask(mask, "replace")
        self.doc.commit(before)
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def move_cursor(self, dx, dy):
        x, y = self.cursor
        self.cursor = (max(0, min(self.doc.width - 1, x + dx)),
                       max(0, min(self.doc.height - 1, y + dy)))
        if self.shape_anchor is not None:
            self.apply_shape(self.cursor[0], self.cursor[1])
        else:
            self.keyboard_rubber()
        self.refresh_status()
        self.request_redraw()

    def commit_typed(self):
        color = self.fg
        if self.typed:
            try:
                color = parse_hex(self.typed)
            except HexError as exc:
                self.report_error(str(exc))
                self.typed = ""
                self.refresh_status()
                return
            self.set_fg(color)
        self.begin_stroke()
        self.paint_at(self.cursor[0], self.cursor[1], "pencil", color)
        self.end_stroke()
        x, y = self.cursor
        self.cursor = (min(self.doc.width - 1, x + 1), y)
        self.typed = ""
        self.refresh_status()
        self.refresh_bytes()
        self.request_redraw()

    # ------------------------------------------------------- keyboard selection
    def keyboard_corner(self):
        """Dwarf-Fortress style: Enter drops a corner, Enter again closes."""
        if self.kb_anchor is None:
            self.kb_before = self.doc.selection.snapshot()
            self.kb_anchor = self.cursor
        else:
            self.kb_anchor = None
            self.kb_before = None
            self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def keyboard_cancel(self):
        """Throw the rubber band away and restore the previous selection."""
        if self.kb_before is not None:
            self.doc.selection.restore(self.kb_before)
        self.kb_anchor = None
        self.kb_before = None
        self.refresh_bytes()
        self.refresh_status()

    def keyboard_rubber(self):
        """Redraw the selection from the anchor to the cursor, live."""
        if self.kb_anchor is None:
            return
        anchor_x, anchor_y = self.kb_anchor
        x, y = self.cursor
        if self.tool == "ellipse":
            mask = selection_ellipse_mask(self.doc.width, self.doc.height,
                                          anchor_x, anchor_y, x, y)
        else:
            mask = selection_rect_mask(self.doc.width, self.doc.height,
                                       anchor_x, anchor_y, x, y)
        if self.kb_before is not None:
            self.doc.selection.restore(self.kb_before)
        self.doc.selection.apply_mask(mask, self.sel_mode)
        self.refresh_bytes()

    def keyboard_activate(self):
        """Enter: run the active tool at the keyboard cursor."""
        x, y = self.cursor
        tool = self.tool
        if tool in ("rect", "ellipse"):
            self.keyboard_corner()
        elif tool == "polygon":
            self.keyboard_polygon()
        elif tool == "fill":
            self.fill_at(x, y)
        elif tool == "pattern":
            self.pattern_at(x, y, "line")
        elif tool == "structure":
            self.pattern_at(x, y, "structure")
        elif tool == "blur":
            before = self.doc.begin()
            self.blur_source = self.doc.bitmap.copy()
            if self.blur_at(x, y):
                self.doc.commit(before)
            self.blur_source = None
        elif tool == "shuffle":
            before = self.doc.begin()
            if self.shuffle_at(x, y):
                self.doc.commit(before)
        elif tool == "text":
            before = self.doc.begin()
            if self.stamp_text_at(x, y):
                self.doc.commit(before)
        elif tool == "spray":
            before = self.doc.begin()
            if self.spray_at(x, y):
                self.doc.commit(before)
        elif tool == "contour":
            self.contour_at()
        elif tool in SHAPE_TOOLS:
            if self.shape_anchor is None:
                self.begin_shape(x, y)
            else:
                self.finish_shape()
        elif tool == "gradient":
            self.keyboard_gradient()
        elif tool == "eraser":
            self.erase_at_cursor()
        elif tool == "picker":
            self.pick_at(x, y)
        elif tool == "transform":
            self.move_selection_to(x, y)
        elif tool in ("lasso", "wand", "similar", "hand"):
            pass
        else:
            self.commit_typed()
        self.refresh_status()
        self.redraw()

    def keyboard_polygon(self, add_vertex=False):
        """Enter closes the polygon once it has three vertices.

        With fewer vertices Enter drops the next one, so a polygon can be
        built entirely from the keyboard. Shift+Enter always adds a vertex,
        which also covers polygons with more corners than Enter could build.
        """
        if len(self.poly_points) >= 3 and not add_vertex:
            self.finish_polygon()
            return
        if not self.poly_points:
            self.sel_backup = self.doc.selection.snapshot()
        if self.poly_points and self.poly_points[-1] == self.cursor:
            return
        self.poly_points.append(self.cursor)

    # ------------------------------------------------------ shapes and gradient
    def brush_shape(self):
        try:
            value = str(self.brush_shape_var.get()).lower()
        except AttributeError:
            return "square"
        if value.startswith("circle"):
            return "circle"
        if value.startswith("custom"):
            return "custom"
        return "square"

    def tip_chosen(self):
        """Choosing Custom before any tip is saved opens the editor."""
        if self.brush_shape() != "custom":
            return
        if str(self.settings.get("custom_brush", "") or "").strip():
            return
        if not self.edit_brush_shape():
            self.brush_shape_var.set("Square")

    def edit_brush_shape(self):
        """Draw the custom tip; True when the dialog was accepted."""
        dialog = BrushShapeDialog(self, custom_brush())
        self.wait_window(dialog)
        if dialog.result is None:
            return False
        set_custom_brush(dialog.result)
        try:
            self.settings.set("custom_brush", custom_brush_text())
        except (OSError, ValueError):
            pass
        self.brush_shape_var.set("Custom")
        return True

    def _load_custom_brush(self):
        """Restore the custom tip saved with the settings."""
        try:
            custom_brush_from_text(self.settings.get("custom_brush", ""))
        except (AttributeError, TypeError, ValueError):
            pass

    def brush_pattern(self):
        """True when the pencil should stamp the Pattern grid as its tip."""
        try:
            enabled = bool(self.brush_pattern_var.get())
        except (AttributeError, tk.TclError):
            return False
        if not enabled:
            return False
        return any(cell for row in self.pattern_cells for cell in row)

    def apply_symmetry(self):
        self.symmetry = self.symmetry_var.get()
        if hasattr(self, "symmetry_option_var"):
            self.symmetry_option_var.set(
                SYMMETRY_LABELS.get(self.symmetry, "Off"))

    def shape_paint_color(self):
        """Shape tools paint in BG instead of FG when Background is on."""
        try:
            enabled = bool(self.shape_bg_var.get())
        except (AttributeError, tk.TclError):
            enabled = False
        return shape_color(self.fg, self.bg, enabled)

    def begin_shape(self, x, y):
        self.shape_before = self.doc.begin()
        self.shape_base = self.doc.bitmap.copy()
        self.shape_anchor = (x, y)
        self.apply_shape(x, y)

    def constrained_shape(self, x, y):
        """Snap a drag for Shift: 45° lines and square/circle shapes."""
        ax, ay = self.shape_anchor
        dx, dy = x - ax, y - ay
        if self.tool == "line":
            adx, ady = abs(dx), abs(dy)
            if adx >= ady * 2:
                dy = 0
            elif ady >= adx * 2:
                dx = 0
            else:
                step = min(adx, ady)
                dx = step if dx >= 0 else -step
                dy = step if dy >= 0 else -step
            return ax + dx, ay + dy
        size = max(abs(dx), abs(dy))
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        return ax + sx * size, ay + sy * size

    def apply_shape(self, x, y, constrain=False):
        if self.shape_anchor is None or self.shape_base is None:
            return
        if constrain and self.tool != "curve":
            x, y = self.constrained_shape(x, y)
        self.doc.bitmap = self.shape_base.copy()
        color = self.shape_paint_color()
        if self.tool == "curve":
            paint_curve(self.doc.bitmap, self.doc.mask(),
                        self.shape_anchor[0], self.shape_anchor[1], x, y,
                        self.brush, color, self.curve_bend(),
                        self.brush_shape(), self.symmetry,
                        self.curve_vectors(), self.curve_direction())
        else:
            kind = (self.tool[len("shape_"):]
                    if self.tool.startswith("shape_") else self.tool)
            paint_shape(self.doc.bitmap, self.doc.mask(), kind,
                        self.shape_anchor[0], self.shape_anchor[1], x, y,
                        self.brush, color, bool(self.shape_filled.get()),
                        self.brush_shape(), self.symmetry)
        self.refresh_bytes()
        self.request_redraw()

    def finish_shape(self):
        if self.shape_anchor is None:
            return
        self.doc.commit(self.shape_before)
        self.shape_before = None
        self.shape_anchor = None
        self.shape_base = None
        self.refresh_bytes()
        self.refresh_status()

    def cancel_shape(self):
        if self.shape_anchor is None:
            return
        if self.shape_base is not None:
            self.doc.bitmap = self.shape_base
        self.shape_before = None
        self.shape_anchor = None
        self.shape_base = None
        self.refresh_bytes()

    def begin_gradient(self, x, y):
        self.gradient_before = self.doc.begin()
        self.gradient_anchor = (x, y)

    def apply_gradient(self):
        if self.gradient_anchor is None:
            return
        x0, y0 = self.gradient_anchor
        x1, y1 = self.cursor
        self.gradient_anchor = None
        try:
            start_color, end_color, stops = self.gradient_stops()
            mode = self.gradient_dither_mode()
            gradient_fill(self.doc.bitmap, self.doc.mask(), x0, y0, x1, y1,
                          start_color, end_color, mode,
                          self.gradient_pattern(mode), colors=stops,
                          radial=self.gradient_radial(),
                          spread=self.gradient_spread(),
                          **self.dither_options())
        except HexError as exc:
            self.report_error(str(exc))
            self.gradient_before = None
            return
        self.doc.commit(self.gradient_before)
        self.gradient_before = None
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def cancel_gradient(self):
        self.gradient_anchor = None
        self.gradient_before = None

    def gradient_colors(self):
        if (getattr(self, "gradient_reverse_var", None) is not None
                and self.gradient_reverse_var.get()):
            return self.bg, self.fg
        return self.fg, self.bg

    def gradient_stops(self):
        """The two end colours plus the full palette ramp when asked for."""
        if (getattr(self, "gradient_palette_var", None) is not None
                and self.gradient_palette_var.get()):
            colors = [tuple(color) for color in self.palette]
            if len(colors) < 2:
                raise HexError("a palette gradient needs at least two colours")
            if (getattr(self, "gradient_reverse_var", None) is not None
                    and self.gradient_reverse_var.get()):
                colors.reverse()
            return colors[0], colors[-1], colors
        start_color, end_color = self.gradient_colors()
        return start_color, end_color, None

    def gradient_radial(self):
        var = getattr(self, "gradient_radial_var", None)
        return bool(var is not None and var.get())

    def gradient_spread(self):
        var = getattr(self, "gradient_spread_var", None)
        return str(var.get()).lower() if var is not None else "clamp"

    def keyboard_gradient(self):
        bounds = self.doc.selection.bounds()
        if bounds:
            left, top, width, height = bounds
            start = (left, top)
            end = (left + width - 1, top + height - 1)
        else:
            start = (0, 0)
            end = (self.doc.width - 1, self.doc.height - 1)
        before = self.doc.begin()
        try:
            start_color, end_color, stops = self.gradient_stops()
            mode = self.gradient_dither_mode()
            gradient_fill(self.doc.bitmap, self.doc.mask(), start[0], start[1],
                          end[0], end[1], start_color, end_color, mode,
                          self.gradient_pattern(mode), colors=stops,
                          radial=self.gradient_radial(),
                          spread=self.gradient_spread(),
                          **self.dither_options())
        except HexError as exc:
            self.report_error(str(exc))
            return
        self.doc.commit(before)
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    # ----------------------------------------------------------------- palette
    def _update_palette_scroll(self):
        box = self.palette_canvas.bbox("all")
        if box:
            self.palette_canvas.configure(scrollregion=box)

    def draw_palette(self):
        if not hasattr(self, "palette_grid"):
            return
        for child in self.palette_grid.winfo_children():
            child.destroy()
        self.palette_chips = []
        if self.palette_selected is not None \
                and self.palette_selected >= len(self.palette):
            self.palette_selected = None
        for index, color in enumerate(self.palette):
            selected = index == self.palette_selected
            chip = tk.Canvas(self.palette_grid, width=18, height=18,
                             highlightthickness=2,
                             highlightbackground=("#2f8fd0" if selected
                                                  else "#9aa0aa"),
                             bg="#%02x%02x%02x" % color[:3])
            chip.grid(row=index // 7, column=index % 7, padx=1, pady=1)
            chip.bind("<Button-1>", lambda event, i=index, c=color:
                      self.pick_palette_color(i, c))
            chip.bind("<Button-3>", lambda event, i=index:
                      self.edit_palette_color(i))
            self.palette_chips.append(chip)
        self.palette_count_var.set("%d / %d colours"
                                   % (len(self.palette), MAX_PALETTE_COLORS))
        self._update_palette_scroll()
        self.after_idle(self._update_palette_scroll)

    def pick_palette_color(self, index, color):
        self.palette_selected = index
        self.set_fg(color)
        self.draw_palette()

    def edit_palette_color(self, index):
        if not (0 <= index < len(self.palette)):
            return
        color = self.palette[index]
        rgb, _hex = colorchooser.askcolor(
            color="#%02x%02x%02x" % color[:3], parent=self,
            title="Palette colour %d" % (index + 1))
        if not rgb:
            return
        self.palette[index] = (int(rgb[0]), int(rgb[1]), int(rgb[2]),
                               color[3])
        self.palette_selected = index
        self.palette_var.set("Custom")
        self.draw_palette()

    def new_palette(self):
        dialog = PaletteDialog(self, self.fg, self.palette_name)
        self.wait_window(dialog)
        if not dialog.result:
            return
        self.palette_name, self.palette = dialog.result
        self.palette_selected = None
        self.palette_var.set("Custom")
        self.draw_palette()

    def load_preset_palette(self):
        name = self.palette_var.get()
        if name not in PALETTES:
            return
        self.palette = preset_colors(name)
        self.palette_name = name
        self.palette_selected = None
        self.draw_palette()

    def add_palette_color(self):
        if len(self.palette) >= MAX_PALETTE_COLORS:
            self.report_error("A palette holds at most %d colours."
                              % MAX_PALETTE_COLORS)
            return
        color = tuple(self.fg)
        if color in self.palette:
            self.palette_selected = self.palette.index(color)
        else:
            self.palette.append(color)
            self.palette_selected = len(self.palette) - 1
        self.palette_var.set("Custom")
        self.draw_palette()

    def set_palette_color(self):
        if self.palette_selected is None:
            self.report_error("Click a swatch to select it first.")
            return
        self.palette[self.palette_selected] = tuple(self.fg)
        self.palette_var.set("Custom")
        self.draw_palette()

    def delete_palette_color(self):
        if self.palette_selected is None:
            self.report_error("Click a swatch to select it first.")
            return
        if len(self.palette) <= 1:
            self.report_error("A palette needs at least one colour.")
            return
        del self.palette[self.palette_selected]
        self.palette_selected = min(self.palette_selected,
                                    len(self.palette) - 1)
        self.palette_var.set("Custom")
        self.draw_palette()

    def save_palette(self):
        if not self.palette:
            self.report_error("The palette is empty.")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Save palette", defaultextension=".256",
            initialfile=(self.palette_name or "palette"),
            filetypes=list(PALETTE_FILETYPES))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(format_palette(path, self.palette_name,
                                            self.palette))
        except OSError as exc:
            self.report_error("Could not save the palette: %s" % exc)

    def load_palette(self):
        path = filedialog.askopenfilename(
            parent=self, title="Load palette",
            filetypes=list(PALETTE_FILETYPES))
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                name, colors, _columns = parse_palette(handle.read(), path)
        except (OSError, HexError) as exc:
            self.report_error("Could not load the palette: %s" % exc)
            return
        self.palette = colors
        self.palette_name = name
        self.palette_selected = None
        self.palette_var.set("Custom")
        self.draw_palette()

    def apply_palette(self, dither=False):
        if not self.palette:
            self.report_error("Create or load a palette first.")
            return
        before = self.doc.begin()
        painted = quantize_bitmap(self.doc.bitmap, self.palette, dither,
                                  self.doc.mask())
        self.doc.commit(before)
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()
        self.status.set("mapped %d pixels to %s%s"
                        % (painted, self.palette_name or "the palette",
                           " (dithered)" if dither else ""))

    # --------------------------------------------------------------- patterns
    def on_pattern_click(self, event, erase=False):
        cell = self.pattern_cell_px
        x = int(event.x) // cell
        y = int(event.y) // cell
        if not (0 <= x < self.pattern_size and 0 <= y < self.pattern_size):
            return
        erase = erase or bool(event.state & 0x0004)
        self.pattern_cells[y][x] = None if erase else tuple(self.fg)
        self.draw_pattern_grid()

    def draw_pattern_grid(self):
        view = self.pattern_view
        view.delete("all")
        cell = self.pattern_cell_px
        for y in range(self.pattern_size):
            for x in range(self.pattern_size):
                color = self.pattern_cells[y][x]
                fill = ("#%02x%02x%02x" % color[:3]) if color else "#232326"
                view.create_rectangle(x * cell, y * cell,
                                      x * cell + cell, y * cell + cell,
                                      fill=fill, outline="#3a3a42")
        self.pattern_text_from_grid()

    def pattern_text_from_grid(self):
        if not hasattr(self, "pattern_text"):
            return
        rows = []
        for y in range(self.pattern_size):
            row = []
            for x in range(self.pattern_size):
                color = self.pattern_cells[y][x]
                row.append("." if color is None
                           else "%02X%02X%02X" % color[:3])
            rows.append(" ".join(row))
        self.pattern_text.delete("1.0", "end")
        self.pattern_text.insert("1.0", "\n".join(rows))

    def pattern_grid_from_text(self):
        try:
            block = parse_block(self.pattern_text.get("1.0", "end"))
        except HexError as exc:
            self.report_error(str(exc))
            return
        size = max(1, min(16, max(block.width, block.height)))
        self.set_pattern_size(size)
        for y, row in enumerate(block.rows):
            if y >= size:
                continue
            for x, pixel in enumerate(row):
                if x >= size:
                    continue
                try:
                    self.pattern_cells[y][x] = tuple(pixel)
                except TypeError:
                    self.pattern_cells[y][x] = None
        self.pattern_size_var.set(size)
        self.draw_pattern_grid()

    def apply_pattern_size(self):
        try:
            size = int(self.pattern_size_var.get())
        except (ValueError, tk.TclError):
            return
        self.set_pattern_size(size)

    def set_pattern_size(self, size):
        size = max(1, min(16, int(size)))
        old = self.pattern_cells
        cells = [[None] * size for _ in range(size)]
        for y in range(min(size, len(old))):
            for x in range(min(size, len(old[y]))):
                cells[y][x] = old[y][x]
        self.pattern_cells = cells
        self.pattern_size = size
        if hasattr(self, "pattern_view"):
            self.pattern_view.configure(
                width=size * self.pattern_cell_px,
                height=size * self.pattern_cell_px)
            self.draw_pattern_grid()

    def pattern_block(self):
        return block_from_grid(self.pattern_cells, self.pattern_size,
                               self.pattern_size)

    def pattern_spec(self, mode=None):
        return PatternSpec(
            self.pattern_block(),
            mode or self.pattern_mode.get(),
            int(self.pattern_scale.get()),
            int(self.pattern_shift.get()),
            bool(self.pattern_mirror.get()),
            int(self.pattern_sw.get()),
            int(self.pattern_sh.get()))

    def pattern_origin(self, x, y):
        bounds = self.doc.selection.bounds()
        return (bounds[0], bounds[1]) if bounds else (x, y)

    def pattern_at(self, x, y, mode="line"):
        """Apply a pattern fill from a mouse click or the keyboard cursor."""
        try:
            spec = self.pattern_spec(mode)
        except (ValueError, tk.TclError) as exc:
            self.report_error(str(exc))
            return
        before = self.doc.begin()
        painted = apply_pattern(self.doc.bitmap, self.doc.mask(), spec,
                                self.pattern_origin(x, y))
        self.doc.commit(before)
        self.pattern_info.set("%s pattern: %d pixels from %d,%d"
                              % (spec.mode, painted, x, y))
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def apply_pattern_fill(self):
        x, y = self.cursor
        self.pattern_at(x, y, self.pattern_mode.get())

    def pattern_from_selection(self):
        bounds = self.doc.selection.bounds()
        if bounds is None:
            self.report_error("Select an area first, then take it as a pattern.")
            return
        left, top, width, height = bounds
        size = max(1, min(16, max(width, height)))
        self.set_pattern_size(size)
        for y in range(min(size, height)):
            for x in range(min(size, width)):
                color = self.doc.bitmap.rgba(left + x, top + y)
                self.pattern_cells[y][x] = color if color[3] else None
        self.pattern_size_var.set(size)
        self.draw_pattern_grid()
        self.pattern_info.set("pattern loaded from %d,%d %dx%d"
                              % (left, top, width, height))

    def clear_pattern(self):
        self.pattern_cells = [[None] * self.pattern_size
                              for _ in range(self.pattern_size)]
        self.draw_pattern_grid()
        self.pattern_info.set("")

    # ----------------------------------------------------------------- macros
    def toggle_recording(self):
        if self.recorder.recording:
            self.recorder.stop()
        else:
            if self.playing:
                self.stop_playback()
            self.recorder.start()
        self.refresh_macro_status()
        self.refresh_status()

    def refresh_macro_status(self):
        record = self.recorder
        if self.playing:
            text = "playing %d/%d" % (self.play_index, len(self.play_steps))
        elif record.recording:
            text = "REC %d keys" % len(record.macro.steps)
        elif len(record.macro.steps):
            text = "%d keys recorded" % len(record.macro.steps)
        else:
            text = "no recording"
        self.macro_info.set(text)
        self.record_button.configure(
            text="Stop recording (F9)" if record.recording
            else "Record keys (F9)")

    def play_macro(self, repeat=1, interval=45, steps=None):
        """Replay recorded keys through the normal key handler."""
        if self.playing:
            return
        macro_steps = list(self.recorder.macro.steps
                           if steps is None else steps)
        if not macro_steps:
            self.report_error("Nothing recorded yet - press F9 and edit.")
            return
        self.play_steps = macro_steps * max(1, int(repeat))
        self.play_index = 0
        self._play_interval = max(0, int(interval))
        self.playing = True
        self.refresh_macro_status()
        self.refresh_status()
        if self._play_interval <= 0:
            for step in self.play_steps:
                self._dispatch_step(step)
                self.play_index += 1
            self._finish_playback()
            return
        self._play_job = self.after(self._play_interval, self._play_next)

    def play_macro_repeated(self):
        count = simpledialog.askinteger(
            "Play macro", "Repeat the recording how many times?",
            initialvalue=10, minvalue=1, maxvalue=999, parent=self)
        if count:
            self.play_macro(repeat=count)

    def _play_next(self):
        self._play_job = None
        if not self.playing:
            return
        if self.play_index >= len(self.play_steps):
            self._finish_playback()
            return
        self._dispatch_step(self.play_steps[self.play_index])
        self.play_index += 1
        self.refresh_macro_status()
        self.refresh_status()
        self._play_job = self.after(self._play_interval, self._play_next)

    def _dispatch_step(self, step):
        self._dispatching = True
        try:
            self.on_key(_KeyEvent(step.keysym, step.char, step.state))
        finally:
            self._dispatching = False

    def stop_playback(self):
        if not self.playing:
            return False
        if self._play_job is not None:
            try:
                self.after_cancel(self._play_job)
            except tk.TclError:
                pass
            self._play_job = None
        self._finish_playback()
        return True

    def _finish_playback(self):
        self.playing = False
        self.play_steps = []
        self.play_index = 0
        self.refresh_macro_status()
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def save_macro(self):
        macro = self.recorder.macro
        if not len(macro):
            self.report_error("There is nothing recorded to save.")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Save keyboard macro",
            defaultextension=".json",
            filetypes=[("hex2pix macro", "*.json"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            macro.save(path)
        except OSError as exc:
            self.report_error("Could not save the macro: %s" % exc)
            return
        self.status.set("saved %d keys to %s"
                        % (len(macro), os.path.basename(path)))

    def load_macro(self):
        path = filedialog.askopenfilename(
            parent=self, title="Load keyboard macro",
            filetypes=[("hex2pix macro", "*.json"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            macro = Macro.load(path)
        except (OSError, ValueError) as exc:
            self.report_error("Could not load the macro: %s" % exc)
            return
        self.recorder.stop()
        self.recorder.macro = macro
        self.refresh_macro_status()
        self.status.set("loaded %d keys from %s"
                        % (len(macro), os.path.basename(path)))

    def clear_macro(self):
        self.recorder.stop()
        self.recorder.macro.clear()
        self.refresh_macro_status()
        self.refresh_status()

    def erase_at_cursor(self):
        before = self.doc.begin()
        self.paint_at(self.cursor[0], self.cursor[1], "eraser")
        self.doc.commit(before)
        self.refresh_status()
        self.request_redraw()

    def min_zoom(self):
        """How far this artwork may zoom out: size x 50,000 on its longer side."""
        return min_zoom_for(self.doc.width, self.doc.height)

    def set_zoom(self, zoom, anchor=None):
        """Zoom to any ratio, keeping the pixel under the pointer still."""
        old_zoom = self.zoom
        pixel = None
        if anchor is not None and self.view_mode == "image":
            pixel = (self.view.canvasx(anchor[0]) / float(old_zoom),
                     self.view.canvasy(anchor[1]) / float(old_zoom))
        max_zoom = min(MAX_ZOOM, 100000.0 / max(1, self.doc.width),
                       100000.0 / max(1, self.doc.height))
        zoom = max(self.min_zoom(), min(max_zoom, float(zoom)))
        self.zoom = zoom
        self.zoom_var.set(self.zoom_label())
        self.redraw()
        if pixel is not None and anchor is not None:
            self._keep_anchor(pixel, anchor)
        else:
            self.center_on_cursor()
        self.refresh_status()

    def zoom_label(self):
        percent = self.zoom * 100.0
        if percent >= 1:
            return "%d%%" % int(round(percent))
        if percent >= 0.01:
            return "%.2f%%" % percent
        return "1:%.0f" % (1.0 / self.zoom)

    def _scroll_box(self):
        """The scrollregion as four floats, with a usable fallback."""
        try:
            box = [float(v) for v in self.view.cget("scrollregion").split()]
        except (tk.TclError, ValueError):
            box = []
        if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
            return (0.0, 0.0, 1.0, 1.0)
        return tuple(box)

    def _keep_anchor(self, pixel, anchor):
        """Keep the pixel under the mouse still while zooming."""
        try:
            box = [float(v) for v in self.view.cget("scrollregion").split()]
        except (tk.TclError, ValueError):
            box = []
        if len(box) != 4:
            self.center_on_cursor()
            return
        span_x = max(1.0, box[2] - box[0])
        span_y = max(1.0, box[3] - box[1])
        left = pixel[0] * self.zoom - anchor[0]
        top = pixel[1] * self.zoom - anchor[1]
        self.view.xview_moveto(max(0.0, min(1.0, (left - box[0]) / span_x)))
        self.view.yview_moveto(max(0.0, min(1.0, (top - box[1]) / span_y)))
        self.request_redraw()

    def center_on_cursor(self):
        cell_w, cell_h = self.cell_size()
        view_w = max(1, self.view.winfo_width())
        view_h = max(1, self.view.winfo_height())
        box = self._scroll_box()
        span_x = max(1.0, box[2] - box[0])
        span_y = max(1.0, box[3] - box[1])
        x, y = self.cursor
        target_x = x * cell_w + cell_w / 2.0 - view_w / 2.0
        target_y = y * cell_h + cell_h / 2.0 - view_h / 2.0
        self.view.xview_moveto(max(0.0, min(1.0,
                                            (target_x - box[0]) / span_x)))
        self.view.yview_moveto(max(0.0, min(1.0,
                                            (target_y - box[1]) / span_y)))
        self.request_redraw()

    def center_document(self):
        """Scroll so the whole artwork sits in the middle of the view."""
        cell_w, cell_h = self.cell_size()
        view_w = max(1, self.view.winfo_width())
        view_h = max(1, self.view.winfo_height())
        try:
            x0, y0, x1, y1 = (float(value) for value in
                              str(self.view.cget("scrollregion")).split())
        except (ValueError, tk.TclError):
            return
        span_x = max(1.0, x1 - x0)
        span_y = max(1.0, y1 - y0)
        target_x = self.doc.width * cell_w / 2.0 - view_w / 2.0
        target_y = self.doc.height * cell_h / 2.0 - view_h / 2.0
        self.view.xview_moveto(max(0.0, min(1.0, (target_x - x0) / span_x)))
        self.view.yview_moveto(max(0.0, min(1.0, (target_y - y0) / span_y)))
        self.request_redraw()

    def _on_view_configure(self):
        self.request_redraw()
        if not getattr(self, "_view_centred", False) \
                and self.view.winfo_width() > 50:
            self._view_centred = True
            self.update_idletasks()
            self.redraw()
            self.center_document()

    def zoom_fit(self):
        if self.view_mode == "hex":
            self.hex_font = 9
            self._update_hex_metrics()
            self.view.xview_moveto(0.0)
            self.view.yview_moveto(0.0)
            self.refresh_status()
            self.redraw()
            return
        available_w = max(60, self.view.winfo_width() - 16)
        available_h = max(60, self.view.winfo_height() - 16)
        zoom = min(available_w / float(max(1, self.doc.width)),
                   available_h / float(max(1, self.doc.height)))
        self.cursor = (self.doc.width // 2, self.doc.height // 2)
        self.set_zoom(zoom)

    def toggle_grid(self):
        self.grid = bool(self.grid_var.get())
        self.redraw()

    # -------------------------------------------------------------- colors
    def set_fg(self, color):
        self.fg = tuple(color)
        self.draw_swatches()
        self.push_recent(self.fg)
        self.alpha_var.set(self.fg[3])
        self.hex_entry.set(hex_str(*self.fg))
        if getattr(self, "wheel", None) is not None:
            self.wheel.set_color(self.fg)

    def wheel_color(self, color, secondary=False):
        if secondary:
            self.bg = tuple(color)
            self.draw_swatches()
        else:
            self.set_fg(tuple(color))

    def draw_swatches(self):
        for canvas, color in ((self.fg_swatch, self.fg), (self.bg_swatch, self.bg)):
            canvas.delete("all")
            canvas.configure(bg="#%02x%02x%02x" % color[:3])

    def push_recent(self, color):
        color = tuple(color)
        if color in self.recent:
            self.recent.remove(color)
        self.recent.insert(0, color)
        del self.recent[8:]
        for child in self.recent_frame.winfo_children():
            child.destroy()
        for index, item in enumerate(self.recent):
            chip = tk.Canvas(self.recent_frame, width=20, height=20,
                             highlightthickness=1,
                             highlightbackground="#555",
                             bg="#%02x%02x%02x" % item[:3])
            chip.grid(row=index // 4, column=index % 4, padx=1, pady=1)
            chip.bind("<Button-1>", lambda e, c=item: self.set_fg(c))

    def apply_hex_entry(self):
        try:
            color = parse_hex(self.hex_entry.get())
        except HexError as exc:
            self.report_error(str(exc))
            return
        self.set_fg(color)
        self.refresh_status()

    def apply_alpha(self):
        alpha = max(0, min(255, int(self.alpha_var.get())))
        self.fg = (self.fg[0], self.fg[1], self.fg[2], alpha)
        self.draw_swatches()
        self.hex_entry.set(hex_str(*self.fg))
        self.refresh_status()

    def swap_colors(self):
        self.fg, self.bg = self.bg, self.fg
        self.draw_swatches()
        self.hex_entry.set(hex_str(*self.fg))
        self.alpha_var.set(self.fg[3])
        self.refresh_status()

    def ask_color(self, which):
        color = self.fg if which == "fg" else self.bg
        value = simpledialog.askstring(
            "Color", "Hex color (#RGB, #RRGGBB or #RRGGBBAA)",
            initialvalue=hex_str(*color), parent=self)
        if not value:
            return
        try:
            parsed = parse_hex(value)
        except HexError as exc:
            self.report_error(str(exc))
            return
        if which == "fg":
            self.set_fg(parsed)
        else:
            self.bg = parsed
            self.draw_swatches()
        self.refresh_status()

    def blur_radius(self):
        return _bounded(self.blur_size_var.get(), 1, 64, 4)

    def blur_strength(self):
        return _bounded(self.blur_strength_var.get(), 0, 100, 60)

    def shuffle_radius(self):
        return _bounded(self.shuffle_size_var.get(), 1, 64, 4)

    def shuffle_mode(self):
        value = str(self.shuffle_mode_var.get()).lower()
        return value if value in ("shuffle", "scatter", "noise") \
            else "shuffle"

    def shuffle_amount(self):
        return _bounded(self.shuffle_amount_var.get(), 0, 100, 40)

    def blur_smear(self):
        return _bounded(self.blur_smear_var.get(), 0, 100, 0)

    def shuffle_blur(self):
        return _bounded(self.shuffle_blur_var.get(), 0, 100, 0)

    def text_scale(self):
        return _bounded(self.text_scale_var.get(), 1, 8, 2)

    def text_spacing(self):
        return _bounded(self.text_spacing_var.get(), 0, 8, 1)

    def spray_size(self):
        return _bounded(self.spray_size_var.get(), 1, 64, 8)

    def spray_flow(self):
        return _bounded(self.spray_flow_var.get(), 1, 100, 25)

    def spray_options(self):
        """The Blur slider and Multicolor switch as spray_area keywords."""
        options = {}
        blur = self.spray_blur()
        if blur:
            options["blur"] = blur
        if self.spray_multi_var.get():
            colors = [tuple(color) for color in getattr(self, "palette", [])
                      if len(color) == 4]
            options["colors"] = colors or [self.fg]
        return options

    def spray_blur(self):
        try:
            value = float(self.spray_blur_var.get())
        except (tk.TclError, TypeError, ValueError):
            return 0
        return max(0, min(100, int(round(value))))

    def spray_blur_changed(self, value=None):
        text = getattr(self, "spray_blur_text", None)
        if text is not None:
            text.set("%d%%" % self.spray_blur())

    def curve_bend(self):
        return _bounded(self.curve_bend_var.get(), -100, 100, 30)

    def curve_vectors(self):
        return _bounded(self.curve_vectors_var.get(), 1, 8, 1)

    def curve_direction(self):
        value = str(self.curve_direction_var.get()).lower()
        return value if value in ("left", "right", "alternate") else "left"

    def contour_width(self):
        return _bounded(self.contour_width_var.get(), 1, 8, 1)

    def contour_side(self):
        return ("inside" if str(self.contour_side_var.get()).lower()
                .startswith("in") else "outside")

    def contour_connectivity(self):
        return 8 if str(self.contour_connect_var.get()).startswith("8") else 4

    def apply_brush(self):
        self.brush = max(1, min(64, int(self.brush_var.get())))
        self.refresh_status()
        self.request_redraw()

    def apply_tolerance(self):
        self.tolerance = max(0, min(255, int(self.tol_var.get())))
        self.refresh_status()

    # --------------------------------------------------------------- panels
    def place_block(self, anchor):
        try:
            block = parse_block(self.block_text.get("1.0", "end"))
        except HexError as exc:
            self.report_error(str(exc))
            return
        block = transform_block(
            block,
            scale=self.block_scale.get(),
            flip_x=bool(self.flip_x.get()),
            flip_y=bool(self.flip_y.get()),
            rotate=int(self.block_rotate.get() or 0))
        width, height = self.doc.width, self.doc.height
        if anchor == "selection":
            bounds = self.doc.selection.bounds()
            origin = (bounds[0], bounds[1]) if bounds else (
                (width - block.width) // 2, (height - block.height) // 2)
        elif anchor == "cursor":
            origin = self.cursor
        else:
            origin = ((width - block.width) // 2, (height - block.height) // 2)
        mode = self.block_mode.get().lower()
        mirror = bool(self.mirror_tiles.get())
        before = self.doc.begin()
        place_block(self.doc.bitmap, self.doc.mask(), block, mode, origin, mirror)
        self.doc.commit(before)
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def load_block_from_selection(self):
        bounds = self.doc.selection.bounds()
        rect = bounds if bounds else (0, 0, self.doc.width, self.doc.height)
        text = block_from_bitmap(self.doc.bitmap, rect)
        self.block_text.delete("1.0", "end")
        self.block_text.insert("1.0", text)

    def load_block_from_file(self):
        path = filedialog.askopenfilename(
            parent=self, title="Load hex block or image",
            filetypes=formats.OPEN_FILETYPES)
        if not path:
            return
        try:
            if os.path.splitext(path)[1].lower() in (".txt", ".hex"):
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            else:
                bitmap = formats.load(path)
                text = block_from_bitmap(bitmap)
        except (OSError, HexError) as exc:
            self.report_error(str(exc))
            return
        self.block_text.delete("1.0", "end")
        self.block_text.insert("1.0", text)

    def apply_operation(self):
        try:
            operand = parse_hex(self.operand.get())
        except HexError as exc:
            self.report_error(str(exc))
            return
        label = self.op_var.get()
        op = {OP_LABELS[key]: key for key in OPS}.get(label, "add")
        before = self.doc.begin()
        apply_op(self.doc.bitmap, self.doc.mask(), operand, op,
                 int(self.strength.get()), bool(self.wrap.get()),
                 bool(self.alpha_ops.get()))
        self.doc.commit(before)
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def apply_map(self):
        try:
            mapping = parse_hex_map(self.map_text.get("1.0", "end"))
        except HexError as exc:
            self.report_error(str(exc))
            return
        if not mapping:
            self.report_error("No OLD=NEW mappings to apply.")
            return
        before = self.doc.begin()
        apply_map(self.doc.bitmap, self.doc.mask(), mapping, self.tolerance)
        self.doc.commit(before)
        self.refresh_bytes()
        self.redraw()

    def apply_hue_action(self):
        before = self.doc.begin()
        apply_hue(self.doc.bitmap, self.doc.mask(), int(self.hue_var.get()))
        self.doc.commit(before)
        self.refresh_bytes()
        self.redraw()

    def apply_ramp_action(self):
        before = self.doc.begin()
        apply_ramp(self.doc.bitmap, self.doc.mask(), int(self.ramp_var.get()))
        self.doc.commit(before)
        self.refresh_bytes()
        self.redraw()

    # ---------------------------------------------------------------- bytes
    def refresh_bytes(self):
        selection = self.doc.selection
        if selection.active:
            bounds = selection.bounds() or (0, 0, 0, 0)
            count = selection.count()
            self.sel_info.set("selection: %d px / %d bytes\nbbox %d,%d %dx%d"
                              % (count, count * 4, bounds[0], bounds[1],
                                 bounds[2], bounds[3]))
            self.dump_info.set("%d px, %d bytes" % (count, count * 4))
        else:
            total = self.doc.width * self.doc.height
            self.sel_info.set("no selection: whole image\n%d px / %d bytes"
                              % (total, total * 4))
            self.dump_info.set("whole image: %d px, %d bytes"
                               % (total, total * 4))
        text = format_dump(self.doc.bitmap, self.doc.mask(), max_rows=1200)
        self.dump_text.configure(state="normal")
        self.dump_text.delete("1.0", "end")
        self.dump_text.insert("1.0", text)
        self.dump_text.configure(state="disabled")

    def copy_dump(self):
        self.clipboard_clear()
        self.clipboard_append(format_dump(self.doc.bitmap, self.doc.mask(),
                                          max_rows=1200))

    def copy_block(self):
        bounds = self.doc.selection.bounds()
        rect = bounds if bounds else (0, 0, self.doc.width, self.doc.height)
        self.clipboard_clear()
        self.clipboard_append(block_from_bitmap(self.doc.bitmap, rect))
        self.export_info.set("copied block text (%dx%d)" % (rect[2], rect[3]))
        if self.silent:
            return

    def paste_block(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.report_error("Clipboard is empty.")
            return
        try:
            block = parse_block(text)
        except HexError as exc:
            self.report_error(str(exc))
            return
        bounds = self.doc.selection.bounds()
        origin = (bounds[0], bounds[1]) if bounds else (
            (self.doc.width - block.width) // 2,
            (self.doc.height - block.height) // 2)
        before = self.doc.begin()
        place_block(self.doc.bitmap, self.doc.mask(), block, "stamp", origin)
        self.doc.commit(before)
        self.refresh_bytes()
        self.redraw()

    def save_bytes(self):
        bounds = self.doc.selection.bounds()
        rect = bounds if bounds else (0, 0, self.doc.width, self.doc.height)
        text = block_from_bitmap(self.doc.bitmap, rect)
        path = filedialog.asksaveasfilename(
            parent=self, title="Save selected bytes as hex text",
            defaultextension=".txt", filetypes=[("Hex text", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text + "\n")
        except OSError as exc:
            self.report_error(str(exc))
            return
        self.export_info.set("saved %s" % os.path.basename(path))

    # ---------------------------------------------------------------- export
    def refresh_export_panel(self):
        """Mirror the active document in the export docker."""
        if not hasattr(self, "project_path_var"):
            return
        path = getattr(self.doc, "path", "") or ""
        self.project_path_var.set(path or "not saved yet")
        try:
            self.export_ppi.set(int(round(self.doc.ppi)))
        except (tk.TclError, ValueError, AttributeError):
            pass
        self._update_export_options()

    def _export_format(self):
        return formats.FORMAT_BY_LABEL.get(self.format_var.get(),
                                           formats.FORMATS[0])

    def _format_changed(self, event=None):
        item = self._export_format()
        path = self.export_path.get().strip()
        if path:
            stem, ext = os.path.splitext(path)
            if ext.lower() != item["ext"]:
                self.export_path.set(stem + item["ext"])
        self._update_export_options()

    def _update_export_options(self, event=None):
        if not hasattr(self, "matte_entry"):
            return
        item = self._export_format()
        key = item["key"]
        if item["alpha"]:
            self.alpha_check.state(["!disabled"])
            keep = bool(self.export_alpha.get())
        else:
            self.alpha_check.state(["disabled"])
            keep = False
        self.matte_entry.state(["disabled"] if keep else ["!disabled"])
        for widget, active in ((self.quality_spin, key == "jpg"),
                               (self.gif_spin, key == "gif"),
                               (self.png_spin, key == "png")):
            widget.state(["!disabled"] if active else ["disabled"])
        self._update_export_hint()

    def _update_export_hint(self, event=None):
        if not hasattr(self, "export_hint") or not hasattr(self, "doc"):
            return
        if self.export_source.get() == "Each visible layer":
            count = sum(1 for layer in self.doc.layers if layer.visible)
            self.export_hint.set("%d visible layer%s, %d x %d px each"
                                 % (count, "" if count == 1 else "s",
                                    self.doc.width, self.doc.height))
            return
        if self.export_source.get() == "Selection":
            bounds = self.doc.selection.bounds()
            if not bounds:
                self.export_hint.set("select an area to export it")
                return
            width, height = bounds[2], bounds[3]
        else:
            width, height = self.doc.width, self.doc.height
        try:
            scale = _bounded(int(self.export_scale.get()), 1, 32, 1)
        except (tk.TclError, ValueError):
            scale = 1
        trimmed = " then trimmed" if self.export_trim.get() else ""
        self.export_hint.set("%d x %d px -> %d x %d px%s"
                             % (width, height, width * scale,
                                height * scale, trimmed))

    def _set_default_export_path(self):
        item = (self._export_format() if hasattr(self, "format_var")
                else formats.FORMATS[0])
        if self.doc.path:
            base = os.path.splitext(self.doc.path)[0] + item["ext"]
        else:
            base = os.path.join(os.path.expanduser("~"),
                                "hex2pix" + item["ext"])
        self.export_path.set(base)
        self.refresh_export_panel()

    def browse_export(self):
        item = self._export_format()
        current = self.export_path.get().strip()
        if current:
            guess = os.path.splitext(current)[0] + item["ext"]
        elif self.doc.path:
            guess = os.path.splitext(self.doc.path)[0] + item["ext"]
        else:
            guess = "hex2pix" + item["ext"]
        path = filedialog.asksaveasfilename(
            parent=self, title="Export image",
            initialfile=os.path.basename(guess),
            defaultextension=item["ext"],
            filetypes=[(item["label"], "*" + item["ext"]),
                       ("All files", "*.*")])
        if path:
            self.export_path.set(path)

    def _export_source_bitmap(self):
        """The pixels for the chosen source, before trim/scale/flatten."""
        source = self.export_source.get()
        if source == "Current layer":
            return self.doc.bitmap.copy()
        if source == "Selection":
            bounds = self.doc.selection.bounds()
            if not bounds:
                raise HexError("Nothing is selected.")
            left, top, width, height = bounds
            piece = self.doc.flatten().rect_rgba(left, top, width, height)
            data = bytearray(piece.data)
            mask = self.doc.selection.mask
            canvas_width = self.doc.width
            for row in range(height):
                for col in range(width):
                    if not mask[(top + row) * canvas_width + left + col]:
                        start = (row * width + col) * 4
                        data[start:start + 4] = b"\x00\x00\x00\x00"
            return Bitmap(width, height, data)
        return self.doc.flatten()

    def export_now(self):
        path = self.export_path.get().strip()
        if not path:
            self.browse_export()
            path = self.export_path.get().strip()
            if not path:
                return
        item = self._export_format()
        ext = item["ext"]
        if not os.path.splitext(path)[1]:
            path += ext
            self.export_path.set(path)
        try:
            matte = parse_hex(self.matte_var.get() or "FFFFFF")[:3]
        except HexError:
            matte = (255, 255, 255)
        try:
            scale = _bounded(int(self.export_scale.get()), 1, 32, 1)
            ppi = _bounded(int(self.export_ppi.get()), 1, 2400, 72)
            quality = _bounded(int(self.quality_var.get()), 1, 100, 92)
            gif_colors = _bounded(int(self.gif_colors_var.get()), 2, 256, 256)
            png_compress = _bounded(int(self.png_compress_var.get()), 0, 9, 6)
        except (tk.TclError, ValueError):
            self.report_error("Export options need whole numbers.")
            return
        resample = ("bilinear" if self.export_resample.get().lower()
                    == "bilinear" else "nearest")
        keep_alpha = bool(item["alpha"]) and bool(self.export_alpha.get())
        options = {
            "scale": scale,
            "resample": resample,
            "trim": bool(self.export_trim.get()),
            "keep_alpha": keep_alpha,
            "matte": matte,
        }
        save_options = {
            "keep_alpha": keep_alpha,
            "gif_colors": gif_colors,
            "png_compress": png_compress,
        }
        try:
            if self.export_source.get() == "Each visible layer":
                layers = [layer for layer in self.doc.layers if layer.visible]
                if not layers:
                    raise HexError("No visible layers to export.")
                for index, layer in enumerate(layers, 1):
                    target = _layer_export_path(path, layer.name, index, ext)
                    prepared = formats.prepare_export(layer.bitmap, **options)
                    formats.save(prepared, target, item["key"], matte,
                                 quality, ppi, **save_options)
                message = ("wrote %d layer file%s beside %s"
                           % (len(layers), "" if len(layers) == 1 else "s",
                              os.path.basename(path)))
            else:
                bitmap = self._export_source_bitmap()
                prepared = formats.prepare_export(bitmap, **options)
                size = formats.save(prepared, path, item["key"], matte,
                                    quality, ppi, **save_options)
                message = ("wrote %s (%d x %d, %d bytes, %s)"
                           % (os.path.basename(path), prepared.width,
                              prepared.height, size, item["label"]))
        except (OSError, HexError, ValueError) as exc:
            self.report_error("Export failed: %s" % exc)
            return
        self.export_info.set(message)
        self.refresh_status()

    def focus_export(self):
        self.notebook.select(self.export_tab)

    # -------------------------------------------------------------- documents
    def new_document(self):
        dialog = CanvasDialog(self, mode="new", title="New Canvas")
        self.wait_window(dialog)
        if dialog.result:
            self.new_canvas(dialog.result)

    def canvas_properties(self, default_resize=None):
        """Edit size, resolution, color model and alpha of the open image."""
        spec = CanvasSpec(self.doc.width, self.doc.height, self.doc.ppi,
                          self.doc.color_mode, self.doc.alpha)
        if default_resize:
            spec.resize_mode = default_resize
        dialog = CanvasDialog(self, document=self.doc, spec=spec,
                              mode="properties", title="Canvas Properties")
        self.wait_window(dialog)
        if dialog.result:
            self.apply_canvas_spec(dialog.result)

    def new_canvas(self, spec):
        document = Document(spec.width, spec.height, ppi=spec.ppi,
                            color_mode=spec.color_mode, alpha=spec.alpha)
        fill = tuple(spec.fill)
        if any(fill):
            if spec.alpha:
                document.bitmap.fill(fill)
            else:
                document.bitmap.fill((fill[0], fill[1], fill[2], 255))
        self.adopt_document(document)
        self.cursor = (0, 0)
        self.kb_anchor = None
        self.kb_before = None
        self._set_default_export_path()
        self.refresh_layers()
        self.refresh_bytes()
        self.refresh_status()
        self.zoom_fit()
        self.redraw()
        self.show_editor()

    def start_preset(self, width, height, ppi=72.0):
        self.new_canvas(CanvasSpec(width, height, ppi))

    def show_home(self):
        self.editor.pack_forget()
        self.home.pack(fill="both", expand=True)
        self.home_visible = True
        self.home.refresh_recent()

    def toggle_fullscreen(self):
        self.set_fullscreen(not self.fullscreen)

    def set_fullscreen(self, enabled):
        """Borderless fullscreen that restores the previous geometry on exit."""
        enabled = bool(enabled)
        if enabled == self.fullscreen:
            return
        try:
            if enabled:
                self._saved_geometry = self.geometry()
                self.attributes("-fullscreen", True)
            else:
                self.attributes("-fullscreen", False)
                if self._saved_geometry:
                    self.geometry(self._saved_geometry)
                    self._saved_geometry = None
        except tk.TclError:
            return
        self.fullscreen = enabled
        self.after(80, self._after_fullscreen)
        self.refresh_status()

    def _after_fullscreen(self):
        self._update_left_scrollregion()
        self.redraw()

    def show_editor(self):
        self.home.pack_forget()
        self.editor.pack(fill="both", expand=True)
        self.home_visible = False
        self.view.focus_set()
        self.redraw()

    # ------------------------------------------------------------------- tabs
    def tab_title(self, index):
        """The name on a tab: file name or Untitled N, plus a dirty star."""
        tab = self.tabs[index]
        if tab.doc.path:
            name = os.path.basename(tab.doc.path)
        else:
            number = sum(1 for other in range(index + 1)
                         if not self.tabs[other].doc.path)
            name = "Untitled" if number == 1 else "Untitled %d" % number
        return name + (" *" if tab.doc.dirty else "")

    def refresh_tabs(self):
        """Redraw the tab strip from the open documents."""
        if not hasattr(self, "tab_bar"):
            return
        for child in self.tab_bar.winfo_children():
            child.destroy()
        for index in range(len(self.tabs)):
            active = index == self.tab_index
            background = (self.colors["button_active"] if active
                          else self.colors["button"])
            frame = tk.Frame(self.tab_bar, bg=background, highlightthickness=1,
                             highlightbackground=self.colors["border"])
            label = tk.Label(frame, text=self.tab_title(index), bg=background,
                             fg=self.colors["fg"], padx=8, pady=3,
                             font=("Segoe UI", 9))
            label.pack(side="left")
            label.bind("<Button-1>",
                       lambda event, i=index: self.switch_tab(i))
            close = tk.Label(frame, text="\u2715", bg=background,
                             fg=self.colors["muted"], padx=6, pady=3,
                             font=("Segoe UI", 8))
            close.pack(side="left")
            close.bind("<Button-1>",
                       lambda event, i=index: self.close_tab(i))
            frame.pack(side="left", padx=(0, 3))
        add = tk.Label(self.tab_bar, text="+", bg=self.colors["button"],
                       fg=self.colors["fg"], padx=8, pady=3,
                       font=("Segoe UI", 10))
        add.pack(side="left")
        add.bind("<Button-1>", lambda event: self.new_document())

    def capture_tab_view(self):
        """Remember zoom, scroll and cursor for the tab being left."""
        if not self.tabs:
            return
        tab = self.tabs[self.tab_index]
        tab.cursor = self.cursor
        tab.zoom = self.zoom
        try:
            tab.xview = self.view.xview()[0]
            tab.yview = self.view.yview()[0]
        except (tk.TclError, IndexError):
            pass

    def restore_tab_view(self):
        if not self.tabs:
            return
        tab = self.tabs[self.tab_index]
        try:
            self.view.xview_moveto(tab.xview)
            self.view.yview_moveto(tab.yview)
        except (tk.TclError, IndexError):
            pass

    def reset_pointer_state(self):
        """Drop half-finished drags when the document under the pointer changes."""
        self.shape_anchor = None
        self.shape_base = None
        self.shape_before = None
        self.gradient_anchor = None
        self.gradient_before = None
        self.transform_base = None
        self.transform_piece = None
        self.transform_before = None
        self.stroke_before = None
        self.drag_anchor = None
        self.poly_points = []
        self.lasso_points = []
        self.blur_source = None
        self.blur_last = None
        self.last_paint = None
        self.kb_anchor = None
        self.kb_before = None
        self.sel_backup = None

    def tab_index_of(self, document):
        for index, tab in enumerate(self.tabs):
            if tab.doc is document:
                return index
        return 0

    def adopt_document(self, document):
        """Show a freshly opened document in a tab of its own."""
        self.capture_tab_view()
        reuse = (not self._tab_reused and len(self.tabs) == 1
                 and not self.tabs[0].doc.path and not self.tabs[0].doc.dirty)
        if reuse:
            self.tabs[0].doc = document
            self.tab_index = 0
        else:
            self.tabs.append(DocumentTab(document))
            self.tab_index = len(self.tabs) - 1
        self._tab_reused = True
        self.doc = document
        self.reset_pointer_state()
        self.refresh_tabs()

    def switch_tab(self, index, capture=True):
        """Make another canvas current, keeping each tab's view where it was."""
        if not self.tabs:
            return False
        index = max(0, min(len(self.tabs) - 1, int(index)))
        if capture:
            self.capture_tab_view()
        self.tab_index = index
        tab = self.tabs[index]
        self.doc = tab.doc
        self.reset_pointer_state()
        self.cursor = (max(0, min(tab.cursor[0], self.doc.width - 1)),
                       max(0, min(tab.cursor[1], self.doc.height - 1)))
        if tab.zoom:
            self.zoom = max(self.min_zoom(), float(tab.zoom))
            if hasattr(self, "zoom_var"):
                self.zoom_var.set(self.zoom_label())
        self._set_default_export_path()
        self.refresh_layers()
        self.refresh_tabs()
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()
        self.after_idle(self.restore_tab_view)
        return True

    def next_tab(self, step=1):
        if len(self.tabs) < 2:
            return False
        return self.switch_tab((self.tab_index + int(step)) % len(self.tabs))

    def confirm_tab_save(self, tab):
        """Offer to save one modified tab; False cancels the close."""
        if self.silent:
            return True
        index = self.tab_index_of(tab.doc)
        if index != self.tab_index:
            self.switch_tab(index)
        name = os.path.basename(tab.doc.path) if tab.doc.path else "Untitled"
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            "\u201c%s\u201d has unsaved changes.\n\nSave it before closing?"
            % name, parent=self)
        if answer is None:
            return False
        if not answer:
            return True
        return bool(self.save_document())

    def close_tab(self, index=None):
        """Close one tab, offering to save it first."""
        if not self.tabs:
            return False
        index = self.tab_index if index is None else int(index)
        if index < 0 or index >= len(self.tabs):
            return False
        tab = self.tabs[index]
        if tab.doc.dirty:
            self.switch_tab(index)
            if not self.confirm_tab_save(tab):
                return False
        closing_current = index == self.tab_index
        del self.tabs[index]
        if not self.tabs:
            self.doc = Document(64, 64)
            self.tabs = [DocumentTab(self.doc)]
            self.tab_index = 0
            self._tab_reused = True
            self.reset_pointer_state()
            self.refresh_layers()
            self.refresh_tabs()
            self.refresh_bytes()
            self.refresh_status()
            self.show_home()
            return True
        self.tab_index = min(index, len(self.tabs) - 1)
        # The closed document's view state must not be written onto its
        # neighbour, so only capture when some other tab is being left.
        return self.switch_tab(self.tab_index, capture=not closing_current)

    def request_close(self):
        """Close the window, asking about every tab holding unsaved work."""
        if getattr(self, "_closing", False):
            return True
        if self.silent:
            self.destroy()
            return True
        for index in range(len(self.tabs)):
            tab = self.tabs[index]
            if not tab.doc.dirty:
                continue
            self.switch_tab(index)
            if not self.confirm_tab_save(tab):
                return False
        self.destroy()
        return True

    # ----------------------------------------------------------- dropped files
    def ask_drop_action(self, paths):
        dialog = DropDialog(self, paths)
        self.wait_window(dialog)
        return dialog.result

    def handle_dropped_paths(self, paths, choose=None):
        """Files dragged from Explorer: insert them or open them in tabs."""
        paths = [str(path) for path in paths if os.path.isfile(str(path))]
        if not paths:
            return []
        if choose is None:
            if self.silent:
                return []
            choose = self.ask_drop_action
        action = choose(paths)
        if action == "layer":
            if self.home_visible:
                self.show_editor()
            return ["layer" if self.insert_path_as_layer(path) else "failed"
                    for path in paths]
        if action == "canvas":
            return ["canvas" if self.load_path(path) else "failed"
                    for path in paths]
        return []

    def paste_bitmap(self, destination, source, x, y):
        """Copy `source` onto `destination` with its top-left at (x, y)."""
        left, top = max(0, x), max(0, y)
        right = min(destination.width, x + source.width)
        bottom = min(destination.height, y + source.height)
        if right <= left or bottom <= top:
            return 0
        for row in range(top, bottom):
            start = ((row - y) * source.width + (left - x)) * 4
            target = (row * destination.width + left) * 4
            length = (right - left) * 4
            destination.data[target:target + length] = \
                source.data[start:start + length]
        return (right - left) * (bottom - top)

    def insert_path_as_layer(self, path):
        """Add an image file as a new layer on this canvas, centred."""
        try:
            bitmap, _ppi = formats.load_document(path)
        except (OSError, HexError) as exc:
            self.report_error(str(exc))
            return False
        before = self.doc.begin()
        layer = self.doc.add_layer(os.path.basename(path))
        self.paste_bitmap(layer.bitmap, bitmap,
                          (self.doc.width - bitmap.width) // 2,
                          (self.doc.height - bitmap.height) // 2)
        self.doc.commit(before)
        self.layers_changed()
        self.refresh_tabs()
        return True

    def note_recent(self, path):
        self.recent_files.add(path)
        if getattr(self, "home", None) is not None:
            self.home.refresh_recent()

    def apply_canvas_spec(self, spec):
        apply_spec_to_document(self.doc, spec, self.doc.matte)
        self.cursor = (min(self.cursor[0], self.doc.width - 1),
                       min(self.cursor[1], self.doc.height - 1))
        self.sel_backup = None
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def open_document(self):
        path = filedialog.askopenfilename(parent=self, title="Open image",
                                          filetypes=formats.OPEN_FILETYPES)
        if not path:
            return
        self.load_path(path)

    def open_project(self):
        path = filedialog.askopenfilename(parent=self, title="Open project",
                                          filetypes=formats.PROJECT_FILETYPES)
        if not path:
            return
        self.load_path(path)

    def load_path(self, path):
        if formats.is_project_path(path):
            return self.load_project_path(path)
        try:
            bitmap, ppi = formats.load_document(path)
        except (OSError, HexError) as exc:
            self.report_error(str(exc))
            return False
        self.adopt_document(Document(bitmap.width, bitmap.height, bitmap.data,
                                     path=path, ppi=ppi))
        self.cursor = (0, 0)
        self._set_default_export_path()
        self.zoom_fit()
        self.refresh_layers()
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()
        self.note_recent(path)
        self.show_editor()
        return True

    def project_extras(self):
        """Editor state stored alongside the layers in a .h2p project."""
        extras = {
            "foreground": list(self.fg),
            "background": list(self.bg),
            "palette": [list(color) for color in self.palette],
            "pattern_size": int(self.pattern_size),
            "pattern": [None if color is None else list(color)
                        for row in self.pattern_cells for color in row],
            "symmetry": self.symmetry,
            "view": self.view_mode,
        }
        palette_name = getattr(self, "palette_var", None)
        if palette_name is not None:
            try:
                extras["palette_name"] = str(palette_name.get())
            except tk.TclError:
                pass
        return extras

    def apply_project_extras(self, extras):
        """Restore palette, pattern, colours, mirror and view from a project."""
        if not extras:
            return
        foreground = extras.get("foreground")
        if isinstance(foreground, (list, tuple)) and len(foreground) == 4:
            self.set_fg(tuple(int(v) for v in foreground))
        background = extras.get("background")
        if isinstance(background, (list, tuple)) and len(background) == 4:
            self.bg = tuple(int(v) for v in background)
        palette = extras.get("palette")
        if isinstance(palette, list) and palette:
            colors = []
            for color in palette:
                if isinstance(color, (list, tuple)) and len(color) in (3, 4):
                    colors.append(tuple(_bounded(int(v), 0, 255, 0)
                                        for v in color))
            if colors:
                self.palette = colors
                self.draw_palette()
        palette_name = getattr(self, "palette_var", None)
        if palette_name is not None and extras.get("palette_name"):
            try:
                palette_name.set(str(extras["palette_name"]))
            except tk.TclError:
                pass
        size = extras.get("pattern_size")
        pattern = extras.get("pattern")
        if isinstance(size, int) and 1 <= size <= 16 and isinstance(pattern, list):
            self.set_pattern_size(size)
            cells = [[None] * size for _ in range(size)]
            for index, color in enumerate(pattern[:size * size]):
                if isinstance(color, (list, tuple)) and len(color) == 4:
                    cells[index // size][index % size] = tuple(
                        _bounded(int(v), 0, 255, 0) for v in color)
            self.pattern_cells = cells
            if hasattr(self, "pattern_size_var"):
                self.pattern_size_var.set(size)
            self.draw_pattern_grid()
        symmetry = extras.get("symmetry")
        if isinstance(symmetry, str) and symmetry in SYMMETRY_LABELS:
            self.symmetry = symmetry
            self.symmetry_option_var.set(SYMMETRY_LABELS[symmetry])
        view = extras.get("view")
        if view in ("hex", "image"):
            self.view_mode = view
            if hasattr(self, "view_mode_var"):
                self.view_mode_var.set(view)

    def load_project_path(self, path):
        try:
            project = formats.load_project(path)
        except (OSError, HexError) as exc:
            self.report_error(str(exc))
            return False
        self.adopt_document(project.document)
        self.cursor = (0, 0)
        self.apply_project_extras(project.extras)
        self._set_default_export_path()
        self.zoom_fit()
        self.refresh_layers()
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()
        self.note_recent(path)
        self.show_editor()
        return True

    def mark_saved(self):
        """A successful save clears the unsaved marker for this tab."""
        self.doc.dirty = False
        self.refresh_tabs()
        self.refresh_status()
        self.refresh_export_panel()
        if getattr(self, "export_info", None) is not None and self.doc.path:
            self.export_info.set("saved %s" % os.path.basename(self.doc.path))
        return True

    def save_project(self):
        if not formats.is_project_path(self.doc.path or ""):
            return self.save_project_as()
        try:
            formats.save_project(self.doc.path, self.doc,
                                 self.project_extras())
        except (OSError, HexError) as exc:
            self.report_error(str(exc))
            return False
        self.note_recent(self.doc.path)
        return self.mark_saved()

    def save_project_as(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save project",
            defaultextension=formats.PROJECT_EXT,
            filetypes=formats.PROJECT_FILETYPES)
        if not path:
            return
        if not formats.is_project_path(path):
            path += formats.PROJECT_EXT
        try:
            formats.save_project(path, self.doc, self.project_extras())
        except (OSError, HexError) as exc:
            self.report_error(str(exc))
            return False
        self.doc.path = path
        self.note_recent(path)
        return self.mark_saved()

    def save_document(self):
        if formats.is_project_path(self.doc.path or ""):
            return self.save_project()
        if not self.doc.path:
            return self.save_document_as()
        key = formats.format_for_path(self.doc.path)
        try:
            formats.save(self.doc.flatten(), self.doc.path, key, self.doc.matte,
                         ppi=self.doc.ppi)
        except (OSError, HexError) as exc:
            self.report_error(str(exc))
            return False
        self.note_recent(self.doc.path)
        return self.mark_saved()

    def save_document_as(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save image", defaultextension=".png",
            filetypes=formats.SAVE_FILETYPES)
        if not path:
            return False
        if formats.is_project_path(path):
            try:
                formats.save_project(path, self.doc, self.project_extras())
            except (OSError, HexError) as exc:
                self.report_error(str(exc))
                return False
            self.doc.path = path
            self.note_recent(path)
            return self.mark_saved()
        key = formats.format_for_path(path)
        try:
            formats.save(self.doc.flatten(), path, key, self.doc.matte,
                         ppi=self.doc.ppi)
        except (OSError, HexError) as exc:
            self.report_error(str(exc))
            return False
        self.doc.path = path
        self.note_recent(path)
        return self.mark_saved()

    # ------------------------------------------------------------------ edits
    def undo(self):
        if self.doc.undo_last():
            self.refresh_layers()
            self.refresh_bytes()
            self.refresh_status()
            self.redraw()

    def redo(self):
        if self.doc.redo_last():
            self.refresh_layers()
            self.refresh_bytes()
            self.refresh_status()
            self.redraw()

    def select_all(self):
        self.doc.selection.select_all()
        self.refresh_bytes()
        self.redraw()

    def deselect(self):
        self.doc.selection.clear()
        self.refresh_bytes()
        self.redraw()

    def invert_selection(self):
        self.doc.selection.invert()
        self.refresh_bytes()
        self.redraw()

    def clear_selection(self):
        before = self.doc.begin()
        clear_region(self.doc.bitmap, self.doc.mask())
        self.doc.commit(before)
        self.refresh_bytes()
        self.redraw()

    def invert_colors_action(self):
        before = self.doc.begin()
        invert_colors(self.doc.bitmap, self.doc.mask())
        self.doc.commit(before)
        self.refresh_bytes()
        self.redraw()

    # ------------------------------------------------------- dither and arts
    def dither_mode(self):
        return dither.DITHER_LABELS.get(str(self.dither_mode_var.get()),
                                        "bayer4")

    def gradient_dither_mode(self):
        return dither.DITHER_LABELS.get(str(self.gradient_dither_var.get()),
                                        "off")

    def gradient_pattern(self, mode):
        return self.pattern_block() if mode == "pattern" else None

    def dither_options(self):
        """The Bias/Strength/Scale/Serpentine settings shared with Gradient."""
        return {
            "scale": _bounded(self.dither_scale_var.get(), 1, 16, 1),
            "bias": _bounded(self.dither_bias_var.get(), -100, 100, 0),
            "strength": _bounded(self.dither_strength_var.get(), 0, 100, 100),
            "serpentine": bool(self.dither_serpentine_var.get()),
        }

    def apply_dither(self):
        mode = self.dither_mode()
        before = self.doc.begin()
        painted = dither.dither_bitmap(
            self.doc.bitmap, self.doc.mask(), self.fg, self.bg, mode,
            self.gradient_pattern(mode), **self.dither_options())
        self.finish_bulk_edit(before, painted)

    def finish_bulk_edit(self, before, changed):
        """Commit one whole-canvas edit and refresh the views."""
        if changed:
            self.doc.commit(before)
        self._mark_dirty(0, 0, self.doc.width - 1, self.doc.height - 1)
        self.refresh_bytes()
        self.redraw()

    def run_filter(self, function, *args):
        before = self.doc.begin()
        changed = function(self.doc.bitmap, self.doc.mask(), *args)
        self.finish_bulk_edit(before, changed)

    def apply_grayscale(self):
        self.run_filter(filters.grayscale)

    def apply_posterize(self):
        self.run_filter(filters.posterize,
                        _bounded(self.posterize_var.get(), 2, 32, 4))

    def apply_threshold(self):
        self.run_filter(filters.threshold,
                        _bounded(self.filter_threshold_var.get(), 0, 255,
                                 128))

    def apply_brightness(self):
        self.run_filter(filters.brightness,
                        _bounded(self.filter_brightness_var.get(), -100, 100,
                                 25))

    def apply_contrast(self):
        self.run_filter(filters.contrast,
                        _bounded(self.filter_contrast_var.get(), -100, 100,
                                 25))

    def apply_saturation(self):
        self.run_filter(filters.saturate,
                        _bounded(self.filter_saturation_var.get(), -100, 100,
                                 30))

    def apply_hue(self):
        self.run_filter(filters.hue_shift,
                        _bounded(self.filter_hue_var.get(), -180, 180, 30))

    def apply_noise(self):
        self.run_filter(filters.noise,
                        _bounded(self.filter_noise_var.get(), 0, 100, 12))

    def apply_sepia(self):
        self.run_filter(filters.sepia)

    def apply_edges(self):
        self.run_filter(filters.edges)

    # ------------------------------------------------------------------ layers
    def layer_row_text(self, index, layer):
        text = "%s %d  %s  %s" % (
            "\u25cf" if layer.visible else "\u25cb", index + 1,
            layer.name, LAYER_OP_LABELS.get(layer.op, layer.op))
        if layer.alpha < 255:
            text += "  %d%%" % int(round(layer.alpha * 100 / 255.0))
        if not layer.visible:
            text += "  (hidden)"
        return text

    def refresh_layers(self):
        if not hasattr(self, "layer_list"):
            return
        self.layer_list.delete(0, "end")
        for index in range(self.doc.layer_count - 1, -1, -1):
            self.layer_list.insert("end", self.layer_row_text(
                index, self.doc.layers[index]))
        row = self.doc.layer_count - 1 - self.doc.layer_index
        self.layer_list.selection_clear(0, "end")
        self.layer_list.selection_set(row)
        self.layer_list.see(row)
        layer = self.doc.active_layer
        self.layer_op_var.set(LAYER_OP_LABELS.get(layer.op, "Normal"))
        self.layer_visible_var.set(bool(layer.visible))
        self.sync_layer_alpha(layer)
        self.layer_info.set("%d layer%s, editing %s"
                            % (self.doc.layer_count,
                               "" if self.doc.layer_count == 1 else "s",
                               layer.name))

    def layers_changed(self):
        self.refresh_layers()
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def on_layer_select(self, event=None):
        picked = self.layer_list.curselection()
        if not picked:
            return
        index = self.doc.layer_count - 1 - picked[0]
        if index == self.doc.layer_index:
            return
        self.doc.layer_index = index
        self.layers_changed()

    def on_layer_toggle(self, event=None):
        """Double-click a row: show or hide that layer, whichever it is."""
        row = self.layer_list.nearest(event.y) if event is not None else -1
        if row < 0 or row >= self.layer_list.size():
            return
        index = self.doc.layer_count - 1 - row
        if index < 0 or index >= self.doc.layer_count:
            return
        layer = self.doc.layers[index]
        before = self.doc.begin()
        layer.visible = not layer.visible
        self.doc.commit(before)
        self.layers_changed()

    def on_layer_op(self, event=None):
        label = self.layer_op_var.get()
        for op, text in LAYER_OP_LABELS.items():
            if text == label:
                self.set_layer_op(op)
                return

    def set_layer_op(self, op):
        if not op or op == self.doc.active_layer.op:
            return
        before = self.doc.begin()
        self.doc.active_layer.op = op
        self.doc.commit(before)
        self.layers_changed()

    def toggle_layer_visible(self):
        before = self.doc.begin()
        self.doc.active_layer.visible = bool(self.layer_visible_var.get())
        self.doc.commit(before)
        self.layers_changed()

    def sync_layer_alpha(self, layer):
        """Point the opacity slider at a layer without firing its command."""
        if not hasattr(self, "layer_alpha_var"):
            return
        percent = layer.alpha * 100.0 / 255.0
        self._layer_alpha_syncing = True
        try:
            self.layer_alpha_var.set(percent)
            self.layer_alpha_label.config(text="%d%%" % int(round(percent)))
        finally:
            self._layer_alpha_syncing = False

    def begin_layer_alpha(self, event=None):
        self.layer_alpha_before = self.doc.begin()
        self.layer_alpha_dirty = False

    def on_layer_alpha(self, value=None):
        """Live opacity: drag the slider and the composite follows."""
        if self._layer_alpha_syncing or not hasattr(self, "view"):
            return
        try:
            percent = float(self.layer_alpha_var.get() if value is None
                            else value)
        except (tk.TclError, TypeError, ValueError):
            return
        percent = max(0.0, min(100.0, percent))
        if self.layer_alpha_before is None:
            self.layer_alpha_before = self.doc.begin()
            self.layer_alpha_dirty = False
        layer = self.doc.active_layer
        alpha = int(round(percent * 255 / 100.0))
        self.layer_alpha_label.config(text="%d%%" % int(round(percent)))
        if layer.alpha == alpha:
            return
        layer.alpha = alpha
        self.layer_alpha_dirty = True
        row = self.doc.layer_count - 1 - self.doc.layer_index
        self.layer_list.delete(row)
        self.layer_list.insert(row, self.layer_row_text(self.doc.layer_index,
                                                        layer))
        self.layer_list.selection_clear(0, "end")
        self.layer_list.selection_set(row)
        self.refresh_status()
        self.redraw()

    def end_layer_alpha(self, event=None):
        if self.layer_alpha_before is not None and self.layer_alpha_dirty:
            self.doc.commit(self.layer_alpha_before)
        self.layer_alpha_before = None
        self.layer_alpha_dirty = False

    def new_layer(self):
        before = self.doc.begin()
        self.doc.add_layer()
        self.doc.commit(before)
        self.layers_changed()

    def duplicate_layer(self):
        before = self.doc.begin()
        self.doc.duplicate_layer()
        self.doc.commit(before)
        self.layers_changed()

    def delete_layer(self):
        if self.doc.layer_count <= 1:
            self.report_error("A document needs at least one layer.")
            return
        before = self.doc.begin()
        self.doc.remove_layer()
        self.doc.commit(before)
        self.layers_changed()

    def move_layer(self, delta):
        before = self.doc.begin()
        if not self.doc.move_layer(delta):
            return
        self.doc.commit(before)
        self.layers_changed()

    def move_layer_to(self, target):
        """Restack the active layer at `target`; used by dragging a row."""
        before = self.doc.begin()
        if not self.doc.reorder_layer(target):
            return
        self.doc.commit(before)
        self.layers_changed()

    def begin_layer_drag(self, event=None):
        """Remember the row a drag starts on; the drop decides the restack."""
        self._layer_drag_row = (self.layer_list.nearest(event.y)
                                if event is not None else None)

    def end_layer_drag(self, event=None):
        """Drop a dragged row onto another row to move that layer there."""
        row = getattr(self, "_layer_drag_row", None)
        self._layer_drag_row = None
        if event is None or not row or row < 0:
            return
        if event.y < 0 or event.y > self.layer_list.winfo_height():
            return
        target_row = self.layer_list.nearest(event.y)
        if target_row < 0 or target_row == row:
            return
        self.move_layer_to(self.doc.layer_count - 1 - target_row)

    def flip_image(self, horizontal):
        before = self.doc.begin()
        self.doc.transform_all(flip_horizontal if horizontal
                               else flip_vertical)
        self.doc.commit(before)
        self.doc.selection.clear()
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    def rotate_image(self, clockwise):
        before = self.doc.begin()
        self.doc.transform_all(lambda bitmap: rotate90(bitmap, clockwise))
        self.doc.commit(before)
        self.doc.selection = type(self.doc.selection)(
            self.doc.width, self.doc.height)
        self.cursor = (min(self.cursor[0], self.doc.width - 1),
                       min(self.cursor[1], self.doc.height - 1))
        self.refresh_bytes()
        self.refresh_status()
        self.redraw()

    # ------------------------------------------------------------------ status
    def refresh_status(self):
        x, y = self.cursor
        color = self.doc.bitmap.rgba(x, y)
        if self.tabs and 0 <= self.tab_index < len(self.tabs):
            name = self.tab_title(self.tab_index)
        else:
            name = (os.path.basename(self.doc.path) if self.doc.path
                    else "untitled")
        self.title("hex2pix \u2014 %s" % name)
        selection = self.doc.selection
        selected = ("%d px / %d B" % (selection.count(), selection.bytes_count())
                    if selection.active else "no selection")
        typed = ("   hex[%s]" % self.typed) if self.typed else ""
        canvas = ("%dx%d @ %g ppi %s%s"
                  % (self.doc.width, self.doc.height, self.doc.ppi,
                     self.doc.color_mode,
                     "+alpha" if self.doc.alpha else ""))
        view = (("hex view, font %d" % self.hex_font)
                if self.view_mode == "hex"
                else ("pixel view %s" % self.zoom_label()))
        if self.view_mode == "hex" and self.doc.layer_count > 1:
            view = "hex view (result), font %d" % self.hex_font
        layer = self.doc.active_layer
        opacity = ("" if layer.alpha >= 255
                   else " %d%%" % int(round(layer.alpha * 100 / 255.0)))
        mode = "L%d/%d %s%s%s  |  " % (
            self.doc.layer_index + 1, self.doc.layer_count,
            LAYER_OP_LABELS.get(layer.op, layer.op), opacity,
            "" if layer.visible else " (hidden)")
        if self.playing:
            mode = "PLAY %d/%d  |  " % (self.play_index,
                                        len(self.play_steps)) + mode
        elif self.recorder.recording:
            mode = "REC %d  |  " % len(self.recorder.macro.steps) + mode
        elif self.kb_anchor is not None:
            mode = "corner %d,%d  |  " % self.kb_anchor + mode
        if self.tool == "polygon" and self.poly_points:
            mode += "poly %d vertices  |  " % len(self.poly_points)
        if self.tool == "transform" and self.transform_box is not None:
            what = self.transform_mode or "move"
            if what == "rotate":
                what += " %d deg" % int(round(self.transform_angle))
            mode += "%s %dx%d  |  " % (what, self.transform_box[2],
                                       self.transform_box[3])
        self.status.set(
            mode + "%s  |  %s  |  %d,%d %s  |  %s  |  %s  brush %d  tol %d  "
            "|  %s%s"
            % (name, canvas, x, y, hex_str(*color), self.tool, view,
               self.brush, self.tolerance, selected, typed))

    def report_error(self, message):
        if self.silent:
            return
        messagebox.showerror("hex2pix", message, parent=self)

    # ------------------------------------------------------------ integrations
    def _start_integrations(self):
        if self._closing or self.silent:
            return
        if (self.settings.get("check_updates")
                and bool(getattr(sys, "frozen", False))):
            self.after(2500, self._auto_check_updates)

    def _auto_check_updates(self):
        if (not self._closing and self.settings.get("check_updates")
                and bool(getattr(sys, "frozen", False))):
            self.check_for_updates(False)

    def _update_auto_changed(self):
        self.settings.set("check_updates", bool(self.update_auto_var.get()))

    def _ask_update_choice(self, release):
        """Per-update opt-in: install this release, skip it, or ask later."""
        background = self.colors["bg"]
        foreground = self.colors.get("fg") or self.colors.get("text")
        dialog = tk.Toplevel(self)
        dialog.title("Update available")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.configure(bg=background)
        body = tk.Frame(dialog, bg=background, padx=18, pady=16)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="hex2pix %s is available." % release.version,
                 bg=background, fg=foreground,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(body,
                 text="You have %s. Download and install this update?"
                 % __version__, bg=background, fg=foreground,
                 wraplength=440, justify="left",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 8))
        notes = str(release.notes or "").strip()
        if notes:
            if len(notes) > 600:
                notes = notes[:600].rstrip() + "..."
            tk.Label(body, text=notes, bg=background,
                     fg=self.colors.get("muted") or foreground,
                     wraplength=440, justify="left",
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 12))
        buttons = tk.Frame(body, bg=background)
        buttons.pack(fill="x", pady=(10, 0))
        choice = {"value": "later"}

        def pick(value):
            choice["value"] = value
            dialog.destroy()

        ttk.Button(buttons, text="Download and install",
                   command=lambda: pick("install")).pack(side="left")
        ttk.Button(buttons, text="Skip this version",
                   command=lambda: pick("skip")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Later",
                   command=lambda: pick("later")).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", lambda: pick("later"))
        dialog.update_idletasks()
        dialog.geometry("+%d+%d" % (self.winfo_rootx() + 120,
                                    self.winfo_rooty() + 140))
        dialog.grab_set()
        self.wait_window(dialog)
        return choice["value"]

    def check_for_updates(self, manual=True):
        if self._closing:
            return
        if self._update_busy:
            if manual and not self.silent:
                messagebox.showinfo("Updates",
                                    "An update check is already running.",
                                    parent=self)
            return
        self._update_busy = True
        self._update_thread = threading.Thread(
            target=self._check_update_worker, args=(bool(manual),),
            name="hex2pix-update-check", daemon=True)
        self._update_thread.start()
        self.after(100, self._poll_update_results)

    def _check_update_worker(self, manual):
        try:
            release = check_for_update(__version__, GITHUB_REPOSITORY)
        except Exception as exc:  # noqa: BLE001 - shown by the UI thread
            self._update_results.put(("check", manual, None, str(exc), None))
        else:
            self._update_results.put(("check", manual, release, None, None))

    def _poll_update_results(self):
        if self._closing:
            return
        while True:
            try:
                result = self._update_results.get_nowait()
            except queue.Empty:
                break
            self._handle_update_result(result)
        if self._update_busy and not self._closing:
            self.after(100, self._poll_update_results)

    def _handle_update_result(self, result):
        kind, manual, release, error, path = result
        self._update_busy = False
        if error:
            if manual and not self.silent:
                messagebox.showerror("Updates", error, parent=self)
            return
        if kind == "check":
            if release is None:
                if manual and not self.silent:
                    messagebox.showinfo(
                        "Updates", "hex2pix %s is up to date." % __version__,
                        parent=self)
                return
            if not bool(getattr(sys, "frozen", False)):
                if manual and not self.silent:
                    if messagebox.askyesno(
                            "Update available",
                            "hex2pix %s is available.\n\n"
                            "Automatic installation works in the packaged "
                            "Windows build. Open the release page?"
                            % release.version, parent=self):
                        webbrowser.open(release.page_url)
                return
            if self.silent:
                return
            if is_folder_build():
                # A folder install is replaced by unzipping the new portable
                # build, not by swapping in the single-file executable.
                if messagebox.askyesno(
                        "Update available",
                        "hex2pix %s is available.\n\n"
                        "This is the portable folder build: download the new "
                        "hex2pix-portable.zip and unzip it over this folder.\n\n"
                        "Open the release page?" % release.version,
                        parent=self):
                    webbrowser.open(release.page_url)
                return
            if (not manual
                    and self.settings.get("skipped_version", "")
                    == release.version):
                return
            choice = self._ask_update_choice(release)
            if choice == "install":
                self._download_update(release)
            elif choice == "skip":
                self.settings.set("skipped_version", release.version)
            return
        if kind == "download":
            if error:
                if not self.silent:
                    messagebox.showerror("Update failed", error, parent=self)
                return
            if self.silent:
                return
            try:
                apply_update(path, sys.executable, os.getpid())
            except UpdateError as exc:
                messagebox.showerror("Update failed", str(exc), parent=self)
                return
            self.destroy()

    def _download_update(self, release):
        if self._update_busy:
            return
        self._update_busy = True
        self._update_thread = threading.Thread(
            target=self._download_update_worker, args=(release,),
            name="hex2pix-update-download", daemon=True)
        self._update_thread.start()

    def _download_update_worker(self, release):
        try:
            path = download_update(release)
        except Exception as exc:  # noqa: BLE001 - shown by the UI thread
            self._update_results.put(("download", True, release, str(exc),
                                      None))
        else:
            self._update_results.put(("download", True, release, None, path))

    def destroy(self):
        if getattr(self, "_destroyed", False):
            return
        self._destroyed = True
        self._closing = True
        try:
            super().destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------- help
    def show_help(self):
        messagebox.showinfo(
            "Keyboard reference",
            "Arrows          move the pixel cursor\n"
            "Shift+arrows    stretch a selection from the cursor\n"
            "Ctrl+arrows     move eight pixels at once\n"
            "Enter           close a polygon (Shift+Enter adds a vertex),\n"
            "                drop/close a selection corner (rect, ellipse),\n"
            "                fill at the cursor (fill, pattern, structure),\n"
            "                otherwise paint the typed color\n"
            "Escape          cancel the rubber band and the typed digits\n"
            "0-9 A-F         type a hex color (up to 8 digits)\n"
            "Backspace       delete a typed digit\n"
            "G               pick the color under the cursor\n"
            "H               switch between pixel and hex views\n"
            "Del             erase the pixel under the cursor\n"
            "+ / -           brush size\n"
            "[ / ]           zoom out / in\n"
            "Ctrl+T / Ctrl+W open / close a canvas tab\n"
            "Ctrl+Tab        next canvas tab\n"
            "Space           swap foreground and background\n"
            "Shift+A..Z      pick a tool (Shift+P pencil, Shift+W wand)\n"
            "Shift+N/R/U/V   line, rectangle, ellipse, gradient\n"
            "Shift+H         hand: drag to pan the canvas anywhere\n"
            "Shift+B         blur: soften the pixels under the brush\n"
            "Shift+J         shuffle: mix, scatter or noise the pixels\n"
            "Shift+X         text: click to stamp the Text option\n"
            "Shift+A         spray can: hold to spray the brush footprint;\n"
            "                Blur softens the spray edge, Multicolor uses\n"
            "                the palette\n"
            "Shift+C         curve: Bend, Vectors (1-8) and Dir make an arc\n"
            "                or a wave (Dir Alternate flips each vector)\n"
            "Shift+G         contour: outline the selection or the artwork\n"
            "Dither tab      Bayer, Floyd-Steinberg, Pattern, Noise or Lines\n"
            "                dither to FG and BG; Bias, Strength, Scale and\n"
            "                Serpentine tune it. Filters: Grayscale, Posterize,\n"
            "                Threshold, Brightness, Contrast, Saturation,\n"
            "                Hue, Noise, Sepia and Find edges\n"
            "Gradient        the Dither option bands a gradient to FG and BG\n"
            "Projects        Save project as .h2p keeps every layer, the\n"
            "                selection, palette, pattern and view; Open\n"
            "                loads them back, and Ctrl+S saves that format\n"
            "                once the document path is a .h2p file\n"
            "Wheel           smooth zoom (1:(side x 50,000) to 6400%)\n"
            "Colour wheel    click sets FG, right-click sets BG\n"
            "Palette         New/Set/Del build one; Map quantizes to it\n"
            "Dark mode       View > Dark mode; on by default\n"
            "Dockers         View menu hides or shows right-side tabs\n"
            "Help menu       GitHub update checks\n"
            "Shift+T         move / scale / rotate selection: drag to move,\n"
            "                Shift+drag scales, Ctrl+drag rotates in 15 deg\n"
            "                steps, Enter moves it to the cursor\n"
            "Filled shapes   checkbox in the left panel\n"
            "Mirror          X / Y / XY mirrors brush, shapes and erasing\n"
            "Tool options    above the canvas follow the active tool\n"
            "Ctrl+Z / Ctrl+Y undo / redo\n"
            "Ctrl+A          select all\n"
            "Ctrl+D          deselect (Escape does it at once, too)\n"
            "Delete          clear the pixels inside the selection\n"
            "Escape          cancel everything and deselect at once\n"
            "Ctrl+C / Ctrl+V copy / paste hex block text\n"
            "Ctrl+O / Ctrl+S open / save\n"
            "Ctrl+E          save / export docker\n"
            "Ctrl+Shift+N    new layer (Normal operation)\n"
            "Alt+N / Alt+C   new layer / copy layer\n"
            "Alt+Delete      delete the active layer\n"
            "Alt+Up/Alt+Down restack the active layer\n"
            "Layers tab      Normal / Add / Subtract / Multiply / Divide,\n"
            "                New / Copy / Delete / Raise / Lower; the\n"
            "                Opacity slider blends the active layer;\n"
            "                drag a row to restack, double-click shows/hides\n"
            "F9              record the keys you press\n"
            "F5              play the recording back (Escape stops)\n"
            "F11             toggle fullscreen (Escape leaves it)\n"
            "\n"
            "Mouse: left draws, right erases or subtracts, middle drags "
            "the view, wheel zooms. Shift+wheel pans sideways, "
            "Ctrl+wheel changes the brush, double-click middle fits the "
            "artwork. Shift snaps Line to 45 degrees and Rectangle/Ellipse "
            "to squares/circles. Shift adds to a selection, Ctrl subtracts, "
            "Alt intersects.", parent=self)

    def show_selection_help(self):
        messagebox.showinfo(
            "Byte selections",
            "Selections are byte masks: one byte per pixel decides whether "
            "that pixel's 4 RGBA bytes take part in an edit.\n\n"
            "Rect, Ellipse, Polygon, Lasso, Wand and Similar build those "
            "masks. Wand follows pixel data that matches the clicked pixel "
            "(4-connected); Similar takes every matching pixel in the image. "
            "The tolerance in the left panel loosens the comparison.\n\n"
            "Bytes tab: Add/Subtract/Multiply/Xor/Nand run per channel on the "
            "selected bytes, with strength and clamp/wrap. The Dump tab shows "
            "the selected bytes as offsets and hex.", parent=self)

    # ------------------------------------------------------------------ keys
    def on_key(self, event):
        focus = self.focus_get()
        if not self._dispatching and isinstance(
                focus, (tk.Text, tk.Entry, ttk.Entry, ttk.Combobox,
                        tk.Spinbox, ttk.Spinbox)):
            if event.keysym == "Escape":
                self.view.focus_set()
            return None
        if self.home_visible:
            return None
        if self.playing and not self._dispatching:
            if event.keysym == "Escape":
                self.stop_playback()
            return "break"

        control = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)
        key = event.keysym
        char = (event.char or "").lower()

        if not self.playing and self.recorder.recording:
            if self.recorder.capture(key, event.char or "", int(event.state)):
                self.refresh_macro_status()

        if bool(event.state & 0x0008):
            layer_actions = {"Up": lambda: self.move_layer(1),
                             "Down": lambda: self.move_layer(-1),
                             "n": self.new_layer,
                             "c": self.duplicate_layer,
                             "Delete": self.delete_layer}
            action = layer_actions.get(key) or layer_actions.get(char)
            if action:
                action()
                return "break"
        if key in ("Up", "Down", "Left", "Right"):
            jump = 8 if control else 1
            moves = {"Up": (0, -jump), "Down": (0, jump),
                     "Left": (-jump, 0), "Right": (jump, 0)}
            if shift and self.kb_anchor is None:
                self.kb_before = self.doc.selection.snapshot()
                self.kb_anchor = self.cursor
            self.move_cursor(*moves[key])
            return "break"
        if key == "F9":
            self.toggle_recording()
            return "break"
        if key == "F5":
            self.play_macro()
            return "break"
        if key == "F11":
            self.toggle_fullscreen()
            return "break"

        if control:
            if key == "Tab":
                self.next_tab(-1 if shift else 1)
                return "break"
            if shift and char == "n":
                self.new_layer()
                return "break"
            actions = {
                "z": self.undo, "y": self.redo, "a": self.select_all,
                "d": self.deselect, "c": self.copy_block,
                "v": self.paste_block, "o": self.open_document,
                "s": self.save_document, "e": self.focus_export,
                "n": self.new_document, "t": self.new_document,
                "w": self.close_tab,
            }
            action = actions.get(char if char.isalpha() else key.lower())
            if action:
                action()
                return "break"
            return None

        if key in ("Return", "KP_Enter"):
            if self.typed:
                self.commit_typed()
            elif shift and self.tool == "polygon":
                self.keyboard_polygon(True)
                self.refresh_status()
                self.redraw()
            else:
                self.keyboard_activate()
            return "break"
        if key == "BackSpace":
            self.typed = self.typed[:-1]
            self.refresh_status()
            return "break"
        if key == "Escape":
            if self.fullscreen:
                self.set_fullscreen(False)
            self.cancel_shape()
            self.cancel_gradient()
            self.cancel_transform()
            self.typed = ""
            self.poly_points = []
            self.lasso_points = []
            self.sel_backup = None
            self.keyboard_cancel()
            self.refresh_status()
            # Escape is the instant deselect: it cancels whatever is in
            # progress and drops the selection in the same press.
            self.deselect()
            return "break"
        if key == "Delete":
            if self.doc.selection.active:
                self.clear_selection()
            else:
                self.erase_at_cursor()
            return "break"
        if key in ("plus", "equal", "KP_Add"):
            self.brush = min(64, self.brush + 1)
            self.brush_var.set(self.brush)
            self.refresh_status()
            self.request_redraw()
            return "break"
        if key in ("minus", "KP_Subtract"):
            self.brush = max(1, self.brush - 1)
            self.brush_var.set(self.brush)
            self.refresh_status()
            self.request_redraw()
            return "break"
        if key == "bracketleft":
            self.zoom_step(-1)
            return "break"
        if key == "bracketright":
            self.zoom_step(1)
            return "break"
        if char == "h" and not shift:
            self.toggle_view()
            return "break"
        if key == "space":
            self.swap_colors()
            return "break"
        if char == "g" and not shift:
            self.pick_at(*self.cursor)
            return "break"
        if shift and char in TOOL_BY_KEY:
            self.set_tool(TOOL_BY_KEY[char])
            return "break"
        if char in HEX_CHARS:
            if len(self.typed) >= 8:
                self.typed = ""
            self.typed += char
            self.refresh_status()
            return "break"
        return None


FILL_CHOICES = ("Transparent", "White", "Black", "Foreground", "Background")
PRESETS = (
    ("Custom", None),
    ("Icon 16 x 16", (16, 16)),
    ("Icon 32 x 32", (32, 32)),
    ("Sprite 64 x 64", (64, 64)),
    ("Sprite 128 x 128", (128, 128)),
    ("Tile 256 x 256", (256, 256)),
    ("Screen 640 x 480", (640, 480)),
    ("HD 1280 x 720", (1280, 720)),
    ("Full HD 1920 x 1080", (1920, 1080)),
)


class CanvasDialog(tk.Toplevel):
    """Collects a CanvasSpec for a new canvas or for changing the current one.

    Modelled on GIMP's New Image dialog: size with a unit, resolution,
    color model, alpha and a fill, plus (for the open image) whether a size
    change pads/crops the canvas or resamples the artwork, with an anchor.
    """

    def __init__(self, parent, document=None, spec=None, mode="new",
                 title="New Canvas"):
        super().__init__(parent)
        self.parent = parent
        self.document = document
        self.mode = mode
        self.result = None
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(padx=10, pady=10)

        base = spec or CanvasSpec(
            document.width if document else 64,
            document.height if document else 64,
            document.ppi if document else 72.0,
            document.color_mode if document else "RGB",
            document.alpha if document else True)
        self.spec = base
        self.original = (max(1, base.width), max(1, base.height))
        self.anchor = base.anchor
        self._unit = "px"
        self.width_px = float(base.width)
        self.height_px = float(base.height)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        row = 0

        if mode == "new":
            ttk.Label(body, text="Preset").grid(row=row, column=0, sticky="w")
            self.preset_var = tk.StringVar(value=PRESETS[0][0])
            preset = ttk.Combobox(body, textvariable=self.preset_var,
                                  values=[name for name, _ in PRESETS],
                                  state="readonly", width=18)
            preset.grid(row=row, column=1, columnspan=3, sticky="ew", pady=2)
            preset.bind("<<ComboboxSelected>>", self._preset_changed)
            row += 1

        ttk.Label(body, text="Width").grid(row=row, column=0, sticky="w")
        self.width_var = tk.StringVar()
        ttk.Spinbox(body, textvariable=self.width_var, from_=0, to=100000,
                    width=10).grid(row=row, column=1, sticky="w", pady=2)
        self.unit_var = tk.StringVar(value="px")
        unit = ttk.Combobox(body, textvariable=self.unit_var, values=UNITS,
                            state="readonly", width=4)
        unit.grid(row=row, column=2, sticky="w")
        unit.bind("<<ComboboxSelected>>", self._unit_changed)
        row += 1

        ttk.Label(body, text="Height").grid(row=row, column=0, sticky="w")
        self.height_var = tk.StringVar()
        ttk.Spinbox(body, textvariable=self.height_var, from_=0, to=100000,
                    width=10).grid(row=row, column=1, sticky="w", pady=2)
        self.lock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="keep ratio", variable=self.lock_var).grid(
            row=row, column=2, columnspan=2, sticky="w")
        row += 1

        ttk.Label(body, text="Resolution").grid(row=row, column=0, sticky="w")
        self.ppi_var = tk.DoubleVar(value=base.ppi)
        ttk.Spinbox(body, textvariable=self.ppi_var, from_=1, to=2400,
                    width=10).grid(row=row, column=1, sticky="w", pady=2)
        ttk.Label(body, text="ppi").grid(row=row, column=2, sticky="w")
        row += 1

        self.physical_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.physical_var, foreground="#777").grid(
            row=row, column=0, columnspan=4, sticky="w")
        row += 1

        ttk.Label(body, text="Color model").grid(row=row, column=0, sticky="w")
        self.model_var = tk.StringVar(
            value="Grayscale" if base.color_mode == "Grayscale" else "RGB color")
        ttk.Combobox(body, textvariable=self.model_var,
                     values=("RGB color", "Grayscale"), state="readonly",
                     width=12).grid(row=row, column=1, sticky="w", pady=2)
        ttk.Label(body, text="8-bit integer").grid(row=row, column=2,
                                                   columnspan=2, sticky="w")
        row += 1

        self.alpha_var = tk.BooleanVar(value=base.alpha)
        ttk.Checkbutton(body, text="Alpha channel",
                        variable=self.alpha_var).grid(
            row=row, column=0, columnspan=2, sticky="w")
        row += 1

        if mode == "new":
            ttk.Label(body, text="Fill with").grid(row=row, column=0, sticky="w")
            self.fill_var = tk.StringVar(value="Transparent")
            ttk.Combobox(body, textvariable=self.fill_var, values=FILL_CHOICES,
                         state="readonly", width=14).grid(
                row=row, column=1, columnspan=3, sticky="w", pady=2)
            row += 1
        else:
            self.fill_var = tk.StringVar(value="Transparent")
            ttk.Separator(body).grid(row=row, column=0, columnspan=4,
                                     sticky="ew", pady=6)
            row += 1
            ttk.Label(body, text="New size by").grid(row=row, column=0,
                                                      sticky="w")
            self.resize_var = tk.StringVar(value=base.resize_mode)
            ttk.Radiobutton(body, text="pad/crop", value="canvas",
                            variable=self.resize_var).grid(row=row, column=1,
                                                           sticky="w")
            ttk.Radiobutton(body, text="resample", value="scale",
                            variable=self.resize_var).grid(row=row, column=2,
                                                           sticky="w")
            row += 1
            ttk.Label(body, text="Resample").grid(row=row, column=0, sticky="w")
            self.resample_var = tk.StringVar(value="Nearest")
            ttk.Combobox(body, textvariable=self.resample_var,
                         values=("Nearest", "Bilinear"), state="readonly",
                         width=10).grid(row=row, column=1, sticky="w", pady=2)
            row += 1
            ttk.Label(body, text="Anchor").grid(row=row, column=0, sticky="nw")
            anchor_grid = ttk.Frame(body)
            anchor_grid.grid(row=row, column=1, columnspan=3, sticky="w")
            self.anchor_var = tk.StringVar(value=base.anchor)
            for index, code in enumerate(ANCHOR_CODES):
                ttk.Radiobutton(anchor_grid, text=ANCHOR_LABELS[code],
                                value=code, variable=self.anchor_var, width=3,
                                command=self._anchor_changed).grid(
                    row=index // 3, column=index % 3)
            row += 1

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(buttons, text="OK", command=self.accept).pack(
            side="right", padx=(0, 6))

        self.bind("<Escape>", lambda event: self.cancel())
        self.bind("<Return>", lambda event: self.accept())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.update_idletasks()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.ppi_var.trace_add("write", self._ppi_trace)
        self._set_size_fields()
        self._refresh_physical()
        self.focus_set()

    # ------------------------------------------------------------- helpers
    def _ppi(self):
        try:
            value = float(self.ppi_var.get())
        except (ValueError, tk.TclError):
            return 72.0
        return value if value > 0 else 72.0

    def _ppi_trace(self, *args):
        if self._unit != "px":
            self._set_size_fields()
        self._refresh_physical()

    def _set_size_fields(self):
        factor = px_per_unit(self._unit, self._ppi())
        text_format = "%.0f" if self._unit == "px" else "%.2f"
        self.width_var.set(text_format % (self.width_px / factor))
        self.height_var.set(text_format % (self.height_px / factor))

    def _read_fields(self):
        factor = px_per_unit(self._unit, self._ppi())
        width = float(str(self.width_var.get()).replace(",", ".")) * factor
        height = float(str(self.height_var.get()).replace(",", ".")) * factor
        return int(round(width)), int(round(height))

    def _unit_changed(self, event=None):
        try:
            old_factor = px_per_unit(self._unit, self._ppi())
            self.width_px = (float(str(self.width_var.get()).replace(",", "."))
                             * old_factor)
            self.height_px = (float(str(self.height_var.get()).replace(",", "."))
                              * old_factor)
        except ValueError:
            pass
        self._unit = self.unit_var.get()
        self._set_size_fields()
        self._refresh_physical()

    def _refresh_physical(self):
        try:
            width, height = self._read_fields()
        except ValueError:
            return
        mm_w, mm_h, in_w, in_h = physical_size(width, height, self._ppi())
        self.physical_var.set("%.1f x %.1f mm   (%.2f x %.2f in)"
                              % (mm_w, mm_h, in_w, in_h))

    def _preset_changed(self, event=None):
        for name, size in PRESETS:
            if name == self.preset_var.get() and size:
                self.width_px = float(size[0])
                self.height_px = float(size[1])
                self._unit = "px"
                self.unit_var.set("px")
                self._set_size_fields()
                self._refresh_physical()
                return

    def _anchor_changed(self):
        self.anchor = self.anchor_var.get()

    # -------------------------------------------------------------- result
    def build_spec(self):
        width, height = self._read_fields()
        if self.lock_var.get() and self.original[0] > 0:
            ratio = self.original[1] / float(self.original[0])
            height = max(1, int(round(width * ratio)))
        if width < 1 or height < 1:
            raise HexError("canvas must be at least 1x1 pixels")
        if width > MAX_SIDE or height > MAX_SIDE:
            raise HexError("canvas is limited to %d pixels per side" % MAX_SIDE)
        fill = (0, 0, 0, 0)
        if self.mode == "new":
            choice = self.fill_var.get()
            if choice == "White":
                fill = (255, 255, 255, 255)
            elif choice == "Black":
                fill = (0, 0, 0, 255)
            elif choice == "Foreground":
                fill = tuple(self.parent.fg)
            elif choice == "Background":
                fill = tuple(self.parent.bg)
        resize_mode = "canvas"
        resample = "nearest"
        if self.mode != "new":
            resize_mode = "scale" if self.resize_var.get() == "scale" else "canvas"
            resample = ("bilinear" if self.resample_var.get() == "Bilinear"
                        else "nearest")
        self.width_px, self.height_px = float(width), float(height)
        color_mode = ("Grayscale" if self.model_var.get().startswith("Gray")
                      else "RGB")
        return CanvasSpec(width, height, self._ppi(), color_mode,
                          bool(self.alpha_var.get()), fill, resize_mode,
                          self.anchor, resample)

    def accept(self):
        try:
            self.result = self.build_spec()
        except (HexError, ValueError) as exc:
            messagebox.showerror("Canvas", str(exc), parent=self)
            return
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


def run_selftest(out_path=None):
    """Run the unit tests plus a headless GUI pass. Returns an exit status."""
    import unittest

    from .tests import (test_bitmapfont, test_brush_tips, test_canvas,
                        test_dither, test_editor_features, test_effects,
                        test_filters,
                        test_formats, test_icons, test_integrations,
                        test_macro, test_model, test_palette, test_pattern,
                        test_project, test_shapes)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.loadTestsFromModule(test_model),
        loader.loadTestsFromModule(test_canvas),
        loader.loadTestsFromModule(test_formats),
        loader.loadTestsFromModule(test_pattern),
        loader.loadTestsFromModule(test_shapes),
        loader.loadTestsFromModule(test_palette),
        loader.loadTestsFromModule(test_macro),
        loader.loadTestsFromModule(test_effects),
        loader.loadTestsFromModule(test_bitmapfont),
        loader.loadTestsFromModule(test_dither),
        loader.loadTestsFromModule(test_filters),
        loader.loadTestsFromModule(test_project),
        loader.loadTestsFromModule(test_icons),
        loader.loadTestsFromModule(test_integrations),
        loader.loadTestsFromModule(test_editor_features),
        loader.loadTestsFromModule(test_brush_tips),
    ])
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    lines = ["hex2pix selftest", ""]
    lines.append("unit tests: %d run, %d failures, %d errors"
                 % (result.testsRun, len(result.failures), len(result.errors)))
    for case, trace in result.failures + result.errors:
        lines.append("  FAILED %s" % case)
        lines.append(trace.strip().splitlines()[-1] if trace.strip() else "")
    ok = result.wasSuccessful()

    temp_dir = tempfile.mkdtemp(prefix="hex2pix-selftest-")
    gui_ok = False
    try:
        app = hex2pixApp(
            Document(16, 16),
            recent_path=os.path.join(temp_dir, "recent.json"),
            settings_path=os.path.join(temp_dir, "settings.json"))
        app.withdraw()
        app.silent = True
        app.show_editor()
        app.update_idletasks()
        default_view_ok = (app.view_mode == "image"
                           and getattr(app, "_photo", None) is not None
                           and not app.view.find_withtag("hex")
                           and str(app.view.itemcget(app.view_image,
                                                     "image")) != "")
        tool_palette_ok = (len(getattr(app, "tool_icons", {})) == len(TOOLS)
                           and len(getattr(app, "tool_buttons", {})) == len(TOOLS))

        app.tool = "rect"
        app.sel_mode = "replace"
        app.sel_backup = app.doc.selection.snapshot()
        app.drag_anchor = (2, 2)
        app.apply_drag_selection(6, 6)
        rect_ok = app.doc.selection.count() == 25

        app.tool = "pencil"
        app.typed = "ff0000"
        app.cursor = (3, 3)
        app.commit_typed()
        pixel_ok = app.doc.bitmap.rgba(3, 3) == (255, 0, 0, 255)
        app.undo()
        undo_ok = app.doc.bitmap.rgba(3, 3) == (0, 0, 0, 0)

        app.tool = "wand"
        app.sel_backup = app.doc.selection.snapshot()
        app.doc.bitmap.set_rgba(5, 5, (0, 255, 0, 255))
        app.doc.bitmap.set_rgba(6, 5, (0, 255, 0, 255))
        app.wand_at(5, 5, True)
        wand_ok = app.doc.selection.count() == 2

        app.doc.selection.clear()
        app.block_text.delete("1.0", "end")
        app.block_text.insert("1.0", "112233 445566")
        app.cursor = (8, 8)
        app.place_block("cursor")
        block_ok = app.doc.bitmap.rgba(9, 8) == (0x44, 0x55, 0x66, 255)

        class _Event(object):
            def __init__(self, x, y):
                self.x = x
                self.y = y
                self.state = 0

        def cell_event(cx, cy):
            """Widget coords for a cell centre, wherever the camera sits."""
            zoom = app.zoom
            return _Event(int(cx * zoom + zoom / 2.0 - app.view.canvasx(0)),
                          int(cy * zoom + zoom / 2.0 - app.view.canvasy(0)))

        app.set_view_mode("image")
        app.update_idletasks()
        app.tool = "pencil"
        app.fg = (255, 255, 255, 255)
        app.on_press(cell_event(2, 2), False)
        app.on_drag(cell_event(5, 2))
        app.on_release(cell_event(5, 2))
        drag_ok = app.doc.bitmap.rgba(4, 2)[3] == 255

        app.on_press(cell_event(2, 2), True)
        app.on_drag(cell_event(5, 2))
        app.on_release(cell_event(5, 2))
        erase_ok = app.doc.bitmap.rgba(4, 2)[3] == 0

        export_path = os.path.join(temp_dir, "gui.png")
        app.export_path.set(export_path)
        app.format_var.set(formats.FORMAT_LABELS[0])
        app.export_now()
        export_ok = os.path.isfile(export_path) and os.path.getsize(export_path) > 0

        app.doc.selection.select_all()
        app.refresh_bytes()
        dump_ok = "112233FF" in app.dump_text.get("1.0", "end")

        app.new_canvas(CanvasSpec(24, 18, 144.0, "RGB", True,
                                  fill=(255, 0, 0, 255)))
        canvas_ok = ((app.doc.width, app.doc.height, app.doc.ppi)
                     == (24, 18, 144.0)
                     and app.doc.bitmap.rgba(23, 17) == (255, 0, 0, 255))

        app.apply_canvas_spec(CanvasSpec(12, 9, 96.0, "Grayscale", False,
                                         resize_mode="scale"))
        props_ok = ((app.doc.width, app.doc.height, app.doc.ppi,
                     app.doc.color_mode, app.doc.alpha)
                    == (12, 9, 96.0, "Grayscale", False)
                    and app.doc.bitmap.rgba(0, 0)[:3] == (54, 54, 54))

        app.undo()
        undo_canvas_ok = ((app.doc.width, app.doc.height, app.doc.ppi,
                           app.doc.color_mode, app.doc.alpha)
                          == (24, 18, 144.0, "RGB", True))

        dialog = CanvasDialog(app, document=app.doc, mode="properties",
                              spec=CanvasSpec(24, 18, 144.0))
        dialog.width_var.set("30")
        dialog.height_var.set("20")
        dialog.ppi_var.set(72.0)
        dialog_spec = dialog.build_spec()
        dialog_ok = ((dialog_spec.width, dialog_spec.height, dialog_spec.ppi)
                     == (30, 20, 72.0))
        dialog.destroy()

        app.set_view_mode("hex")
        app.update_idletasks()
        app.redraw()
        hex_items = len(app.view.find_withtag("hex"))
        hex_view_ok = app.view_mode == "hex" and hex_items > 0
        cell_ok = app.hex_cell[0] > 20 and app.hex_cell[1] > 10
        app.set_view_mode("image")
        app.update_idletasks()
        image_view_ok = (app.view_mode == "image"
                         and not app.view.find_withtag("hex")
                         and app._photo is not None
                         and str(app.view.itemcget(app.view_image,
                                                   "image")) != "")
        app.set_view_mode("hex")
        cell_w, cell_h = app.hex_cell
        offset_x = int(app.view.canvasx(0))
        offset_y = int(app.view.canvasy(0))
        hex_probe = app.clamped(
            _Event(cell_w * 3 + cell_w // 2 - offset_x,
                   cell_h * 2 + cell_h // 2 - offset_y))
        hex_map_ok = hex_probe == (3, 2)

        app.new_canvas(CanvasSpec(16, 16))
        app.tool = "rect"
        app.sel_mode = "replace"
        app.cursor = (2, 2)
        app.keyboard_corner()
        app.move_cursor(3, 2)
        app.keyboard_corner()
        kb_ok = (app.doc.selection.bounds() == (2, 2, 4, 3)
                 and app.doc.selection.count() == 12)
        app.cursor = (9, 9)
        app.keyboard_corner()
        app.move_cursor(2, 2)
        app.keyboard_cancel()
        kb_cancel_ok = app.doc.selection.bounds() == (2, 2, 4, 3)

        app.doc.selection.clear()
        app.tool = "pencil"
        app.cursor = (1, 1)
        app.set_fg((255, 255, 255, 255))
        app.view.focus_set()
        app.recorder.start()
        for digit in "00ff00":
            app.on_key(_KeyEvent(digit, digit))
        app.on_key(_KeyEvent("Return", "\r"))
        app.recorder.stop()
        record_ok = len(app.recorder.macro.steps) == 7
        app.undo()
        app.cursor = (1, 1)
        app.play_macro(interval=0)
        play_ok = (app.doc.bitmap.rgba(1, 1) == (0, 255, 0, 255)
                   and not app.playing)

        app.new_canvas(CanvasSpec(8, 8))
        app.tool = "fill"
        app.set_fg((255, 0, 0, 255))
        app.cursor = (2, 2)
        app.keyboard_activate()
        kb_fill_ok = all(app.doc.bitmap.rgba(x, y) == (255, 0, 0, 255)
                         for x in range(8) for y in range(8))

        app.new_canvas(CanvasSpec(8, 8))
        app.doc.selection.select_all()
        app.set_pattern_size(2)
        app.pattern_cells = [[(255, 0, 0, 255), None], [None, None]]
        app.pattern_scale.set(1)
        app.pattern_shift.set(0)
        app.pattern_at(0, 0, "line")
        line_fill_ok = (app.doc.bitmap.rgba(0, 0) == (255, 0, 0, 255)
                        and app.doc.bitmap.rgba(2, 0) == (255, 0, 0, 255)
                        and app.doc.bitmap.rgba(1, 0)[3] == 0
                        and app.doc.bitmap.rgba(0, 1)[3] == 0
                        and app.doc.bitmap.rgba(0, 2) == (255, 0, 0, 255))

        app.new_canvas(CanvasSpec(8, 8))
        app.doc.selection.select_all()
        app.set_pattern_size(2)
        app.pattern_cells = [[(255, 0, 0, 255), (0, 0, 255, 255)],
                             [None, None]]
        app.pattern_sw.set(2)
        app.pattern_sh.set(1)
        app.pattern_mirror.set(True)
        app.pattern_at(0, 0, "structure")
        structure_ok = (app.doc.bitmap.rgba(0, 0) == (255, 0, 0, 255)
                        and app.doc.bitmap.rgba(1, 0) == (0, 0, 255, 255)
                        and app.doc.bitmap.rgba(2, 0) == (0, 0, 255, 255)
                        and app.doc.bitmap.rgba(3, 0) == (255, 0, 0, 255))

        app.pattern_text.delete("1.0", "end")
        app.pattern_text.insert("1.0", "FF0000 0000FF")
        app.pattern_grid_from_text()
        pattern_text_ok = (app.pattern_size == 2
                           and app.pattern_cells[0][1] == (0, 0, 255, 255))

        app.new_canvas(CanvasSpec(8, 8))
        app.tool = "pencil"
        app.set_fg((255, 0, 0, 255))
        app.symmetry = "x"
        app.cursor = (1, 2)
        app.commit_typed()
        symmetry_ok = (app.doc.bitmap.rgba(1, 2) == (255, 0, 0, 255)
                       and app.doc.bitmap.rgba(6, 2) == (255, 0, 0, 255))
        app.symmetry = ""

        app.new_canvas(CanvasSpec(8, 8))
        app.tool = "line"
        app.set_fg((0, 255, 0, 255))
        app.brush = 1
        app.begin_shape(0, 0)
        app.apply_shape(3, 0)
        app.finish_shape()
        shape_ok = (all(app.doc.bitmap.rgba(x, 0) == (0, 255, 0, 255)
                        for x in range(4))
                    and app.doc.bitmap.rgba(2, 1)[3] == 0)

        app.new_canvas(CanvasSpec(8, 8))
        app.doc.selection.select_all()
        app.set_fg((255, 0, 0, 255))
        app.bg = (0, 0, 255, 255)
        app.tool = "gradient"
        app.begin_gradient(0, 0)
        app.cursor = (7, 0)
        app.apply_gradient()
        gradient_ok = (app.doc.bitmap.rgba(0, 0)[:3] == (255, 0, 0)
                       and app.doc.bitmap.rgba(7, 0)[:3] == (0, 0, 255))

        palette_ok = (len(app.palette) == 16
                      and len(app.palette_chips) == 16
                      and app.palette_count_var.get().startswith("16"))
        wheel_ok = (app.wheel is not None
                    and app.wheel.winfo_width() > 50)
        app.new_canvas(CanvasSpec(4, 4))
        app.doc.bitmap.fill((10, 20, 30, 255))
        app.palette = [(255, 0, 0, 255)]
        app.palette_name = "Test"
        app.apply_palette(False)
        quantize_ok = all(app.doc.bitmap.rgba(x, y) == (255, 0, 0, 255)
                          for x in range(4) for y in range(4))
        app.palette = preset_colors("PICO-8")
        app.palette_name = "PICO-8"
        app.draw_palette()

        dark_ok = (app.dark_mode and app.dark_var.get()
                   and str(app.cget("bg")) == THEMES["dark"]["bg"])
        app.dark_var.set(False)
        app.toggle_dark_mode()
        app.update_idletasks()
        light_ok = (not app.dark_mode
                    and str(app.cget("bg")) == THEMES["light"]["bg"])
        app.dark_var.set(True)
        app.toggle_dark_mode()
        app.update_idletasks()
        dark_ok = dark_ok and app.dark_mode
        docker_keys = list(app.docker_tabs)
        dockers_ok = len(docker_keys) >= 7
        for key in docker_keys:
            app.docker_vars[key].set(False)
            app.toggle_docker(key)
        dockers_ok = dockers_ok and all(
            str(app.notebook.tab(app.docker_tabs[key], "state")) == "hidden"
            for key in docker_keys)
        for key in docker_keys:
            app.docker_vars[key].set(True)
            app.toggle_docker(key)
        dockers_ok = dockers_ok and all(
            str(app.notebook.tab(app.docker_tabs[key], "state")) == "normal"
            for key in docker_keys)

        # Fractional zoom used to round the selection mask one pixel away
        # from the drawn image, which made the overlay raise ValueError.
        app.doc.selection.select_all()
        overlay_ok = True
        for probe_zoom in (0.9, 1.0, 3.7, 8.25):
            probe_size = (max(1, int(round(app.doc.width * probe_zoom))),
                          max(1, int(round(app.doc.height * probe_zoom))))
            try:
                probe = app.selection_overlay(
                    Image.new("RGBA", probe_size, (0, 0, 0, 0)),
                    app.doc.width, app.doc.height, probe_zoom)
            except ValueError:
                overlay_ok = False
            else:
                overlay_ok = overlay_ok and probe.size == probe_size
        app.doc.selection.clear()

        # Layer verbs and the instant deselect are keyboard reachable.
        app.new_canvas(CanvasSpec(16, 16))
        app.on_key(_KeyEvent("n", "", 0x0008))
        app.on_key(_KeyEvent("c", "", 0x0008))
        layer_keys_ok = app.doc.layer_count == 3
        app.on_key(_KeyEvent("Delete", "", 0x0008))
        layer_keys_ok = layer_keys_ok and app.doc.layer_count == 2
        app.on_key(_KeyEvent("Up", "", 0x0008))
        app.on_key(_KeyEvent("Down", "", 0x0008))
        app.doc.selection.select_all()
        app.on_key(_KeyEvent("Escape", "\x1b"))
        layer_keys_ok = layer_keys_ok and not app.doc.selection.active

        old_tool = app.tool
        app.tool = "line"
        app.shape_anchor = (0, 0)
        mouse_ok = app.constrained_shape(10, 3) == (10, 0)
        app.tool = "shape_rect"
        mouse_ok = mouse_ok and app.constrained_shape(10, 3) == (10, 10)
        app.shape_anchor = None
        app.tool = old_tool
        app.set_view_mode("image")
        app.brush = 5
        app.brush_var.set(5)

        class Wheel(object):
            x = 20
            y = 20
            delta = 120
            state = 0x0004

        app.on_wheel(Wheel())
        mouse_ok = mouse_ok and app.brush == 6
        app.brush = 1
        app.brush_var.set(1)

        app.set_fullscreen(True)
        app.update_idletasks()
        fullscreen_ok = app.fullscreen
        app.set_fullscreen(False)
        app.update_idletasks()
        fullscreen_ok = fullscreen_ok and not app.fullscreen
        app._update_left_scrollregion()
        left_box = app.left_canvas.bbox("all")
        left_scroll_ok = bool(left_box) and (left_box[3]
                                             >= app.left_inner.winfo_reqheight())

        app.show_home()
        home_ok = app.home_visible and not app.editor.winfo_manager()
        app.show_editor()
        editor_ok = (not app.home_visible) and bool(app.editor.winfo_manager())
        app.start_preset(48, 32)
        preset_ok = ((app.doc.width, app.doc.height) == (48, 32)
                     and not app.home_visible)

        store = RecentStore(os.path.join(temp_dir, "recent.json"), limit=3)
        for name in ("a.png", "b.png", "c.png", "d.png"):
            path = os.path.join(temp_dir, name)
            with open(path, "wb") as handle:
                handle.write(b"x")
            store.add(path)
        reloaded = RecentStore(store.path, limit=3)
        recent_ok = ([os.path.basename(p) for p in reloaded.items]
                     == ["d.png", "c.png", "b.png"])
        store.add(os.path.join(temp_dir, "gone.png"))
        recent_filter_ok = all(os.path.isfile(p)
                               for p in RecentStore(store.path).items)
        manual_ok = manual_path() is not None

        # Tabs, the unsaved marker, dropped images and the new sliders
        tabs_before = len(app.tabs)
        app.new_canvas(CanvasSpec(10, 6))
        tabs_ok = (len(app.tabs) == tabs_before + 1
                   and (app.doc.width, app.doc.height) == (10, 6))
        tabs_ok = tabs_ok and bool(app.switch_tab(0))
        tabs_ok = tabs_ok and app.doc.width != 10
        tabs_ok = tabs_ok and bool(app.switch_tab(len(app.tabs) - 1))
        tabs_ok = tabs_ok and bool(app.next_tab(-1))

        dirty_ok = not app.doc.dirty
        before = app.doc.begin()
        app.doc.commit(before)
        dirty_ok = dirty_ok and app.doc.dirty
        dirty_ok = (dirty_ok and bool(app.mark_saved())
                    and not app.doc.dirty)

        drop_path = os.path.join(temp_dir, "dropped.png")
        Image.new("RGBA", (4, 3), (0, 255, 0, 255)).save(drop_path)
        layers_before = app.doc.layer_count
        handled = app.handle_dropped_paths(
            [drop_path], choose=lambda paths: "layer")
        drop_ok = (handled == ["layer"]
                   and app.doc.layer_count == layers_before + 1)
        centre = app.doc.active_layer.bitmap.rgba(app.doc.width // 2,
                                                  app.doc.height // 2)
        drop_ok = drop_ok and centre == (0, 255, 0, 255)
        tabs_ok = tabs_ok and app.handle_dropped_paths(
            [drop_path], choose=lambda paths: "canvas") == ["canvas"]
        tabs_ok = tabs_ok and app.doc.path == drop_path

        smear_ok = app.blur_smear() == 0 and app.shuffle_blur() == 0
        app.blur_smear_var.set(50)
        app.shuffle_blur_var.set(25)
        smear_ok = (smear_ok and app.blur_smear() == 50
                    and app.shuffle_blur() == 25)
        app.blur_smear_var.set(0)
        app.shuffle_blur_var.set(0)

        tips_ok = app.brush_shape() == "square"
        app.brush_shape_var.set("Circle")
        tips_ok = tips_ok and app.brush_shape() == "circle"
        app.brush_shape_var.set("Custom")
        tips_ok = tips_ok and app.brush_shape() == "custom"
        tips_ok = tips_ok and bool(brush_offsets(6, "custom"))
        app.brush_shape_var.set("Square")
        pattern_ok = app.pattern_size >= 1
        app.set_pattern_size(3)
        app.pattern_cells[0][0] = (7, 8, 9, 255)
        app.brush_pattern_var.set(True)
        painted = app.pattern_dab(2, 2)
        pattern_ok = (pattern_ok and app.brush_pattern() and painted >= 1
                      and app.doc.active_layer.bitmap.rgba(1, 1)
                      == (7, 8, 9, 255))
        app.brush_pattern_var.set(False)
        app.pattern_cells[0][0] = None
        lines.append("tabs=%s dirty=%s drop=%s sliders=%s tips=%s pattern=%s"
                     % (tabs_ok, dirty_ok, drop_ok, smear_ok, tips_ok,
                        pattern_ok))
        app.destroy()
        gui_ok = all((rect_ok, pixel_ok, undo_ok, wand_ok, block_ok, drag_ok,
                      tool_palette_ok,
                      erase_ok, export_ok, dump_ok, canvas_ok, props_ok,
                      undo_canvas_ok, dialog_ok, default_view_ok, hex_view_ok,
                      image_view_ok, cell_ok, hex_map_ok, home_ok, editor_ok,
                      preset_ok, recent_ok, recent_filter_ok, manual_ok,
                      kb_ok, kb_cancel_ok, record_ok, play_ok, kb_fill_ok,
                      line_fill_ok, structure_ok, pattern_text_ok,
                      fullscreen_ok, left_scroll_ok, symmetry_ok, shape_ok,
                      gradient_ok, palette_ok, wheel_ok, quantize_ok,
                      dark_ok, light_ok, dockers_ok, overlay_ok,
                      layer_keys_ok, mouse_ok))
        gui_ok = gui_ok and all((tabs_ok, dirty_ok, drop_ok, smear_ok,
                                 tips_ok, pattern_ok))
        lines.append("gui checks: rect=%s pixel=%s undo=%s wand=%s block=%s "
                     "drag=%s erase=%s export=%s dump=%s canvas=%s props=%s "
                     "canvas_undo=%s dialog=%s default_view=%s hex_view=%s "
                     "image_view=%s cells=%s hex_map=%s home=%s editor=%s "
                     "preset=%s recent=%s recent_filter=%s manual=%s kb=%s "
                     "kb_cancel=%s record=%s play=%s kb_fill=%s line_fill=%s "
                     "structure=%s pattern_text=%s fullscreen=%s "
                     "left_scroll=%s symmetry=%s shape=%s gradient=%s "
                     "palette=%s wheel=%s quantize=%s dark=%s light=%s "
                     "dockers=%s overlay=%s layer_keys=%s mouse=%s"
                     % (rect_ok, pixel_ok, undo_ok, wand_ok, block_ok, drag_ok,
                        erase_ok, export_ok, dump_ok, canvas_ok, props_ok,
                        undo_canvas_ok, dialog_ok, default_view_ok, hex_view_ok,
                        image_view_ok, cell_ok, hex_map_ok, home_ok, editor_ok,
                        preset_ok, recent_ok, recent_filter_ok, manual_ok,
                        kb_ok, kb_cancel_ok, record_ok, play_ok, kb_fill_ok,
                        line_fill_ok, structure_ok, pattern_text_ok,
                        fullscreen_ok, left_scroll_ok, symmetry_ok, shape_ok,
                        gradient_ok, palette_ok, wheel_ok, quantize_ok,
                        dark_ok, light_ok, dockers_ok, overlay_ok,
                        layer_keys_ok, mouse_ok))
    except Exception as exc:  # noqa: BLE001 - reported below
        import traceback
        lines.append("gui checks crashed: %s: %s" % (type(exc).__name__, exc))
        lines.append(traceback.format_exc())
        gui_ok = False

    lines.append("")
    lines.append("RESULT: %s" % ("PASS" if (ok and gui_ok) else "FAIL"))
    report = "\n".join(lines)
    target = out_path or os.path.join(os.getcwd(), "hex2pix-selftest.txt")
    try:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
    except OSError:
        pass
    print(report)
    return 0 if (ok and gui_ok) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        rest = [a for a in argv if a != "--selftest"]
        out = None
        if "--report" in rest:
            index = rest.index("--report")
            if index + 1 < len(rest):
                out = rest[index + 1]
        return run_selftest(out)

    initial = next((a for a in argv if not a.startswith("-")), None)
    app = hex2pixApp(show_home=(initial is None))
    app.withdraw()
    if initial and not app.load_path(initial):
        app.show_home()
    app.deiconify()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
