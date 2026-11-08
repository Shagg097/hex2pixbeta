"""Keyboard macros: record editing keystrokes and play them back.

A step stores the raw key data tkinter delivered (keysym, char, modifier
state), so playback goes through the editor's normal key handler. Only keys
the editor acts on are recorded, which keeps a macro deterministic: it can
never open a dialog or leave the canvas half way through a sequence.

No tkinter imports here, so the recording rules are unit-testable.
"""

import json

ARROW_KEYS = ("Up", "Down", "Left", "Right")

# Named keys the editor handles as editing actions.
NAMED_KEYS = frozenset(ARROW_KEYS + (
    "Return", "KP_Enter", "BackSpace", "Escape", "Delete",
    "plus", "equal", "KP_Add", "minus", "KP_Subtract",
    "bracketleft", "bracketright", "space",
))

# Control combinations that edit the document instead of opening a dialog.
CONTROL_KEYS = frozenset(("z", "y", "a", "d"))

# Unshifted characters the editor consumes (hex digits, picker, view toggle).
PLAIN_CHARS = frozenset("0123456789abcdefgh")

MOD_SHIFT = 0x0001
MOD_CONTROL = 0x0004

MAX_STEPS = 4000

MACRO_FORMAT = "hex2pix-macro"


def is_recordable(keysym, char, state):
    """True when this key is an editor action worth recording."""
    text = char or ""
    low = text.lower()
    if state & MOD_CONTROL:
        # Ctrl+Z arrives with char '\x1a' but keysym 'z', so match the keysym.
        return str(keysym).lower() in CONTROL_KEYS
    if keysym in NAMED_KEYS:
        return True
    if len(text) != 1:
        return False
    if state & MOD_SHIFT and low.isalpha():
        return True  # Shift+letter selects a tool
    return low in PLAIN_CHARS


class Step(object):
    """One recorded key press."""

    __slots__ = ("keysym", "char", "state")

    def __init__(self, keysym, char="", state=0):
        self.keysym = str(keysym)
        self.char = str(char or "")
        self.state = int(state)

    def __repr__(self):
        return "Step(%r, %r, %d)" % (self.keysym, self.char, self.state)

    def __eq__(self, other):
        return (isinstance(other, Step)
                and (self.keysym, self.char, self.state)
                == (other.keysym, other.char, other.state))

    def to_dict(self):
        return {"keysym": self.keysym, "char": self.char, "state": self.state}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("a macro step must be an object")
        keysym = data.get("keysym")
        if not isinstance(keysym, str) or not keysym:
            raise ValueError("a macro step needs a keysym")
        char = data.get("char")
        char = char[:4] if isinstance(char, str) else ""
        state = data.get("state") or 0
        try:
            state = int(state)
        except (TypeError, ValueError):
            state = 0
        return cls(keysym, char, state)


class Macro(object):
    """An ordered list of key steps, with JSON storage."""

    def __init__(self, steps=None):
        self.steps = [step for step in (steps or []) if isinstance(step, Step)]

    def __len__(self):
        return len(self.steps)

    def add(self, step):
        self.steps.append(step)

    def clear(self):
        self.steps = []

    def expanded(self, repeat=1):
        """The steps repeated `repeat` times, in order."""
        return list(self.steps) * max(1, int(repeat))

    def to_dict(self):
        return {
            "format": MACRO_FORMAT,
            "version": 1,
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or data.get("format", MACRO_FORMAT) != MACRO_FORMAT:
            raise ValueError("not a hex2pix macro file")
        steps = []
        for item in data.get("steps", []) or []:
            try:
                steps.append(Step.from_dict(item))
            except (TypeError, ValueError):
                continue  # tolerate damaged entries rather than losing all
        return cls(steps)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)


class Recorder(object):
    """Collects recordable keys while recording."""

    def __init__(self, limit=MAX_STEPS):
        self.macro = Macro()
        self.recording = False
        self.limit = max(1, int(limit))

    def start(self):
        self.macro.clear()
        self.recording = True

    def stop(self):
        self.recording = False

    def toggle(self):
        if self.recording:
            self.stop()
        else:
            self.start()
        return self.recording

    def capture(self, keysym, char, state):
        """Record one key if it is an editor action. Returns True if stored."""
        if not self.recording or len(self.macro.steps) >= self.limit:
            return False
        if not is_recordable(keysym, char, state):
            return False
        self.macro.add(Step(keysym, char, state))
        return True


def dispatch(steps, handler):
    """Feed each step to `handler(step)` in order; returns the count sent."""
    count = 0
    for step in steps:
        handler(step)
        count += 1
    return count
