"""Closed action vocabulary. Model-generated data is untrusted input."""
from dataclasses import dataclass
from typing import Union

MAX_ACTIONS = 8
MAX_TEXT = 128
MAX_BATCH_MS = 1000
MODIFIERS = frozenset({"CTRL", "SHIFT", "ALT", "META"})
KEYS = frozenset(chr(i) for i in range(65, 91)) | frozenset("0123456789") | frozenset({
    "ENTER", "ESCAPE", "BACKSPACE", "TAB", "SPACE", "DELETE", "HOME", "END",
    "PAGEUP", "PAGEDOWN", "RIGHT", "LEFT", "DOWN", "UP",
}) | frozenset(f"F{i}" for i in range(1, 13))


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Text:
    value: str


@dataclass(frozen=True)
class KeyChord:
    keys: tuple[str, ...]
    hold_ms: int = 30


@dataclass(frozen=True)
class ReleaseAll:
    pass


Action = Union[Text, KeyChord, ReleaseAll]


def _fields(value: dict, required: set[str], optional: frozenset[str] = frozenset()) -> None:
    if set(value) - required - optional or not required <= set(value):
        raise ValidationError("Missing or unknown action fields")


def parse_action(value: object) -> Action:
    if not isinstance(value, dict):
        raise ValidationError("Each action must be an object")
    kind = value.get("type")
    if kind == "text":
        _fields(value, {"type", "value"})
        text = value["value"]
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
            raise ValidationError(f"Text must contain 1–{MAX_TEXT} characters")
        if any(not 32 <= ord(char) <= 126 for char in text):
            raise ValidationError("Text is printable US-ASCII only; control keys must be explicit key chords")
        return Text(text)
    if kind == "keychord":
        _fields(value, {"type", "keys"}, frozenset({"hold_ms"}))
        keys, hold = value["keys"], value.get("hold_ms", 30)
        if not isinstance(keys, list) or not 1 <= len(keys) <= 10:
            raise ValidationError("A key chord needs 1–10 named keys")
        if any(not isinstance(key, str) or key not in KEYS | MODIFIERS for key in keys):
            raise ValidationError("Unknown key name; use the documented uppercase vocabulary")
        if len(keys) != len(set(keys)) or sum(key in KEYS for key in keys) > 6:
            raise ValidationError("Keys must be unique, with at most six non-modifier keys")
        if type(hold) is not int or not 5 <= hold <= 100:
            raise ValidationError("hold_ms must be an integer between 5 and 100")
        return KeyChord(tuple(keys), hold)
    if kind == "release_all":
        _fields(value, {"type"})
        return ReleaseAll()
    raise ValidationError("Unsupported action; only text, keychord, and release_all are enabled")


def action_dict(action: Action) -> dict:
    if type(action) is Text:
        return {"type": "text", "value": action.value}
    if type(action) is KeyChord:
        return {"type": "keychord", "keys": list(action.keys), "hold_ms": action.hold_ms}
    if type(action) is ReleaseAll:
        return {"type": "release_all"}
    raise ValidationError("Unknown action object")


def duration_ms(action: Action) -> int:
    if isinstance(action, Text):
        return len(action.value) * 20
    if isinstance(action, KeyChord):
        return action.hold_ms + 10
    return 0


def parse_batch(value: object) -> tuple[Action, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ACTIONS:
        raise ValidationError(f"A batch needs 1–{MAX_ACTIONS} actions")
    actions = tuple(parse_action(item) for item in value)
    if sum(duration_ms(action) for action in actions) > MAX_BATCH_MS:
        raise ValidationError(f"Batch exceeds {MAX_BATCH_MS} ms; split it and approve each batch separately")
    return actions


def validate_batch(actions: tuple[Action, ...]) -> tuple[Action, ...]:
    return parse_batch([action_dict(action) for action in actions])
