import unittest
from digit.actions import (KeyChord, ReleaseAll, Text, ValidationError, action_dict,
                           parse_batch, validate_batch)


class ActionTests(unittest.TestCase):
    def test_valid_roundtrip(self):
        actions = (Text("Hello, Digit!"), KeyChord(("CTRL", "L")), ReleaseAll())
        self.assertEqual(parse_batch([action_dict(a) for a in actions]), actions)

    def test_all_printable_ascii_supported_in_short_batches(self):
        for code in range(32, 127):
            self.assertEqual(parse_batch([{"type": "text", "value": chr(code)}]), (Text(chr(code)),))

    def test_reject_unknown_actions_and_fields(self):
        for action in [{"type": "shell", "command": "echo hello"}, {"type": "mouse", "x": 1},
                       {"type": "text", "value": "x", "surprise": True}, {"type": "release_all", "x": 1}]:
            with self.subTest(action=action), self.assertRaises(ValidationError):
                parse_batch([action])

    def test_control_unicode_and_empty_text_rejected(self):
        for value in ["", "a\n", "\t", "\x00", "é", "abc\u202e", "x" * 129, True]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_batch([{"type": "text", "value": value}])

    def test_keys_and_durations_are_strict(self):
        for keys, hold in [(["ctrl", "A"], 30), (["A", "A"], 30), (["A"], True),
                           (["A"], 0), (["A"], 101), (["A"], 5.0), (["NOPE"], 30),
                           (["A", "B", "C", "D", "E", "F", "G"], 30), ([[]], 30)]:
            with self.subTest(keys=keys, hold=hold), self.assertRaises(ValidationError):
                parse_batch([{"type": "keychord", "keys": keys, "hold_ms": hold}])

    def test_short_batch_and_count_limits(self):
        for actions in [[], [{"type": "release_all"}] * 9, [{"type": "text", "value": "x" * 51}]]:
            with self.assertRaises(ValidationError):
                parse_batch(actions)
        self.assertEqual(len(parse_batch([{"type": "text", "value": "x" * 50}])), 1)

    def test_constructed_objects_are_revalidated(self):
        with self.assertRaises(ValidationError):
            validate_batch((Text("\n"),))
        with self.assertRaises(ValidationError):
            validate_batch((KeyChord(("CTRL", "A"), True),))
