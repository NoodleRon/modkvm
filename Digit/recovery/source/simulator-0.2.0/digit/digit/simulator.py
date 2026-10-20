"""Deterministic fake target. No USB, network, keyboard, screen, or camera I/O.

The watchdog is advanced by tick() in this simulation. It is NOT an
independent hardware watchdog. A production backend must prove its own
release/timeout behavior before it is enabled.
"""
from dataclasses import dataclass
import time
from .actions import Text, KeyChord, ReleaseAll, parse_batch, ValidationError
from .protocol import Frame, ProtocolError, canonical, decode, encode

ARM_LEASE_SECONDS = 60.0
HOLD_WATCHDOG_SECONDS = 0.2


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Time cannot run backward")
        self.value += seconds


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class Event:
    at: float
    kind: str
    keys: tuple[str, ...]


class Simulator:
    def __init__(self, clock=None) -> None:
        self.clock = clock or ManualClock()
        self.armed = False
        self.local_arm_until = 0.0
        self.arm_until = 0.0
        self.session = None
        self.last_sequence = 0
        self.last_request = None
        self.last_ack = None
        self.pressed: tuple[str, ...] = ()
        self.hold_until = None
        self.events: list[Event] = []
        self.executed_batches = 0
        self.stall_after_press = 0.0  # Failure injection, seconds.

    def local_arm(self) -> None:
        """Simulate the operator's local enable switch, never a model action."""
        self.local_arm_until = self.clock.now() + 10.0

    def _release(self, reason: str = "release") -> None:
        self.pressed = ()
        self.hold_until = None
        self.events.append(Event(self.clock.now(), reason, ()))

    def disarm(self, reason: str = "stop") -> None:
        self._release(reason)
        self.armed = False
        self.local_arm_until = 0.0

    def disconnect(self) -> None:
        self.disarm("disconnect")

    def tick(self) -> None:
        now = self.clock.now()
        if self.hold_until is not None and now >= self.hold_until:
            self.disarm("hold_watchdog")
        elif self.armed and now >= self.arm_until:
            self.disarm("arm_expired")

    def _press(self, keys: tuple[str, ...], seconds: float) -> None:
        self.tick()
        if not self.armed:
            raise ValidationError("Device disarmed before input")
        self.pressed = keys
        self.hold_until = self.clock.now() + HOLD_WATCHDOG_SECONDS
        self.events.append(Event(self.clock.now(), "press", keys))
        self.clock.sleep(seconds + self.stall_after_press)
        self.tick()
        if not self.armed:
            raise ValidationError("Device watchdog expired during input")
        self._release()
        self.clock.sleep(0.01)
        self.tick()
        if not self.armed:
            raise ValidationError("Device arm lease expired before input completion")

    def _ack(self, frame: Frame, status: str, detail: str, count: int = 0) -> bytes:
        return encode(Frame(frame.session, frame.seq, "ack", {
            "status": status, "detail": detail,
            "state": "armed" if self.armed else "disarmed", "action_count": count,
        }))

    def handle(self, wire: bytes) -> bytes:
        self.tick()
        try:
            frame = decode(wire)
        except ProtocolError:
            self.disarm("protocol_fault")
            raise
        try:
            # Release is deliberately available even without an armed session
            # and across sequence gaps. Repeating release never creates input.
            if frame.kind == "release_all":
                if frame.body:
                    raise ValidationError("release_all body must be empty")
                self.disarm("stop")
                return self._ack(frame, "ok", "Released and disarmed")
            if frame.kind == "arm":
                if frame.body or frame.seq != 1:
                    raise ValidationError("arm requires an empty body and sequence 1")
                if self.armed or self.clock.now() >= self.local_arm_until:
                    raise ValidationError("A fresh local operator enable is required")
                if frame.session == self.session:
                    raise ValidationError("A new arm requires a fresh session")
                self._release("arm_release")
                self.armed = True
                self.local_arm_until = 0.0
                self.arm_until = self.clock.now() + ARM_LEASE_SECONDS
                self.session = frame.session
                self.last_sequence = 1
                self.last_request = canonical(frame.body)
                self.last_ack = self._ack(frame, "ok", "Simulator armed")
                return self.last_ack
            if frame.kind != "execute":
                raise ValidationError("Device accepts arm, execute, and release_all only")
            if not self.armed or frame.session != self.session:
                raise ValidationError("Device is not armed for this session")
            fingerprint = canonical({"type": frame.kind, "body": frame.body})
            if frame.seq == self.last_sequence:
                if fingerprint != self.last_request:
                    raise ValidationError("Sequence reuse with different content")
                return self.last_ack
            if frame.seq != self.last_sequence + 1:
                raise ValidationError("Out-of-order sequence; input was not executed")
            if set(frame.body) != {"actions"}:
                raise ValidationError("execute body requires actions only")
            actions = parse_batch(frame.body["actions"])
            # Validate the whole batch before emitting its first event.
            for action in actions:
                if isinstance(action, Text):
                    for char in action.value:
                        self._press((char,), 0.01)
                elif isinstance(action, KeyChord):
                    self._press(action.keys, action.hold_ms / 1000)
                elif isinstance(action, ReleaseAll):
                    self._release()
            self._release("batch_complete")
            self.executed_batches += 1
            self.last_sequence = frame.seq
            self.last_request = fingerprint
            self.last_ack = self._ack(frame, "ok", "Simulated input completed", len(actions))
            return self.last_ack
        except (ValidationError, ProtocolError) as exc:
            self.disarm("rejected")
            return self._ack(frame, "rejected", str(exc))
        except BaseException:
            self.disarm("backend_fault")
            raise


class LoopbackTransport:
    """In-memory adapter with deterministic failure injection."""
    def __init__(self, device: Simulator) -> None:
        self.device = device
        self.closed = False
        self.drop_next_ack = False
        self.corrupt_next_ack = False
        self.requests: list[bytes] = []

    def exchange(self, request: bytes, timeout: float) -> bytes:
        if self.closed:
            raise ConnectionError("Simulator transport is closed")
        if timeout <= 0:
            raise TimeoutError("No exchange time budget")
        self.requests.append(request)
        reply = self.device.handle(request)
        if self.drop_next_ack:
            self.drop_next_ack = False
            raise TimeoutError("Simulated acknowledgment loss after device processing")
        if self.corrupt_next_ack:
            self.corrupt_next_ack = False
            return reply.replace(b'"crc32":"', b'"crc32":"bad', 1)
        return reply

    def close(self) -> None:
        self.closed = True
        self.device.disconnect()
