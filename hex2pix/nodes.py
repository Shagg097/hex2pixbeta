"""Modular per-layer render graphs for Complex Render.

A layer keeps the original Quick Render path (operation, gate and blend
combined per pixel).  A layer in Complex Render mode instead runs a small
node graph: nodes read the layer's own pixel, the stack below it and their
own parameters, and the graph's Output node becomes that layer's contribution
to the composite.

The module is deliberately free of tkinter so the graph can be unit-tested and
saved to a project without a display.
"""

import colorsys
import math
import random
from functools import lru_cache

RENDER_QUICK = "quick"
RENDER_COMPLEX = "complex"
RENDER_MODES = (RENDER_QUICK, RENDER_COMPLEX)
RENDER_LABELS = {
    RENDER_QUICK: "Quick Render",
    RENDER_COMPLEX: "Complex Render",
}
RENDER_BY_LABEL = {label: mode for mode, label in RENDER_LABELS.items()}

COLOR = "color"
VALUE = "value"
VALUE_INPUT_NAMES = ("A", "B", "C", "D", "E", "F", "G", "H")

# A port value is either an RGBA tuple of ints (0..255) or a float.
TRANSPARENT = (0, 0, 0, 0)
DEFAULT_COLOR = (255, 255, 255, 255)

MATH_MODES = ("add", "subtract", "multiply", "divide", "min", "max")
MATH_LABELS = {
    "add": "Add", "subtract": "Subtract", "multiply": "Multiply",
    "divide": "Divide", "min": "Minimum", "max": "Maximum",
}

MASK_SOURCES = ("alpha", "luminance", "red", "green", "blue")
MASK_SOURCE_LABELS = {
    "alpha": "Alpha",
    "luminance": "Luminance",
    "red": "Red",
    "green": "Green",
    "blue": "Blue",
}

# Micro gates live on a socket.  Every binary gate gets its own B input
# socket; the socket's normal value is the A operand.  NOT has no B.
MICRO_GATES = ("and", "or", "xor", "not", "nand", "nor", "xnor")
MICRO_GATE_LABELS = {
    "and": "AND",
    "or": "OR",
    "xor": "XOR",
    "not": "NOT",
    "nand": "NAND",
    "nor": "NOR",
    "xnor": "XNOR",
}
GATE_INPUT_PREFIX = "@B:"


def gate_input_name(side, name):
    """The hidden input socket that supplies a micro gate's B operand."""
    return "%s%s:%s" % (GATE_INPUT_PREFIX, side, name)


def gate_input_info(name):
    """Split a micro gate B socket name into (side, source socket)."""
    if not isinstance(name, str) or not name.startswith(GATE_INPUT_PREFIX):
        return None
    parts = name[len(GATE_INPUT_PREFIX):].split(":", 1)
    if len(parts) != 2 or parts[0] not in ("in", "out"):
        return None
    return parts[0], parts[1]


def is_gate_input(name):
    return gate_input_info(name) is not None


RAMP_SOURCES = ("luminance", "hue", "saturation", "lightness",
                "brightness", "chroma")
RAMP_LABELS = {
    "luminance": "Luminance",
    "hue": "Hue",
    "saturation": "Saturation",
    "lightness": "Lightness",
    "brightness": "Brightness",
    "chroma": "Chroma",
}

RAMP_INTERPOLATIONS = ("linear", "constant", "smooth")
RAMP_INTERPOLATION_LABELS = {
    "linear": "Linear",
    "constant": "Constant",
    "smooth": "Smooth",
}

GRADIENT_PRESETS = (
    ("Black to White", "0:000000FF,1:FFFFFFFF"),
    ("White to Black", "0:FFFFFFFF,1:000000FF"),
    ("Fire", "0:1A0000FF,0.35:D21B00FF,0.7:FF8C00FF,1:FFFF66FF"),
    ("Ocean", "0:001B44FF,0.45:006D77FF,0.75:83C5BEFF,1:EDF6F9FF"),
    ("Sunset", "0:2B1055FF,0.35:7B2C7BFF,0.7:FF6B6BFF,1:FFD166FF"),
    ("Rainbow", "0:FF0000FF,0.17:FF9900FF,0.34:FFFF00FF,"
                "0.51:00CC44FF,0.68:0066FFFF,0.85:6600CCFF,1:FF00FFFF"),
    ("Vaporwave", "0:00F5FFFF,0.5:FF00FFFF,1:2B0B3FFF"),
    ("Fade to Clear", "0:FFFFFFFF,1:FFFFFF00"),
)


def _choice(name, label, choices, labels, default):
    return {"name": name, "kind": "choice", "label": label,
            "choices": tuple(choices), "labels": dict(labels),
            "default": default}


def _number(name, label, low, high, default, step=1):
    return {"name": name, "kind": "number", "label": label, "min": low,
            "max": high, "default": default, "step": step}


def _color_param(name, label, default):
    return {"name": name, "kind": "color", "label": label, "default": default}


def _gradient_param(name, label, default):
    return {"name": name, "kind": "gradient", "label": label,
            "default": default}


def _count_param(name, label, low, high, default):
    return {"name": name, "kind": "count", "label": label, "min": low,
            "max": high, "default": default}


# Every node type names its sockets, their types and its editable parameters.
# Input A is the layer/source side of an operation and B is the stack below.
NODE_TYPES = {
    "layer": {
        "label": "Layer",
        "hint": "the layer's colour, channels and measurements",
        "inputs": (),
        "outputs": (("Color", COLOR), ("Red", VALUE), ("Green", VALUE),
                    ("Blue", VALUE), ("Alpha", VALUE), ("Opacity", VALUE),
                    ("Hue", VALUE), ("Saturation", VALUE),
                    ("HSL Saturation", VALUE), ("Brightness", VALUE),
                    ("Lightness", VALUE), ("Luminance", VALUE),
                    ("Gamma", VALUE), ("Chroma", VALUE)),
        "params": (),
    },
    "below": {
        "label": "Below",
        "hint": "the composited stack below",
        "inputs": (),
        "outputs": (("Color", COLOR),),
        "params": (),
    },
    "output": {
        "label": "Output",
        "hint": "the layer's contribution",
        "inputs": (("Color", COLOR),),
        "outputs": (),
        "params": (),
    },
    "operation": {
        "label": "Operation",
        "hint": "arithmetic on A and B",
        "inputs": (("A", COLOR), ("B", COLOR)),
        "outputs": (("Color", COLOR),),
        "params": (_choice("mode", "Operation",
                           ("add", "subtract", "multiply", "divide"),
                           {"add": "Add", "subtract": "Subtract",
                            "multiply": "Multiply", "divide": "Divide"},
                           "add"),),
    },
    "gate": {
        "label": "Gate",
        "hint": "bitwise gate of A and B",
        "inputs": (("A", COLOR), ("B", COLOR)),
        "outputs": (("Color", COLOR),),
        "params": (_choice("mode", "Gate",
                           ("and", "or", "xor", "not", "nand", "nor", "xnor"),
                           {"and": "And", "or": "Or", "xor": "Xor",
                            "not": "Not", "nand": "Nand", "nor": "Nor",
                            "xnor": "Xnor"},
                           "nand"),),
    },
    "blend": {
        "label": "Blend",
        "hint": "blend A into B",
        "inputs": (("A", COLOR), ("B", COLOR)),
        "outputs": (("Color", COLOR),),
        "params": (_choice("mode", "Blend",
                           ("screen", "overlay", "darken", "lighten",
                            "difference"),
                           {"screen": "Screen", "overlay": "Overlay",
                            "darken": "Darken", "lighten": "Lighten",
                            "difference": "Difference"},
                           "screen"),),
    },
    "perlin": {
        "label": "Perlin Noise",
        "hint": "deterministic 2D noise",
        "inputs": (("Scale", VALUE), ("Seed", VALUE)),
        "outputs": (("Color", COLOR), ("Value", VALUE)),
        "params": (_number("scale", "Scale", 1, 512, 32),
                   _number("seed", "Seed", 0, 9999, 0)),
    },
    "hue": {
        "label": "Hue Shift",
        "hint": "rotate colour hue",
        "inputs": (("Color", COLOR), ("Shift", VALUE)),
        "outputs": (("Color", COLOR),),
        "params": (_number("shift", "Shift (deg)", -180, 180, 30),),
    },
    "ramp": {
        "label": "Color Ramp",
        "hint": "map a measurement through a gradient",
        "inputs": (("Value", VALUE),),
        "outputs": (("Color", COLOR),),
        "params": (_choice("mode", "Source", RAMP_SOURCES, RAMP_LABELS,
                           "luminance"),
                   _choice("interpolation", "Interpolation",
                           RAMP_INTERPOLATIONS, RAMP_INTERPOLATION_LABELS,
                           "linear"),
                   _gradient_param("gradient", "Gradient",
                                   "0:000000FF,1:FFFFFFFF")),
    },
    "mask": {
        "label": "Mask",
        "hint": "turn a colour into a grayscale mask",
        "inputs": (("Color", COLOR),),
        "outputs": (("Color", COLOR), ("Value", VALUE)),
        "params": (_choice("source", "Source", MASK_SOURCES,
                           MASK_SOURCE_LABELS, "alpha"),
                   _choice("invert", "Invert", ("no", "yes"),
                           {"no": "No", "yes": "Yes"}, "no")),
    },
    "mask_apply": {
        "label": "Apply Mask",
        "hint": "multiply colour alpha by a mask",
        "inputs": (("Color", COLOR), ("Mask", VALUE)),
        "outputs": (("Color", COLOR),),
        "params": (),
    },
    "constant": {
        "label": "Color",
        "hint": "a constant colour",
        "inputs": (),
        "outputs": (("Color", COLOR),),
        "params": (_color_param("color", "Colour", "FFFFFFFF"),),
    },
    "value": {
        "label": "Value",
        "hint": "a base number plus added inputs",
        "inputs": tuple((name, VALUE) for name in VALUE_INPUT_NAMES),
        "outputs": (("Value", VALUE),),
        "params": (_number("value", "Base value", None, None, 0.5, 0.1),
                   _count_param("inputs", "Inputs", 0,
                                len(VALUE_INPUT_NAMES), 0)),
    },
    "math": {
        "label": "Math",
        "hint": "combine two numbers",
        "inputs": (("A", VALUE), ("B", VALUE)),
        "outputs": (("Value", VALUE),),
        "params": (_choice("mode", "Math", MATH_MODES, MATH_LABELS, "add"),),
    },
}

NODE_ORDER = ("layer", "below", "operation", "gate", "blend", "perlin",
              "hue", "ramp", "mask", "mask_apply", "constant", "value",
              "math", "output")

_INPUT_KINDS = {key: dict(spec["inputs"]) for key, spec in NODE_TYPES.items()}
_PARAM_DEFAULTS = {
    key: {param["name"]: param["default"] for param in spec["params"]}
    for key, spec in NODE_TYPES.items()
}


def node_spec(node_type):
    return NODE_TYPES.get(node_type) or NODE_TYPES["layer"]


def node_inputs(node):
    """The input sockets a node currently exposes.

    Most node types have fixed sockets; a Value node shows only as many of
    its A-H adders as its input count asks for.
    """
    if node.type != "value":
        return NODE_TYPES[node.type]["inputs"]
    try:
        count = int(node.params.get("inputs", 0))
    except (TypeError, ValueError):
        count = 0
    count = max(0, min(len(VALUE_INPUT_NAMES), count))
    if node._inputs_key != count:
        node._inputs_cache = tuple((name, VALUE)
                                   for name in VALUE_INPUT_NAMES[:count])
        node._inputs_key = count
    return node._inputs_cache


def node_outputs(node):
    """The output sockets a node currently exposes.

    Multi-output nodes can hide trailing outputs; a node with one output can
    be given duplicates ("Color 2", "Color 3", ...) for easy fan-out.
    """
    base = NODE_TYPES[node.type]["outputs"]
    if not base:
        return ()
    maximum = len(base) if len(base) > 1 else 8
    try:
        count = int(node.params.get("outputs", len(base)))
    except (TypeError, ValueError):
        count = len(base)
    count = max(1, min(maximum, count))
    if node._outputs_key != count:
        visible = list(base[:min(count, len(base))])
        if count > len(base):
            name, kind = base[0]
            for index in range(len(base), count):
                visible.append(("%s %d" % (name, index + 1), kind))
        node._outputs_cache = tuple(visible)
        node._outputs_key = count
    return node._outputs_cache


def gate_socket_kind(node, side, name):
    """The port kind of the socket a micro gate is attached to."""
    if side == "out":
        return dict(node_outputs(node)).get(name, COLOR)
    return _INPUT_KINDS[node.type].get(name, COLOR)


def node_gate_inputs(node):
    """Hidden B sockets exposed by binary micro gates on this node."""
    return tuple((gate_input_name(side, name),
                  gate_socket_kind(node, side, name))
                 for side, name, mode in node_micro_gates(node)
                 if mode != "not")


def node_micro_gates(node):
    """Every configured socket gate as (side, socket, gate mode)."""
    entries = []
    for side, ports in (("in", node_inputs(node)),
                        ("out", node_outputs(node))):
        for name, _kind in ports:
            mode = node.params.get("@gate:%s:%s" % (side, name))
            if mode in MICRO_GATES:
                entries.append((side, name, mode))
    return tuple(entries)


def node_all_inputs(node):
    """Inputs users can wire to, including micro gate B sockets."""
    return tuple(node_inputs(node)) + node_gate_inputs(node)


def node_output_sources(node):
    """(visible output name, stored output that supplies it) pairs."""
    outputs = node_outputs(node)
    if node._sources_key is not outputs:
        base = NODE_TYPES[node.type]["outputs"]
        node._sources_cache = tuple(
            (name, name if index < len(base) else base[0][0])
            for index, (name, _kind) in enumerate(outputs))
        node._sources_key = outputs
    return node._sources_cache


def default_params(node_type):
    spec = node_spec(node_type)
    return {param["name"]: param["default"] for param in spec["params"]}


def parse_color(text, fallback=DEFAULT_COLOR):
    """Parse RRGGBB / RRGGBBAA into an RGBA tuple."""
    raw = str(text or "").strip().lstrip("#")
    if len(raw) == 6:
        raw += "FF"
    if len(raw) != 8:
        return fallback
    try:
        return tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4, 6))
    except ValueError:
        return fallback


def color_text(color):
    return "".join("%02X" % max(0, min(255, int(value))) for value in color)


def _clamp(value, low=0.0, high=1.0):
    return low if value < low else high if value > high else value


class ChannelValue(float):
    """A numeric Layer channel that knows its colour and source alpha."""

    def __new__(cls, value, channel, alpha=1.0):
        instance = float.__new__(cls, float(value))
        instance.channel = channel
        instance.alpha = _clamp(float(alpha))
        return instance


def _as_color(value):
    """Coerce a socket value to RGBA; numbers become opaque grey.

    Layer channel values are special: wiring Green into a colour socket
    produces a green-only image with the layer's alpha instead of grey.
    """
    if isinstance(value, ChannelValue):
        amount = int(round(_clamp(float(value)) * 255))
        alpha = int(round(_clamp(value.alpha) * 255))
        if value.channel == "red":
            return (amount, 0, 0, alpha)
        if value.channel == "blue":
            return (0, 0, amount, alpha)
        return (0, amount, 0, alpha)
    if isinstance(value, (int, float)):
        grey = int(round(_clamp(float(value)) * 255))
        return (grey, grey, grey, 255)
    return value if isinstance(value, tuple) else TRANSPARENT


def _as_value(value):
    """Coerce a socket value to a number; colours use their brightness."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        red, green, blue = value[0], value[1], value[2]
    except (TypeError, IndexError, KeyError):
        return 0.0
    return (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0


def mask_measurement(color, source):
    """Return a 0..1 mask measurement from one RGBA colour."""
    red, green, blue, alpha = color
    if source == "luminance":
        return (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0
    if source == "red":
        return red / 255.0
    if source == "green":
        return green / 255.0
    if source == "blue":
        return blue / 255.0
    return alpha / 255.0


def _average_colors(colors):
    """Average several RGBA wires feeding one colour input."""
    if len(colors) == 1:
        return colors[0]
    count = len(colors)
    return tuple(int(round(sum(color[index] for color in colors) / count))
                 for index in range(4))


def _gate_number(below, above, mode):
    """Continuous 0..1 gate logic used by value sockets."""
    if mode == "and":
        return min(below, above)
    if mode == "or":
        return max(below, above)
    if mode == "xor":
        return abs(above - below)
    if mode == "not":
        return 1.0 - above
    if mode == "nand":
        return 1.0 - min(below, above)
    if mode == "nor":
        return 1.0 - max(below, above)
    if mode == "xnor":
        return 1.0 - abs(above - below)
    return above


def _gate_channel(below, above, mode):
    """Bitwise 0..255 gate math, matching the Quick Render gate node."""
    below, above = int(below) & 255, int(above) & 255
    if mode == "and":
        return below & above
    if mode == "or":
        return below | above
    if mode == "xor":
        return below ^ above
    if mode == "not":
        return (~above) & 255
    if mode == "nand":
        return (~(below & above)) & 255
    if mode == "nor":
        return (~(below | above)) & 255
    if mode == "xnor":
        return (~(below ^ above)) & 255
    return above


def apply_socket_gate(value, mode, other=None):
    """Apply one micro socket gate to a value.

    The socket value is operand A and the gate's B input is `other`.  An
    unconnected B defaults to all ones, so NAND and NOT invert a socket as
    before while the other gates have a useful reference operand.  Colour
    gates operate bitwise per RGB channel and keep A's alpha; numbers use
    continuous 0..1 logic.
    """
    if mode not in MICRO_GATES:
        return value
    if isinstance(value, ChannelValue):
        below = 1.0 if other is None else _clamp(_as_value(other))
        return ChannelValue(_gate_number(below, _clamp(float(value)), mode),
                            value.channel, value.alpha)
    if isinstance(value, (int, float)):
        below = 1.0 if other is None else _clamp(_as_value(other))
        return _gate_number(below, _clamp(float(value)), mode)
    try:
        red, green, blue, alpha = value
    except (TypeError, ValueError):
        return value
    below = DEFAULT_COLOR if other is None else _as_color(other)
    return (_gate_channel(below[0], red, mode),
            _gate_channel(below[1], green, mode),
            _gate_channel(below[2], blue, mode), alpha)


_LAYER_VALUE_NAMES = frozenset((
    "Color", "Red", "Green", "Blue", "Alpha", "Opacity", "Hue",
    "Saturation", "HSL Saturation", "Brightness", "Lightness",
    "Luminance", "Gamma", "Chroma"))
_HSV_NAMES = frozenset(("Hue", "Saturation", "Brightness"))
_HLS_NAMES = frozenset(("HSL Saturation", "Lightness"))


def layer_values(pixel, names=None):
    """Extract channels and colour measurements from one RGBA pixel.

    Channel values, opacity and colour measurements are 0..1; hue is in
    degrees. Gamma is gamma-corrected luminance, a perceptual brightness.
    `names` limits the work to the outputs a graph actually consumes.
    """
    red, green, blue, alpha = pixel
    r, g, b = red / 255.0, green / 255.0, blue / 255.0
    if names is None:
        wanted = _LAYER_VALUE_NAMES
    elif isinstance(names, (set, frozenset)):
        wanted = names
    else:
        wanted = set(names)
    values = {}
    if "Color" in wanted:
        values["Color"] = pixel
    alpha_value = alpha / 255.0
    if "Red" in wanted:
        values["Red"] = ChannelValue(r, "red", alpha_value)
    if "Green" in wanted:
        values["Green"] = ChannelValue(g, "green", alpha_value)
    if "Blue" in wanted:
        values["Blue"] = ChannelValue(b, "blue", alpha_value)
    if "Alpha" in wanted:
        values["Alpha"] = alpha_value
    if "Opacity" in wanted:
        values["Opacity"] = alpha / 255.0
    if not _HSV_NAMES.isdisjoint(wanted):
        hue, saturation, brightness = colorsys.rgb_to_hsv(r, g, b)
        if "Hue" in wanted:
            values["Hue"] = hue * 360.0
        if "Saturation" in wanted:
            values["Saturation"] = saturation
        if "Brightness" in wanted:
            values["Brightness"] = brightness
    if not _HLS_NAMES.isdisjoint(wanted):
        _h, lightness, hsl_saturation = colorsys.rgb_to_hls(r, g, b)
        if "HSL Saturation" in wanted:
            values["HSL Saturation"] = hsl_saturation
        if "Lightness" in wanted:
            values["Lightness"] = lightness
    if "Luminance" in wanted or "Gamma" in wanted:
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        if "Luminance" in wanted:
            values["Luminance"] = luminance
        if "Gamma" in wanted:
            values["Gamma"] = luminance ** (1.0 / 2.2)
    if "Chroma" in wanted:
        values["Chroma"] = max(r, g, b) - min(r, g, b)
    return values


_RAMP_KEYS = {"luminance": "Luminance", "saturation": "Saturation",
              "lightness": "Lightness", "brightness": "Brightness",
              "chroma": "Chroma"}


def _ramp_source(pixel, mode):
    """The default 0..1 source for an unconnected Color Ramp input."""
    mode = str(mode or "luminance")
    if mode == "hue":
        return layer_values(pixel, ("Hue",))["Hue"] / 360.0
    key = _RAMP_KEYS.get(mode, "Luminance")
    return layer_values(pixel, (key,)).get(key, 0.0)


_GRADIENT_FALLBACK = ((0.0, (0, 0, 0, 255)),
                      (1.0, (255, 255, 255, 255)))


@lru_cache(maxsize=256)
def _parse_gradient_cached(text):
    stops = []
    for piece in text.split(","):
        piece = piece.strip()
        if ":" not in piece:
            continue
        position_text, color_value = piece.split(":", 1)
        try:
            position = float(position_text)
        except (TypeError, ValueError):
            continue
        stops.append((max(0.0, min(1.0, position)),
                      parse_color(color_value.strip())))
    if not stops:
        return _GRADIENT_FALLBACK
    stops.sort(key=lambda stop: stop[0])
    return tuple(stops)


def parse_gradient(text):
    """Parse "position:RRGGBBAA,..." into sorted gradient stops."""
    return _parse_gradient_cached(str(text or ""))


def gradient_text(stops):
    return ",".join("%g:%s" % (round(float(position), 4), color_text(color))
                    for position, color in sorted(stops,
                                                  key=lambda stop: stop[0]))


def reverse_gradient(text):
    """Mirror a gradient: stop positions and their colours swap ends."""
    stops = parse_gradient(text)
    return gradient_text([(1.0 - position, color)
                          for position, color in reversed(stops)])


def gradient_color(text, value, interpolation="linear"):
    """Sample a gradient at a 0..1 value, interpolating RGBA stops."""
    return _gradient_color_stops(parse_gradient(text), value, interpolation)


def _gradient_color_stops(stops, value, interpolation="linear"):
    """Sample already-parsed gradient stops.

    Linear blends between stops, Constant holds the left stop until the next
    one, and Smooth eases with a smoothstep curve.
    """
    mode = str(interpolation or "linear").strip().lower()
    if mode not in RAMP_INTERPOLATIONS:
        mode = "linear"
    try:
        amount = _clamp(float(value))
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= stops[0][0]:
        return stops[0][1]
    if amount >= stops[-1][0]:
        return stops[-1][1]
    for index in range(1, len(stops)):
        left_position, left_color = stops[index - 1]
        right_position, right_color = stops[index]
        if amount <= right_position:
            span = right_position - left_position
            fraction = 0.0 if span <= 0 else (amount - left_position) / span
            if mode == "constant":
                if amount < right_position:
                    return left_color
                continue
            if mode == "smooth":
                fraction = fraction * fraction * (3.0 - 2.0 * fraction)
            return tuple(
                int(round(left + (right - left) * fraction))
                for left, right in zip(left_color, right_color))
    return stops[-1][1]


# ------------------------------------------------------------------ noise
_PERM_CACHE = {}


def _permutation(seed):
    key = int(seed) & 0xFFFF
    table = _PERM_CACHE.get(key)
    if table is None:
        rng = random.Random(key)
        values = list(range(256))
        rng.shuffle(values)
        table = values + values
        _PERM_CACHE[key] = table
    return table


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(a, b, t):
    return a + (b - a) * t


_GRADIENTS = ((1, 1), (-1, 1), (1, -1), (-1, -1),
              (1, 0), (-1, 0), (0, 1), (0, -1))


def _grad(hash_value, x, y):
    gx, gy = _GRADIENTS[hash_value & 7]
    return gx * x + gy * y


def perlin2(x, y, seed=0):
    """Classic 2D Perlin noise in 0..1, deterministic for a seed."""
    table = _permutation(seed)
    xi = int(math.floor(x)) & 255
    yi = int(math.floor(y)) & 255
    xf = x - math.floor(x)
    yf = y - math.floor(y)
    u = _fade(xf)
    v = _fade(yf)
    aa = table[table[xi] + yi]
    ab = table[table[xi] + yi + 1]
    ba = table[table[xi + 1] + yi]
    bb = table[table[xi + 1] + yi + 1]
    low = _lerp(_grad(aa, xf, yf), _grad(ba, xf - 1, yf), u)
    high = _lerp(_grad(ab, xf, yf - 1), _grad(bb, xf - 1, yf - 1), u)
    return _clamp(0.5 + 0.70710678 * _lerp(low, high, v))


def hue_shift(color, degrees):
    """Rotate the hue of an RGBA tuple, keeping saturation, value and alpha."""
    red, green, blue, alpha = color
    h, s, v = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)
    h = (h + float(degrees) / 360.0) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(h, s, v)
    return (int(round(red * 255)), int(round(green * 255)),
            int(round(blue * 255)), int(alpha))


# --------------------------------------------------------------------- nodes
class Node(object):
    """One graph node: a type, canvas position and parameter values."""

    __slots__ = ("id", "type", "x", "y", "params",
                 "_inputs_key", "_inputs_cache",
                 "_outputs_key", "_outputs_cache",
                 "_sources_key", "_sources_cache",
                 "_gradient_text", "_gradient_stops")

    def __init__(self, node_id, node_type, x=40.0, y=40.0, params=None):
        self.id = int(node_id)
        self.type = node_type if node_type in NODE_TYPES else "layer"
        self.x = float(x)
        self.y = float(y)
        self.params = default_params(self.type)
        if params:
            self.params.update(params)
        self._inputs_key = None
        self._inputs_cache = ()
        self._outputs_key = None
        self._outputs_cache = ()
        self._sources_key = None
        self._sources_cache = ()
        self._gradient_text = None
        self._gradient_stops = ()

    def param(self, name):
        return self.params.get(
            name, _PARAM_DEFAULTS.get(self.type, {}).get(name))

    def to_state(self):
        return {"id": self.id, "type": self.type, "x": self.x, "y": self.y,
                "params": dict(self.params)}

    @classmethod
    def from_state(cls, state):
        params = state.get("params") if isinstance(state, dict) else None
        node = cls(state.get("id", 0), state.get("type", "layer"),
                   state.get("x", 40.0), state.get("y", 40.0),
                   params if isinstance(params, dict) else None)
        # Projects saved before the adjustable inputs exposed all eight.
        if node.type == "value" and not (isinstance(params, dict)
                                          and "inputs" in params):
            node.params["inputs"] = len(VALUE_INPUT_NAMES)
        return node


class NodeGraph(object):
    """A small directed graph of render nodes for one layer."""

    __slots__ = ("nodes", "links", "next_id", "_order", "_inputs",
                 "_used_outputs", "_outputs", "last_error")

    def __init__(self):
        self.nodes = {}
        self.links = []
        self.next_id = 1
        self._order = None
        self._inputs = None
        self._used_outputs = None
        self._outputs = ()
        self.last_error = ""

    # ---------------------------------------------------------- bookkeeping
    def _invalidate(self):
        self._order = None
        self._inputs = None
        self._used_outputs = None
        self._outputs = ()

    def is_empty(self):
        return not self.nodes

    def output_ids(self):
        return [node.id for node in self.nodes.values()
                if node.type == "output"]

    def is_active(self):
        """True when an Output node has a colour feeding it."""
        outputs = set(self.output_ids())
        return any(link["to"] in outputs for link in self.links)

    def is_passthrough(self):
        """True for the untouched Layer -> Output starter graph."""
        if len(self.nodes) != 2 or len(self.links) != 1:
            return False
        layer = next((node for node in self.nodes.values()
                      if node.type == "layer"), None)
        output = next((node for node in self.nodes.values()
                       if node.type == "output"), None)
        if layer is None or output is None:
            return False
        link = self.links[0]
        return (link["from"] == layer.id and link["from_port"] == "Color"
                and link["to"] == output.id and link["to_port"] == "Color"
                and not layer.params.get("@gate:out:Color")
                and not output.params.get("@gate:in:Color"))

    def add_node(self, node_type, x=40.0, y=40.0, params=None):
        if node_type not in NODE_TYPES:
            raise ValueError("unknown node type: %s" % (node_type,))
        node = Node(self.next_id, node_type, x, y, params)
        self.next_id += 1
        self.nodes[node.id] = node
        self._invalidate()
        return node

    def duplicate_node(self, node_id, dx=24.0, dy=24.0):
        """Copy a node and its parameters, without copying any wires."""
        source = self.nodes.get(int(node_id))
        if source is None:
            return None
        return self.add_node(
            source.type,
            max(0.0, source.x + float(dx)),
            max(0.0, source.y + float(dy)),
            dict(source.params))

    def remove_node(self, node_id):
        node_id = int(node_id)
        if node_id not in self.nodes:
            return False
        del self.nodes[node_id]
        self.links = [link for link in self.links
                      if link["from"] != node_id and link["to"] != node_id]
        self._invalidate()
        return True

    def link(self, from_id, from_port, to_id, to_port):
        """Connect an output to an input; inputs may take several wires."""
        from_id, to_id = int(from_id), int(to_id)
        source = self.nodes.get(from_id)
        target = self.nodes.get(to_id)
        if source is None or target is None or from_id == to_id:
            return False
        outputs = dict(node_outputs(source))
        inputs = dict(node_all_inputs(target))
        if from_port not in outputs or to_port not in inputs:
            return False
        if self._reaches(to_id, from_id):
            return False
        for link in self.links:
            if (link["from"] == from_id and link["from_port"] == from_port
                    and link["to"] == to_id
                    and link["to_port"] == to_port):
                return False
        self.links.append({"from": from_id, "from_port": from_port,
                           "to": to_id, "to_port": to_port})
        self._invalidate()
        return True

    def unlink(self, from_id, from_port, to_id, to_port):
        """Remove one specific wire, leaving other wires intact."""
        before = len(self.links)
        self.links = [link for link in self.links
                      if not (link["from"] == int(from_id)
                              and link["from_port"] == from_port
                              and link["to"] == int(to_id)
                              and link["to_port"] == to_port)]
        if len(self.links) != before:
            self._invalidate()
            return True
        return False

    def unlink_input(self, to_id, to_port):
        to_id = int(to_id)
        before = len(self.links)
        self.links = [link for link in self.links
                      if not (link["to"] == to_id
                              and link["to_port"] == to_port)]
        if len(self.links) != before:
            self._invalidate()
            return True
        return False

    def links_into(self, to_id, to_port):
        to_id = int(to_id)
        return [link for link in self.links
                if link["to"] == to_id and link["to_port"] == to_port]

    def link_into(self, to_id, to_port):
        links = self.links_into(to_id, to_port)
        return links[0] if links else None

    def _reaches(self, start, target):
        """True when `target` is downstream of `start`."""
        stack = [int(start)]
        seen = set()
        while stack:
            node_id = stack.pop()
            if node_id == target:
                return True
            if node_id in seen:
                continue
            seen.add(node_id)
            for link in self.links:
                if link["from"] == node_id:
                    stack.append(link["to"])
        return False

    # ------------------------------------------------------------- ordering
    def _compile(self):
        if self._order is not None:
            return
        self._inputs = {}
        used_outputs = {}
        for link in self.links:
            self._inputs.setdefault((link["to"], link["to_port"]),
                                    []).append(link)
            used_outputs.setdefault(link["from"], set()).add(
                link["from_port"])
        self._used_outputs = used_outputs
        order = []
        seen = set()
        visiting = set()

        def visit(node_id):
            if node_id in seen:
                return
            if node_id in visiting:
                return
            node = self.nodes.get(node_id)
            if node is None:
                return
            visiting.add(node_id)
            for name, _kind in node_all_inputs(node):
                for link in self._inputs.get((node_id, name), ()):
                    visit(link["from"])
            visiting.discard(node_id)
            seen.add(node_id)
            order.append(node_id)

        outputs = []
        for node in self.nodes.values():
            if node.type == "output":
                outputs.append(node.id)
                visit(node.id)
        self._order = order
        self._outputs = tuple(outputs)

    # ------------------------------------------------------------ evaluation
    def _input_kind(self, node, name):
        """The kind of a normal input or a micro gate's hidden B input."""
        kind = _INPUT_KINDS[node.type].get(name)
        if kind is not None:
            return kind
        info = gate_input_info(name)
        if info is not None:
            side, source_name = info
            if side == "out":
                return dict(node_outputs(node)).get(source_name, COLOR)
            return _INPUT_KINDS[node.type].get(source_name, COLOR)
        return COLOR

    def _gate_operand(self, node, side, name, values, layer_px, below_px):
        mode = node.params.get("@gate:%s:%s" % (side, name))
        if mode not in MICRO_GATES or mode == "not":
            return None
        return self._input(node, gate_input_name(side, name), values,
                           layer_px, below_px)

    def _input(self, node, name, values, layer_px, below_px):
        """Resolve one input socket, then apply its micro gate."""
        links = self._inputs.get((node.id, name))
        kind = self._input_kind(node, name)
        value = None
        if links:
            received = []
            for link in links:
                source = values.get(link["from"], {})
                if link["from_port"] in source:
                    raw = source[link["from_port"]]
                    received.append(_as_value(raw) if kind == VALUE
                                    else _as_color(raw))
            if received:
                value = (sum(received) if kind == VALUE
                         else _average_colors(received))
        if value is None:
            if is_gate_input(name):
                # A gate with no B wire behaves as if its other operand is
                # all ones: NAND still inverts, AND still passes the value.
                value = DEFAULT_COLOR if kind == COLOR else 1.0
            elif node.type in ("mask", "mask_apply") and name == "Color":
                value = layer_px
            elif node.type == "mask_apply" and name == "Mask":
                value = 1.0
            elif node.type == "ramp" and name == "Value":
                value = _ramp_source(layer_px, node.param("mode"))
            elif kind == VALUE:
                # An unconnected number socket uses the node's own parameter,
                # so "Shift 120" works before anything is wired into it.
                try:
                    value = float(node.params.get(name.lower(), 0.0))
                except (TypeError, ValueError):
                    value = 0.0
            # Implicit defaults make a single added node useful before wiring:
            # A is the layer, B is the stack below.
            elif node.type in ("operation", "gate", "blend"):
                value = layer_px if name == "A" else below_px
            elif node.type == "hue" and name == "Color":
                value = layer_px
            else:
                value = TRANSPARENT
        if is_gate_input(name):
            return value
        gate = node.params.get("@gate:in:" + name)
        if gate not in MICRO_GATES:
            return value
        return apply_socket_gate(
            value, gate,
            self._gate_operand(node, "in", name, values, layer_px,
                               below_px))

    def _eval_node(self, node, values, x, y, width, height, layer_px,
                   below_px):
        node_type = node.type
        if node_type == "layer":
            fields = (None if self._used_outputs is None
                      else self._used_outputs.get(node.id, ()))
            return layer_values(layer_px, fields)
        if node_type == "below":
            return {"Color": below_px}
        if node_type == "output":
            return {"Color": self._input(node, "Color", values, layer_px,
                                         below_px)}
        if node_type == "constant":
            return {"Color": parse_color(node.param("color"))}
        if node_type == "value":
            try:
                total = float(node.param("value"))
            except (TypeError, ValueError):
                total = 0.0
            for name, _kind in node_inputs(node):
                total += self._input(node, name, values, layer_px, below_px)
            return {"Value": total}
        if node_type in ("operation", "gate", "blend"):
            a = self._input(node, "A", values, layer_px, below_px)
            b = self._input(node, "B", values, layer_px, below_px)
            mode = str(node.param("mode") or "add")
            if node_type == "operation":
                from .model import combine
                rgb = [combine(b[index], a[index], mode, 255)
                       for index in range(3)]
            elif node_type == "gate":
                from .model import gate_value
                rgb = [gate_value(b[index], a[index], mode)
                       for index in range(3)]
            else:
                from .model import combine
                rgb = [combine(b[index], a[index], mode, 255)
                       for index in range(3)]
            return {"Color": (rgb[0], rgb[1], rgb[2], a[3])}
        if node_type == "perlin":
            try:
                scale = float(self._input(node, "Scale", values, layer_px,
                                          below_px)) or 32.0
            except (TypeError, ValueError):
                scale = 32.0
            try:
                seed = int(self._input(node, "Seed", values, layer_px,
                                       below_px))
            except (TypeError, ValueError):
                seed = 0
            value = perlin2(x / scale, y / scale, seed)
            grey = int(round(value * 255))
            return {"Color": (grey, grey, grey, 255), "Value": value}
        if node_type == "hue":
            color = self._input(node, "Color", values, layer_px, below_px)
            try:
                shift = float(self._input(node, "Shift", values, layer_px,
                                          below_px))
            except (TypeError, ValueError):
                shift = float(node.param("shift") or 0)
            return {"Color": hue_shift(color, shift)}
        if node_type == "ramp":
            source = self._input(node, "Value", values, layer_px, below_px)
            text = node.param("gradient")
            if not node._gradient_stops or node._gradient_text != text:
                node._gradient_stops = parse_gradient(text)
                node._gradient_text = text
            return {"Color": _gradient_color_stops(
                node._gradient_stops, source, node.param("interpolation"))}
        if node_type == "mask":
            color = self._input(node, "Color", values, layer_px, below_px)
            value = mask_measurement(color, node.param("source"))
            if str(node.param("invert") or "").lower() in ("yes", "true", "1"):
                value = 1.0 - value
            value = _clamp(value)
            grey = int(round(value * 255))
            return {"Color": (grey, grey, grey, 255), "Value": value}
        if node_type == "mask_apply":
            color = self._input(node, "Color", values, layer_px, below_px)
            try:
                amount = float(self._input(node, "Mask", values, layer_px,
                                           below_px))
            except (TypeError, ValueError):
                amount = 0.0
            if not math.isfinite(amount):
                amount = 0.0
            amount = _clamp(amount)
            return {"Color": (color[0], color[1], color[2],
                              int(round(color[3] * amount)))}
        if node_type == "math":
            try:
                left = float(self._input(node, "A", values, layer_px,
                                         below_px))
            except (TypeError, ValueError):
                left = 0.0
            try:
                right = float(self._input(node, "B", values, layer_px,
                                          below_px))
            except (TypeError, ValueError):
                right = 0.0
            mode = str(node.param("mode") or "add")
            if mode == "subtract":
                result = left - right
            elif mode == "multiply":
                result = left * right
            elif mode == "divide":
                result = left / right if right else 0.0
            elif mode == "min":
                result = min(left, right)
            elif mode == "max":
                result = max(left, right)
            else:
                result = left + right
            return {"Value": result}
        return {}

    def eval_pixel(self, x, y, layer_px, below_px, width, height):
        """Evaluate the graph at one pixel and return its RGBA colour."""
        self._compile()
        values = {}
        nodes_by_id = self.nodes
        used_by_id = self._used_outputs
        try:
            for node_id in self._order:
                node = nodes_by_id[node_id]
                result = self._eval_node(
                    node, values, x, y, width, height, layer_px, below_px)
                # Gates belong to one socket. Only outputs with a live wire
                # matter; duplicate outputs copy the raw value, not a gated
                # base output.
                used = (None if used_by_id is None
                        else used_by_id.get(node_id, ()))
                only = next(iter(used)) if used and len(used) == 1 else None
                if used != () and not (
                        only is not None and len(result) == 1
                        and only in result
                        and not node.params.get("@gate:out:" + only)):
                    raw = dict(result)
                    for name, source_name in node_output_sources(node):
                        if used is not None and name not in used:
                            continue
                        if name in raw:
                            source_value = raw[name]
                        elif source_name in raw:
                            source_value = raw[source_name]
                        else:
                            continue
                        gate = node.params.get("@gate:out:" + name)
                        if gate not in MICRO_GATES:
                            result[name] = source_value
                            continue
                        other = self._gate_operand(
                            node, "out", name, values, layer_px, below_px)
                        result[name] = apply_socket_gate(
                            source_value, gate, other)
                values[node_id] = result
            for node_id in self._outputs:
                if not self._inputs.get((node_id, "Color")):
                    continue
                source = values.get(node_id) or {}
                if "Color" in source:
                    return source["Color"]
        except Exception as exc:  # noqa: BLE001 - keep the canvas alive
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
        return TRANSPARENT

    # ---------------------------------------------------------- persistence
    def to_state(self):
        return {
            "next_id": self.next_id,
            "nodes": [node.to_state()
                      for node in sorted(self.nodes.values(),
                                         key=lambda item: item.id)],
            "links": [dict(link) for link in self.links],
        }

    @classmethod
    def from_state(cls, state):
        graph = cls()
        if not isinstance(state, dict):
            return graph
        raw_nodes = state.get("nodes")
        if isinstance(raw_nodes, list):
            for entry in raw_nodes:
                if not isinstance(entry, dict):
                    continue
                node = Node.from_state(entry)
                if node.id <= 0 or node.id in graph.nodes:
                    node.id = graph.next_id
                graph.nodes[node.id] = node
        highest = max([0] + list(graph.nodes))
        graph.next_id = max(int(state.get("next_id") or 0), highest + 1)
        raw_links = state.get("links")
        if isinstance(raw_links, list):
            for entry in raw_links:
                if not isinstance(entry, dict):
                    continue
                graph.link(entry.get("from", 0), entry.get("from_port"),
                           entry.get("to", 0), entry.get("to_port"))
        return graph

    @classmethod
    def starter(cls):
        """Layer -> Output, so Complex Render starts identical to Quick."""
        graph = cls()
        layer = graph.add_node("layer", 40, 70)
        output = graph.add_node("output", 360, 70)
        graph.link(layer.id, "Color", output.id, "Color")
        return graph
