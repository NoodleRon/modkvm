"""Test-only synthetic adapters. No camera, image decoding, network or HID I/O."""
from dataclasses import dataclass
import json
from digit_observation import (CaptureBinding, ClockSample, Dimensions, FrameId,
                               FrameMetadata, ObservationGuard, ObservationState,
                               ValidationPolicy)
from .controller import Controller
from .loop import (FrozenFrame, InputStatus, ModelContext, ObservationLoop, SourceStatus)
from .protocol import canonical
from .simulator import LoopbackTransport, ManualClock, Simulator


class FakeTransport(LoopbackTransport):
    def __init__(self, device: Simulator, target_id="synthetic-target", target_epoch="layout-1"):
        super().__init__(device)
        self.target_id = target_id
        self.target_epoch = target_epoch

    def input_status(self) -> InputStatus:
        return InputStatus(self.target_id, self.target_epoch, not self.closed)


class FakeFrameSource:
    def __init__(self, device: Simulator, now):
        self.device = device
        self.now = now
        self.binding = CaptureBinding("synthetic-source", "capture-session-1", "synthetic-target", "layout-1")
        self.dimensions = Dimensions(64, 48)
        self.target_dimensions = Dimensions(640, 480)
        self.state = ObservationState.READY
        self.sequence = -1
        self.current_frame_id = None
        self.closed = False

    def capture(self) -> FrozenFrame:
        self.sequence += 1
        self.current_frame_id = FrameId(self.binding.source_id, self.binding.session_id, self.sequence)
        metadata = FrameMetadata(self.current_frame_id, self.now(), self.binding,
            self.dimensions, self.target_dimensions, self.state)
        # Synthetic text is derived from fake input events, not screen capture.
        text = "".join(e.keys[0] for e in self.device.events
                       if e.kind == "press" and len(e.keys) == 1 and len(e.keys[0]) == 1)[-2048:]
        return FrozenFrame(metadata, canonical({"screen_text": text}), "application/x-digit-synthetic-screen+json")

    def status(self) -> SourceStatus:
        return SourceStatus(self.binding, self.dimensions, self.target_dimensions,
                            self.state, self.current_frame_id)

    def close(self) -> None:
        self.closed = True
        self.state = ObservationState.DISCONNECTED


class FakeModel:
    """Deterministic fixture for one task, not vision, OCR, or language inference."""
    def __init__(self):
        self.last_frame = None
        self.last_context = None
        self.calls = 0

    def propose(self, frame: FrozenFrame, context: ModelContext) -> bytes:
        self.last_frame, self.last_context = frame, context
        self.calls += 1
        if context.task != "Type A":
            raise ValueError("FakeModel supports the explicit fixture task 'Type A' only")
        screen_text = json.loads(frame.payload)["screen_text"]
        actions = [{"type": "release_all"}] if screen_text == "A" else [{"type": "text", "value": "A"}]
        return canonical({"observation": context.observation.as_dict(), "actions": actions,
                          "expected_result": "The synthetic screen contains A"})


@dataclass
class FakeLoopBundle:
    loop: ObservationLoop
    clock: ManualClock
    device: Simulator
    transport: FakeTransport
    source: FakeFrameSource
    model: FakeModel
    guard: ObservationGuard


def make_fake_loop(policy=ValidationPolicy()) -> FakeLoopBundle:
    clock = ManualClock()
    now = lambda: ClockSample("synthetic-monotonic", round(clock.now() * 1_000_000_000))
    device = Simulator(clock)
    transport = FakeTransport(device)
    source = FakeFrameSource(device, now)
    model = FakeModel()
    guard = ObservationGuard(source.binding, source.dimensions, source.target_dimensions,
                             "synthetic-monotonic", policy)
    controller = Controller(transport, now=clock.now)
    loop = ObservationLoop(controller, source, model, guard, now)
    return FakeLoopBundle(loop, clock, device, transport, source, model, guard)
