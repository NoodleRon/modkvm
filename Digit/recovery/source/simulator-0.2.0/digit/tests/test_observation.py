from dataclasses import FrozenInstanceError, replace
import math
import unittest

from digit_observation import (
    Calibration, CaptureBinding, ClockSample, Dimensions, FrameId, FrameMetadata,
    GeometryError, Homography, ObservationGuard, ObservationRejected,
    ObservationState, Point, RejectionCode, ValidationPolicy,
)

BINDING = CaptureBinding("camera-1", "session-1", "target-windows", "layout-1")
SOURCE = Dimensions(1001, 1001)
TARGET = Dimensions(1920, 1080)
QUAD = (Point(100, 100), Point(900, 100), Point(900, 900), Point(100, 900))


def now(value=1_000_000_000, clock="test-clock"):
    return ClockSample(clock, value)


def frame(sequence=0, timestamp=1_000_000_000, **changes):
    binding = changes.pop("binding", BINDING)
    return FrameMetadata(FrameId(binding.source_id, binding.session_id, sequence),
                         now(timestamp), binding, changes.pop("dimensions", SOURCE),
                         changes.pop("target_dimensions", TARGET), **changes)


def ready(quad=QUAD, policy=ValidationPolicy(), error=0.5):
    guard = ObservationGuard(BINDING, SOURCE, TARGET, "test-clock", policy)
    observation = frame()
    guard.accept_frame(observation, now=now())
    calibration = Calibration("cal-1", observation, TARGET, quad, error)
    guard.set_calibration(calibration, now=now())
    return guard, observation, calibration


class ContractTests(unittest.TestCase):
    def test_metadata_is_immutable(self):
        sample = frame()
        with self.assertRaises(FrozenInstanceError):
            sample.frame_id = FrameId("camera-1", "session-1", 2)
        with self.assertRaises(FrozenInstanceError):
            sample.frame_id.sequence = 10
        with self.assertRaises(ValueError):
            replace(sample, uncertainty_reasons=["occluded"])

    def test_bad_dimensions(self):
        for value in (True, False, 0, 1, -1, 2.5, math.nan, math.inf, 1_000_001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Dimensions(value, 10)

    def test_bad_points(self):
        for value in (math.nan, math.inf, -math.inf, True, "1", 10 ** 1000):
            with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                Point(value, 0)

    def test_bad_clock_and_identifiers(self):
        for value in (-1, True, 1.2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ClockSample("clock", value)
        with self.assertRaises(ValueError):
            CaptureBinding(" ", "s", "t", "e")
        with self.assertRaises(ValueError):
            FrameId("s", "session", True)
        with self.assertRaises(ValueError):
            replace(frame(), frame_id=FrameId("other-source", "session-1", 0))
        with self.assertRaises(ValueError):
            replace(frame(), state="ready")

    def test_policy_validation(self):
        for kwargs in ({"max_frame_age_ns": True}, {"max_frame_age_ns": -1},
                       {"max_calibration_age_ns": -1}, {"max_declared_calibration_error_px": math.nan}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ValidationPolicy(**kwargs)

    def test_pixels_are_not_part_of_the_contract(self):
        self.assertNotIn("pixels", frame().__dataclass_fields__)
        self.assertNotIn("image", frame().__dataclass_fields__)


class GeometryTests(unittest.TestCase):
    def assertPointAlmostEqual(self, actual, expected, delta=1e-7):
        self.assertAlmostEqual(actual.x, expected.x, delta=delta)
        self.assertAlmostEqual(actual.y, expected.y, delta=delta)

    def test_rectangle_corners_and_center(self):
        guard, sample, _ = ready()
        expected = (Point(0, 0), Point(1919, 0), Point(1919, 1079), Point(0, 1079))
        for source, target in zip(QUAD, expected):
            self.assertPointAlmostEqual(guard.map_image_point(sample.frame_id, source, now=now()).target_point, target)
        result = guard.map_image_point(sample.frame_id, Point(500, 500), now=now())
        self.assertPointAlmostEqual(result.target_point, Point(959.5, 539.5))
        self.assertEqual(result.frame_id, sample.frame_id)
        self.assertEqual(result.calibration_id, "cal-1")
        self.assertEqual(result.declared_calibration_error_px, 0.5)

    def test_perspective_independent_known_transform_grid(self):
        # Generate camera coordinates independently using a chosen projective
        # transform from the unit display to the camera plane.
        def expected_camera(u, v):
            return Point(1000 * (0.65 * u + 0.10 * v + 0.10) / (0.20 * u + 0.15 * v + 1),
                         1000 * (0.05 * u + 0.70 * v + 0.12) / (0.20 * u + 0.15 * v + 1))
        quad = tuple(expected_camera(u, v) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)))
        guard, sample, _ = ready(quad)
        for u in (0.1, 0.2, 0.5, 0.8, 0.9):
            for v in (0.1, 0.2, 0.5, 0.8, 0.9):
                source = expected_camera(u, v)
                mapped = guard.map_image_point(sample.frame_id, source, now=now())
                self.assertPointAlmostEqual(mapped.normalized_target, Point(u, v))
                reverse = guard.map_target_point(sample.frame_id, Point(1919 * u, 1079 * v), now=now())
                self.assertPointAlmostEqual(reverse, source)
        for source, target in zip(quad, (Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1))):
            self.assertPointAlmostEqual(guard.map_image_point(sample.frame_id, source, now=now()).normalized_target, target)

    def test_perspective_boundary_round_trips(self):
        size = Dimensions(1920, 1080)
        target = Dimensions(3840, 2160)
        quad = (Point(100, 100), Point(1800, 120), Point(1700, 950), Point(150, 1000))
        sample = frame(dimensions=size, target_dimensions=target)
        guard = ObservationGuard(BINDING, size, target, "test-clock")
        guard.accept_frame(sample, now=now())
        guard.set_calibration(Calibration("perspective", sample, target, quad, 0), now=now())
        for point in (Point(0, 0), Point(3839, 0), Point(3839, 2159), Point(0, 2159),
                      Point(1919.5, 0), Point(3839, 1079.5), Point(1919.5, 2159), Point(0, 1079.5)):
            image_point = guard.map_target_point(sample.frame_id, point, now=now())
            actual = guard.map_image_point(sample.frame_id, image_point, now=now()).target_point
            self.assertPointAlmostEqual(actual, point)

    def test_full_frame_kvm_mapping(self):
        quad = (Point(0, 0), Point(1000, 0), Point(1000, 1000), Point(0, 1000))
        guard, sample, _ = ready(quad)
        self.assertPointAlmostEqual(guard.map_image_point(sample.frame_id, Point(1000, 1000), now=now()).target_point,
                                    Point(1919, 1079))

    def test_outside_frame_rejected_no_clamp(self):
        guard, sample, _ = ready()
        for point in (Point(-0.0001, 500), Point(1000.0001, 500), Point(500, 1000.1)):
            with self.subTest(point=point), self.assertRaises(ObservationRejected) as error:
                guard.map_image_point(sample.frame_id, point, now=now())
            self.assertEqual(error.exception.code, RejectionCode.OUTSIDE_FRAME)

    def test_outside_screen_inside_frame_rejected(self):
        guard, sample, _ = ready()
        for point in (Point(50, 500), Point(99.999999, 500), Point(900.000001, 500), Point(500, 901)):
            with self.subTest(point=point), self.assertRaises(ObservationRejected) as error:
                guard.map_image_point(sample.frame_id, point, now=now())
            self.assertEqual(error.exception.code, RejectionCode.OUTSIDE_SCREEN)

    def test_inverse_target_bounds(self):
        guard, sample, _ = ready()
        with self.assertRaises(ObservationRejected) as error:
            guard.map_target_point(sample.frame_id, Point(1920, 1080), now=now())
        self.assertEqual(error.exception.code, RejectionCode.OUTSIDE_SCREEN)

    def test_invalid_quadrilaterals(self):
        invalid = [
            QUAD[:3], list(QUAD),
            (QUAD[0], QUAD[0], QUAD[2], QUAD[3]),
            (Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)),
            (QUAD[0], QUAD[2], QUAD[1], QUAD[3]),
            (Point(100, 100), Point(900, 100), Point(200, 200), Point(100, 900)),
            tuple(reversed(QUAD)),
            (Point(-1, 0), Point(900, 0), Point(900, 900), Point(0, 900)),
            (Point(1, 1), Point(1.00001, 1), Point(1.00001, 1.00001), Point(1, 1.00001)),
        ]
        for quad in invalid:
            with self.subTest(quad=quad), self.assertRaises(GeometryError):
                Calibration("bad", frame(), TARGET, quad, 0)

    def test_projective_horizon_rejected(self):
        transform = Homography((1, 0, 0, 0, 1, 0, -1, 0, 1))
        with self.assertRaises(GeometryError):
            transform.project(Point(1, 0))

    def test_singular_fit(self):
        points = (Point(0, 0),) * 4
        with self.assertRaises(GeometryError):
            Homography.fit(points, points)


class GuardTests(unittest.TestCase):
    def reject_frame(self, guard, sample, expected, clock=None):
        with self.assertRaises(ObservationRejected) as error:
            guard.accept_frame(sample, now=clock or now())
        self.assertEqual(error.exception.code, expected)
        self.assertIsNone(guard.current_frame)
        self.assertIsNone(guard.calibration)

    def test_frame_freshness_exact_boundary(self):
        guard, sample, _ = ready()
        guard.map_image_point(sample.frame_id, Point(500, 500), now=now(1_500_000_000))
        with self.assertRaises(ObservationRejected) as error:
            guard.map_image_point(sample.frame_id, Point(500, 500), now=now(1_500_000_001))
        self.assertEqual(error.exception.code, RejectionCode.STALE_FRAME)
        self.assertIsNone(guard.calibration)

    def test_stale_ingestion(self):
        guard, _, _ = ready()
        self.reject_frame(guard, frame(1), RejectionCode.STALE_FRAME, now(1_500_000_001))

    def test_future_timestamp(self):
        guard, _, _ = ready()
        self.reject_frame(guard, frame(1, 1_000_000_001), RejectionCode.FUTURE_TIMESTAMP)

    def test_clock_domain_mismatch(self):
        guard, _, _ = ready()
        self.reject_frame(guard, frame(1), RejectionCode.CLOCK_CHANGED, now(clock="other-clock"))

    def test_capture_clock_domain_mismatch(self):
        guard, _, _ = ready()
        self.reject_frame(guard, replace(frame(1), captured_at=now(clock="other-clock")), RejectionCode.CLOCK_CHANGED)

    def test_clock_regression(self):
        guard, sample, _ = ready()
        guard.map_image_point(sample.frame_id, Point(500, 500), now=now(1_000_000_100))
        with self.assertRaises(ObservationRejected) as error:
            guard.map_image_point(sample.frame_id, Point(500, 500), now=now(1_000_000_099))
        self.assertEqual(error.exception.code, RejectionCode.CLOCK_REGRESSION)

    def test_capture_timestamp_regression(self):
        guard, _, _ = ready()
        self.reject_frame(guard, frame(1, 999_999_999), RejectionCode.REPLAYED_FRAME)

    def test_replayed_sequence_and_changed_metadata(self):
        for sample in (frame(), frame(timestamp=1_000_000_001)):
            guard, _, _ = ready()
            self.reject_frame(guard, sample, RejectionCode.REPLAYED_FRAME, now(1_000_000_001))

    def test_target_and_epoch_changes(self):
        for binding in (replace(BINDING, target_id="other-target"), replace(BINDING, target_epoch="layout-2")):
            guard, _, _ = ready()
            self.reject_frame(guard, frame(1, binding=binding), RejectionCode.BINDING_CHANGED)

    def test_capture_session_and_source_changes(self):
        for binding in (replace(BINDING, session_id="session-2"), replace(BINDING, source_id="camera-2")):
            guard, _, _ = ready()
            self.reject_frame(guard, frame(1, binding=binding), RejectionCode.BINDING_CHANGED)

    def test_source_and_target_resolution_changes(self):
        for changes in ({"dimensions": Dimensions(640, 480)}, {"target_dimensions": Dimensions(2560, 1440)}):
            guard, _, _ = ready()
            self.reject_frame(guard, frame(1, **changes), RejectionCode.RESOLUTION_CHANGED)

    def test_disconnect_uncertain_and_explicit_reasons(self):
        for changes, expected in (({"state": ObservationState.DISCONNECTED}, RejectionCode.DISCONNECTED),
                                  ({"state": ObservationState.UNCERTAIN}, RejectionCode.UNCERTAIN),
                                  ({"uncertainty_reasons": ("occluded",)}, RejectionCode.UNCERTAIN)):
            guard, _, _ = ready()
            self.reject_frame(guard, frame(1, **changes), expected)

    def test_disconnection_permanently_invalidates_session(self):
        guard, sample, _ = ready()
        self.reject_frame(guard, frame(1, state=ObservationState.DISCONNECTED), RejectionCode.DISCONNECTED)
        self.reject_frame(guard, frame(2), RejectionCode.SESSION_INVALIDATED)
        with self.assertRaises(ObservationRejected) as error:
            guard.map_image_point(sample.frame_id, Point(500, 500), now=now())
        self.assertEqual(error.exception.code, RejectionCode.SESSION_INVALIDATED)

    def test_binding_change_cannot_revert_to_old_target(self):
        guard, _, _ = ready()
        self.reject_frame(guard, frame(1, binding=replace(BINDING, target_epoch="changed")), RejectionCode.BINDING_CHANGED)
        self.reject_frame(guard, frame(2), RejectionCode.SESSION_INVALIDATED)

    def test_lifecycle_signal_closes_even_with_replayed_sequence(self):
        events = ((frame(0, state=ObservationState.DISCONNECTED), RejectionCode.DISCONNECTED),
                  (frame(0, binding=replace(BINDING, target_epoch="new")), RejectionCode.BINDING_CHANGED),
                  (frame(0, dimensions=Dimensions(640, 480)), RejectionCode.RESOLUTION_CHANGED),
                  (replace(frame(0, state=ObservationState.UNCERTAIN), captured_at=now(clock="changed")),
                   RejectionCode.CLOCK_CHANGED))
        for sample, code in events:
            with self.subTest(code=code):
                guard, _, _ = ready()
                self.reject_frame(guard, sample, code)
                self.reject_frame(guard, frame(1), RejectionCode.SESSION_INVALIDATED)

    def test_uncertain_recovery_requires_new_frame_and_calibration(self):
        guard, _, _ = ready()
        self.reject_frame(guard, frame(1, state=ObservationState.UNCERTAIN), RejectionCode.UNCERTAIN)
        self.reject_frame(guard, frame(1), RejectionCode.REPLAYED_FRAME)
        new_frame = frame(2)
        guard.accept_frame(new_frame, now=now())
        self.assertIsNone(guard.calibration)
        guard.set_calibration(Calibration("new-cal", new_frame, TARGET, QUAD, 0), now=now())
        self.assertEqual(guard.map_image_point(new_frame.frame_id, Point(500, 500), now=now()).calibration_id, "new-cal")

    def test_wrong_frame_request(self):
        guard, sample, _ = ready()
        guard.accept_frame(frame(1), now=now())
        with self.assertRaises(ObservationRejected) as error:
            guard.map_image_point(sample.frame_id, Point(500, 500), now=now())
        self.assertEqual(error.exception.code, RejectionCode.WRONG_FRAME)

    def test_calibration_expiry_checked_against_new_frame(self):
        policy = ValidationPolicy(max_frame_age_ns=500, max_calibration_age_ns=1000)
        guard, _, _ = ready(policy=policy)
        current = frame(1, 1_000_001_000)
        guard.accept_frame(current, now=now(1_000_001_000))
        guard.map_image_point(current.frame_id, Point(500, 500), now=now(1_000_001_000))
        with self.assertRaises(ObservationRejected) as error:
            guard.map_image_point(current.frame_id, Point(500, 500), now=now(1_000_001_001))
        self.assertEqual(error.exception.code, RejectionCode.STALE_CALIBRATION)

    def test_recalibration_invalidates_prior_mapping_even_with_reused_id(self):
        guard, sample, calibration = ready()
        mapping = guard.map_image_point(sample.frame_id, Point(500, 500), now=now())
        guard.verify_mapping(mapping, now=now())
        guard.set_calibration(calibration, now=now())
        self.assertEqual(guard.calibration_revision, 2)
        with self.assertRaises(ObservationRejected) as error:
            guard.verify_mapping(mapping, now=now())
        self.assertEqual(error.exception.code, RejectionCode.OBSOLETE_MAPPING)

    def test_modified_mapping_fails_verification(self):
        guard, sample, _ = ready()
        mapping = guard.map_image_point(sample.frame_id, Point(500, 500), now=now())
        mapping = replace(mapping, target_point=Point(0, 0))
        with self.assertRaises(ObservationRejected) as error:
            guard.verify_mapping(mapping, now=now())
        self.assertEqual(error.exception.code, RejectionCode.INVALID_MAPPING)

    def test_mapping_verification_rechecks_freshness(self):
        guard, sample, _ = ready()
        mapping = guard.map_image_point(sample.frame_id, Point(500, 500), now=now())
        with self.assertRaises(ObservationRejected) as error:
            guard.verify_mapping(mapping, now=now(1_500_000_001))
        self.assertEqual(error.exception.code, RejectionCode.STALE_FRAME)

    def test_uncertain_calibration_rejected(self):
        guard, _, calibration = ready()
        with self.assertRaises(ObservationRejected) as error:
            guard.set_calibration(replace(calibration, declared_error_px=3), now=now())
        self.assertEqual(error.exception.code, RejectionCode.CALIBRATION_UNCERTAIN)
        self.assertIsNone(guard.calibration)

    def test_calibration_target_resolution_consistency(self):
        with self.assertRaises(ValueError):
            Calibration("bad", frame(), Dimensions(800, 600), QUAD, 0)

    def test_calibration_requires_exact_current_anchor(self):
        guard, _, calibration = ready()
        guard.accept_frame(frame(1), now=now())
        with self.assertRaises(ObservationRejected) as error:
            guard.set_calibration(calibration, now=now())
        self.assertEqual(error.exception.code, RejectionCode.WRONG_CALIBRATION_ANCHOR)
        self.assertIsNone(guard.calibration)

    def test_calibration_rejects_uncertain_anchor(self):
        with self.assertRaises(ValueError):
            Calibration("bad", frame(state=ObservationState.UNCERTAIN), TARGET, QUAD, 0)

    def test_explicit_invalidation(self):
        guard, sample, _ = ready()
        guard.invalidate()
        with self.assertRaises(ObservationRejected) as error:
            guard.map_image_point(sample.frame_id, Point(500, 500), now=now())
        self.assertEqual(error.exception.code, RejectionCode.NO_CURRENT_FRAME)

    def test_malformed_frame_clears_state(self):
        guard, _, _ = ready()
        with self.assertRaises(ValueError):
            guard.accept_frame(None, now=now())
        self.assertIsNone(guard.current_frame)
        self.assertIsNone(guard.calibration)


if __name__ == "__main__":
    unittest.main()
