"""Immutable metadata contracts. No pixels, files, devices or network access."""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
import math
from typing import Protocol


def _text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} must be a nonempty string of at most 256 characters")


def _integer(name: str, value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _finite(name: str, value: float, minimum: float | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (minimum is not None and value < minimum):
        raise ValueError(f"{name} must be finite" + (f" and >= {minimum}" if minimum is not None else ""))


@dataclass(frozen=True)
class Dimensions:
    width: int
    height: int

    def __post_init__(self) -> None:
        # Pixel centers, including the far edge, define the coordinate space.
        _integer("width", self.width, 2)
        _integer("height", self.height, 2)
        if self.width > 1_000_000 or self.height > 1_000_000:
            raise ValueError("dimensions exceed the supported numerical range")

    def contains(self, point: Point) -> bool:
        return 0 <= point.x <= self.width - 1 and 0 <= point.y <= self.height - 1


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        _finite("x", self.x)
        _finite("y", self.y)


@dataclass(frozen=True)
class ClockSample:
    """A timestamp in one adapter's monotonic clock domain, never wall time."""
    clock_id: str
    monotonic_ns: int

    def __post_init__(self) -> None:
        _text("clock_id", self.clock_id)
        _integer("monotonic_ns", self.monotonic_ns)


@dataclass(frozen=True)
class CaptureBinding:
    source_id: str
    session_id: str
    target_id: str
    target_epoch: str

    def __post_init__(self) -> None:
        for name in ("source_id", "session_id", "target_id", "target_epoch"):
            _text(name, getattr(self, name))


@dataclass(frozen=True)
class FrameId:
    """Unique within an adapter: source + fresh session + increasing sequence."""
    source_id: str
    session_id: str
    sequence: int

    def __post_init__(self) -> None:
        _text("source_id", self.source_id)
        _text("session_id", self.session_id)
        _integer("sequence", self.sequence)


class ObservationState(str, Enum):
    READY = "ready"
    UNCERTAIN = "uncertain"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class FrameMetadata:
    frame_id: FrameId
    captured_at: ClockSample
    binding: CaptureBinding
    dimensions: Dimensions
    target_dimensions: Dimensions
    state: ObservationState = ObservationState.READY
    uncertainty_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, expected in (("frame_id", FrameId), ("captured_at", ClockSample),
                               ("binding", CaptureBinding), ("dimensions", Dimensions),
                               ("target_dimensions", Dimensions),
                               ("state", ObservationState)):
            if not isinstance(getattr(self, name), expected):
                raise ValueError(f"{name} must be {expected.__name__}")
        if (self.frame_id.source_id, self.frame_id.session_id) != (self.binding.source_id, self.binding.session_id):
            raise ValueError("frame ID and capture binding must identify the same source/session")
        if not isinstance(self.uncertainty_reasons, tuple):
            raise ValueError("uncertainty_reasons must be an immutable tuple")
        for reason in self.uncertainty_reasons:
            _text("uncertainty reason", reason)


@dataclass(frozen=True)
class ValidationPolicy:
    max_frame_age_ns: int = 500_000_000
    max_calibration_age_ns: int = 30_000_000_000
    max_declared_calibration_error_px: float = 2.0

    def __post_init__(self) -> None:
        _integer("max_frame_age_ns", self.max_frame_age_ns)
        _integer("max_calibration_age_ns", self.max_calibration_age_ns)
        _finite("max_declared_calibration_error_px", self.max_declared_calibration_error_px, 0)


class ObservationAdapter(Protocol):
    """Future adapters provide metadata and bounded, explicitly borrowed pixels.

    capture() never reads a saved frame as though it were new. DISCONNECTED and
    UNCERTAIN events also get a new sequence. No implementation is bundled.
    """
    def capture(self) -> FrameMetadata: ...
    def close(self) -> None: ...


class PixelProvider(Protocol):
    """Optional pixel access outside the guard. No persistence is authorized.

    The lease must refer to exactly frame_id, expose a read-only view, and keep
    the buffer stable until exit. Never silently return the latest different
    frame. The consumer must not retain/copy the view after exit. Python cannot
    enforce zeroization or stop a caller retaining/copying pixels.
    """
    def borrow_pixels(self, frame_id: FrameId) -> AbstractContextManager[memoryview]: ...
