"""Run with: python -m examples.synthetic_mapping"""
from digit_observation import (Calibration, CaptureBinding, ClockSample, Dimensions,
                               FrameId, FrameMetadata, ObservationGuard, Point)


def main():
    binding = CaptureBinding("synthetic-camera", "synthetic-session-1", "demo-display", "layout-1")
    source = Dimensions(1001, 1001)
    target = Dimensions(1920, 1080)
    # Explicit synthetic time avoids pretending this example captures anything.
    now = ClockSample("synthetic-clock", 1_000_000_000)
    frame = FrameMetadata(FrameId(binding.source_id, binding.session_id, 0), now, binding, source, target)
    guard = ObservationGuard(binding, source, target, now.clock_id)
    guard.accept_frame(frame, now=now)
    guard.set_calibration(Calibration("manual-demo-1", frame, target,
                          (Point(100, 100), Point(900, 100), Point(900, 900), Point(100, 900)),
                          declared_error_px=0.5), now=now)
    result = guard.map_image_point(frame.frame_id, Point(500, 500), now=now)
    guard.verify_mapping(result, now=now)
    print(f"Synthetic mapping: target x={result.target_point.x:.1f}, y={result.target_point.y:.1f}")
    print(f"Frame sequence: {result.frame_id.sequence}; calibration revision: {result.calibration_revision}")
    print("No image captured and no input executed")


if __name__ == "__main__":
    main()
