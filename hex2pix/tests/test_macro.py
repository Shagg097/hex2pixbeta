"""Tests for the keyboard macro model."""

import json
import os
import tempfile
import unittest

from hex2pix.macro import Macro, Recorder, Step, dispatch, is_recordable

SHIFT = 0x0001
CONTROL = 0x0004


class RecordableTest(unittest.TestCase):
    def test_named_editing_keys_record(self):
        for key in ("Up", "Down", "Left", "Right", "Return", "KP_Enter",
                    "BackSpace", "Escape", "Delete", "plus", "minus",
                    "bracketleft", "bracketright", "space"):
            self.assertTrue(is_recordable(key, "", 0), key)

    def test_hex_digits_and_commands_record(self):
        for char in "0123456789abcdefgh":
            self.assertTrue(is_recordable(char, char, 0), char)

    def test_plain_letters_do_not_record(self):
        for char in "ijklmnopqrstuvwxyz":
            self.assertFalse(is_recordable(char, char, 0), char)

    def test_shift_letter_is_a_tool_shortcut(self):
        self.assertTrue(is_recordable("P", "P", SHIFT))
        self.assertTrue(is_recordable("W", "W", SHIFT))

    def test_control_records_edits_only(self):
        self.assertTrue(is_recordable("z", "\x1a", CONTROL))
        self.assertTrue(is_recordable("y", "\x19", CONTROL))
        self.assertTrue(is_recordable("d", "\x04", CONTROL))
        self.assertFalse(is_recordable("s", "\x13", CONTROL))
        self.assertFalse(is_recordable("o", "\x0f", CONTROL))
        self.assertFalse(is_recordable("n", "\x0e", CONTROL))

    def test_function_and_modifier_keys_do_not_record(self):
        self.assertFalse(is_recordable("F5", "", 0))
        self.assertFalse(is_recordable("F9", "", 0))
        self.assertFalse(is_recordable("Shift_L", "", SHIFT))
        self.assertFalse(is_recordable("Tab", "\t", 0))

    def test_multi_character_input_does_not_record(self):
        self.assertFalse(is_recordable("a", "ab", 0))


class MacroTest(unittest.TestCase):
    def make(self):
        macro = Macro()
        macro.add(Step("a", "a", 0))
        macro.add(Step("Return", "\r", 0))
        return macro

    def test_add_and_len(self):
        macro = self.make()
        self.assertEqual(len(macro), 2)
        self.assertEqual(macro.steps[1].keysym, "Return")

    def test_expanded_repeats_in_order(self):
        macro = self.make()
        steps = macro.expanded(3)
        self.assertEqual(len(steps), 6)
        self.assertEqual([s.keysym for s in steps[:2]], ["a", "Return"])
        self.assertEqual(macro.expanded(0), macro.steps)

    def test_dict_round_trip(self):
        macro = self.make()
        copy = Macro.from_dict(macro.to_dict())
        self.assertEqual([s.keysym for s in copy.steps], ["a", "Return"])
        self.assertEqual(copy.steps[0].char, "a")
        self.assertEqual(macro.to_dict()["format"], "hex2pix-macro")

    def test_from_dict_skips_junk(self):
        data = {"format": "hex2pix-macro", "steps": [
            {"keysym": "a", "char": "a", "state": 0},
            {"char": "b"},
            "nope",
            {"keysym": "Right", "state": 1},
        ]}
        macro = Macro.from_dict(data)
        self.assertEqual([s.keysym for s in macro.steps], ["a", "Right"])
        self.assertEqual(macro.steps[1].state, 1)

    def test_save_and_load(self):
        macro = self.make()
        path = os.path.join(tempfile.mkdtemp(prefix="hex2pix-macro-"),
                            "macro.json")
        macro.save(path)
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        self.assertEqual(raw["format"], "hex2pix-macro")
        self.assertEqual(len(raw["steps"]), 2)
        loaded = Macro.load(path)
        self.assertEqual([s.to_dict() for s in loaded.steps],
                         [s.to_dict() for s in macro.steps])

    def test_load_rejects_other_json(self):
        path = os.path.join(tempfile.mkdtemp(prefix="hex2pix-macro-"),
                            "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[1, 2, 3]")
        with self.assertRaises(ValueError):
            Macro.load(path)


class RecorderTest(unittest.TestCase):
    def test_capture_only_while_recording(self):
        recorder = Recorder()
        self.assertFalse(recorder.capture("a", "a", 0))
        recorder.start()
        self.assertTrue(recorder.capture("a", "a", 0))
        recorder.stop()
        self.assertFalse(recorder.capture("b", "b", 0))
        self.assertEqual(len(recorder.macro), 1)

    def test_start_clears_previous_recording(self):
        recorder = Recorder()
        recorder.start()
        recorder.capture("a", "a", 0)
        recorder.stop()
        recorder.start()
        self.assertEqual(len(recorder.macro), 0)

    def test_ignores_unrecordable_keys(self):
        recorder = Recorder()
        recorder.start()
        self.assertFalse(recorder.capture("F5", "", 0))
        self.assertFalse(recorder.capture("s", "\x13", CONTROL))
        self.assertEqual(len(recorder.macro), 0)

    def test_limit_stops_recording(self):
        recorder = Recorder(limit=2)
        recorder.start()
        self.assertTrue(recorder.capture("a", "a", 0))
        self.assertTrue(recorder.capture("b", "b", 0))
        self.assertFalse(recorder.capture("c", "c", 0))
        self.assertEqual(len(recorder.macro), 2)

    def test_toggle_flips_recording(self):
        recorder = Recorder()
        self.assertTrue(recorder.toggle())
        self.assertTrue(recorder.recording)
        self.assertFalse(recorder.toggle())
        self.assertFalse(recorder.recording)


class DispatchTest(unittest.TestCase):
    def test_dispatch_feeds_steps_in_order(self):
        recorder = Recorder()
        recorder.start()
        for char in ("a", "b", "c"):
            recorder.capture(char, char, 0)
        recorder.stop()
        seen = []
        count = dispatch(recorder.macro.steps,
                         lambda step: seen.append((step.keysym, step.char)))
        self.assertEqual(count, 3)
        self.assertEqual(seen, [("a", "a"), ("b", "b"), ("c", "c")])


if __name__ == "__main__":
    unittest.main()
