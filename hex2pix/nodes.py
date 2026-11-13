"""Modular per-layer render graphs for Complex Render.

A layer keeps the original Quick Render path (operation, gate and blend
combined per pixel).  A layer in Complex Render mode instead runs a small
node graph: nodes read the layer's own pixel, the stack below it, any selected
layer or group source, and their own parameters, and the graph's Output node
becomes that layer's contribution to the composite.

The module is deliberately free of tkinter so the graph can be unit-tested and
saved to a project without a display.
"""

import array
import colorsys
import math
import random
import sys
import time
import zlib
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
WIRE = "wire"
VALUE_INPUT_NAMES = ("A", "B", "C", "D", "E", "F", "G", "H")
WIRE_INPUT_NAMES = VALUE_INPUT_NAMES
GATE_COLOR_DEFAULT = "E5B0FF"

# A port value is either an RGBA tuple of ints (0..255) or a float.
TRANSPARENT = (0, 0, 0, 0)
_COMBINE_LAYER = None
_COMBINE_CHANNEL = None
_GATE_CHANNEL = None
DEFAULT_COLOR = (255, 255, 255, 255)

# Bumped on every in-place node parameter edit.  Compiled evaluators cache
# parameters (gradient stops, modes, counts), and the node editor and tests
# also write ``node.params[name] = value`` without calling param_changed(),
# so the compilation records this value and rebuilds when it moved.
_PARAM_EPOCH = [0]


def param_epoch():
    """The current in-place parameter edit counter.

    Callers that cache anything derived from a graph can compare it with the
    value they saw before instead of walking every node's parameters.
    """
    return _PARAM_EPOCH[0]

# The render sink accepts every value a node can produce. RGB sockets keep
# their channel tint; numeric measurements render as grayscale.
OUTPUT_INPUTS = (
    ("Color", COLOR), ("Value", VALUE),
    ("Red", COLOR), ("Green", COLOR), ("Blue", COLOR),
    ("Alpha", VALUE), ("Opacity", VALUE), ("Hue", VALUE),
    ("Saturation", VALUE), ("HSL Saturation", VALUE),
    ("Brightness", VALUE), ("Lightness", VALUE), ("Luminance", VALUE),
    ("Gamma", VALUE), ("Chroma", VALUE),
)

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
LAYER_OUTPUTS = (("Color", COLOR), ("Red", VALUE), ("Green", VALUE),
                  ("Blue", VALUE), ("Alpha", VALUE), ("Opacity", VALUE),
                  ("Hue", VALUE), ("Saturation", VALUE),
                  ("HSL Saturation", VALUE), ("Brightness", VALUE),
                  ("Lightness", VALUE), ("Luminance", VALUE),
                  ("Gamma", VALUE), ("Chroma", VALUE))
_LAYER_OUTPUT_PORTS = frozenset(name for name, _kind in LAYER_OUTPUTS)

# Input A is the layer/source side of an operation and B is the stack below.
NODE_TYPES = {
    "layer": {
        "label": "Layer",
        "hint": "the layer's colour, channels and measurements",
        "inputs": (),
        "outputs": LAYER_OUTPUTS,
        "params": (),
    },
    "below": {
        "label": "Below",
        "hint": "the composited stack below",
        "inputs": (),
        "outputs": (("Color", COLOR),),
        "params": (),
    },
    "layer_source": {
        "label": "Layer Source",
        "hint": "another layer's colour, channels and measurements",
        "inputs": (),
        "outputs": LAYER_OUTPUTS,
        "params": (_choice("layer", "Layer", (), {}, ""),),
    },
    "group_source": {
        "label": "Group Source",
        "hint": "a layer group's composited colour and measurements",
        "inputs": (),
        "outputs": LAYER_OUTPUTS,
        "params": (_choice("group", "Group", (), {}, ""),),
    },
    "output": {
        "label": "Output",
        "hint": "the layer's contribution; accepts any value",
        "inputs": OUTPUT_INPUTS,
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
                           "nand"),
                   _color_param("color", "Gate / wire colour",
                                GATE_COLOR_DEFAULT)),
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
    "splitter": {
        "label": "Wire Splitter",
        "hint": "join the inputs, then copy the wire to every output",
        "inputs": tuple((name, WIRE) for name in WIRE_INPUT_NAMES),
        "outputs": (("Wire", WIRE),),
        "params": (_count_param("inputs", "Inputs", 1,
                                len(WIRE_INPUT_NAMES), 1),),
    },
    "joiner": {
        "label": "Wire Joiner",
        "hint": "merge several wires into one output",
        "inputs": tuple((name, WIRE) for name in WIRE_INPUT_NAMES),
        "outputs": (("Wire", WIRE),),
        "params": (_count_param("inputs", "Inputs", 2,
                                len(WIRE_INPUT_NAMES), 2),),
    },
    "router": {
        "label": "Wire Router",
        "hint": "route A to Out 1, B to Out 2, and so on",
        "inputs": tuple((name, WIRE) for name in WIRE_INPUT_NAMES),
        "outputs": tuple(("Out %d" % index, WIRE)
                         for index in range(1, len(WIRE_INPUT_NAMES) + 1)),
        "params": (_count_param("inputs", "Inputs", 2,
                                len(WIRE_INPUT_NAMES), 2),),
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

NODE_ORDER = ("layer", "below", "layer_source", "group_source",
              "operation", "gate", "blend", "perlin", "hue", "ramp",
              "mask", "mask_apply", "splitter", "joiner", "router",
              "constant", "value", "math", "output")

DEFAULT_OUTPUT_COUNTS = {"splitter": 3, "joiner": 1, "router": 2}

_INPUT_KINDS = {key: dict(spec["inputs"]) for key, spec in NODE_TYPES.items()}
_PARAM_DEFAULTS = {
    key: {param["name"]: param["default"] for param in spec["params"]}
    for key, spec in NODE_TYPES.items()
}


def node_spec(node_type):
    return NODE_TYPES.get(node_type) or NODE_TYPES["layer"]


def node_reversed(node):
    """True when a node's former inputs and outputs have changed roles."""
    return bool(node.params.get("@reverse"))


def base_node_inputs(node):
    """The input sockets a node has before its direction is reversed.

    Most node types have fixed sockets. Value adders and the wire-routing
    nodes can hide trailing A-H sockets based on their input count.
    """
    if node.type not in ("value", "splitter", "joiner", "router"):
        return NODE_TYPES[node.type]["inputs"]
    if node.type == "value":
        default = 0
        kind = VALUE
    else:
        default = 1 if node.type == "splitter" else 2
        kind = WIRE
    try:
        count = int(node.params.get("inputs", default))
    except (TypeError, ValueError):
        count = default
    count = max(0, min(len(VALUE_INPUT_NAMES), count))
    key = (node.type, count)
    if node._inputs_key != key:
        node._inputs_cache = tuple((name, kind)
                                   for name in VALUE_INPUT_NAMES[:count])
        node._inputs_key = key
    return node._inputs_cache


def base_node_outputs(node):
    """The output sockets a node has before its direction is reversed.

    Multi-output nodes can hide trailing outputs; a node with one output can
    be given duplicates ("Color 2", "Color 3", ...) for easy fan-out.
    """
    base = NODE_TYPES[node.type]["outputs"]
    if not base:
        return ()
    maximum = len(base) if len(base) > 1 else 8
    default = DEFAULT_OUTPUT_COUNTS.get(node.type, len(base))
    try:
        count = int(node.params.get("outputs", default))
    except (TypeError, ValueError):
        count = default
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


def node_reverse_aliases(node):
    """Map old input names to the new inputs that replace them.

    Reversing turns every old output into an input and every old input into an
    output.  A replacement input is matched to an old input by name, then by
    value kind, then by the first free slot.  For a Gate that maps Color to A,
    leaving B on its default; its A/B outputs expose the normal Color result.
    """
    if not node_reversed(node):
        return {}
    old_inputs = tuple(base_node_inputs(node))
    new_inputs = tuple(base_node_outputs(node))
    key = (old_inputs, new_inputs)
    if node._reverse_key == key:
        return node._reverse_cache
    free = list(range(len(old_inputs)))
    aliases = {}
    for name, kind in new_inputs:
        chosen = next((index for index in free
                       if old_inputs[index][0] == name), None)
        if chosen is None:
            chosen = next((index for index in free
                           if old_inputs[index][1] == kind), None)
        if chosen is None and free:
            chosen = free[0]
        if chosen is None:
            continue
        free.remove(chosen)
        aliases[old_inputs[chosen][0]] = name
    node._reverse_key = key
    node._reverse_cache = aliases
    return aliases


def node_inputs(node):
    """The sockets that currently accept incoming wires."""
    if node_reversed(node):
        return base_node_outputs(node)
    return base_node_inputs(node)


def node_outputs(node):
    """The sockets that currently emit values."""
    if node_reversed(node):
        return base_node_inputs(node)
    return base_node_outputs(node)


def node_output_sources(node):
    """(visible output name, stored output that supplies it) pairs."""
    outputs = node_outputs(node)
    key = (node_reversed(node), outputs)
    if node._sources_key != key:
        if node_reversed(node):
            raw = base_node_outputs(node)
            source = raw[0][0] if raw else "Color"
            node._sources_cache = tuple((name, source)
                                        for name, _kind in outputs)
        else:
            base = NODE_TYPES[node.type]["outputs"]
            node._sources_cache = tuple(
                (name, name if index < len(base) else base[0][0])
                for index, (name, _kind) in enumerate(outputs))
        node._sources_key = key
    return node._sources_cache


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


def _layer_socket_value(pixel, name):
    """One Layer output socket's raw value for a pixel."""
    red, green, blue, alpha = pixel
    alpha_value = alpha / 255.0
    if name == "Color":
        return pixel
    if name == "Red":
        return ChannelValue(red / 255.0, "red", alpha_value)
    if name == "Green":
        return ChannelValue(green / 255.0, "green", alpha_value)
    if name == "Blue":
        return ChannelValue(blue / 255.0, "blue", alpha_value)
    if name in ("Alpha", "Opacity"):
        return alpha_value
    return layer_values(pixel, (name,)).get(name, 0.0)


def _output_socket_colour(name, value):
    """Convert one output socket's value to the colour it renders.

    Red, Green and Blue select that channel from the incoming value, so a
    numeric value renders in the named channel instead of as grey.  Color
    keeps a channel value's tint, and all other sockets render as grayscale.
    """
    if name == "Color":
        return _as_color(value)
    if name in ("Red", "Green", "Blue"):
        colour = _as_color(value)
        index = ("Red", "Green", "Blue").index(name)
        channels = [0, 0, 0, colour[3]]
        channels[index] = colour[index]
        return tuple(channels)
    amount = int(round(_clamp(_as_value(value)) * 255))
    return (amount, amount, amount, 255)


def _channel_tuple(amount, index, alpha):
    channels = [0, 0, 0, alpha]
    channels[index] = int(amount)
    return tuple(channels)


def _layer_measurement_amount(pixel, name):
    return int(round(_clamp(layer_values(pixel, (name,)).get(name, 0.0))
                      * 255))


def _layer_measurement_grey(pixel, name):
    amount = _layer_measurement_amount(pixel, name)
    return (amount, amount, amount, 255)


def _layer_measurement_colour(pixel, name):
    if name == "Color":
        amount = int(round(0.299 * pixel[0] + 0.587 * pixel[1]
                           + 0.114 * pixel[2]))
        return (amount, amount, amount, 255)
    return _layer_measurement_grey(pixel, name)


def _channel_measurement(pixel, name, index):
    amount = _layer_measurement_amount(pixel, name)
    return _channel_tuple(amount, index, 255)


def _compose_output(entries):
    """Render one Output node from its connected sockets.

    Multiple RGB channel sockets compose into one colour instead of the first
    one hiding the rest.  Alpha/opacity sockets supply the alpha.  A Color or
    any other single socket keeps the old first-connected behaviour.
    """
    if not entries:
        return TRANSPARENT
    names = set(name for name, _value in entries)
    if names <= set(("Red", "Green", "Blue", "Alpha", "Opacity")):
        channels = [0, 0, 0, 255]
        alpha = None
        for name, value in entries:
            if name in ("Red", "Green", "Blue"):
                colour = _output_socket_colour(name, value)
                index = ("Red", "Green", "Blue").index(name)
                channels[index] = colour[index]
                alpha = (colour[3] if alpha is None
                         else max(alpha, colour[3]))
            else:
                alpha = int(round(_clamp(_as_value(value)) * 255))
        if alpha is not None:
            channels[3] = alpha
        return tuple(channels)
    name, value = entries[0]
    return _output_socket_colour(name, value)


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


def _join_wires(values):
    """Join wire values: numbers add, colours average."""
    if not values:
        return TRANSPARENT
    if len(values) == 1:
        return values[0]
    if all(isinstance(value, (int, float))
           and not isinstance(value, ChannelValue) for value in values):
        return sum(values)
    return _average_colors([_as_color(value) for value in values])


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


def _ramp_sampler(stops, interpolation="linear"):
    """Compile a gradient into a fast 0..1 -> RGBA sampler.

    The arithmetic mirrors `_gradient_color_stops` exactly, but the mode and
    the stop search are resolved once instead of once per pixel.  A spatial
    graph samples a ramp for every pixel it renders, so this removes a string
    normalisation and a generator from the hot loop.
    """
    mode = str(interpolation or "linear").strip().lower()
    if mode not in RAMP_INTERPOLATIONS:
        mode = "linear"
    first_position, first_color = stops[0]
    last_position, last_color = stops[-1]
    if len(stops) == 2 and mode != "constant":
        span = last_position - first_position
        dr = last_color[0] - first_color[0]
        dg = last_color[1] - first_color[1]
        db = last_color[2] - first_color[2]
        da = last_color[3] - first_color[3]
        r0, g0, b0, a0 = first_color

        def sample(value):
            try:
                amount = _clamp(float(value))
            except (TypeError, ValueError):
                amount = 0.0
            if amount <= first_position:
                return first_color
            if amount >= last_position:
                return last_color
            fraction = 0.0 if span <= 0 else (amount - first_position) / span
            if mode == "smooth":
                fraction = fraction * fraction * (3.0 - 2.0 * fraction)
            return (int(round(r0 + dr * fraction)),
                    int(round(g0 + dg * fraction)),
                    int(round(b0 + db * fraction)),
                    int(round(a0 + da * fraction)))

        return sample

    def sample(value):
        try:
            amount = _clamp(float(value))
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= first_position:
            return first_color
        if amount >= last_position:
            return last_color
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
                return (int(round(left_color[0]
                                  + (right_color[0] - left_color[0]) * fraction)),
                        int(round(left_color[1]
                                  + (right_color[1] - left_color[1]) * fraction)),
                        int(round(left_color[2]
                                  + (right_color[2] - left_color[2]) * fraction)),
                        int(round(left_color[3]
                                  + (right_color[3] - left_color[3]) * fraction)))
        return last_color

    return sample


def bitmap_digest(bitmap, rect):
    """A cheap CRC of one rectangle's pixels, for render caches and tiles."""
    return _bitmap_digest(bitmap, rect)


def _bitmap_digest(bitmap, rect):
    """A cheap CRC of one rectangle's pixels, for render caches.

    `zlib.crc32` reads the buffer without copying it, so checking whether the
    pixels under a graph changed costs far less than evaluating the graph.
    """
    x, y, width, height = rect
    view = memoryview(bitmap.data)
    stride = bitmap.width * 4
    if x == 0 and width == bitmap.width:
        start = y * stride
        return zlib.crc32(view[start:start + height * stride])
    row_bytes = width * 4
    crc = 0
    for row in range(y, y + height):
        start = row * stride + x * 4
        crc = zlib.crc32(view[start:start + row_bytes], crc)
    return crc


def _dag_input(spec, values, layer_px, below_px):
    """Read one compiled socket for the fast DAG evaluator.

    A spec is produced once by `NodeGraph._build_fast_dag`, so this only has
    to fetch a stored value, apply the socket conversion recorded at compile
    time, or fall back to the layer, the stack below, a ramp measurement or a
    constant.
    """
    code = spec[0]
    if code == "wire":
        value = values[spec[1]]
        convert = spec[2]
        return value if convert is None else convert(value)
    if code == "const":
        return spec[1]
    if code == "layer":
        return layer_px
    if code == "below":
        return below_px
    return _ramp_source(layer_px, spec[1])


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


@lru_cache(maxsize=8192)
def hue_shift(color, degrees):
    """Rotate the hue of an RGBA tuple, keeping saturation, value and alpha."""
    red, green, blue, alpha = color
    h, s, v = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)
    h = (h + float(degrees) / 360.0) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(h, s, v)
    return (int(round(red * 255)), int(round(green * 255)),
            int(round(blue * 255)), int(alpha))


@lru_cache(maxsize=8192)
def _fast_hue(color, degrees):
    """The same hue rotation as `hue_shift`, with the colorsys calls inlined.

    The per-pixel fast paths call this hundreds of thousands of times; keeping
    the exact colorsys arithmetic avoids a visible colour change while removing
    four Python function calls per pixel.
    """
    red, green, blue, alpha = color
    r, g, b = red / 255.0, green / 255.0, blue / 255.0
    high = r if r >= g and r >= b else g if g >= b else b
    low = r if r <= g and r <= b else g if g <= b else b
    v = high
    if low == high:
        h = 0.0
        s = 0.0
    else:
        delta = high - low
        s = delta / high
        rc = (high - r) / delta
        gc = (high - g) / delta
        bc = (high - b) / delta
        if r == high:
            h = bc - gc
        elif g == high:
            h = 2.0 + rc - bc
        else:
            h = 4.0 + gc - rc
        h = (h / 6.0) % 1.0
    h = (h + float(degrees) / 360.0) % 1.0
    if s == 0.0:
        rr = gg = bb = v
    else:
        i = int(h * 6.0)
        f = (h * 6.0) - i
        p = v * (1.0 - s)
        q = v * (1.0 - s * f)
        t = v * (1.0 - s * (1.0 - f))
        i %= 6
        if i == 0:
            rr, gg, bb = v, t, p
        elif i == 1:
            rr, gg, bb = q, v, p
        elif i == 2:
            rr, gg, bb = p, v, t
        elif i == 3:
            rr, gg, bb = p, q, v
        elif i == 4:
            rr, gg, bb = t, p, v
        else:
            rr, gg, bb = v, p, q
    return (int(round(rr * 255)), int(round(gg * 255)),
            int(round(bb * 255)), int(alpha))


# --------------------------------------------------------------------- nodes
class _Params(dict):
    """Node parameters that announce their own edits.

    Compiled evaluators cache parameters (gradient stops, modes, counts), but
    the node editor and the tests also write ``node.params[name] = value``
    directly instead of going through `NodeGraph.param_changed`.  Every write
    therefore bumps a shared epoch, which is what `NodeGraph._compile`
    compares against before reusing a compiled path.
    """

    __slots__ = ()

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        _PARAM_EPOCH[0] += 1

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        _PARAM_EPOCH[0] += 1

    def pop(self, *args):
        _PARAM_EPOCH[0] += 1
        return dict.pop(self, *args)

    def popitem(self):
        _PARAM_EPOCH[0] += 1
        return dict.popitem(self)

    def clear(self):
        _PARAM_EPOCH[0] += 1
        dict.clear(self)

    def setdefault(self, key, default=None):
        if key not in self:
            _PARAM_EPOCH[0] += 1
        return dict.setdefault(self, key, default)

    def update(self, *args, **kwargs):
        _PARAM_EPOCH[0] += 1
        dict.update(self, *args, **kwargs)


class Node(object):
    """One graph node: a type, canvas position and parameter values."""

    __slots__ = ("id", "type", "x", "y", "params",
                 "_inputs_key", "_inputs_cache",
                 "_outputs_key", "_outputs_cache",
                 "_sources_key", "_sources_cache",
                 "_reverse_key", "_reverse_cache",
                 "_gradient_text", "_gradient_stops")

    def __init__(self, node_id, node_type, x=40.0, y=40.0, params=None):
        try:
            self.id = int(node_id)
        except (TypeError, ValueError, OverflowError):
            self.id = 0
        self.type = (node_type if isinstance(node_type, str)
                     and node_type in NODE_TYPES else "layer")
        try:
            self.x = float(x)
            self.y = float(y)
        except (TypeError, ValueError, OverflowError):
            self.x, self.y = 40.0, 40.0
        if not math.isfinite(self.x):
            self.x = 40.0
        if not math.isfinite(self.y):
            self.y = 40.0
        # Keep corrupt project coordinates within a range Tk can draw.
        self.x = max(-1000000.0, min(1000000.0, self.x))
        self.y = max(-1000000.0, min(1000000.0, self.y))
        self.params = _Params(default_params(self.type))
        if isinstance(params, dict):
            self.params.update(params)
        self._inputs_key = None
        self._inputs_cache = ()
        self._outputs_key = None
        self._outputs_cache = ()
        self._sources_key = None
        self._sources_cache = ()
        self._reverse_key = None
        self._reverse_cache = {}
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
        state = state if isinstance(state, dict) else {}
        params = state.get("params")
        node = cls(state.get("id", 0), state.get("type", "layer"),
                   state.get("x", 40.0), state.get("y", 40.0),
                   params if isinstance(params, dict) else None)
        # Projects saved before the adjustable inputs exposed all eight.
        if node.type == "value" and not (isinstance(params, dict)
                                          and "inputs" in params):
            node.params["inputs"] = len(VALUE_INPUT_NAMES)
        return node


def _as_node_id(value):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


class NodeGraph(object):
    """A small directed graph of render nodes for one layer."""

    __slots__ = ("nodes", "links", "next_id", "_order", "_inputs",
                 "_used_outputs", "_outputs", "_fast_chain", "_fast_dag",
                 "_fast_shifts", "_fast_direct", "_direct_spec", "_fast_spatial",
                 "_fast_below_free", "_fast_layer_free", "_fast_dag_below_free",
                 "_fast_dag_layer_free", "_fast_dag_external",
                 "last_render_cost", "revision",
                 "_render_cache", "_render_cache_bytes", "_param_epoch",
                 "_cache_enabled", "_pixel_cache",
                 "_spatial", "_source_layers", "_source_groups",
                 "_source_layer_map", "_group_member_map", "_source_indexed",
                 "_output_gate_ids", "_output_alias_ids", "_simple_inputs",
                 "_default_inputs", "_source_nodes",
                 "_spatial_sources_only", "_failed_frame", "_active_layer",
                 "last_error")

    def __init__(self):
        self.nodes = {}
        self.links = []
        self.next_id = 1
        self._order = None
        self._param_epoch = None
        self._inputs = None
        self._used_outputs = None
        self._outputs = ()
        self._fast_chain = None
        self._fast_dag = None
        self._fast_shifts = None
        self._fast_direct = False
        self._direct_spec = ()
        self._fast_spatial = False
        self._fast_below_free = False
        self._fast_layer_free = False
        self._fast_dag_below_free = False
        self._fast_dag_layer_free = False
        self._fast_dag_external = False
        # Seconds per pixel of the last real bulk render, so the editor can
        # size its preview tiles from measured cost instead of node counts.
        self.last_render_cost = 0.0
        # Bumped whenever the compiled graph is rebuilt, so the editor can tell
        # an edited graph from an unchanged one without inspecting every node.
        self.revision = 0
        self._render_cache = {}
        self._render_cache_bytes = 0
        self._cache_enabled = False
        self._pixel_cache = {}
        self._spatial = None
        self._source_layers = None
        self._source_groups = None
        self._source_layer_map = {}
        self._group_member_map = {}
        self._source_indexed = False
        self._output_gate_ids = frozenset()
        self._output_alias_ids = frozenset()
        self._simple_inputs = {}
        self._default_inputs = {}
        self._source_nodes = ()
        self._spatial_sources_only = False
        self._failed_frame = False
        self._active_layer = None
        self.last_error = ""

    # ---------------------------------------------------------- bookkeeping
    def _invalidate(self):
        self.revision += 1
        self._order = None
        self._param_epoch = None
        self._inputs = None
        self._used_outputs = None
        self._outputs = ()
        self._fast_chain = None
        self._fast_dag = None
        self._fast_shifts = None
        self._fast_direct = False
        self._direct_spec = ()
        self._fast_spatial = False
        self._fast_below_free = False
        self._fast_layer_free = False
        self._fast_dag_below_free = False
        self._fast_dag_layer_free = False
        self._fast_dag_external = False
        self._render_cache = {}
        self._render_cache_bytes = 0
        self._cache_enabled = False
        self._pixel_cache = {}
        self._spatial = None
        self._output_gate_ids = frozenset()
        self._output_alias_ids = frozenset()
        self._simple_inputs = {}
        self._default_inputs = {}
        self._source_nodes = ()
        self._spatial_sources_only = False
        self._failed_frame = False
        # A repaired graph should not keep showing the previous render's
        # error in the Debug panel or the node editor status line.
        self.last_error = ""

    def param_changed(self):
        """Forget compiled values that depend on node parameters."""
        self._invalidate()

    def begin_frame(self, layers=None, groups=None, active_layer=None):
        """Start one bitmap render and supply document sources.

        `layers` and `groups` let Layer Source and Group Source nodes read the
        document that owns this graph.  `active_layer` lets the compiled path
        reuse the pixel it is already rendering instead of reading the same
        bitmap again.  These values are not part of the saved graph, so opening
        the same project elsewhere resolves its own layers.
        """
        if layers is not None:
            self._source_layers = tuple(layers)
            self._index_source_layers()
        if groups is not None:
            self._source_groups = dict(groups)
        self._active_layer = active_layer
        self.last_error = ""
        self._failed_frame = False
        self._cache_enabled = True
        self._pixel_cache = {}
        if self._spatial is None:
            # Only nodes reachable from an Output can affect the pixels, so
            # disconnected spatial nodes must not disable the per-colour cache.
            self._compile()
            spatial = [self.nodes.get(node_id) for node_id in self._order]
            spatial = [node for node in spatial if node is not None
                       and node.type in ("perlin", "layer_source",
                                         "group_source")]
            self._spatial = bool(spatial)
            self._source_nodes = tuple(
                node for node in spatial
                if node.type in ("layer_source", "group_source"))
            # Layer and group reads vary by pixel but their source value is
            # part of the cache key below, so repeated source colours can
            # still reuse the whole branch result.  Noise varies continuously
            # and would make the key effectively unique.
            self._spatial_sources_only = bool(
                self._source_nodes
                and not any(node.type == "perlin" for node in spatial))

    def is_empty(self):
        return not self.nodes

    def output_ids(self):
        return [node.id for node in self.nodes.values()
                if node.type == "output"]

    def is_active(self):
        """True when an Output node has a normal input feeding it."""
        outputs = set(self.output_ids())
        input_names = {
            node_id: {name for name, _kind in node_inputs(self.nodes[node_id])}
            for node_id in outputs
        }
        return any(link["to"] in outputs
                   and link["to_port"] in input_names[link["to"]]
                   for link in self.links)

    def is_direct_passthrough(self):
        """True when an Output still receives only the Layer's own colour.

        Disconnected extra nodes do not change the result, so this is broader
        than the untouched starter graph check used by `is_passthrough`.
        """
        found = False
        for output_id in self.output_ids():
            output = self.nodes[output_id]
            links = [link for link in self.links
                     if link["to"] == output_id
                     and link["to_port"] == "Color"]
            if not links:
                continue
            found = True
            if len(links) != 1:
                return False
            link = links[0]
            layer = self.nodes.get(link["from"])
            if (layer is None or layer.type != "layer"
                    or link["from_port"] != "Color"):
                return False
            if (layer.params.get("@gate:out:Color")
                    or output.params.get("@gate:in:Color")):
                return False
        return found

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
        node_id = _as_node_id(node_id)
        source = self.nodes.get(node_id) if node_id is not None else None
        if source is None:
            return None
        return self.add_node(
            source.type,
            max(0.0, source.x + float(dx)),
            max(0.0, source.y + float(dy)),
            dict(source.params))

    def remove_node(self, node_id):
        node_id = _as_node_id(node_id)
        if node_id is None or node_id not in self.nodes:
            return False
        del self.nodes[node_id]
        self.links = [link for link in self.links
                      if link["from"] != node_id and link["to"] != node_id]
        self._invalidate()
        return True

    def link(self, from_id, from_port, to_id, to_port):
        """Connect an output to an input with no cycle or fan-in limit.

        Self-links, cycles and repeated wires are deliberately allowed.  The
        evaluator breaks cycles by leaving a value unavailable until its
        source has been evaluated, so such wires behave as implicit defaults
        until the cycle closes rather than blocking the connection.
        """
        from_id, to_id = _as_node_id(from_id), _as_node_id(to_id)
        if from_id is None or to_id is None:
            return False
        if not isinstance(from_port, str) or not isinstance(to_port, str):
            return False
        source = self.nodes.get(from_id)
        target = self.nodes.get(to_id)
        if source is None or target is None:
            return False
        outputs = dict(node_outputs(source))
        inputs = dict(node_all_inputs(target))
        if from_port not in outputs or to_port not in inputs:
            return False
        self.links.append({"from": from_id, "from_port": from_port,
                           "to": to_id, "to_port": to_port})
        self._invalidate()
        return True

    def unlink(self, from_id, from_port, to_id, to_port):
        """Remove one specific wire, leaving duplicate wires intact."""
        from_id, to_id = _as_node_id(from_id), _as_node_id(to_id)
        if from_id is None or to_id is None:
            return False
        for index, link in enumerate(self.links):
            if (link["from"] == from_id
                    and link["from_port"] == from_port
                    and link["to"] == to_id
                    and link["to_port"] == to_port):
                del self.links[index]
                self._invalidate()
                return True
        return False

    def unlink_input(self, to_id, to_port):
        to_id = _as_node_id(to_id)
        if to_id is None:
            return False
        before = len(self.links)
        self.links = [link for link in self.links
                      if not (link["to"] == to_id
                              and link["to_port"] == to_port)]
        if len(self.links) != before:
            self._invalidate()
            return True
        return False

    def links_into(self, to_id, to_port):
        to_id = _as_node_id(to_id)
        if to_id is None:
            return []
        return [link for link in self.links
                if link["to"] == to_id and link["to_port"] == to_port]

    def link_into(self, to_id, to_port):
        links = self.links_into(to_id, to_port)
        return links[0] if links else None

    # ------------------------------------------------------------- ordering
    def _compile(self):
        epoch = _PARAM_EPOCH[0]
        if self._order is not None:
            if self._param_epoch == epoch:
                return
            # A parameter changed in place since the last compile: drop the
            # compiled paths and the renders they cached.
            self._invalidate()
        self._inputs = {}
        used_outputs = {}
        for link in self.links:
            self._inputs.setdefault((link["to"], link["to_port"]),
                                    []).append(link)
            used_outputs.setdefault(link["from"], set()).add(
                link["from_port"])
        self._used_outputs = used_outputs
        # Output gates are the only nodes that need a second pass over their
        # result dictionary. Most graphs have none, so remember that once
        # instead of rebuilding and scanning every result for every pixel.
        gated = set()
        aliases = set()
        for node_id, node in self.nodes.items():
            for key, value in node.params.items():
                if key.startswith("@gate:out:") and value in MICRO_GATES:
                    gated.add(node_id)
                    break
            if any(name != source for name, source
                   in node_output_sources(node)):
                aliases.add(node_id)
        self._output_gate_ids = frozenset(gated)
        self._output_alias_ids = frozenset(aliases)
        # Most inputs have one wire and no micro gate. Resolve those once so
        # the per-pixel evaluator can copy the value without rebuilding alias
        # maps, socket kinds and link lists for every pixel.
        simple = {}
        defaults = {}
        for node_id, node in self.nodes.items():
            reversed_node = node_reversed(node)
            aliases = node_reverse_aliases(node)
            for name, _kind in node_all_inputs(node):
                hidden = is_gate_input(name)
                if hidden:
                    gate_name = None
                else:
                    gate_name = aliases.get(name, name) if reversed_node else name
                    if node.params.get("@gate:in:" + gate_name) in MICRO_GATES:
                        continue
                lookup = name
                if reversed_node and not hidden:
                    lookup = aliases.get(name)
                links = self._inputs.get((node_id, lookup), ())
                kind = self._input_kind(node, name)
                if len(links) == 1:
                    simple[(node_id, name)] = (
                        links[0]["from"], links[0]["from_port"], kind)
                    continue
                if links:
                    continue
                if hidden:
                    defaults[(node_id, name)] = ("ones", kind)
                    continue
                # Mirror the unconnected-socket defaults in _input once,
                # rather than rediscovering them for every rendered pixel.
                if node.type in ("mask", "mask_apply") and name == "Color":
                    defaults[(node_id, name)] = ("layer",)
                elif node.type == "mask_apply" and name == "Mask":
                    defaults[(node_id, name)] = ("one",)
                elif node.type == "ramp" and name == "Value":
                    defaults[(node_id, name)] = ("ramp", node)
                elif kind == VALUE:
                    defaults[(node_id, name)] = ("param", name.lower())
                elif kind == WIRE:
                    defaults[(node_id, name)] = ("transparent",)
                elif node.type in ("operation", "gate", "blend"):
                    defaults[(node_id, name)] = (
                        "layer" if name == "A" else "below",)
                elif node.type == "hue" and name == "Color":
                    defaults[(node_id, name)] = ("layer",)
                else:
                    defaults[(node_id, name)] = ("transparent",)
        self._simple_inputs = simple
        self._default_inputs = defaults

        # Resolve dependencies once. A recursive visitor hit Python's
        # recursion limit on long node chains; the explicit stack keeps
        # compile depth independent of graph size.
        dependencies = {}
        for node_id, node in self.nodes.items():
            sources = []
            for name, _kind in node_all_inputs(node):
                for link in self._inputs.get((node_id, name), ()):
                    source = link["from"]
                    if source not in sources:
                        sources.append(source)
            dependencies[node_id] = sources

        order = []
        seen = set()
        visiting = set()

        def visit(root):
            root = int(root)
            if root in seen or root in visiting:
                return
            visiting.add(root)
            stack = [(root, 0)]
            while stack:
                node_id, index = stack[-1]
                if node_id in seen:
                    visiting.discard(node_id)
                    stack.pop()
                    continue
                node = self.nodes.get(node_id)
                if node is None:
                    seen.add(node_id)
                    visiting.discard(node_id)
                    stack.pop()
                    continue
                sources = dependencies.get(node_id, ())
                if index < len(sources):
                    source = sources[index]
                    stack[-1] = (node_id, index + 1)
                    if source not in seen and source not in visiting:
                        visiting.add(source)
                        stack.append((source, 0))
                    continue
                visiting.discard(node_id)
                seen.add(node_id)
                order.append(node_id)
                stack.pop()

        outputs = []
        for node in self.nodes.values():
            if node.type == "output":
                outputs.append(node.id)
                visit(node.id)
        self._order = order
        self._outputs = tuple(outputs)
        self._fast_chain = self._build_fast_chain()
        self._fast_dag = (None if self._fast_chain is not None
                          else self._build_fast_dag())
        self._param_epoch = _PARAM_EPOCH[0]

    def _build_direct_output(self, output):
        """Fast path for Layer sockets wired straight into an Output node."""
        names = tuple(name for name, _kind in node_inputs(output))
        if not self.links or any(link["to"] != output.id
                                 for link in self.links):
            return None
        targets = {}
        for link in self.links:
            target = link["to_port"]
            source = self.nodes.get(link["from"])
            if (target not in names or target in targets
                    or source is None or source.type != "layer"
                    or node_micro_gates(source)
                    or link["from_port"] not in _LAYER_OUTPUT_PORTS):
                return None
            targets[target] = link["from_port"]
        if not targets:
            return None
        order = {name: index for index, name in enumerate(names)}
        entries = tuple(sorted(targets.items(),
                               key=lambda item: order[item[0]]))
        self._direct_spec = entries
        if entries == (("Color", "Color"),):
            return lambda layer_px, below_px, x, y: layer_px
        rgb = ("Red", "Green", "Blue")
        if len(entries) == 1 and entries[0][0] == "Color":
            source = entries[0][1]
            if source in ("Red", "Green", "Blue"):
                index = rgb.index(source)
                return (lambda layer_px, below_px, x, y, _i=index:
                        (layer_px[_i], 0, 0, layer_px[3]) if _i == 0
                        else (0, layer_px[_i], 0, layer_px[3]) if _i == 1
                        else (0, 0, layer_px[_i], layer_px[3]))
            if source in ("Alpha", "Opacity"):
                return (lambda layer_px, below_px, x, y:
                        (layer_px[3],) * 3 + (255,))
            if source not in _LAYER_OUTPUT_PORTS:
                return None
            if source in ("Color",) or source not in _LAYER_OUTPUT_PORTS:
                return None
            return (lambda layer_px, below_px, x, y, _name=source:
                    _layer_measurement_colour(layer_px, _name))
        if all(name in rgb for name, _source in entries) \
                and all(source in (name, "Color")
                        for name, source in entries):
            present = set(name for name, _source in entries)
            if len(present) == 3:
                return lambda layer_px, below_px, x, y: layer_px
            return (lambda layer_px, below_px, x, y, _present=present:
                    (layer_px[0] if "Red" in _present else 0,
                     layer_px[1] if "Green" in _present else 0,
                     layer_px[2] if "Blue" in _present else 0,
                     layer_px[3]))
        if len(entries) == 1:
            target, source = entries[0]
            if target in ("Alpha", "Opacity"):
                if source in rgb:
                    index = rgb.index(source)
                    return (lambda layer_px, below_px, x, y, _i=index:
                            (0, 0, 0, layer_px[_i]))
                if source == "Color":
                    return (lambda layer_px, below_px, x, y:
                            (0, 0, 0, int(round(
                                0.299 * layer_px[0] + 0.587 * layer_px[1]
                                + 0.114 * layer_px[2]))))
                if source in ("Alpha", "Opacity"):
                    return lambda layer_px, below_px, x, y: (0, 0, 0,
                                                             layer_px[3])
                return (lambda layer_px, below_px, x, y, _name=source:
                        (0, 0, 0,
                         _layer_measurement_amount(layer_px, _name)))
            if target in rgb:
                index = rgb.index(target)
                if source in rgb:
                    return (lambda layer_px, below_px, x, y:
                            (0, 0, 0, layer_px[3]))
                if source in ("Alpha", "Opacity"):
                    return (lambda layer_px, below_px, x, y, _i=index:
                            _channel_tuple(layer_px[3], _i, 255))
                return (lambda layer_px, below_px, x, y,
                        _name=source, _i=index:
                        _channel_measurement(layer_px, _name, _i))
            if source == "Color":
                return (lambda layer_px, below_px, x, y:
                        _layer_measurement_colour(layer_px, "Color"))
            if source in rgb:
                index = rgb.index(source)
                return (lambda layer_px, below_px, x, y, _i=index:
                        (layer_px[_i],) * 3 + (255,))
            if source in ("Alpha", "Opacity"):
                return (lambda layer_px, below_px, x, y:
                        (layer_px[3],) * 3 + (255,))
            return (lambda layer_px, below_px, x, y, _name=source:
                    _layer_measurement_grey(layer_px, _name))

        def render(layer_px, below_px, x, y, _entries=entries):
            return _compose_output([
                (target, _layer_socket_value(layer_px, source_port))
                for target, source_port in _entries])

        return render

    def _build_fast_chain(self):
        """A direct Layer -> one-input nodes -> Output evaluator.

        Hue, operation, gate and blend nodes can run without the per-node
        dictionaries when the graph is a straight chain and the second operand
        is implicit. Branched and micro-gated graphs use the general evaluator.
        """
        self._fast_shifts = None
        self._fast_direct = False
        self._fast_spatial = False
        self._fast_below_free = False
        self._fast_layer_free = False
        if len(self._outputs) != 1:
            return None
        output = self.nodes.get(self._outputs[0])
        if output is None or node_micro_gates(output):
            return None
        direct = self._build_direct_output(output)
        if direct is not None:
            self._fast_direct = True
            return direct
        links = self._inputs.get((output.id, "Color"), ())
        if len(links) != 1:
            return None
        link = links[0]
        chain = []
        seen = set()
        allowed = ("hue", "operation", "gate", "blend", "perlin",
                   "ramp", "mask", "mask_apply")
        # Chains carry the socket each node reads from, because a number socket
        # is not interchangeable with a colour socket: a Color Ramp wired from
        # Perlin's Value output must see the noise itself, not its grey pixels.
        used_links = 1
        while True:
            source_port = link["from_port"]
            if source_port not in ("Color", "Value"):
                return None
            node = self.nodes.get(link["from"])
            if node is None:
                return None
            if node.id in seen:
                # A cycle is legal in the editor but cannot use the fast chain.
                return None
            seen.add(node.id)
            if node.type in ("layer", "perlin"):
                if node_micro_gates(node):
                    return None
                if source_port == "Value" and node.type != "perlin":
                    return None
                chain.append((node, source_port))
                break
            if node.type not in allowed or node_micro_gates(node):
                return None
            if source_port == "Value" and node.type != "mask":
                return None
            if node.type == "hue":
                primary, other = "Color", "Shift"
            elif node.type == "ramp":
                primary, other = "Value", None
            elif node.type == "mask":
                primary, other = "Color", None
            elif node.type == "mask_apply":
                primary, other = "Color", "Mask"
            else:
                primary, other = "A", "B"
            if other and self._inputs.get((node.id, other)):
                return None
            primary_links = self._inputs.get((node.id, primary), ())
            if not primary_links:
                # An unwired Color Ramp reads its own layer measurement, which
                # is still a straight chain and worth compiling.
                if node.type != "ramp":
                    return None
                chain.append((node, None))
                break
            if len(primary_links) != 1:
                return None
            if (node.type != "ramp"
                    and primary_links[0]["from_port"] != "Color"):
                return None
            chain.append((node, source_port))
            link = primary_links[0]
            used_links += 1
        if len(self.links) != used_links:
            return None

        from .model import combine, gate_value

        operations = []
        all_hue = True
        below_free = True
        previous_port = None
        for node, source_port in reversed(chain):
            # `source_port` is the socket this node was read through by the
            # step below it; `previous_port` is the same for its own input.
            feed = previous_port
            previous_port = source_port
            if node.type == "hue":
                try:
                    shift = float(node.param("shift") or 0)
                except (TypeError, ValueError):
                    return None
                if not math.isfinite(shift):
                    return None
                operations.append(("hue", shift))
            elif node.type == "perlin":
                all_hue = False
                if self._inputs.get((node.id, "Scale")) \
                        or self._inputs.get((node.id, "Seed")):
                    return None
                try:
                    scale = float(node.param("scale") or 0) or 32.0
                    seed = int(node.param("seed") or 0)
                except (TypeError, ValueError):
                    return None
                if not math.isfinite(scale):
                    return None
                operations.append(("perlin", (scale, seed)))
            elif node.type == "ramp":
                all_hue = False
                if feed == "Value":
                    source = "value"
                elif feed == "Color":
                    source = "color"
                else:
                    source = "measure"
                operations.append(("ramp", (
                    _ramp_sampler(parse_gradient(node.param("gradient")),
                                  node.param("interpolation")),
                    str(node.param("mode") or "luminance"),
                    source)))
            elif node.type == "mask":
                all_hue = False
                operations.append(("mask", (
                    node.param("source"), node.param("invert"))))
            elif node.type == "mask_apply":
                all_hue = False
                operations.append(("mask_apply", None))
            elif node.type == "layer":
                all_hue = False
                operations.append(("layer", None))
            else:
                all_hue = False
                below_free = False
                operations.append((node.type,
                                   str(node.param("mode") or "add")))
        operations = tuple(operations)
        self._fast_spatial = any(step[0] == "perlin"
                                 for step in operations)
        # A chain of hue/ramp/mask steps never reads the stack below it, so the
        # composite can render it once as a bitmap instead of per pixel.
        self._fast_below_free = below_free
        # A noise chain that starts at the Perlin node never reads the layer
        # pixel, so its picture only changes when the graph (or the stack
        # below it) does - not when the user paints.
        self._fast_layer_free = bool(operations) \
            and operations[0][0] == "perlin" \
            and not any(step[0] == "ramp" and step[1][2] == "measure"
                        for step in operations)
        if all_hue:
            self._fast_shifts = tuple(step[1] for step in operations)

        def render(layer_px, below_px, x, y, _operations=operations):
            color = layer_px
            numeric = None
            for kind, argument in _operations:
                if kind == "hue":
                    color = _fast_hue(color, argument)
                    numeric = None
                    continue
                if kind == "perlin":
                    scale, seed = argument
                    numeric = perlin2(x / scale, y / scale, seed)
                    grey = int(round(numeric * 255))
                    color = (grey, grey, grey, 255)
                    continue
                if kind == "ramp":
                    sampler, mode, feed = argument
                    if feed == "value" and numeric is not None:
                        numeric = float(numeric)
                    elif feed == "measure":
                        numeric = _ramp_source(layer_px, mode)
                    else:
                        numeric = _as_value(color)
                    color = sampler(numeric)
                    continue
                if kind == "mask":
                    numeric = mask_measurement(color, argument[0])
                    if str(argument[1] or "").lower() in ("yes", "true", "1"):
                        numeric = 1.0 - numeric
                    grey = int(round(_clamp(numeric) * 255))
                    color = (grey, grey, grey, 255)
                    continue
                if kind == "mask_apply":
                    numeric = None
                    color = (color[0], color[1], color[2], color[3])
                    continue
                if kind == "layer":
                    numeric = None
                    color = layer_px
                    continue
                a = color
                b = below_px
                if kind == "gate":
                    rgb = [gate_value(b[index], a[index], argument)
                           for index in range(3)]
                elif kind == "operation" and argument == "add":
                    rgb = [min(255, b[index] + a[index])
                           for index in range(3)]
                elif kind == "operation" and argument == "subtract":
                    rgb = [max(0, b[index] - a[index])
                           for index in range(3)]
                elif kind == "operation" and argument == "multiply":
                    rgb = [(b[index] * a[index]) // 255
                           for index in range(3)]
                else:
                    rgb = [combine(b[index], a[index], argument, 255)
                           for index in range(3)]
                color = (rgb[0], rgb[1], rgb[2], a[3])
                numeric = None
            return color

        return render

    def _build_fast_dag(self):
        """Compile a branched, type-aware graph into one pixel evaluator.

        The general evaluator builds a result dictionary and resolves socket
        names for every node at every pixel.  This path keeps that exact
        behaviour for the common graph (Layer/Layer Source, Below, Constant,
        Hue, Operation, Gate, Blend, Mask, Apply Mask, Color Ramp, Value,
        Math, Perlin and Output) but compiles every socket once, colour or
        number, so a branched graph pays a small flat loop per pixel instead
        of that dictionary churn.  Anything else falls back to eval_pixel.
        """
        if len(self._outputs) != 1 or not self._order:
            return None
        output_id = self._outputs[0]
        nodes = self.nodes
        output = nodes.get(output_id)
        if output is None or node_micro_gates(output) or node_reversed(output):
            return None
        order = self._order
        index = {node_id: pos for pos, node_id in enumerate(order)}
        # The sockets this compiler can fill without the general evaluator.
        ports = {
            "layer": ("Color",),
            "layer_source": ("Color",),
            "group_source": ("Color",),
            "below": ("Color",),
            "constant": ("Color",),
            "hue": ("Color",),
            "operation": ("Color",),
            "gate": ("Color",),
            "blend": ("Color",),
            "mask_apply": ("Color",),
            "ramp": ("Color",),
            "perlin": ("Color", "Value"),
            "mask": ("Color", "Value"),
            "value": ("Value",),
            "math": ("Value",),
        }
        for node_id in order:
            node = nodes.get(node_id)
            if node is None:
                return None
            if node_id == output_id:
                continue
            if node.type not in ports:
                return None
            if node_micro_gates(node) or node_reversed(node):
                return None
        kinds = {}
        for node_id in order:
            node = nodes[node_id]
            if node_id == output_id:
                continue
            for name, kind in node_outputs(node):
                kinds[(node_id, name)] = kind
        # One wire per socket, and only sockets that carry a colour or number.
        wires = {}
        for link in self.links:
            source_id = link["from"]
            target_id = link["to"]
            source = nodes.get(source_id)
            target = nodes.get(target_id)
            if source is None or target is None:
                return None
            if source_id not in index or target_id not in index:
                return None
            source_port = link["from_port"]
            if source_port not in ports.get(source.type, ()):
                return None
            if target_id == output_id:
                if link["to_port"] != "Color" or source_port != "Color":
                    return None
            else:
                target_kind = dict(node_inputs(target)).get(link["to_port"])
                if target_kind not in (COLOR, VALUE):
                    return None
            key = (target_id, link["to_port"])
            if key in wires:
                return None
            wires[key] = (source_id, source_port)
        if (output_id, "Color") not in wires:
            return None
        for (target_id, _port), (source_id, _source_port) in wires.items():
            if index[source_id] >= index[target_id]:
                return None

        below_free = [True]
        layer_free = [True]
        # Set when the graph reads another layer's pixels, which a digest of
        # this layer alone cannot cover, so rectangles must not be reused.
        external = [False]

        def input_spec(node_id, name, kind):
            """Resolve one socket to a per-pixel read, once per compile.

            A spec is ``("wire", slot, convert)`` for a connected socket,
            ``("layer",)``/``("below",)`` for the implicit stack pixels,
            ``("ramp", mode)`` for a ramp's own measurement and
            ``("const", value)`` for everything else.  Values are stored per
            node as a colour in ``slot`` and, when the node has one, a number
            in ``slot + 1``.
            """
            wire = wires.get((node_id, name))
            if wire is not None:
                source_id, source_port = wire
                source_kind = kinds[(source_id, source_port)]
                slot = index[source_id] * 2 + (1 if source_kind == VALUE else 0)
                if kind == VALUE:
                    return ("wire", slot,
                            None if source_kind == VALUE else _as_value)
                return ("wire", slot,
                        None if source_kind == COLOR else _as_color)
            target = nodes[node_id]
            default = self._default_inputs.get((node_id, name))
            if default is not None:
                code = default[0]
                if code == "layer":
                    layer_free[0] = False
                    return ("layer",)
                if code == "below":
                    below_free[0] = False
                    return ("below",)
                if code == "transparent":
                    return ("const", TRANSPARENT)
                if code == "one":
                    return ("const", 1.0)
                if code == "ones":
                    return ("const", DEFAULT_COLOR if kind == COLOR else 1.0)
                if code == "ramp":
                    return ("ramp", default[1].param("mode"))
                try:
                    return ("const", float(target.params.get(default[1], 0.0)))
                except (TypeError, ValueError):
                    return ("const", 0.0)
            # Mirror _input's implicit defaults when no entry was cached.
            if is_gate_input(name):
                return ("const", DEFAULT_COLOR if kind == COLOR else 1.0)
            if target.type in ("mask", "mask_apply") and name == "Color":
                layer_free[0] = False
                return ("layer",)
            if target.type == "mask_apply" and name == "Mask":
                return ("const", 1.0)
            if target.type == "ramp" and name == "Value":
                return ("ramp", target.param("mode"))
            if kind == VALUE:
                try:
                    return ("const",
                            float(target.params.get(name.lower(), 0.0)))
                except (TypeError, ValueError):
                    return ("const", 0.0)
            if target.type in ("operation", "gate", "blend"):
                if name == "A":
                    layer_free[0] = False
                    return ("layer",)
                below_free[0] = False
                return ("below",)
            if target.type == "hue" and name == "Color":
                layer_free[0] = False
                return ("layer",)
            return ("const", TRANSPARENT)

        def source_fn(node):
            if node.type == "layer_source":
                name = node.param("layer")
                state = [None, None]

                def source(layer_px, below_px, x, y, _name=name,
                           _state=state):
                    token = self._source_layers
                    if _state[0] is not token:
                        _state[0] = token
                        _state[1] = self._source_layer(_name)
                    layer = _state[1]
                    if layer is None:
                        return TRANSPARENT
                    if layer is self._active_layer:
                        return layer_px
                    return layer.bitmap.rgba(x, y)
                return source
            name = node.param("group")

            def source(layer_px, below_px, x, y, _name=name):
                return self._group_pixel(_name, x, y)
            return source

        from .model import combine, gate_value

        def operation_fn(mode):
            if mode == "add":
                return lambda a, b: (min(255, b[0] + a[0]),
                                     min(255, b[1] + a[1]),
                                     min(255, b[2] + a[2]), a[3])
            if mode == "subtract":
                return lambda a, b: (max(0, b[0] - a[0]),
                                     max(0, b[1] - a[1]),
                                     max(0, b[2] - a[2]), a[3])
            if mode == "multiply":
                return lambda a, b: ((b[0] * a[0]) // 255,
                                     (b[1] * a[1]) // 255,
                                     (b[2] * a[2]) // 255, a[3])
            if mode == "divide":
                return lambda a, b: (
                    (b[0] * 255) // a[0] if a[0] else (255 if b[0] else 0),
                    (b[1] * 255) // a[1] if a[1] else (255 if b[1] else 0),
                    (b[2] * 255) // a[2] if a[2] else (255 if b[2] else 0),
                    a[3])
            return lambda a, b: (combine(b[0], a[0], mode, 255),
                                 combine(b[1], a[1], mode, 255),
                                 combine(b[2], a[2], mode, 255), a[3])

        def gate_fn(mode):
            return lambda a, b: (gate_value(b[0], a[0], mode),
                                 gate_value(b[1], a[1], mode),
                                 gate_value(b[2], a[2], mode), a[3])

        plans = []
        for node_id in order:
            if node_id == output_id:
                continue
            node = nodes[node_id]
            slot = index[node_id] * 2
            if node.type == "layer":
                layer_free[0] = False
                plans.append(("layer", slot))
            elif node.type == "below":
                below_free[0] = False
                plans.append(("below", slot))
            elif node.type in ("layer_source", "group_source"):
                layer_free[0] = False
                external[0] = True
                plans.append(("source", slot, source_fn(node)))
            elif node.type == "constant":
                plans.append(("constant", slot,
                              parse_color(node.param("color"))))
            elif node.type == "hue":
                plans.append(("hue", slot,
                              input_spec(node_id, "Color", COLOR),
                              input_spec(node_id, "Shift", VALUE)))
            elif node.type == "perlin":
                plans.append(("perlin", slot,
                              input_spec(node_id, "Scale", VALUE),
                              input_spec(node_id, "Seed", VALUE)))
            elif node.type == "ramp":
                plans.append(("ramp", slot,
                              input_spec(node_id, "Value", VALUE),
                              _ramp_sampler(parse_gradient(
                                  node.param("gradient")),
                                  node.param("interpolation"))))
            elif node.type == "mask":
                plans.append(("mask", slot,
                              input_spec(node_id, "Color", COLOR),
                              node.param("source"),
                              str(node.param("invert") or "").lower()
                              in ("yes", "true", "1")))
            elif node.type == "mask_apply":
                plans.append(("mask_apply", slot,
                              input_spec(node_id, "Color", COLOR),
                              input_spec(node_id, "Mask", VALUE)))
            elif node.type == "value":
                specs = []
                for name, _kind in base_node_inputs(node):
                    spec = input_spec(node_id, name, VALUE)
                    if spec[0] == "const" and spec[1] == 0.0:
                        continue
                    specs.append(spec)
                try:
                    total = float(node.param("value"))
                except (TypeError, ValueError):
                    total = 0.0
                plans.append(("value", slot, total, tuple(specs)))
            elif node.type == "math":
                plans.append(("math", slot,
                              input_spec(node_id, "A", VALUE),
                              input_spec(node_id, "B", VALUE),
                              str(node.param("mode") or "add")))
            else:
                mode = str(node.param("mode") or "add")
                if node.type == "gate":
                    apply = gate_fn(mode)
                elif node.type == "operation":
                    apply = operation_fn(mode)
                else:
                    apply = operation_fn(mode) if mode in (
                        "add", "subtract", "multiply", "divide") else None
                    if apply is None:
                        apply = lambda a, b, _mode=mode: (
                            combine(b[0], a[0], _mode, 255),
                            combine(b[1], a[1], _mode, 255),
                            combine(b[2], a[2], _mode, 255), a[3])
                plans.append(("apply", slot,
                              input_spec(node_id, "A", COLOR),
                              input_spec(node_id, "B", COLOR),
                              apply))

        self._fast_dag_below_free = below_free[0]
        self._fast_dag_layer_free = layer_free[0]
        self._fast_dag_external = external[0]
        result_slot = index[wires[(output_id, "Color")][0]] * 2

        # Keep the compiled path static: no generated Python, which is both
        # easier to audit and friendlier to packaged/anti-virus environments.
        def render(layer_px, below_px, x, y, _plans=tuple(plans),
                   _size=len(order) * 2, _result=result_slot):
            values = [None] * _size
            for plan in _plans:
                kind = plan[0]
                slot = plan[1]
                if kind == "layer":
                    values[slot] = layer_px
                elif kind == "below":
                    values[slot] = below_px
                elif kind == "source":
                    values[slot] = plan[2](layer_px, below_px, x, y)
                elif kind == "constant":
                    values[slot] = plan[2]
                elif kind == "hue":
                    shift = _dag_input(plan[3], values, layer_px, below_px)
                    values[slot] = _fast_hue(
                        _dag_input(plan[2], values, layer_px, below_px),
                        float(shift))
                elif kind == "perlin":
                    scale = float(_dag_input(plan[2], values, layer_px,
                                             below_px)) or 32.0
                    seed = int(_dag_input(plan[3], values, layer_px,
                                          below_px))
                    noise = perlin2(x / scale, y / scale, seed)
                    grey = int(round(noise * 255))
                    values[slot] = (grey, grey, grey, 255)
                    values[slot + 1] = noise
                elif kind == "ramp":
                    values[slot] = plan[3](
                        _dag_input(plan[2], values, layer_px, below_px))
                elif kind == "mask":
                    amount = mask_measurement(
                        _dag_input(plan[2], values, layer_px, below_px),
                        plan[3])
                    if plan[4]:
                        amount = 1.0 - amount
                    amount = _clamp(amount)
                    grey = int(round(amount * 255))
                    values[slot] = (grey, grey, grey, 255)
                    values[slot + 1] = amount
                elif kind == "mask_apply":
                    color = _dag_input(plan[2], values, layer_px, below_px)
                    amount = float(_dag_input(plan[3], values, layer_px,
                                              below_px))
                    if not math.isfinite(amount):
                        amount = 0.0
                    amount = _clamp(amount)
                    values[slot] = (color[0], color[1], color[2],
                                    int(round(color[3] * amount)))
                elif kind == "value":
                    total = plan[2]
                    for spec in plan[3]:
                        total += _dag_input(spec, values, layer_px, below_px)
                    values[slot + 1] = total
                elif kind == "math":
                    left = float(_dag_input(plan[2], values, layer_px,
                                            below_px))
                    right = float(_dag_input(plan[3], values, layer_px,
                                             below_px))
                    mode = plan[4]
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
                    values[slot + 1] = result
                else:
                    values[slot] = plan[4](
                        _dag_input(plan[2], values, layer_px, below_px),
                        _dag_input(plan[3], values, layer_px, below_px))
            return values[_result]

        return render

    def _render_dag_bitmap(self, bitmap, rect=None, below=TRANSPARENT):
        """Apply a compiled color DAG to a whole bitmap."""
        out = bitmap.rect_rgba(*rect) if rect else bitmap.copy()
        render = self._fast_dag
        if self._fast_dag_palette_ok():
            mapped = self._map_chain_palette(out, render, below)
            if mapped is not None:
                return mapped
        data = out.data
        start_x = rect[0] if rect else 0
        start_y = rect[1] if rect else 0
        width = out.width
        started = time.perf_counter()
        index = 0
        for row in range(out.height):
            y = start_y + row
            x = start_x
            for _column in range(width):
                color = render((data[index], data[index + 1],
                                data[index + 2], data[index + 3]),
                               below, x, y)
                data[index] = color[0]
                data[index + 1] = color[1]
                data[index + 2] = color[2]
                data[index + 3] = color[3]
                index += 4
                x += 1
        self._note_render_cost(started, len(data) // 4)
        return out

    def _fast_dag_palette_ok(self):
        """True when a color result depends only on the source pixel.

        Pixel art repeats a handful of colours, so bulk rendering must reuse
        their results.  Spatial sources and Perlin noise break that key.
        """
        for node_id in self._order or ():
            node = self.nodes.get(node_id)
            if node is None:
                continue
            if node.type == "perlin":
                return False
            if node.type == "group_source":
                return False
            if node.type == "layer_source":
                if (self._active_layer is None
                        or self._source_layer(node.param("layer"))
                        is not self._active_layer):
                    return False
        return True

    def _render_direct_bitmap(self, bitmap, rect=None):
        """Bulk-render direct Layer socket mappings without per-pixel calls."""
        spec = self._direct_spec
        if not spec:
            return None
        out = bitmap.rect_rgba(*rect) if rect else bitmap.copy()
        data = out.data
        count = len(data) // 4
        zeros = bytes(count)
        white = b"\xff" * count
        rgb = ("Red", "Green", "Blue")
        if all(target in rgb for target, _source in spec) \
                and all(source in rgb or source == "Color"
                        for _target, source in spec):
            present = set(target for target, _source in spec)
            for target, source in spec:
                index = rgb.index(target)
                if source in rgb and source != target:
                    data[index::4] = zeros
            for target in rgb:
                if target not in present:
                    data[rgb.index(target)::4] = zeros
            return out

        if len(spec) == 1:
            target, source = spec[0]
            if target == "Color":
                if source == "Color":
                    return out
                if source in rgb:
                    index = rgb.index(source)
                    band = bytes(data[index::4])
                    data[0::4] = zeros
                    data[1::4] = zeros
                    data[2::4] = zeros
                    data[index::4] = band
                    return out
                if source in ("Alpha", "Opacity"):
                    band = bytes(data[3::4])
                    data[0::4] = band
                    data[1::4] = band
                    data[2::4] = band
                    data[3::4] = white
                    return out
                return None
            if target in rgb:
                index = rgb.index(target)
                if source in ("Alpha", "Opacity"):
                    band = bytes(data[3::4])
                    data[0::4] = zeros
                    data[1::4] = zeros
                    data[2::4] = zeros
                    data[index::4] = band
                    data[3::4] = white
                    return out
                return None
            if target in ("Alpha", "Opacity"):
                if source in rgb:
                    band = bytes(data[rgb.index(source)::4])
                elif source in ("Alpha", "Opacity"):
                    band = bytes(data[3::4])
                else:
                    return None
                data[0::4] = zeros
                data[1::4] = zeros
                data[2::4] = zeros
                data[3::4] = band
                return out
            if source in rgb:
                band = bytes(data[rgb.index(source)::4])
            elif source in ("Alpha", "Opacity"):
                band = bytes(data[3::4])
            else:
                return None
            data[0::4] = band
            data[1::4] = band
            data[2::4] = band
            data[3::4] = white
            return out
        return None

    def render_hue_bitmap(self, bitmap, rect=None):
        """Apply a below-independent fast chain to a whole bitmap."""
        try:
            self._compile()
            if (self._fast_shifts is None and not self._fast_direct
                    and not self._fast_below_free
                    and not self._fast_dag_below_free):
                return None
            return self.render_chain_bitmap(bitmap, rect, TRANSPARENT)
        except Exception as exc:  # noqa: BLE001 - fall back to the evaluator
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self._failed_frame = True
            return None

    def render_chain_bitmap(self, bitmap, rect=None, below=TRANSPARENT):
        """Apply the fast chain to a whole bitmap with a fixed below pixel."""
        try:
            self._compile()
            if self._fast_direct:
                direct = self._render_direct_bitmap(bitmap, rect)
                if direct is not None:
                    return direct
            chain = self._fast_chain
            render = chain if chain is not None else self._fast_dag
            if render is None:
                return None
            spatial = (self._fast_spatial if chain is not None
                       else not self._fast_dag_palette_ok())
            cache_key = None
            if spatial and not self._fast_dag_external:
                # The below pixel is fixed here, so a rectangle stays valid
                # while the layer pixels the graph reads are unchanged.  A
                # digest of just that rectangle lets a dab re-render the tiles
                # it touched and reuse every other one.
                area = rect or (0, 0, bitmap.width, bitmap.height)
                if (self._fast_layer_free if chain is not None
                        else self._fast_dag_layer_free):
                    cache_key = (rect, below, bitmap.width, bitmap.height)
                else:
                    cache_key = ("layer", rect, below,
                                 _bitmap_digest(bitmap, area),
                                 bitmap.width, bitmap.height)
                cached = self._render_cache.get(cache_key)
                if cached is not None:
                    return cached.copy()
            if chain is None:
                out = self._render_dag_bitmap(bitmap, rect, below)
                if out is not None and cache_key is not None:
                    self._store_render(cache_key, out.copy())
                return out
            out = bitmap.rect_rgba(*rect) if rect else bitmap.copy()
            if not self._fast_spatial:
                mapped = self._map_chain_palette(out, chain, below)
                if mapped is not None:
                    return mapped
            data = out.data
            start_x = rect[0] if rect else 0
            start_y = rect[1] if rect else 0
            width = out.width
            # Spatial steps read each pixel's own position, so they cannot be
            # deduplicated by colour; a flat row loop avoids the ``%`` and
            # ``//`` per pixel that the old counter needed.
            started = time.perf_counter()
            index = 0
            for row in range(out.height):
                y = start_y + row
                x = start_x
                for _column in range(width):
                    color = chain((data[index], data[index + 1],
                                   data[index + 2], data[index + 3]),
                                  below, x, y)
                    data[index] = color[0]
                    data[index + 1] = color[1]
                    data[index + 2] = color[2]
                    data[index + 3] = color[3]
                    index += 4
                    x += 1
            self._note_render_cost(started, len(data) // 4)
            if cache_key is not None:
                self._store_render(cache_key, out)
            return out
        except Exception as exc:  # noqa: BLE001 - fall back to the evaluator
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            # Do not leave a broken compiled path active: the caller can now
            # use the general evaluator for this frame instead of a blank one.
            self._fast_chain = None
            self._fast_shifts = None
            self._fast_direct = False
            self._fast_dag = None
            self._fast_below_free = False
            self._fast_layer_free = False
            self._fast_dag_below_free = False
            self._fast_dag_layer_free = False
            self._fast_dag_external = False
            self._render_cache = {}
            self._render_cache_bytes = 0
            self._failed_frame = False
            return None

    def render_below_bitmap(self, bitmap, below, rect=None):
        """Render a rectangle whose graph reads the stack below per pixel.

        The compositor evaluates such a graph for every pixel of every redraw,
        although the picture only changes when the graph, the layer pixels the
        graph reads, or the layers under it change.  Rendering the rectangle
        once and keeping it while a CRC of those inputs is unchanged makes
        painting on a noise layer as cheap as painting on a plain one.

        `below` must be a rectangle-sized Bitmap holding the stack under this
        layer.  Returns None when the compiled path cannot render this way, so
        the caller falls back to the general evaluator.
        """
        try:
            self._compile()
            chain = self._fast_chain
            render = chain if chain is not None else self._fast_dag
            if render is None or self._fast_direct:
                return None
            if self._fast_below_free or self._fast_dag_below_free:
                # Nothing below is read, so the bulk path (and its palette)
                # already handles this graph.
                return self.render_chain_bitmap(bitmap, rect, TRANSPARENT)
            below_data = getattr(below, "data", None)
            if below_data is None:
                return None
            x, y, width, height = rect or (0, 0, bitmap.width,
                                           bitmap.height)
            if (below.width != width or below.height != height
                    or len(below_data) != width * height * 4):
                return None
            layer_crc = None
            if not (self._fast_layer_free if chain is not None
                    else self._fast_dag_layer_free):
                # The graph reads its own layer, so the rectangle is only
                # reusable while those pixels are unchanged; a dab then costs
                # the tiles it touched instead of the whole viewport.
                layer_crc = _bitmap_digest(bitmap, (x, y, width, height))
            key = None
            if not self._fast_dag_external:
                key = (
                    "below", (x, y, width, height),
                    _bitmap_digest(below, (0, 0, width, height)),
                    layer_crc, bitmap.width, bitmap.height,
                )
                cached = self._render_cache.get(key)
                if cached is not None:
                    return cached.copy()
            from .model import Bitmap
            out = Bitmap(width, height)
            data = out.data
            layer_data = bitmap.data
            layer_stride = bitmap.width * 4
            # Without a spatial source the result depends only on the two
            # pixels, and pixel art repeats a handful of pairs, so cache them.
            spatial = (self._fast_spatial if chain is not None
                       else not self._fast_dag_palette_ok())
            palette = None if spatial else {}
            started = time.perf_counter()
            index = 0
            for row in range(height):
                pixel_y = y + row
                source = pixel_y * layer_stride + x * 4
                under = row * width * 4
                pixel_x = x
                for _column in range(width):
                    above_px = (layer_data[source], layer_data[source + 1],
                                layer_data[source + 2], layer_data[source + 3])
                    below_px = (below_data[under], below_data[under + 1],
                                below_data[under + 2], below_data[under + 3])
                    if palette is None:
                        color = render(above_px, below_px, pixel_x, pixel_y)
                    else:
                        pair = (above_px, below_px)
                        color = palette.get(pair)
                        if color is None:
                            color = render(above_px, below_px, pixel_x,
                                           pixel_y)
                            if len(palette) < 16384:
                                palette[pair] = color
                    data[index] = color[0]
                    data[index + 1] = color[1]
                    data[index + 2] = color[2]
                    data[index + 3] = color[3]
                    index += 4
                    source += 4
                    under += 4
                    pixel_x += 1
            self._note_render_cost(started, width * height)
            if key is not None:
                self._store_render(key, out)
            return out
        except Exception as exc:  # noqa: BLE001 - fall back to the evaluator
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self._fast_chain = None
            self._fast_dag = None
            self._fast_direct = False
            self._fast_below_free = False
            self._fast_layer_free = False
            self._fast_dag_below_free = False
            self._fast_dag_layer_free = False
            self._fast_dag_external = False
            self._render_cache = {}
            self._render_cache_bytes = 0
            return None

    def _note_render_cost(self, started, pixels):
        """Record seconds per pixel for the last per-pixel bulk render.

        The editor sizes its preview tiles from this, so a heavy graph gets
        small slices and a cheap one gets large ones, without guessing from
        node counts.
        """
        if pixels > 0:
            self.last_render_cost = (time.perf_counter() - started) / pixels

    def _store_render(self, key, out):
        """Remember one rendered rectangle for layer-independent chains."""
        size = len(out.data)
        if size > 8 * 1024 * 1024:
            return
        if self._render_cache_bytes + size > 24 * 1024 * 1024:
            self._render_cache = {}
            self._render_cache_bytes = 0
        self._render_cache[key] = out
        self._render_cache_bytes += size

    def _map_chain_palette(self, out, render, below):
        """Rewrite a bitmap through `render`, once per distinct colour.

        Pixel art repeats a handful of colours, so the whole image can be
        mapped with a dictionary and C-level array passes instead of calling
        the evaluator once per pixel.  Returns None when that is not possible,
        leaving the caller to fall back to the straightforward loop.
        """
        if array.array("I").itemsize != 4:
            return None
        order = sys.byteorder
        pixels = array.array("I")
        pixels.frombytes(bytes(out.data))
        if len(pixels) != len(out.data) // 4:
            return None
        unique = set(pixels)
        if len(unique) > 262144:
            # Photographic content: a per-colour table would hold most of the
            # image in memory, so let the caller use the plain loop instead.
            return None
        table = {}
        for packed in unique:
            color = render(tuple(packed.to_bytes(4, order)), below, 0, 0)
            table[packed] = int.from_bytes(bytes(color), order)
        out.data[:] = array.array("I", map(table.__getitem__,
                                           pixels)).tobytes()
        return out

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

    def _index_source_layers(self):
        """Lookup tables for Layer Source and Group Source nodes.

        A document render asks for one source pixel per output pixel, so the
        old linear scan over every layer became the dominant cost in graphs
        that read the layer stack.  Layer names are not guaranteed unique;
        `setdefault` preserves the old first-match rule.
        """
        self._source_layer_map = {}
        members = {}
        for layer in self._source_layers or ():
            self._source_layer_map.setdefault(str(layer.name), layer)
            if layer.group:
                members.setdefault(layer.group, []).append(layer)
        self._group_member_map = {
            name: tuple(group_layers)
            for name, group_layers in members.items()}
        self._source_indexed = True

    def _source_layer(self, name):
        """Find the layer selected by a Layer Source node."""
        if not self._source_indexed and self._source_layers:
            self._index_source_layers()
        return self._source_layer_map.get(str(name or ""))

    def _group_pixel(self, name, x, y):
        """Composite one group's source bitmaps at a pixel.

        Source nodes are explicit reads, so they still expose hidden layers
        and groups.  Layer opacity, the quick op/gate/blend options and group
        opacity are applied, but a member's own node graph is not run.  This
        keeps source reads non-recursive, including a layer reading its own
        group.
        """
        name = str(name or "")
        if not self._source_indexed and self._source_layers:
            self._index_source_layers()
        members = self._group_member_map.get(name, ())
        if not members:
            return TRANSPARENT
        group = (self._source_groups or {}).get(name)
        group_alpha = int(group.alpha) if group is not None else 255
        if group_alpha <= 0:
            return TRANSPARENT
        global _COMBINE_LAYER
        if _COMBINE_LAYER is None:
            from .model import combine_layer
            _COMBINE_LAYER = combine_layer
        combine_layer = _COMBINE_LAYER

        dest = [0, 0, 0, 0]
        for layer in members:
            red, green, blue, alpha = layer.bitmap.rgba(x, y)
            above = alpha * int(layer.alpha) * group_alpha // 255 // 255
            if not above:
                continue
            if not dest[3]:
                dest[:] = [red, green, blue, above]
                continue
            for index, value in enumerate((red, green, blue)):
                dest[index] = combine_layer(dest[index], value, layer.op,
                                            layer.gate, layer.blend, above)
            dest[3] = dest[3] + (255 - dest[3]) * above // 255
        return tuple(dest)

    def _source_pixel(self, node, x, y):
        """The RGBA pixel behind a Layer Source or Group Source node."""
        if node.type == "layer_source":
            layer = self._source_layer(node.param("layer"))
            if layer is None:
                return TRANSPARENT
            return layer.bitmap.rgba(x, y)
        if node.type == "group_source":
            return self._group_pixel(node.param("group"), x, y)
        return TRANSPARENT

    def _input(self, node, name, values, layer_px, below_px):
        """Resolve one input socket, then apply its micro gate."""
        # Common case: one wired source and no input gate.  The source may
        # be missing while a cycle is being evaluated, in which case the
        # normal fallback path below supplies the socket's default.
        plan = self._simple_inputs.get((node.id, name))
        if plan is not None:
            source = values.get(plan[0])
            if source is not None and plan[1] in source:
                raw = source[plan[1]]
                kind = plan[2]
                if kind == WIRE:
                    return raw
                if kind == VALUE:
                    return _as_value(raw)
                return raw if isinstance(raw, tuple) else _as_color(raw)
        default = self._default_inputs.get((node.id, name))
        if default is not None:
            code = default[0]
            if code == "layer":
                return layer_px
            if code == "below":
                return below_px
            if code == "transparent":
                return TRANSPARENT
            if code == "one":
                return 1.0
            if code == "ones":
                return DEFAULT_COLOR if default[1] == COLOR else 1.0
            if code == "ramp":
                return _ramp_source(layer_px, default[1].param("mode"))
            try:
                return float(node.params.get(default[1], 0.0))
            except (TypeError, ValueError):
                return 0.0
        lookup = name
        if node_reversed(node) and not is_gate_input(name):
            lookup = node_reverse_aliases(node).get(name)
        links = (self._inputs.get((node.id, lookup))
                 if lookup is not None else None)
        kind = self._input_kind(node, name)
        value = None
        if links:
            received = []
            for link in links:
                source = values.get(link["from"], {})
                if link["from_port"] in source:
                    raw = source[link["from_port"]]
                    if kind == WIRE:
                        received.append(raw)
                    else:
                        received.append(_as_value(raw) if kind == VALUE
                                        else _as_color(raw))
            if received:
                if kind == WIRE:
                    value = _join_wires(received)
                else:
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
            elif kind == WIRE:
                value = TRANSPARENT
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
        # Micro gates belong to the socket the user sees.  On a reversed
        # node the internal A/B name maps to a different visible input, so
        # look the gate up under that name and use the same name for its B
        # operand.
        gate_name = name
        if node_reversed(node):
            gate_name = node_reverse_aliases(node).get(name, name)
        gate = node.params.get("@gate:in:" + gate_name)
        if gate not in MICRO_GATES:
            return value
        return apply_socket_gate(
            value, gate,
            self._gate_operand(node, "in", gate_name, values, layer_px,
                               below_px))

    def _output_colour(self, node, values, layer_px, below_px):
        """Render an Output node from its connected sockets."""
        entries = [(name, self._input(node, name, values, layer_px, below_px))
                   for name, _kind in node_inputs(node)
                   if self._inputs.get((node.id, name))]
        return _compose_output(entries)

    def _eval_node(self, node, values, x, y, width, height, layer_px,
                   below_px):
        global _COMBINE_CHANNEL, _GATE_CHANNEL
        node_type = node.type
        if node_type == "layer":
            fields = (None if self._used_outputs is None
                      else self._used_outputs.get(node.id, ()))
            return layer_values(layer_px, fields)
        if node_type in ("layer_source", "group_source"):
            fields = (None if self._used_outputs is None
                      else self._used_outputs.get(node.id, ()))
            return layer_values(self._source_pixel(node, x, y), fields)
        if node_type == "below":
            return {"Color": below_px}
        if node_type == "output":
            return {"Color": self._output_colour(node, values, layer_px,
                                                   below_px)}
        if node_type in ("splitter", "joiner"):
            joined = _join_wires([
                self._input(node, name, values, layer_px, below_px)
                for name, _kind in base_node_inputs(node)
                if self._inputs.get((node.id, node_reverse_aliases(node).get(
                        name, name)))])
            return {"Wire": joined}
        if node_type == "router":
            visible = set(name for name, _kind in node_outputs(node))
            routed = {}
            for index, (name, _kind) in enumerate(node_inputs(node), 1):
                output_name = "Out %d" % index
                if (output_name in visible
                        and self._inputs.get((node.id, name))):
                    routed[output_name] = self._input(
                        node, name, values, layer_px, below_px)
            return routed
        if node_type == "constant":
            return {"Color": parse_color(node.param("color"))}
        if node_type == "value":
            try:
                total = float(node.param("value"))
            except (TypeError, ValueError):
                total = 0.0
            for name, _kind in base_node_inputs(node):
                total += self._input(node, name, values, layer_px, below_px)
            return {"Value": total}
        if node_type in ("operation", "gate", "blend"):
            a = self._input(node, "A", values, layer_px, below_px)
            b = self._input(node, "B", values, layer_px, below_px)
            mode = str(node.param("mode") or "add")
            if node_type == "gate":
                gate_value = _GATE_CHANNEL
                if gate_value is None:
                    from .model import gate_value
                    _GATE_CHANNEL = gate_value
                rgb = [gate_value(b[index], a[index], mode)
                       for index in range(3)]
            elif mode == "add":
                rgb = [min(255, b[index] + a[index]) for index in range(3)]
            elif mode == "subtract":
                rgb = [max(0, b[index] - a[index]) for index in range(3)]
            elif mode == "multiply":
                rgb = [(b[index] * a[index]) // 255 for index in range(3)]
            elif mode == "divide":
                rgb = [(b[index] * 255) // a[index] if a[index]
                       else (255 if b[index] else 0) for index in range(3)]
            else:
                combine = _COMBINE_CHANNEL
                if combine is None:
                    from .model import combine
                    _COMBINE_CHANNEL = combine
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
        if self._failed_frame:
            return TRANSPARENT
        cache_key = None
        if (self._cache_enabled
                and (not self._spatial or self._spatial_sources_only)
                and isinstance(layer_px, tuple)
                and isinstance(below_px, tuple)):
            cache_key = (layer_px, below_px)
            if self._spatial_sources_only:
                cache_key += (tuple(self._source_pixel(node, x, y)
                                    for node in self._source_nodes),)
            cached = self._pixel_cache.get(cache_key)
            if cached is not None:
                return cached
        values = {}
        nodes_by_id = self.nodes
        used_by_id = self._used_outputs
        try:
            self._compile()
            gated_ids = self._output_gate_ids
            alias_ids = self._output_alias_ids
            if self._fast_chain is not None:
                rendered = self._fast_chain(layer_px, below_px, x, y)
                if cache_key is not None and len(self._pixel_cache) < 8192:
                    self._pixel_cache[cache_key] = rendered
                return rendered
            if self._fast_dag is not None:
                rendered = self._fast_dag(layer_px, below_px, x, y)
                if cache_key is not None and len(self._pixel_cache) < 8192:
                    self._pixel_cache[cache_key] = rendered
                return rendered
            for node_id in self._order:
                node = nodes_by_id[node_id]
                result = self._eval_node(
                    node, values, x, y, width, height, layer_px, below_px)
                # Gates belong to one socket. Only outputs with a live wire
                # matter; duplicate outputs copy the raw value, not a gated
                # base output.
                used = (None if used_by_id is None
                        else used_by_id.get(node_id, ()))
                if (used != () and
                        (node_id in gated_ids
                         or node_id in alias_ids)):
                    only = (next(iter(used)) if used and len(used) == 1
                            else None)
                    if (only is not None and len(result) == 1
                            and only in result
                            and not node.params.get("@gate:out:" + only)):
                        values[node_id] = result
                        continue
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
                output = nodes_by_id.get(node_id)
                if output is None:
                    continue
                if not any(self._inputs.get((node_id, name))
                           for name, _kind in node_inputs(output)):
                    continue
                source = values.get(node_id) or {}
                if "Color" in source:
                    rendered = source["Color"]
                    if cache_key is not None and len(self._pixel_cache) < 8192:
                        self._pixel_cache[cache_key] = rendered
                    return rendered
        except Exception as exc:  # noqa: BLE001 - keep the canvas alive
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            # A broken compiled path should not poison the whole frame. Drop
            # it once and retry through the general evaluator.
            if (self._fast_chain is not None or self._fast_dag is not None
                    or self._fast_direct):
                self._fast_chain = None
                self._fast_shifts = None
                self._fast_direct = False
                self._fast_dag = None
                self._fast_below_free = False
                self._fast_layer_free = False
                self._fast_dag_below_free = False
                self._fast_dag_layer_free = False
                self._fast_dag_external = False
                self._failed_frame = False
                return self.eval_pixel(x, y, layer_px, below_px,
                                       width, height)
            # A malformed graph would otherwise throw once per pixel and
            # make the whole renderer appear to hang. Stop at the first error
            # until a parameter or wire change rebuilds the graph.
            self._failed_frame = True
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
        try:
            next_id = int(state.get("next_id") or 0)
        except (TypeError, ValueError, OverflowError):
            next_id = 0
        graph.next_id = max(next_id, highest + 1)
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
