"""A simulator transport contract, not an implemented KVM API or firmware.

CRC32 detects corruption, not malicious changes. Production network adapters
must use authenticated, encrypted upstream APIs, not expose this wire format.
"""
import json
import re
import zlib
from dataclasses import dataclass

MAX_FRAME_BYTES = 4096
MAX_SEQUENCE = 2**31 - 1
SESSION_RE = re.compile(r"[0-9a-f]{32}\Z")
MESSAGE_TYPES = {"arm", "execute", "release_all", "ack"}


class ProtocolError(ValueError):
    pass


def canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False).encode("ascii")
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ProtocolError("Value is not bounded canonical JSON") from exc


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class Frame:
    session: str
    seq: int
    kind: str
    body: dict


def _validate(payload: dict) -> None:
    if set(payload) != {"v", "session", "seq", "type", "body"}:
        raise ProtocolError("Missing or unknown frame fields")
    if type(payload["v"]) is not int or payload["v"] != 1:
        raise ProtocolError("Unsupported protocol version")
    if not isinstance(payload["session"], str) or SESSION_RE.fullmatch(payload["session"]) is None:
        raise ProtocolError("Session must be 32 lowercase hexadecimal characters")
    if type(payload["seq"]) is not int or not 1 <= payload["seq"] <= MAX_SEQUENCE:
        raise ProtocolError("Invalid sequence number")
    if not isinstance(payload["type"], str) or payload["type"] not in MESSAGE_TYPES:
        raise ProtocolError("Unknown message type")
    if not isinstance(payload["body"], dict):
        raise ProtocolError("Body must be an object")


def encode(frame: Frame) -> bytes:
    payload = {"v": 1, "session": frame.session, "seq": frame.seq,
               "type": frame.kind, "body": frame.body}
    _validate(payload)
    checksum = f"{zlib.crc32(canonical(payload)) & 0xFFFFFFFF:08x}"
    wire = canonical({**payload, "crc32": checksum}) + b"\n"
    if len(wire) > MAX_FRAME_BYTES:
        raise ProtocolError("Frame exceeds byte limit")
    return wire


def decode(wire: bytes) -> Frame:
    if not isinstance(wire, bytes) or not 1 <= len(wire) <= MAX_FRAME_BYTES:
        raise ProtocolError("Invalid frame size")
    if not wire.endswith(b"\n") or b"\n" in wire[:-1]:
        raise ProtocolError("Frame must have exactly one terminal newline")
    try:
        payload = json.loads(wire.decode("ascii"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("Malformed frame JSON") from exc
    if not isinstance(payload, dict) or "crc32" not in payload:
        raise ProtocolError("Missing checksum")
    checksum = payload.pop("crc32")
    _validate(payload)
    if not isinstance(checksum, str) or checksum != f"{zlib.crc32(canonical(payload)) & 0xFFFFFFFF:08x}":
        raise ProtocolError("Checksum mismatch")
    return Frame(payload["session"], payload["seq"], payload["type"], payload["body"])


class FrameReader:
    """Bounded partial-frame buffering. A violation poisons the reader."""
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.failed = False

    def feed(self, chunk: bytes) -> list[Frame]:
        if self.failed:
            raise ProtocolError("Reader requires reset after a framing error")
        if not isinstance(chunk, bytes):
            self.failed = True
            raise ProtocolError("Transport chunks must be bytes")
        frames = []
        try:
            for byte in chunk:
                self.buffer.append(byte)
                if len(self.buffer) > MAX_FRAME_BYTES:
                    raise ProtocolError("Frame exceeds byte limit")
                if byte == 10:
                    frames.append(decode(bytes(self.buffer)))
                    self.buffer.clear()
                elif len(self.buffer) == MAX_FRAME_BYTES:
                    raise ProtocolError("No room for terminating newline")
        except ProtocolError:
            self.buffer.clear()
            self.failed = True
            raise
        return frames

    def reset(self) -> None:
        self.buffer.clear()
        self.failed = False
