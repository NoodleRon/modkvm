"""Fail-closed, observation-only admission and coordinate mapping."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

from .contract import (CaptureBinding, ClockSample, Dimensions, FrameId, FrameMetadata,
                       ObservationState, Point, ValidationPolicy, _finite, _text)
from .geometry import (GeometryError, Homography, UNIT_SCREEN, denormalize,
                       inside_quad, normalize, validate_quad)


class RejectionCode(str, Enum):
    SESSION_INVALIDATED = "session_invalidated"
    DISCONNECTED = "disconnected"
    UNCERTAIN = "uncertain"
    BINDING_CHANGED = "binding_changed"
    RESOLUTION_CHANGED = "resolution_changed"
    CLOCK_CHANGED = "clock_changed"
    CLOCK_REGRESSION = "clock_regression"
    FUTURE_TIMESTAMP = "future_timestamp"
    STALE_FRAME = "stale_frame"
    REPLAYED_FRAME = "replayed_frame"
    NO_CURRENT_FRAME = "no_current_frame"
    WRONG_FRAME = "wrong_frame"
    NO_CALIBRATION = "no_calibration"
    WRONG_CALIBRATION_ANCHOR = "wrong_calibration_anchor"
    OBSOLETE_MAPPING = "obsolete_mapping"
    INVALID_MAPPING = "invalid_mapping"
    STALE_CALIBRATION = "stale_calibration"
    CALIBRATION_UNCERTAIN = "calibration_uncertain"
    OUTSIDE_FRAME = "outside_frame"
    OUTSIDE_SCREEN = "outside_screen"
    INVALID_GEOMETRY = "invalid_geometry"


class ObservationRejected(ValueError):
    def __init__(self, code: RejectionCode, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


@dataclass(frozen=True)
class Calibration:
    calibration_id: str
    anchor: FrameMetadata
    target_dimensions: Dimensions
    screen_corners: tuple[Point, ...]
    declared_error_px: float
    _quad: tuple[Point, ...] = field(init=False, repr=False)
    _to_target: Homography = field(init=False, repr=False)
    _to_image: Homography = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _text("calibration_id", self.calibration_id)
        if not isinstance(self.anchor, FrameMetadata) or not isinstance(self.target_dimensions, Dimensions):
            raise ValueError("calibration requires a frame anchor and target dimensions")
        if self.anchor.state is not ObservationState.READY or self.anchor.uncertainty_reasons:
            raise ValueError("an uncertain or disconnected frame cannot anchor calibration")
        if self.target_dimensions != self.anchor.target_dimensions:
            raise ValueError("calibration target dimensions must match its anchor")
        _finite("declared_error_px", self.declared_error_px, 0)
        quad = validate_quad(self.screen_corners, self.anchor.dimensions)
        object.__setattr__(self, "_quad", quad)
        object.__setattr__(self, "_to_target", Homography.fit(quad, UNIT_SCREEN))
        object.__setattr__(self, "_to_image", Homography.fit(UNIT_SCREEN, quad))


@dataclass(frozen=True)
class MappedPoint:
    frame_id: FrameId
    calibration_id: str
    calibration_revision: int
    binding: CaptureBinding
    source_point: Point
    target_point: Point
    normalized_target: Point
    target_dimensions: Dimensions
    declared_calibration_error_px: float


class ObservationGuard:
    """One exact session/target/layout. Single-owner; not thread-safe.

    Rejected frames invalidate current metadata AND calibration. A new source,
    session, target epoch or resolution requires a newly constructed guard and
    calibration. This class stores metadata only and never executes actions.
    """
    def __init__(self, binding: CaptureBinding, source_dimensions: Dimensions,
                 target_dimensions: Dimensions, clock_id: str,
                 policy: ValidationPolicy = ValidationPolicy()) -> None:
        for value, expected in ((binding, CaptureBinding), (source_dimensions, Dimensions),
                                (target_dimensions, Dimensions), (policy, ValidationPolicy)):
            if not isinstance(value, expected):
                raise ValueError(f"expected {expected.__name__}")
        _text("clock_id", clock_id)
        self.binding = binding
        self.source_dimensions = source_dimensions
        self.target_dimensions = target_dimensions
        self.clock_id = clock_id
        self.policy = policy
        self._current: FrameMetadata | None = None
        self._calibration: Calibration | None = None
        self._highest_sequence = -1
        self._latest_capture_ns = -1
        self._latest_now_ns = -1
        self._closed = False
        self._calibration_revision = 0

    @property
    def current_frame(self) -> FrameMetadata | None:
        return self._current

    @property
    def calibration(self) -> Calibration | None:
        return self._calibration

    @property
    def calibration_revision(self) -> int:
        return self._calibration_revision

    def invalidate(self) -> None:
        """Disarm after temporary uncertainty; use close() on lifecycle failure."""
        self._current = None
        self._calibration = None

    def close(self) -> None:
        """Permanently invalidate this guard; start a new session to resume."""
        self._closed = True
        self.invalidate()

    def _handle_rejection(self, error: ObservationRejected) -> None:
        self.invalidate()
        if error.code in (RejectionCode.DISCONNECTED, RejectionCode.BINDING_CHANGED,
                          RejectionCode.RESOLUTION_CHANGED, RejectionCode.CLOCK_CHANGED,
                          RejectionCode.CLOCK_REGRESSION):
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise ObservationRejected(RejectionCode.SESSION_INVALIDATED)

    def _age(self, captured: ClockSample, now: ClockSample) -> int:
        if not isinstance(now, ClockSample):
            raise ValueError("now must be a ClockSample")
        if captured.clock_id != self.clock_id or now.clock_id != self.clock_id:
            raise ObservationRejected(RejectionCode.CLOCK_CHANGED)
        if now.monotonic_ns < self._latest_now_ns:
            raise ObservationRejected(RejectionCode.CLOCK_REGRESSION)
        self._latest_now_ns = now.monotonic_ns
        age = now.monotonic_ns - captured.monotonic_ns
        if age < 0:
            raise ObservationRejected(RejectionCode.FUTURE_TIMESTAMP)
        return age

    def _check_frame(self, frame: FrameMetadata, now: ClockSample) -> None:
        if frame.binding != self.binding:
            raise ObservationRejected(RejectionCode.BINDING_CHANGED)
        if (frame.dimensions != self.source_dimensions or
                frame.target_dimensions != self.target_dimensions):
            raise ObservationRejected(RejectionCode.RESOLUTION_CHANGED)
        if frame.state is ObservationState.DISCONNECTED:
            raise ObservationRejected(RejectionCode.DISCONNECTED)
        age = self._age(frame.captured_at, now)
        if frame.state is ObservationState.UNCERTAIN or frame.uncertainty_reasons:
            raise ObservationRejected(RejectionCode.UNCERTAIN)
        if age > self.policy.max_frame_age_ns:
            raise ObservationRejected(RejectionCode.STALE_FRAME)

    def accept_frame(self, frame: FrameMetadata, *, now: ClockSample) -> None:
        """Admit one strictly newer capture; consume rejected IDs, too."""
        self._current = None
        try:
            self._ensure_open()
            if not isinstance(frame, FrameMetadata):
                raise ValueError("frame must be FrameMetadata")
            same_stream = (frame.frame_id.source_id, frame.frame_id.session_id) == (self.binding.source_id, self.binding.session_id)
            replayed = same_stream and frame.frame_id.sequence <= self._highest_sequence
            if same_stream:
                # A rejected immutable observation ID cannot be reused with
                # modified status/timestamp/metadata on a later attempt.
                self._highest_sequence = max(self._highest_sequence, frame.frame_id.sequence)
            # Lifecycle signals take precedence over replay classification:
            # even a duplicate disconnect must permanently invalidate a guard.
            self._check_frame(frame, now)
            if replayed or frame.captured_at.monotonic_ns < self._latest_capture_ns:
                raise ObservationRejected(RejectionCode.REPLAYED_FRAME)
        except ObservationRejected as error:
            self._handle_rejection(error)
            raise
        except (ValueError, TypeError):
            self._calibration = None
            raise
        self._latest_capture_ns = frame.captured_at.monotonic_ns
        self._current = frame

    def _check_calibration(self, calibration: Calibration, now: ClockSample) -> None:
        if calibration.anchor.binding != self.binding:
            raise ObservationRejected(RejectionCode.BINDING_CHANGED)
        if (calibration.anchor.dimensions != self.source_dimensions or
                calibration.target_dimensions != self.target_dimensions):
            raise ObservationRejected(RejectionCode.RESOLUTION_CHANGED)
        if self._age(calibration.anchor.captured_at, now) > self.policy.max_calibration_age_ns:
            raise ObservationRejected(RejectionCode.STALE_CALIBRATION)
        if calibration.declared_error_px > self.policy.max_declared_calibration_error_px:
            raise ObservationRejected(RejectionCode.CALIBRATION_UNCERTAIN)

    def set_calibration(self, calibration: Calibration, *, now: ClockSample) -> None:
        """Attach only a calibration anchored to the currently admitted frame."""
        self._calibration = None
        try:
            self._ensure_open()
            if not isinstance(calibration, Calibration):
                raise ValueError("calibration must be Calibration")
            self._check_calibration(calibration, now)
            if self._current is None:
                raise ObservationRejected(RejectionCode.NO_CURRENT_FRAME)
            self._check_frame(self._current, now)
            if calibration.anchor != self._current:
                raise ObservationRejected(RejectionCode.WRONG_CALIBRATION_ANCHOR)
        except ObservationRejected as error:
            self._handle_rejection(error)
            raise
        self._calibration_revision += 1
        self._calibration = calibration

    def _ready(self, frame_id: FrameId, now: ClockSample) -> tuple[FrameMetadata, Calibration]:
        self._ensure_open()
        if self._current is None:
            raise ObservationRejected(RejectionCode.NO_CURRENT_FRAME)
        if frame_id != self._current.frame_id:
            raise ObservationRejected(RejectionCode.WRONG_FRAME)
        try:
            self._check_frame(self._current, now)
            if self._calibration is None:
                raise ObservationRejected(RejectionCode.NO_CALIBRATION)
            self._check_calibration(self._calibration, now)
        except ObservationRejected as error:
            self._handle_rejection(error)
            raise
        return self._current, self._calibration

    def map_image_point(self, frame_id: FrameId, point: Point, *, now: ClockSample) -> MappedPoint:
        frame, calibration = self._ready(frame_id, now)
        if not isinstance(point, Point):
            raise ValueError("point must be Point")
        if not self.source_dimensions.contains(point):
            raise ObservationRejected(RejectionCode.OUTSIDE_FRAME)
        normalized = normalize(point, self.source_dimensions)
        if not inside_quad(normalized, calibration._quad):
            raise ObservationRejected(RejectionCode.OUTSIDE_SCREEN)
        try:
            target = calibration._to_target.project(normalized)
        except GeometryError as exc:
            raise ObservationRejected(RejectionCode.INVALID_GEOMETRY) from exc
        # This tolerance only removes floating-point roundoff after source
        # bounds and numerically tolerant polygon admission.
        if not (-1e-9 <= target.x <= 1 + 1e-9 and -1e-9 <= target.y <= 1 + 1e-9):
            raise ObservationRejected(RejectionCode.INVALID_GEOMETRY)
        target = Point(min(1.0, max(0.0, target.x)), min(1.0, max(0.0, target.y)))
        return MappedPoint(frame.frame_id, calibration.calibration_id, self._calibration_revision, self.binding,
                           point, denormalize(target, self.target_dimensions), target,
                           self.target_dimensions, calibration.declared_error_px)

    def verify_mapping(self, mapping: MappedPoint, *, now: ClockSample) -> None:
        """Recheck an earlier result against the exact current observation.

        This check provides neither action authorization nor an atomic guarantee
        that an external display will remain unchanged after return.
        """
        if not isinstance(mapping, MappedPoint):
            raise ValueError("mapping must be MappedPoint")
        _, calibration = self._ready(mapping.frame_id, now)
        if (mapping.calibration_id != calibration.calibration_id or
                mapping.calibration_revision != self._calibration_revision):
            raise ObservationRejected(RejectionCode.OBSOLETE_MAPPING)
        if mapping != self.map_image_point(mapping.frame_id, mapping.source_point, now=now):
            raise ObservationRejected(RejectionCode.INVALID_MAPPING)

    def map_target_point(self, frame_id: FrameId, point: Point, *, now: ClockSample) -> Point:
        """Reverse mapping for overlays/verification, with the same live checks."""
        _, calibration = self._ready(frame_id, now)
        if not isinstance(point, Point):
            raise ValueError("point must be Point")
        if not self.target_dimensions.contains(point):
            raise ObservationRejected(RejectionCode.OUTSIDE_SCREEN)
        try:
            image_point = calibration._to_image.project(normalize(point, self.target_dimensions))
            if not (-1e-9 <= image_point.x <= 1 + 1e-9 and -1e-9 <= image_point.y <= 1 + 1e-9):
                raise GeometryError("inverse projection is outside the source")
            image_point = Point(min(1.0, max(0.0, image_point.x)), min(1.0, max(0.0, image_point.y)))
            return denormalize(image_point, self.source_dimensions)
        except GeometryError as exc:
            raise ObservationRejected(RejectionCode.INVALID_GEOMETRY) from exc
