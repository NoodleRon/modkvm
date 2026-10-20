import json
import unittest
from digit.protocol import Frame, FrameReader, MAX_FRAME_BYTES, ProtocolError, decode, encode

SESSION = "1" * 32


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.frame = Frame(SESSION, 1, "arm", {})
        self.wire = encode(self.frame)

    def test_roundtrip(self):
        self.assertEqual(decode(self.wire), self.frame)

    def test_corruption_rejected(self):
        with self.assertRaises(ProtocolError):
            decode(self.wire.replace(b'"seq":1', b'"seq":2'))

    def test_partial_and_multiple_frames(self):
        reader = FrameReader()
        self.assertEqual(reader.feed(self.wire[:10]), [])
        self.assertEqual(reader.feed(self.wire[10:] + self.wire), [self.frame, self.frame])
        self.assertFalse(reader.buffer)

    def test_oversize_poison_and_reset(self):
        reader = FrameReader()
        with self.assertRaises(ProtocolError):
            reader.feed(b"x" * MAX_FRAME_BYTES)
        self.assertFalse(reader.buffer)
        with self.assertRaises(ProtocolError):
            reader.feed(self.wire)
        reader.reset()
        self.assertEqual(reader.feed(self.wire), [self.frame])

    def test_duplicate_key_rejected(self):
        with self.assertRaises(ProtocolError):
            decode(self.wire.replace(b'"seq":1', b'"seq":1,"seq":1'))

    def test_bad_types_versions_newlines_and_depth(self):
        for wire in [self.wire[:-1], self.wire + b"\n", b"null\n", b"\xff\n",
                     b"[" * 1200 + b"]" * 1200 + b"\n"]:
            with self.subTest(wire=wire[:50]), self.assertRaises(ProtocolError):
                decode(wire)
        for seq in [True, 0, -1, 1.0, 2**31]:
            with self.assertRaises(ProtocolError):
                encode(Frame(SESSION, seq, "arm", {}))
        for session in ["a", "A" * 32, None]:
            with self.assertRaises(ProtocolError):
                encode(Frame(session, 1, "arm", {}))

    def test_unknown_fields_and_non_finite_values(self):
        payload = json.loads(self.wire)
        payload["extra"] = 1
        with self.assertRaises(ProtocolError):
            decode(json.dumps(payload).encode() + b"\n")
        with self.assertRaises(ProtocolError):
            encode(Frame(SESSION, 1, "arm", {"bad": float("nan")}))

    def test_encoding_size_limit(self):
        with self.assertRaises(ProtocolError):
            encode(Frame(SESSION, 1, "execute", {"text": "x" * MAX_FRAME_BYTES}))
