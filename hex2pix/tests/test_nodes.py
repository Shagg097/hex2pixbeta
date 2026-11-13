"""Tests for the Complex Render node graph."""

import unittest
from unittest import mock

from hex2pix import model, nodes
from hex2pix.model import Document, Layer

RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)


def evaluate(graph, layer=RED, below=BLUE, x=0, y=0, width=8, height=8):
    return graph.eval_pixel(x, y, layer, below, width, height)


class NodeGraphTests(unittest.TestCase):
    def test_starter_graph_is_active_and_passes_the_layer_through(self):
        graph = nodes.NodeGraph.starter()
        self.assertTrue(graph.is_active())
        self.assertTrue(graph.is_passthrough())
        self.assertEqual(evaluate(graph, layer=(10, 20, 30, 200)),
                         (10, 20, 30, 200))

    def test_passthrough_detection_rejects_extra_work(self):
        graph = nodes.NodeGraph.starter()
        layer = next(node for node in graph.nodes.values()
                     if node.type == "layer")
        output = next(node for node in graph.nodes.values()
                      if node.type == "output")
        layer.params["@gate:out:Color"] = "nand"
        self.assertFalse(graph.is_passthrough())

        graph = nodes.NodeGraph.starter()
        output = next(node for node in graph.nodes.values()
                      if node.type == "output")
        output.params["@gate:in:Color"] = "nand"
        self.assertFalse(graph.is_passthrough())

        graph = nodes.NodeGraph.starter()
        graph.add_node("hue")
        self.assertFalse(graph.is_passthrough())

    def test_links_allow_any_socket_pair_but_not_bad_ports(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        noise = graph.add_node("perlin")
        output = graph.add_node("output")
        self.assertTrue(graph.link(layer.id, "Color", output.id, "Color"))
        self.assertTrue(graph.link(noise.id, "Value", output.id, "Color"))
        self.assertFalse(graph.link(output.id, "Color", layer.id, "Color"))
        self.assertFalse(graph.link(layer.id, "Color", layer.id, "Color"))

    def test_value_and_colour_sockets_convert_in_both_directions(self):
        # A number feeding a colour input becomes opaque grey.
        graph = nodes.NodeGraph()
        value = graph.add_node("value", params={"value": 0.5})
        operation = graph.add_node("operation", params={"mode": "add"})
        output = graph.add_node("output")
        self.assertTrue(graph.link(value.id, "Value", operation.id, "A"))
        self.assertTrue(graph.link(operation.id, "Color", output.id,
                                   "Color"))
        self.assertEqual(evaluate(graph, below=(0, 0, 0, 255)),
                         (128, 128, 128, 255))

        # A colour feeding a number input becomes its brightness (0..1).
        graph = nodes.NodeGraph()
        source = graph.add_node("constant", params={"color": "FF0000FF"})
        math_node = graph.add_node("math", params={"mode": "add"})
        output = graph.add_node("output")
        self.assertTrue(graph.link(source.id, "Color", math_node.id, "A"))
        self.assertTrue(graph.link(math_node.id, "Value", output.id,
                                   "Color"))
        self.assertEqual(evaluate(graph, below=(0, 0, 0, 255)),
                         (76, 76, 76, 255))

    def test_several_links_per_input_and_duplicate_wires_are_allowed(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        other = graph.add_node("constant")
        output = graph.add_node("output")
        self.assertTrue(graph.link(layer.id, "Color", output.id, "Color"))
        self.assertTrue(graph.link(other.id, "Color", output.id, "Color"))
        self.assertTrue(graph.link(layer.id, "Color", output.id, "Color"))
        self.assertEqual(len(graph.links), 3)
        self.assertEqual(len(graph.links_into(output.id, "Color")), 3)
        self.assertTrue(graph.unlink(layer.id, "Color", output.id, "Color"))
        self.assertEqual(len(graph.links), 2)
        self.assertEqual(len(graph.links_into(output.id, "Color")), 2)
        self.assertTrue(graph.unlink(layer.id, "Color", output.id, "Color"))
        self.assertEqual(len(graph.links), 1)
        self.assertFalse(graph.unlink(layer.id, "Color", output.id, "Color"))

    def test_cycles_and_self_links_are_allowed_and_safe(self):
        graph = nodes.NodeGraph()
        first = graph.add_node("operation", params={"mode": "add"})
        second = graph.add_node("operation", params={"mode": "add"})
        output = graph.add_node("output")
        self.assertTrue(graph.link(first.id, "Color", second.id, "A"))
        self.assertTrue(graph.link(second.id, "Color", first.id, "A"))
        self.assertTrue(graph.link(first.id, "Color", first.id, "B"))
        self.assertTrue(graph.link(second.id, "Color", output.id, "Color"))
        result = evaluate(graph, below=BLUE)
        self.assertEqual(len(result), 4)
        self.assertEqual(graph.last_error, "")

    def test_self_link_does_not_use_the_fast_chain(self):
        graph = nodes.NodeGraph()
        operation = graph.add_node("operation", params={"mode": "add"})
        output = graph.add_node("output")
        self.assertTrue(graph.link(operation.id, "Color", operation.id, "A"))
        self.assertTrue(graph.link(operation.id, "Color", output.id,
                                   "Color"))
        self.assertEqual(evaluate(graph, layer=RED, below=BLUE),
                         (255, 0, 255, 255))
        self.assertEqual(graph.last_error, "")

    def test_reverse_layout_is_saved_with_the_node(self):
        graph = nodes.NodeGraph()
        node = graph.add_node("math")
        self.assertFalse(nodes.node_reversed(node))
        node.params["@reverse"] = True
        self.assertTrue(nodes.node_reversed(node))
        restored = nodes.NodeGraph.from_state(graph.to_state())
        self.assertTrue(nodes.node_reversed(restored.nodes[node.id]))

    def test_reversed_gate_turns_its_output_into_the_input(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        gate = graph.add_node("gate", params={"mode": "or"})
        output = graph.add_node("output")
        gate.params["@reverse"] = True

        self.assertEqual([name for name, _kind in nodes.node_inputs(gate)],
                         ["Color"])
        self.assertEqual([name for name, _kind in nodes.node_outputs(gate)],
                         ["A", "B"])
        self.assertEqual(dict(nodes.node_output_sources(gate)),
                         {"A": "Color", "B": "Color"})

        self.assertTrue(graph.link(layer.id, "Color", gate.id, "Color"))
        self.assertTrue(graph.link(gate.id, "A", output.id, "Color"))
        self.assertEqual(evaluate(graph, layer=(10, 20, 30, 200),
                                  below=(0, 0, 0, 0)),
                         (10, 20, 30, 200))
        self.assertEqual(graph.last_error, "")

    def test_reversed_gate_keeps_color_left_and_ab_right(self):
        from hex2pix.app import hex2pixApp

        graph = nodes.NodeGraph()
        gate = graph.add_node("gate", params={"mode": "nand"})
        self.assertEqual(hex2pixApp._visual_side(None, gate, "in"), "left")
        self.assertEqual(hex2pixApp._visual_side(None, gate, "out"), "right")

        gate.params["@reverse"] = True
        self.assertEqual(hex2pixApp._visual_side(None, gate, "in"), "left")
        self.assertEqual(hex2pixApp._visual_side(None, gate, "out"), "right")
        self.assertEqual(hex2pixApp._port_y(None, gate, "in", 1, 0),
                         gate.y + 50)
        self.assertEqual(
            [hex2pixApp._port_y(None, gate, "out", 2, index)
             for index in range(2)],
            [gate.y + 36, gate.y + 58])

    def test_reversed_operation_matches_the_normal_operation(self):
        normal = nodes.NodeGraph()
        layer = normal.add_node("layer")
        operation = normal.add_node("operation", params={"mode": "add"})
        output = normal.add_node("output")
        normal.link(layer.id, "Color", operation.id, "A")
        normal.link(operation.id, "Color", output.id, "Color")

        reversed_graph = nodes.NodeGraph()
        layer = reversed_graph.add_node("layer")
        operation = reversed_graph.add_node("operation", params={"mode": "add"})
        output = reversed_graph.add_node("output")
        operation.params["@reverse"] = True
        reversed_graph.link(layer.id, "Color", operation.id, "Color")
        reversed_graph.link(operation.id, "A", output.id, "Color")

        self.assertEqual(evaluate(normal, layer=RED, below=BLUE),
                         evaluate(reversed_graph, layer=RED, below=BLUE))
        self.assertEqual(reversed_graph.last_error, "")

    def test_micro_gate_follows_a_reversed_input(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        gate = graph.add_node("gate", params={"mode": "or"})
        output = graph.add_node("output")
        gate.params["@reverse"] = True
        gate.params["@gate:in:Color"] = "nand"

        self.assertTrue(graph.link(layer.id, "Color", gate.id, "Color"))
        self.assertTrue(graph.link(gate.id, "A", output.id, "Color"))
        self.assertEqual(evaluate(graph, layer=(10, 20, 30, 200),
                                  below=(0, 0, 0, 0)),
                         (245, 235, 225, 200))
        self.assertEqual(graph.last_error, "")

    def test_reversed_nand_gate_keeps_nand_logic(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        gate = graph.add_node("gate", params={"mode": "nand"})
        output = graph.add_node("output")
        gate.params["@reverse"] = True

        self.assertTrue(graph.link(layer.id, "Color", gate.id, "Color"))
        self.assertTrue(graph.link(gate.id, "A", output.id, "Color"))
        self.assertEqual(evaluate(graph, layer=(10, 20, 30, 255),
                                  below=(10, 20, 30, 255)),
                         (245, 235, 225, 255))
        self.assertEqual(evaluate(graph, layer=(10, 20, 30, 255),
                                  below=(0, 0, 0, 0)),
                         (255, 255, 255, 255))
        self.assertEqual(graph.last_error, "")

    def test_connected_output_wins_over_an_unwired_duplicate(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        graph.add_node("output")
        second = graph.add_node("output")
        self.assertTrue(graph.link(layer.id, "Color", second.id, "Color"))
        self.assertTrue(graph.is_active())
        self.assertEqual(evaluate(graph, layer=(10, 20, 30, 128)),
                         (10, 20, 30, 128))

    def test_output_exposes_every_value_socket(self):
        graph = nodes.NodeGraph()
        output = graph.add_node("output")
        self.assertEqual(
            [name for name, _kind in nodes.node_inputs(output)],
            ["Color", "Value", "Red", "Green", "Blue", "Alpha", "Opacity",
             "Hue", "Saturation", "HSL Saturation", "Brightness",
             "Lightness", "Luminance", "Gamma", "Chroma"])

    def test_output_only_renders_connected_inputs(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        output = graph.add_node("output")
        self.assertFalse(graph.is_active())
        self.assertEqual(evaluate(graph, layer=(10, 20, 30, 128)),
                         nodes.TRANSPARENT)

        self.assertTrue(graph.link(layer.id, "Green", output.id, "Green"))
        self.assertTrue(graph.is_active())
        self.assertEqual(evaluate(graph, layer=(10, 20, 30, 128)),
                         (0, 20, 0, 128))

        graph.unlink_input(output.id, "Green")
        value = graph.add_node("value", params={"value": 0.5})
        graph.link(value.id, "Value", output.id, "Value")
        self.assertEqual(evaluate(graph), (128, 128, 128, 255))

    def test_output_channel_sockets_compose_and_tint_values(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        output = graph.add_node("output")
        graph.link(layer.id, "Blue", output.id, "Blue")
        graph.link(layer.id, "Red", output.id, "Red")
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (100, 0, 200, 128))

        graph.link(layer.id, "Green", output.id, "Green")
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (100, 150, 200, 128))
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 64)),
                         (100, 150, 200, 64))

        graph = nodes.NodeGraph()
        value = graph.add_node("value", params={"value": 0.5})
        output = graph.add_node("output")
        graph.link(value.id, "Value", output.id, "Red")
        self.assertEqual(evaluate(graph), (128, 0, 0, 255))

        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        output = graph.add_node("output")
        graph.link(layer.id, "Green", output.id, "Value")
        self.assertEqual(evaluate(graph, layer=(10, 150, 30, 128)),
                         (150, 150, 150, 255))

    def test_multiple_wires_average_colours_and_sum_numbers(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        white = graph.add_node("constant", params={"color": "FFFFFFFF"})
        output = graph.add_node("output")
        graph.link(layer.id, "Color", output.id, "Color")
        graph.link(white.id, "Color", output.id, "Color")
        self.assertEqual(evaluate(graph, layer=(255, 0, 0, 255)),
                         (255, 128, 128, 255))

        graph = nodes.NodeGraph()
        first = graph.add_node("value", params={"value": 0.25})
        second = graph.add_node("value", params={"value": 0.5})
        math_node = graph.add_node("math", params={"mode": "add"})
        output = graph.add_node("output")
        graph.link(first.id, "Value", math_node.id, "A")
        graph.link(second.id, "Value", math_node.id, "A")
        graph.link(math_node.id, "Value", output.id, "Color")
        self.assertEqual(evaluate(graph, below=(0, 0, 0, 255)),
                         (191, 191, 191, 255))

    def test_layer_values_can_skip_unconsumed_measurements(self):
        values = nodes.layer_values((255, 0, 0, 128), ("Red", "Alpha"))
        self.assertEqual(values, {"Red": 1.0, "Alpha": 128 / 255.0})
        self.assertEqual(nodes.layer_values((255, 0, 0, 128), set()), {})
        self.assertEqual(nodes.layer_values((255, 0, 0, 128), ["Hue"]),
                         {"Hue": 0.0})

    def test_layer_colour_channels_render_as_channel_colours(self):
        for channel, expected in (
                ("Red", (100, 0, 0, 128)),
                ("Green", (0, 150, 0, 128)),
                ("Blue", (0, 0, 200, 128))):
            graph = nodes.NodeGraph()
            layer = graph.add_node("layer")
            output = graph.add_node("output")
            graph.link(layer.id, channel, output.id, "Color")
            self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                             expected, channel)

        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        output = graph.add_node("output")
        layer.params["@gate:out:Green"] = "nand"
        graph.link(layer.id, "Green", output.id, "Color")
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (0, 105, 0, 128))

    def test_layer_exposes_channels_and_measurements(self):
        values = nodes.layer_values((255, 0, 0, 128))
        self.assertEqual(values["Color"], (255, 0, 0, 128))
        self.assertEqual(values["Red"], 1.0)
        self.assertEqual(values["Green"], 0.0)
        self.assertEqual(values["Blue"], 0.0)
        self.assertAlmostEqual(values["Alpha"], 128 / 255.0)
        self.assertAlmostEqual(values["Opacity"], 128 / 255.0)
        self.assertEqual(values["Hue"], 0.0)
        self.assertEqual(values["Saturation"], 1.0)
        self.assertEqual(values["HSL Saturation"], 1.0)
        self.assertEqual(values["Brightness"], 1.0)
        self.assertEqual(values["Lightness"], 0.5)
        self.assertAlmostEqual(values["Luminance"], 0.299)
        self.assertAlmostEqual(values["Gamma"], 0.299 ** (1.0 / 2.2))
        self.assertEqual(values["Chroma"], 1.0)

        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        output = graph.add_node("output")
        self.assertTrue(graph.link(layer.id, "Lightness", output.id,
                                   "Color"))
        self.assertEqual(evaluate(graph, layer=(128, 128, 128, 255)),
                         (128, 128, 128, 255))

    def test_value_node_adds_eight_inputs_without_limits(self):
        spec = nodes.NODE_TYPES["value"]
        self.assertEqual(len(spec["inputs"]), 8)
        self.assertIsNone(spec["params"][0]["min"])
        self.assertIsNone(spec["params"][0]["max"])
        graph = nodes.NodeGraph()
        value = graph.add_node("value", params={"value": 0.1, "inputs": 2})
        first = graph.add_node("value", params={"value": 0.2})
        second = graph.add_node("value", params={"value": 0.3})
        output = graph.add_node("output")
        self.assertTrue(graph.link(first.id, "Value", value.id, "A"))
        self.assertTrue(graph.link(second.id, "Value", value.id, "B"))
        self.assertTrue(graph.link(value.id, "Value", output.id, "Color"))
        self.assertEqual(evaluate(graph, below=(0, 0, 0, 255)),
                         (153, 153, 153, 255))
        huge = graph.add_node("value", params={"value": 1e6})
        self.assertEqual(huge.params["value"], 1e6)

        # Sockets are adjustable and only the chosen number is exposed.
        self.assertEqual(len(nodes.node_inputs(value)), 2)
        fresh = graph.add_node("value")
        self.assertEqual(len(nodes.node_inputs(fresh)), 0)
        fresh.params["inputs"] = 8
        self.assertEqual(len(nodes.node_inputs(fresh)), 8)
        fresh.params["inputs"] = 99
        self.assertEqual(len(nodes.node_inputs(fresh)), 8)
        old = nodes.Node.from_state({"id": 99, "type": "value",
                                     "params": {"value": 0.5}})
        self.assertEqual(len(nodes.node_inputs(old)), 8)

    def test_wire_splitter_joins_its_inputs_and_fans_out(self):
        graph = nodes.NodeGraph()
        splitter = graph.add_node("splitter")
        output = graph.add_node("output")
        self.assertEqual(nodes.node_inputs(splitter), (("A", nodes.WIRE),))
        self.assertEqual([name for name, _kind in nodes.node_outputs(splitter)],
                         ["Wire", "Wire 2", "Wire 3"])
        self.assertEqual(dict(nodes.node_outputs(splitter))["Wire 2"],
                         nodes.WIRE)

        layer = graph.add_node("layer")
        graph.link(layer.id, "Color", splitter.id, "A")
        graph.link(splitter.id, "Wire 2", output.id, "Color")
        self.assertEqual(evaluate(graph), RED)

        splitter.params["inputs"] = 2
        blue = graph.add_node("constant", params={"color": "0000FFFF"})
        graph.link(blue.id, "Color", splitter.id, "B")
        self.assertEqual(evaluate(graph), (128, 0, 128, 255))
        splitter.params["inputs"] = 1
        self.assertEqual(evaluate(graph), RED)

    def test_wire_joiner_sums_numbers_and_can_duplicate_outputs(self):
        graph = nodes.NodeGraph()
        joiner = graph.add_node("joiner")
        first = graph.add_node("value", params={"value": 0.25})
        second = graph.add_node("value", params={"value": 0.5})
        output = graph.add_node("output")
        self.assertEqual(len(nodes.node_inputs(joiner)), 2)
        graph.link(first.id, "Value", joiner.id, "A")
        graph.link(second.id, "Value", joiner.id, "B")
        joiner.params["outputs"] = 2
        graph.link(joiner.id, "Wire 2", output.id, "Color")
        self.assertEqual(evaluate(graph), (191, 191, 191, 255))

    def test_wire_router_pairs_inputs_and_outputs(self):
        graph = nodes.NodeGraph()
        router = graph.add_node("router")
        layer = graph.add_node("layer")
        blue = graph.add_node("constant", params={"color": "0000FFFF"})
        output = graph.add_node("output")
        self.assertEqual([name for name, _kind in nodes.node_inputs(router)],
                         ["A", "B"])
        self.assertEqual([name for name, _kind in nodes.node_outputs(router)],
                         ["Out 1", "Out 2"])
        graph.link(layer.id, "Color", router.id, "A")
        graph.link(blue.id, "Color", router.id, "B")
        graph.link(router.id, "Out 1", output.id, "Color")
        self.assertEqual(evaluate(graph), RED)
        graph.unlink(router.id, "Out 1", output.id, "Color")
        graph.link(router.id, "Out 2", output.id, "Color")
        self.assertEqual(evaluate(graph), BLUE)

        # Counts are adjustable, and unused trailing sockets disappear.
        router.params["inputs"] = 3
        router.params["outputs"] = 3
        self.assertEqual([name for name, _kind in nodes.node_inputs(router)],
                         ["A", "B", "C"])
        self.assertEqual([name for name, _kind in nodes.node_outputs(router)],
                         ["Out 1", "Out 2", "Out 3"])
        router.params["outputs"] = 1
        self.assertEqual([name for name, _kind in nodes.node_outputs(router)],
                         ["Out 1"])

    def test_wire_node_counts_survive_state_round_trips(self):
        graph = nodes.NodeGraph()
        splitter = graph.add_node("splitter", params={"inputs": 3})
        splitter.params["outputs"] = 5
        joiner = graph.add_node("joiner", params={"inputs": 4})
        joiner.params["outputs"] = 2
        router = graph.add_node("router", params={"inputs": 5})
        router.params["outputs"] = 6
        loaded = nodes.NodeGraph.from_state(graph.to_state())
        self.assertEqual(len(nodes.node_inputs(loaded.nodes[splitter.id])), 3)
        self.assertEqual(len(nodes.node_outputs(loaded.nodes[splitter.id])), 5)
        self.assertEqual(len(nodes.node_inputs(loaded.nodes[joiner.id])), 4)
        self.assertEqual(len(nodes.node_outputs(loaded.nodes[joiner.id])), 2)
        self.assertEqual(len(nodes.node_inputs(loaded.nodes[router.id])), 5)
        self.assertEqual(len(nodes.node_outputs(loaded.nodes[router.id])), 6)

    def test_ramp_maps_values_and_hue_through_a_gradient(self):
        graph = nodes.NodeGraph()
        ramp = graph.add_node("ramp")
        output = graph.add_node("output")
        graph.link(ramp.id, "Color", output.id, "Color")
        # Default luminance source: red luminance 0.299 -> grey 76.
        self.assertEqual(evaluate(graph, layer=(255, 0, 0, 255)),
                         (76, 76, 76, 255))
        value = graph.add_node("value", params={"value": 0.5})
        graph.link(value.id, "Value", ramp.id, "Value")
        self.assertEqual(evaluate(graph), (128, 128, 128, 255))
        graph.unlink(value.id, "Value", ramp.id, "Value")
        ramp.params["mode"] = "hue"
        # Green's hue is 120 degrees -> 0.333 -> grey 85.
        self.assertEqual(evaluate(graph, layer=(0, 255, 0, 255)),
                         (85, 85, 85, 255))

        graph.link(value.id, "Value", ramp.id, "Value")
        ramp.params["mode"] = "luminance"
        ramp.params["gradient"] = "0:FF0000FF,1:0000FFFF"
        self.assertEqual(evaluate(graph), (128, 0, 128, 255))
        ramp.params["gradient"] = "0:00FF00FF,1:000000FF"
        self.assertEqual(evaluate(graph), (0, 128, 0, 255))
        ramp.params["gradient"] = None
        self.assertEqual(evaluate(graph), (128, 128, 128, 255))

    def test_ramp_interpolation_modes(self):
        text = "0:000000FF,1:FFFFFFFF"
        self.assertEqual(nodes.gradient_color(text, 0.25, "linear"),
                         (64, 64, 64, 255))
        self.assertEqual(nodes.gradient_color(text, 0.25, "constant"),
                         (0, 0, 0, 255))
        self.assertEqual(nodes.gradient_color(text, 0.25, "smooth"),
                         (40, 40, 40, 255))
        self.assertEqual(nodes.gradient_color(text, 0.75, "smooth"),
                         (215, 215, 215, 255))
        self.assertEqual(nodes.gradient_color(text, 0.25, "unknown"),
                         (64, 64, 64, 255))
        steps = "0:FF0000FF,0.5:00FF00FF,1:0000FFFF"
        self.assertEqual(nodes.gradient_color(steps, 0.49, "constant"),
                         (255, 0, 0, 255))
        self.assertEqual(nodes.gradient_color(steps, 0.5, "constant"),
                         (0, 255, 0, 255))

        graph = nodes.NodeGraph()
        ramp = graph.add_node("ramp")
        value = graph.add_node("value", params={"value": 0.25})
        output = graph.add_node("output")
        graph.link(value.id, "Value", ramp.id, "Value")
        graph.link(ramp.id, "Color", output.id, "Color")
        self.assertEqual(evaluate(graph), (64, 64, 64, 255))
        ramp.params["interpolation"] = "constant"
        self.assertEqual(evaluate(graph), (0, 0, 0, 255))
        ramp.params["interpolation"] = "smooth"
        self.assertEqual(evaluate(graph), (40, 40, 40, 255))

    def test_mask_node_measures_channels_and_inverts(self):
        self.assertAlmostEqual(
            nodes.mask_measurement((100, 150, 200, 128), "alpha"),
            128 / 255.0)
        self.assertAlmostEqual(
            nodes.mask_measurement((100, 150, 200, 128), "luminance"),
            140.75 / 255.0)

        graph = nodes.NodeGraph()
        mask = graph.add_node("mask", params={"source": "alpha"})
        output = graph.add_node("output")
        graph.link(mask.id, "Color", output.id, "Color")
        expected = {
            "alpha": 128,
            "luminance": 141,
            "red": 100,
            "green": 150,
            "blue": 200,
        }
        for source, grey in expected.items():
            mask.params["source"] = source
            self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                             (grey, grey, grey, 255), source)

        mask.params["source"] = "alpha"
        mask.params["invert"] = "yes"
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (127, 127, 127, 255))

    def test_apply_mask_multiplies_source_alpha(self):
        graph = nodes.NodeGraph()
        apply_mask = graph.add_node("mask_apply")
        output = graph.add_node("output")
        graph.link(apply_mask.id, "Color", output.id, "Color")
        # An unconnected mask means fully visible.
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (100, 150, 200, 128))

        amount = graph.add_node("value", params={"value": 0.5})
        graph.link(amount.id, "Value", apply_mask.id, "Mask")
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (100, 150, 200, 64))
        amount.params["value"] = 2.0
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (100, 150, 200, 128))
        amount.params["value"] = -1.0
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (100, 150, 200, 0))

        graph.unlink(amount.id, "Value", apply_mask.id, "Mask")
        mask = graph.add_node("mask", params={"source": "alpha"})
        graph.link(mask.id, "Value", apply_mask.id, "Mask")
        self.assertEqual(evaluate(graph, layer=(100, 150, 200, 128)),
                         (100, 150, 200, 64))

    def test_mask_nodes_survive_state_roundtrip(self):
        graph = nodes.NodeGraph()
        mask = graph.add_node("mask", params={"source": "green",
                                                "invert": "yes"})
        apply_mask = graph.add_node("mask_apply")
        output = graph.add_node("output")
        graph.link(mask.id, "Value", apply_mask.id, "Mask")
        graph.link(apply_mask.id, "Color", output.id, "Color")
        before = evaluate(graph, layer=(100, 150, 200, 128))
        restored = nodes.NodeGraph.from_state(graph.to_state())
        self.assertEqual(evaluate(restored, layer=(100, 150, 200, 128)),
                         before)
        restored_mask = next(node for node in restored.nodes.values()
                             if node.type == "mask")
        restored_apply = next(node for node in restored.nodes.values()
                              if node.type == "mask_apply")
        self.assertEqual(restored_mask.params["source"], "green")
        self.assertEqual(restored_mask.params["invert"], "yes")
        self.assertEqual(restored_apply.params, {})

    def test_gradient_presets_and_reverse(self):
        labels = [label for label, _value in nodes.GRADIENT_PRESETS]
        self.assertIn("Rainbow", labels)
        for label, text in nodes.GRADIENT_PRESETS:
            self.assertTrue(label and text)
            self.assertEqual(nodes.gradient_text(nodes.parse_gradient(text)),
                             text)
            self.assertEqual(
                nodes.reverse_gradient(nodes.reverse_gradient(text)),
                nodes.gradient_text(nodes.parse_gradient(text)))
        self.assertEqual(
            nodes.reverse_gradient("0:FF0000FF,1:0000FFFF"),
            "0:0000FFFF,1:FF0000FF")
        self.assertEqual(
            nodes.reverse_gradient("0:000000FF,0.25:FF0000FF,"
                                   "0.6:00FF00FF,1:0000FFFF"),
            "0:0000FFFF,0.4:00FF00FF,0.75:FF0000FF,1:000000FF")

    def test_gradient_stops_parse_sort_and_clamp(self):
        text = "1:FFFFFFFF,0:000000FF"
        stops = nodes.parse_gradient(text)
        self.assertEqual(stops[0], (0.0, (0, 0, 0, 255)))
        self.assertEqual(stops[-1], (1.0, (255, 255, 255, 255)))
        self.assertEqual(nodes.gradient_color(text, 0.5),
                         (128, 128, 128, 255))
        self.assertEqual(nodes.gradient_color(text, -1), (0, 0, 0, 255))
        self.assertEqual(nodes.gradient_color(text, 2),
                         (255, 255, 255, 255))
        self.assertEqual(nodes.gradient_color("0:FF000000,1:FF0000FF", 0.5),
                         (255, 0, 0, 128))
        self.assertEqual(nodes.parse_gradient("nonsense"),
                         ((0.0, (0, 0, 0, 255)),
                          (1.0, (255, 255, 255, 255))))
        self.assertEqual(nodes.gradient_text(stops), "0:000000FF,1:FFFFFFFF")

    def test_output_sockets_are_adjustable(self):
        graph = nodes.NodeGraph()
        value = graph.add_node("value", params={"value": 0.5})
        self.assertEqual([name for name, _kind in nodes.node_outputs(value)],
                         ["Value"])
        value.params["outputs"] = 3
        self.assertEqual([name for name, _kind in nodes.node_outputs(value)],
                         ["Value", "Value 2", "Value 3"])
        output = graph.add_node("output")
        self.assertTrue(graph.link(value.id, "Value 2", output.id, "Color"))
        self.assertEqual(evaluate(graph), (128, 128, 128, 255))

        layer = graph.add_node("layer")
        self.assertEqual(len(nodes.node_outputs(layer)), 14)
        layer.params["outputs"] = 3
        self.assertEqual([name for name, _kind in nodes.node_outputs(layer)],
                         ["Color", "Red", "Green"])
        self.assertFalse(graph.link(layer.id, "Blue", output.id, "Color"))

    def test_micro_nand_gates_invert_sockets(self):
        graph = nodes.NodeGraph()
        value = graph.add_node("value", params={"value": 0.25})
        value.params["@gate:out:Value"] = "nand"
        output = graph.add_node("output")
        graph.link(value.id, "Value", output.id, "Color")
        self.assertEqual(evaluate(graph), (191, 191, 191, 255))

        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        layer.params["@gate:out:Color"] = "nand"
        output = graph.add_node("output")
        graph.link(layer.id, "Color", output.id, "Color")
        self.assertEqual(evaluate(graph, layer=(255, 10, 20, 128)),
                         (0, 245, 235, 128))

        graph = nodes.NodeGraph()
        value = graph.add_node("value", params={"value": 0.25})
        math_node = graph.add_node("math", params={"mode": "add"})
        math_node.params["@gate:in:A"] = "nand"
        graph.link(value.id, "Value", math_node.id, "A")
        output = graph.add_node("output")
        graph.link(math_node.id, "Value", output.id, "Color")
        self.assertEqual(evaluate(graph), (191, 191, 191, 255))

    def test_micro_gates_cover_every_logic_type(self):
        expected = {
            "and": 0.25, "or": 1.0, "xor": 0.75, "not": 0.75,
            "nand": 0.75, "nor": 0.0, "xnor": 0.25,
        }
        self.assertEqual(tuple(nodes.MICRO_GATES), tuple(expected))
        for mode, result in expected.items():
            self.assertAlmostEqual(nodes.apply_socket_gate(0.25, mode),
                                   result, msg=mode)

        # Unconnected B defaults to all ones, preserving the old NAND
        # behaviour while giving the other gate types a reference operand.
        self.assertEqual(nodes.apply_socket_gate((255, 10, 20, 128),
                                                 "nand"),
                         (0, 245, 235, 128))
        self.assertEqual(nodes.apply_socket_gate((255, 10, 20, 128),
                                                 "or"),
                         (255, 255, 255, 128))
        self.assertEqual(nodes.apply_socket_gate((255, 10, 20, 128),
                                                 "nor"),
                         (0, 0, 0, 128))

    def test_micro_gates_expose_and_evaluate_their_own_b_sockets(self):
        expected = {
            "and": 64, "or": 191, "xor": 128, "not": 191,
            "nand": 191, "nor": 64, "xnor": 128,
        }
        for mode, channel in expected.items():
            graph = nodes.NodeGraph()
            source = graph.add_node("value", params={"value": 0.25})
            operand = graph.add_node("value", params={"value": 0.75})
            output = graph.add_node("output")
            source.params["@gate:out:Value"] = mode
            self.assertEqual(nodes.node_micro_gates(source),
                             (("out", "Value", mode),))
            self.assertEqual(
                [name for name, _kind in nodes.node_gate_inputs(source)],
                [] if mode == "not" else ["@B:out:Value"])
            self.assertTrue(graph.link(source.id, "Value", output.id,
                                       "Color"))
            b_port = nodes.gate_input_name("out", "Value")
            linked = graph.link(operand.id, "Value", source.id, b_port)
            self.assertEqual(linked, mode != "not", mode)
            self.assertEqual(evaluate(graph),
                             (channel, channel, channel, 255), mode)

    def test_micro_gate_b_wires_work_on_inputs_and_colours(self):
        graph = nodes.NodeGraph()
        source = graph.add_node("value", params={"value": 0.25})
        operand = graph.add_node("value", params={"value": 0.75})
        math_node = graph.add_node("math", params={"mode": "add"})
        output = graph.add_node("output")
        math_node.params["@gate:in:A"] = "xor"
        self.assertTrue(graph.link(source.id, "Value", math_node.id, "A"))
        self.assertTrue(graph.link(
            operand.id, "Value", math_node.id,
            nodes.gate_input_name("in", "A")))
        self.assertTrue(graph.link(math_node.id, "Value", output.id,
                                   "Color"))
        self.assertEqual(evaluate(graph), (128, 128, 128, 255))

        graph = nodes.NodeGraph()
        red = graph.add_node("constant", params={"color": "FF000080"})
        green = graph.add_node("constant", params={"color": "00FF00FF"})
        output = graph.add_node("output")
        red.params["@gate:out:Color"] = "or"
        self.assertTrue(graph.link(
            green.id, "Color", red.id,
            nodes.gate_input_name("out", "Color")))
        self.assertTrue(graph.link(red.id, "Color", output.id, "Color"))
        self.assertEqual(evaluate(graph), (255, 255, 0, 128))

    def test_micro_gate_b_wires_survive_state_roundtrip(self):
        graph = nodes.NodeGraph()
        source = graph.add_node("value", params={"value": 0.25})
        operand = graph.add_node("value", params={"value": 0.75})
        output = graph.add_node("output")
        source.params["@gate:out:Value"] = "xnor"
        graph.link(source.id, "Value", output.id, "Color")
        graph.link(operand.id, "Value", source.id,
                   nodes.gate_input_name("out", "Value"))

        restored = nodes.NodeGraph.from_state(graph.to_state())
        restored_source = next(node for node in restored.nodes.values()
                               if node.type == "value"
                               and node.id == source.id)
        self.assertEqual(restored_source.params.get("@gate:out:Value"),
                         "xnor")
        self.assertEqual(len(restored.links), 2)
        self.assertEqual(evaluate(restored), (128, 128, 128, 255))

    def test_output_count_and_gates_survive_state_roundtrip(self):
        graph = nodes.NodeGraph()
        value = graph.add_node("value",
                               params={"value": 0.25, "outputs": 2})
        value.params["@gate:out:Value"] = "nand"
        output = graph.add_node("output")
        output.params["@gate:in:Color"] = "nand"
        graph.link(value.id, "Value 2", output.id, "Color")

        restored = nodes.NodeGraph.from_state(graph.to_state())
        restored_value = next(node for node in restored.nodes.values()
                              if node.type == "value")
        restored_output = next(node for node in restored.nodes.values()
                               if node.type == "output")
        self.assertEqual(len(nodes.node_outputs(restored_value)), 2)
        self.assertEqual(restored_value.params.get("@gate:out:Value"),
                         "nand")
        self.assertEqual(restored_output.params.get("@gate:in:Color"),
                         "nand")
        self.assertEqual(evaluate(restored), (191, 191, 191, 255))

    def test_implicit_a_and_b_defaults_are_layer_and_below(self):
        graph = nodes.NodeGraph()
        op = graph.add_node("operation", params={"mode": "add"})
        output = graph.add_node("output")
        graph.link(op.id, "Color", output.id, "Color")
        self.assertEqual(evaluate(graph, layer=(100, 0, 0, 255),
                                  below=(200, 0, 0, 255)), (255, 0, 0, 255))

    def test_operation_gate_and_blend_nodes(self):
        for node_type, mode, expected in (
                ("operation", "add", (255, 0, 0, 255)),
                ("operation", "multiply", (78, 0, 0, 255)),
                ("gate", "nand", (191, 255, 255, 255)),
                ("blend", "darken", (100, 0, 0, 255)),
                ("blend", "screen", (222, 0, 0, 255))):
            graph = nodes.NodeGraph()
            node = graph.add_node(node_type, params={"mode": mode})
            output = graph.add_node("output")
            graph.link(node.id, "Color", output.id, "Color")
            self.assertEqual(evaluate(graph, layer=(100, 0, 0, 255),
                                      below=(200, 0, 0, 255)), expected,
                             (node_type, mode))

    def test_disconnected_nodes_keep_a_direct_output_passthrough(self):
        graph = nodes.NodeGraph.starter()
        graph.add_node("hue")
        self.assertTrue(graph.is_direct_passthrough())
        self.assertFalse(graph.is_passthrough())

    def test_long_hue_chain_does_not_hit_the_recursion_limit(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        previous = layer
        for _index in range(1100):
            hue = graph.add_node("hue")
            graph.link(previous.id, "Color", hue.id, "Color")
            previous = hue
        output = graph.add_node("output")
        graph.link(previous.id, "Color", output.id, "Color")
        result = evaluate(graph)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[3], 255)

    def test_gate_nodes_keep_their_wire_colour(self):
        graph = nodes.NodeGraph()
        gate = graph.add_node("gate", params={"mode": "xor"})
        self.assertEqual(gate.params.get("color"),
                         nodes.GATE_COLOR_DEFAULT)
        gate.params["color"] = "20C0FF"
        restored = nodes.NodeGraph.from_state(graph.to_state())
        restored_gate = next(node for node in restored.nodes.values()
                             if node.type == "gate")
        self.assertEqual(restored_gate.params.get("color"), "20C0FF")

    def test_hue_shift_node_can_be_driven_by_a_value_node(self):
        graph = nodes.NodeGraph()
        hue = graph.add_node("hue")
        amount = graph.add_node("value", params={"value": 120})
        output = graph.add_node("output")
        graph.link(amount.id, "Value", hue.id, "Shift")
        graph.link(hue.id, "Color", output.id, "Color")
        red, green, blue, alpha = evaluate(graph, layer=RED)
        self.assertLess(red, 10)
        self.assertGreater(green, 245)
        self.assertLess(blue, 10)
        self.assertEqual(alpha, 255)

    def test_math_node_drives_a_hue_shift(self):
        graph = nodes.NodeGraph()
        left = graph.add_node("value", params={"value": 40})
        right = graph.add_node("value", params={"value": 3})
        math_node = graph.add_node("math", params={"mode": "multiply"})
        hue = graph.add_node("hue")
        output = graph.add_node("output")
        graph.link(left.id, "Value", math_node.id, "A")
        graph.link(right.id, "Value", math_node.id, "B")
        graph.link(math_node.id, "Value", hue.id, "Shift")
        graph.link(hue.id, "Color", output.id, "Color")
        red, green, _blue, _alpha = evaluate(graph, layer=RED)
        self.assertLess(red, 10)
        self.assertGreater(green, 245)

    def test_perlin_node_is_deterministic_and_in_range(self):
        graph = nodes.NodeGraph()
        noise = graph.add_node("perlin", params={"scale": 8, "seed": 3})
        output = graph.add_node("output")
        graph.link(noise.id, "Color", output.id, "Color")
        first = evaluate(graph, x=3, y=5)
        self.assertEqual(first, evaluate(graph, x=3, y=5))
        self.assertGreaterEqual(first[0], 0)
        self.assertLessEqual(first[0], 255)
        self.assertEqual(first[0], first[1])
        self.assertEqual(first[3], 255)
        self.assertNotEqual(first[0], evaluate(graph, x=7, y=2)[0])

    def test_state_roundtrip_keeps_nodes_links_and_positions(self):
        graph = nodes.NodeGraph.starter()
        graph.add_node("hue", x=120, y=230, params={"shift": -45})
        restored = nodes.NodeGraph.from_state(graph.to_state())
        self.assertEqual(len(restored.nodes), len(graph.nodes))
        self.assertEqual(len(restored.links), len(graph.links))
        self.assertTrue(restored.is_active())
        restored_nodes = sorted(restored.nodes.values(), key=lambda n: n.id)
        self.assertEqual((restored_nodes[0].x, restored_nodes[0].y), (40, 70))
        self.assertEqual(evaluate(restored, layer=(10, 20, 30, 255)),
                         (10, 20, 30, 255))

    def test_duplicate_node_copies_parameters_and_offsets_position(self):
        graph = nodes.NodeGraph()
        source = graph.add_node("value", 10, 20,
                                {"value": 0.75, "inputs": 2,
                                 "outputs": 3,
                                 "@gate:out:Value": "nand"})
        output = graph.add_node("output")
        graph.link(source.id, "Value", output.id, "Color")
        copy = graph.duplicate_node(source.id)
        self.assertIsNotNone(copy)
        self.assertNotEqual(copy.id, source.id)
        self.assertEqual((copy.type, copy.x, copy.y), ("value", 34, 44))
        self.assertEqual(copy.params, source.params)
        self.assertIsNot(copy.params, source.params)
        copy.params["value"] = 0.1
        self.assertEqual(source.params["value"], 0.75)
        self.assertEqual(len(graph.links), 1)
        self.assertTrue(all(link["from"] != copy.id
                            for link in graph.links))
        self.assertIsNone(graph.duplicate_node(999))
        self.assertEqual(len(graph.nodes), 3)

    def test_from_state_ignores_invalid_entries(self):
        graph = nodes.NodeGraph.from_state({
            "nodes": [{"id": 1, "type": "missing"},
                      {"id": 2, "type": "layer"},
                      "junk"],
            "links": [{"from": 2, "from_port": "Color", "to": 99,
                       "to_port": "Color"}, "junk"],
        })
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(graph.links, [])


class ComplexRenderTests(unittest.TestCase):
    def test_hue_chain_flattens_through_the_fast_path(self):
        document = Document(2, 1)
        document.layers[0].bitmap.set_rgba(0, 0, (200, 10, 20, 255))
        document.layers[0].bitmap.set_rgba(1, 0, (20, 80, 200, 128))
        document.layers[0].render_mode = model.RENDER_COMPLEX
        graph = nodes.NodeGraph.starter()
        layer = next(node for node in graph.nodes.values()
                     if node.type == "layer")
        output = next(node for node in graph.nodes.values()
                      if node.type == "output")
        hue = graph.add_node("hue")
        graph.unlink_input(output.id, "Color")
        graph.link(layer.id, "Color", hue.id, "Color")
        graph.link(hue.id, "Color", output.id, "Color")
        document.layers[0].graph = graph
        flattened = document.flatten()
        self.assertEqual(flattened.rgba(0, 0),
                         nodes.hue_shift((200, 10, 20, 255), 30))
        self.assertEqual(flattened.rgba(1, 0),
                         nodes.hue_shift((20, 80, 200, 128), 30))

    def test_branched_complex_graph_fast_path_matches_evaluator(self):
        document = Document(2, 1)
        document.layers[0].bitmap.set_rgba(0, 0, (200, 10, 20, 255))
        document.layers[0].bitmap.set_rgba(1, 0, (20, 80, 200, 128))
        document.layers[0].render_mode = model.RENDER_COMPLEX
        graph = nodes.NodeGraph()
        source = graph.add_node("layer_source",
                                params={"layer": document.layers[0].name})
        hue = graph.add_node("hue")
        operation = graph.add_node("operation", params={"mode": "add"})
        output = graph.add_node("output")
        graph.link(source.id, "Color", hue.id, "Color")
        graph.link(source.id, "Color", operation.id, "A")
        graph.link(hue.id, "Color", operation.id, "B")
        graph.link(operation.id, "Color", output.id, "Color")
        document.layers[0].graph = graph

        fast = document.flatten()
        self.assertIsNotNone(graph._fast_dag)
        reference = bytes(fast.data)
        graph._fast_dag = None
        general = document.flatten()
        self.assertEqual(bytes(general.data), reference)

    def test_direct_complex_passthrough_avoids_graph_evaluation(self):
        document = Document(2, 1)
        document.layers[0].bitmap.set_rgba(0, 0, (1, 2, 3, 4))
        document.layers[0].render_mode = model.RENDER_COMPLEX
        graph = nodes.NodeGraph.starter()
        graph.add_node("hue")
        document.layers[0].graph = graph
        with mock.patch.object(nodes.NodeGraph, "eval_pixel",
                               side_effect=AssertionError(
                                   "direct passthrough evaluated the graph")):
            self.assertEqual(document.flatten().rgba(0, 0), (1, 2, 3, 4))

    def make_doc(self):
        document = Document(2, 1)
        document.layers[0].bitmap.set_rgba(0, 0, (200, 0, 0, 255))
        document.layers[0].bitmap.set_rgba(1, 0, (50, 10, 10, 255))
        document.add_layer()
        document.layers[1].bitmap.set_rgba(0, 0, (100, 0, 0, 255))
        document.layers[1].bitmap.set_rgba(1, 0, (20, 0, 0, 255))
        return document

    def test_starter_graph_matches_plain_source_over(self):
        document = self.make_doc()
        layer = document.layers[1]
        self.assertEqual(document.flatten().rgba(0, 0), (100, 0, 0, 255))
        layer.render_mode = nodes.RENDER_COMPLEX
        layer.graph = nodes.NodeGraph.starter()
        self.assertEqual(document.flatten().rgba(0, 0), (100, 0, 0, 255))
        self.assertEqual(document.flatten().rgba(1, 0), (20, 0, 0, 255))

    def test_complex_render_ignores_the_quick_options(self):
        document = self.make_doc()
        layer = document.layers[1]
        layer.op = "add"
        self.assertEqual(document.flatten().rgba(0, 0), (255, 0, 0, 255))
        layer.render_mode = nodes.RENDER_COMPLEX
        layer.graph = nodes.NodeGraph.starter()
        self.assertEqual(document.flatten().rgba(0, 0), (100, 0, 0, 255))

    def test_output_gates_disable_the_starter_fast_path(self):
        document = self.make_doc()
        layer = document.layers[1]
        layer.render_mode = nodes.RENDER_COMPLEX
        graph = nodes.NodeGraph.starter()
        output = next(node for node in graph.nodes.values()
                      if node.type == "output")
        output.params["@gate:in:Color"] = "nand"
        layer.graph = graph
        self.assertFalse(graph.is_passthrough())
        self.assertEqual(document.flatten().rgba(0, 0),
                         (155, 255, 255, 255))

    def test_layer_channel_renders_only_that_colour(self):
        document = Document(2, 1)
        document.layers[0].bitmap.set_rgba(0, 0, (10, 20, 30, 255))
        document.layers[0].bitmap.set_rgba(1, 0, (10, 20, 30, 255))
        document.add_layer()
        document.layers[1].bitmap.set_rgba(0, 0, (100, 150, 200, 255))
        document.layers[1].bitmap.set_rgba(1, 0, (40, 80, 120, 255))
        layer = document.layers[1]
        layer.render_mode = nodes.RENDER_COMPLEX
        graph = nodes.NodeGraph()
        layer_node = graph.add_node("layer")
        output = graph.add_node("output")
        graph.link(layer_node.id, "Green", output.id, "Color")
        layer.graph = graph
        bitmap = document.flatten()
        self.assertEqual(bitmap.rgba(0, 0), (0, 150, 0, 255))
        self.assertEqual(bitmap.rgba(1, 0), (0, 80, 0, 255))

    def test_output_channel_fast_path_preserves_channels_and_values(self):
        document = Document(2, 1)
        layer = document.layers[0]
        layer.bitmap.set_rgba(0, 0, (100, 150, 200, 128))
        layer.bitmap.set_rgba(1, 0, (10, 20, 30, 255))
        layer.render_mode = nodes.RENDER_COMPLEX
        graph = nodes.NodeGraph()
        source = graph.add_node("layer")
        output = graph.add_node("output")
        graph.link(source.id, "Red", output.id, "Red")
        graph.link(source.id, "Green", output.id, "Green")
        graph.link(source.id, "Blue", output.id, "Blue")
        layer.graph = graph
        bitmap = document.flatten()
        self.assertEqual(bitmap.rgba(0, 0), (100, 150, 200, 128))
        self.assertEqual(bitmap.rgba(1, 0), (10, 20, 30, 255))

        graph.unlink_input(output.id, "Red")
        graph.unlink_input(output.id, "Green")
        graph.unlink_input(output.id, "Blue")
        graph.link(source.id, "Green", output.id, "Value")
        bitmap = document.flatten()
        self.assertEqual(bitmap.rgba(0, 0), (150, 150, 150, 255))
        self.assertEqual(bitmap.rgba(1, 0), (20, 20, 20, 255))

    def test_layer_source_reads_any_layer_by_name(self):
        document = Document(2, 1)
        backdrop = document.layers[0]
        backdrop.name = "Backdrop"
        backdrop.bitmap.set_rgba(0, 0, (255, 0, 0, 255))
        backdrop.bitmap.set_rgba(1, 0, (0, 255, 0, 255))
        top = document.add_layer("Top")
        top.bitmap.set_rgba(0, 0, (0, 0, 255, 255))
        top.bitmap.set_rgba(1, 0, (0, 0, 255, 255))
        top.render_mode = nodes.RENDER_COMPLEX
        graph = nodes.NodeGraph()
        source = graph.add_node("layer_source",
                                params={"layer": "Backdrop"})
        output = graph.add_node("output")
        graph.link(source.id, "Color", output.id, "Color")
        top.graph = graph
        bitmap = document.flatten()
        self.assertEqual(bitmap.rgba(0, 0), (255, 0, 0, 255))
        self.assertEqual(bitmap.rgba(1, 0), (0, 255, 0, 255))

    def test_group_source_composites_every_group_member(self):
        document = Document(1, 1)
        document.layers[0].name = "Backdrop"
        document.layers[0].bitmap.set_rgba(0, 0, (255, 0, 0, 255))
        middle = document.add_layer("Middle")
        middle.bitmap.set_rgba(0, 0, (0, 255, 0, 255))
        document.group_layer(0, "Paint")
        document.group_layer(1, "Paint")
        top = document.add_layer("Top")
        top.bitmap.set_rgba(0, 0, (0, 0, 255, 255))
        top.render_mode = nodes.RENDER_COMPLEX
        graph = nodes.NodeGraph()
        source = graph.add_node("group_source",
                                params={"group": "Paint"})
        output = graph.add_node("output")
        graph.link(source.id, "Color", output.id, "Color")
        top.graph = graph
        self.assertEqual(document.flatten().rgba(0, 0), (0, 255, 0, 255))

    def test_source_references_follow_layer_and_group_renames(self):
        document = Document(1, 1)
        document.layers[0].name = "Old layer"
        document.group_layer(0, "Old group")
        document.add_layer("Top")
        layer_source = document.active_layer.graph.add_node(
            "layer_source", params={"layer": "Old layer"})
        group_source = document.active_layer.graph.add_node(
            "group_source", params={"group": "Old group"})
        self.assertTrue(document.rename_layer(0, "New layer"))
        self.assertTrue(document.rename_group("Old group", "New group"))
        self.assertEqual(layer_source.params["layer"], "New layer")
        self.assertEqual(group_source.params["group"], "New group")

    def test_operation_node_reads_the_stack_below(self):
        document = self.make_doc()
        layer = document.layers[1]
        layer.render_mode = nodes.RENDER_COMPLEX
        graph = nodes.NodeGraph()
        operation = graph.add_node("operation", params={"mode": "add"})
        output = graph.add_node("output")
        graph.link(operation.id, "Color", output.id, "Color")
        layer.graph = graph
        self.assertEqual(document.flatten().rgba(0, 0), (255, 0, 0, 255))
        self.assertEqual(document.flatten().rgba(1, 0), (70, 10, 10, 255))

    def test_single_layer_complex_graph_still_runs(self):
        document = Document(1, 1)
        document.layers[0].bitmap.set_rgba(0, 0, (255, 0, 0, 255))
        graph = nodes.NodeGraph()
        hue = graph.add_node("hue", params={"shift": 120})
        output = graph.add_node("output")
        graph.link(hue.id, "Color", output.id, "Color")
        document.layers[0].render_mode = nodes.RENDER_COMPLEX
        document.layers[0].graph = graph
        red, green, _blue, alpha = document.flatten().rgba(0, 0)
        self.assertLess(red, 10)
        self.assertGreater(green, 245)
        self.assertEqual(alpha, 255)

    def test_inactive_complex_graph_falls_back_to_quick_options(self):
        document = self.make_doc()
        layer = document.layers[1]
        layer.op = "add"
        layer.render_mode = nodes.RENDER_COMPLEX
        layer.graph = nodes.NodeGraph()
        self.assertEqual(document.flatten().rgba(0, 0), (255, 0, 0, 255))

    def test_unconnected_output_node_makes_the_layer_transparent(self):
        document = Document(1, 1)
        document.layers[0].bitmap.set_rgba(0, 0, (255, 0, 0, 255))
        document.layers[0].render_mode = nodes.RENDER_COMPLEX
        graph = nodes.NodeGraph()
        graph.add_node("layer")
        graph.add_node("output")
        document.layers[0].graph = graph
        self.assertEqual(document.flatten().rgba(0, 0), nodes.TRANSPARENT)

    def test_layer_state_roundtrip_keeps_the_graph(self):
        layer = Layer(2, 1)
        layer.render_mode = nodes.RENDER_COMPLEX
        layer.graph = nodes.NodeGraph.starter()
        restored = Layer.from_state(layer.to_state(), 2, 1)
        self.assertEqual(restored.render_mode, nodes.RENDER_COMPLEX)
        self.assertTrue(restored.graph.is_active())
        self.assertEqual(len(restored.graph.nodes), 2)

    def test_project_roundtrip_keeps_complex_render(self):
        import os
        import tempfile

        from hex2pix import formats

        document = self.make_doc()
        layer = document.layers[1]
        layer.render_mode = nodes.RENDER_COMPLEX
        layer.graph = nodes.NodeGraph.starter()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "graph.h2p")
            formats.save_project(path, document)
            loaded = formats.load_project(path).document
        self.assertEqual(loaded.layers[1].render_mode, nodes.RENDER_COMPLEX)
        self.assertTrue(loaded.layers[1].graph.is_active())
        self.assertEqual(loaded.flatten().rgba(0, 0), (100, 0, 0, 255))

    def test_copy_and_merge_handle_the_graph(self):
        document = self.make_doc()
        layer = document.layers[1]
        layer.render_mode = nodes.RENDER_COMPLEX
        layer.graph = nodes.NodeGraph.starter()
        copied = layer.copy()
        self.assertEqual(copied.render_mode, nodes.RENDER_COMPLEX)
        self.assertTrue(copied.graph.is_active())
        document.merge_down()
        self.assertEqual(document.layers[0].render_mode, nodes.RENDER_QUICK)
        self.assertFalse(document.layers[0].graph.nodes)

    def test_graph_undo_is_lightweight_and_restores_graph_only(self):
        document = Document(3, 2)
        layer = document.layers[0]
        layer.bitmap.set_rgba(0, 0, (12, 34, 56, 78))
        layer.render_mode = nodes.RENDER_COMPLEX
        layer.graph = nodes.NodeGraph.starter()
        with mock.patch.object(Document, "state",
                               side_effect=AssertionError(
                                   "graph edit copied layer pixels")):
            before = document.begin_graph()
            added = layer.graph.add_node("hue")
            document.commit_graph(before)
            self.assertEqual(document.undo._undo[-1][0].get("__kind__"),
                             "graph")
            self.assertTrue(document.undo_last())
            self.assertNotIn(added.id, layer.graph.nodes)
            self.assertTrue(document.redo_last())
            self.assertIn(added.id, layer.graph.nodes)
        self.assertEqual(layer.bitmap.rgba(0, 0), (12, 34, 56, 78))

    def test_a_graph_error_stops_after_the_first_pixel(self):
        graph = nodes.NodeGraph()
        layer = graph.add_node("layer")
        value = graph.add_node("value")
        output = graph.add_node("output")
        graph.link(layer.id, "Color", output.id, "Color")
        graph.link(value.id, "Value", output.id, "Alpha")
        graph.begin_frame()
        with mock.patch.object(nodes.NodeGraph, "_eval_node",
                               side_effect=ValueError("bad graph")) as broken:
            self.assertEqual(graph.eval_pixel(0, 0, RED, BLUE, 8, 8),
                             nodes.TRANSPARENT)
            self.assertEqual(graph.eval_pixel(1, 0, RED, BLUE, 8, 8),
                             nodes.TRANSPARENT)
        self.assertEqual(broken.call_count, 1)
        self.assertIn("bad graph", graph.last_error)


class NodeMathTests(unittest.TestCase):
    def test_perlin_is_a_seeded_0_to_1_field(self):
        values = [nodes.perlin2(x / 4.0, y / 4.0, 7)
                  for x in range(0, 20) for y in range(0, 20)]
        self.assertTrue(all(0.0 <= value <= 1.0 for value in values))
        self.assertGreater(max(values) - min(values), 0.1)
        self.assertEqual(nodes.perlin2(1.5, 2.5, 7),
                         nodes.perlin2(1.5, 2.5, 7))
        self.assertNotEqual(nodes.perlin2(1.5, 2.5, 7),
                            nodes.perlin2(1.5, 2.5, 8))

    def test_hue_shift_walks_the_colour_wheel(self):
        red = nodes.hue_shift(RED, 120)
        self.assertLess(red[0], 10)
        self.assertGreater(red[1], 245)
        self.assertLess(red[2], 10)
        self.assertEqual(red[3], 255)
        self.assertEqual(nodes.hue_shift((10, 20, 30, 128), 0),
                         (10, 20, 30, 128))

    def test_colour_helpers_round_trip(self):
        self.assertEqual(nodes.parse_color("FF000080"),
                         (255, 0, 0, 128))
        self.assertEqual(nodes.parse_color("#00ff00"), (0, 255, 0, 255))
        self.assertEqual(nodes.parse_color("nope"), (255, 255, 255, 255))
        self.assertEqual(nodes.color_text((1, 2, 3, 4)), "01020304")

    def test_combine_and_gate_match_the_quick_render_math(self):
        self.assertEqual(model.combine(200, 100, "multiply"), 78)
        self.assertEqual(model.combine(200, 100, "darken"), 100)
        self.assertEqual(model.gate_value(200, 100, "nand"), 191)
