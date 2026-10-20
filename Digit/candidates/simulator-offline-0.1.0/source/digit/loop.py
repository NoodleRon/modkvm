"""Single-owner observation -> proposal -> approval -> input -> observation loop.

All adapters and operator methods are trusted application code. Models return
bounded JSON data only. This module opens no devices or provider connections.
It is deliberately synchronous and does not promise atomic target stability.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import secrets
from typing import Callable, Protocol

from digit_observation import (Calibration, CaptureBinding, ClockSample, Dimensions,
                               FrameId, FrameMetadata, MappedPoint, ObservationGuard,
                               ObservationState, Point)
from .actions import Action, parse_batch
from .controller import Approval, Controller, Preview, SafetyError
from .protocol import canonical, unique_object

MAX_MODEL_BYTES = 8192
MAX_FRAME_BYTES = 1024 * 1024


class LoopError(SafetyError):
    pass


@dataclass(frozen=True)
class FrozenFrame:
    metadata: FrameMetadata
    payload: bytes
    media_type: str

    def __post_init__(self):
        if not isinstance(self.metadata, FrameMetadata):
            raise ValueError("Frame metadata is required")
        if type(self.payload) is not bytes or not 1 <= len(self.payload) <= MAX_FRAME_BYTES:
            raise ValueError("Frame payload must be immutable bytes, at most 1 MiB")
        if not isinstance(self.media_type, str) or not 1 <= len(self.media_type) <= 128:
            raise ValueError("A bounded media type is required")


@dataclass(frozen=True)
class SourceStatus:
    binding: CaptureBinding
    dimensions: Dimensions
    target_dimensions: Dimensions
    state: ObservationState
    current_frame_id: FrameId | None


@dataclass(frozen=True)
class InputStatus:
    target_id: str
    target_epoch: str
    connected: bool


class FrameSource(Protocol):
    def capture(self) -> FrozenFrame: ...
    def status(self) -> SourceStatus: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class ObservationPin:
    frame: FrameMetadata
    payload_sha256: str
    calibration_id: str
    calibration_revision: int

    def as_dict(self) -> dict:
        frame = self.frame
        return {
            "frame_id": {"source_id": frame.frame_id.source_id,
                         "session_id": frame.frame_id.session_id,
                         "sequence": frame.frame_id.sequence},
            "binding": {"source_id": frame.binding.source_id,
                        "session_id": frame.binding.session_id,
                        "target_id": frame.binding.target_id,
                        "target_epoch": frame.binding.target_epoch},
            "captured_at": {"clock_id": frame.captured_at.clock_id,
                            "monotonic_ns": frame.captured_at.monotonic_ns},
            "dimensions": {"width": frame.dimensions.width, "height": frame.dimensions.height},
            "target_dimensions": {"width": frame.target_dimensions.width, "height": frame.target_dimensions.height},
            "payload_sha256": self.payload_sha256,
            "calibration_id": self.calibration_id,
            "calibration_revision": self.calibration_revision,
        }


@dataclass(frozen=True)
class ExecutionReceipt:
    receipt_id: str
    observation: ObservationPin
    input_sequence: int
    action_count: int
    completed_at: ClockSample
    expected_result: str
    status: str = "input_acknowledged"


@dataclass(frozen=True)
class ModelContext:
    task: str
    observation: ObservationPin
    previous_receipt: ExecutionReceipt | None = None


class ProposalModel(Protocol):
    def propose(self, frame: FrozenFrame, context: ModelContext) -> bytes:
        """Return bounded JSON, never executable code or operator authority."""
        ...


@dataclass(frozen=True)
class BoundProposal:
    observation: ObservationPin
    expected_result: str
    preview: Preview
    mapping: MappedPoint
    digest: str

    @property
    def actions(self) -> tuple[Action, ...]:
        return self.preview.actions

    @property
    def confirmation(self) -> str:
        return f"APPROVE {self.digest}"


@dataclass(frozen=True)
class LoopApproval:
    proposal: BoundProposal
    nonce: str


@dataclass(frozen=True)
class ObservedResult:
    receipt: ExecutionReceipt
    frame: FrozenFrame
    # A fresh observation is evidence to inspect, not automatic task success.
    outcome: str = "unverified"


def parse_proposal(raw: bytes, expected: ObservationPin) -> tuple[tuple[Action, ...], str]:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_MODEL_BYTES:
        raise LoopError("Model output must be bounded JSON bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LoopError("Model output is not valid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"observation", "actions", "expected_result"}:
        raise LoopError("Model output requires observation, actions, and expected_result only")
    # Compare canonical bytes, not Python equality (where True equals 1).
    if canonical(value["observation"]) != canonical(expected.as_dict()):
        raise LoopError("Model proposal does not match the exact observation binding")
    result = value["expected_result"]
    if not isinstance(result, str) or not 1 <= len(result) <= 512 or any(ord(c) < 32 for c in result):
        raise LoopError("Expected result must be a bounded, single-line description")
    return parse_batch(value["actions"]), result


class ObservationLoop:
    """Serialize all methods on one owner thread; no background watcher exists.

    The controller transport must expose input_status() on the same object
    performing exchanges. No separate, potentially unrelated target monitor is
    accepted. Real adapters remain responsible for honest identity/status data.
    """
    def __init__(self, controller: Controller, source: FrameSource, model: ProposalModel,
                 guard: ObservationGuard, now: Callable[[], ClockSample]) -> None:
        self.controller = controller
        self.source = source
        self.model = model
        self.guard = guard
        self.now = now
        self.state = "needs_observation"
        self._frame: FrozenFrame | None = None
        self._proposal: BoundProposal | None = None
        self._approval: LoopApproval | None = None
        self._core_approval: Approval | None = None
        self._receipt: ExecutionReceipt | None = None
        self._previous_receipt: ExecutionReceipt | None = None

    def _revoke(self) -> None:
        self._proposal = None
        self._approval = None
        self._core_approval = None

    def _abort(self, error: BaseException):
        self._revoke()
        self._frame = None
        self.state = "fault"
        self.guard.close()
        try:
            self.controller.stop()
        except BaseException as cleanup_error:
            if isinstance(error, Exception) and not isinstance(cleanup_error, Exception):
                raise cleanup_error from error
            raise error from cleanup_error
        raise error

    def _status_check(self, metadata: FrameMetadata, *, exact_frame: bool = True) -> None:
        status = self.source.status()
        if not isinstance(status, SourceStatus) or status.state is not ObservationState.READY:
            raise LoopError("Observation source is not ready")
        if status.binding != metadata.binding:
            raise LoopError("Observation target or source binding changed")
        if status.dimensions != metadata.dimensions or status.target_dimensions != metadata.target_dimensions:
            raise LoopError("Observation resolution changed")
        if exact_frame and status.current_frame_id != metadata.frame_id:
            raise LoopError("The proposal frame is no longer current")
        input_status = self.controller.transport.input_status()
        if not isinstance(input_status, InputStatus) or input_status.connected is not True:
            raise LoopError("Input transport is disconnected")
        if (input_status.target_id, input_status.target_epoch) != (metadata.binding.target_id, metadata.binding.target_epoch):
            raise LoopError("Input target does not match the observed target")

    def _pin_current(self) -> tuple[ObservationPin, MappedPoint]:
        if self._frame is None or self.guard.current_frame != self._frame.metadata:
            raise LoopError("A current admitted observation is required")
        self._status_check(self._frame.metadata)
        calibration = self.guard.calibration
        if calibration is None:
            raise LoopError("An operator-established calibration is required")
        now = self.now()
        # Keyboard-only actions still bind to the exact calibration revision.
        # A verified screen corner is an admission anchor, not a mouse action.
        anchor = self.guard.map_target_point(self._frame.metadata.frame_id, Point(0, 0), now=now)
        mapping = self.guard.map_image_point(self._frame.metadata.frame_id, anchor, now=now)
        pin = ObservationPin(self._frame.metadata, hashlib.sha256(self._frame.payload).hexdigest(),
                             calibration.calibration_id, self.guard.calibration_revision)
        return pin, mapping

    def _check_proposal(self, proposal: BoundProposal) -> None:
        pin, _ = self._pin_current()
        if pin != proposal.observation:
            raise LoopError("Proposal observation or calibration is obsolete")
        self.guard.verify_mapping(proposal.mapping, now=self.now())

    def observe(self) -> FrozenFrame:
        try:
            if self.state not in {"needs_observation", "observed"}:
                raise LoopError("Finish or stop the pending cycle before observing again")
            self._revoke()
            frame = self.source.capture()
            if not isinstance(frame, FrozenFrame):
                raise LoopError("Source did not return an immutable frame")
            self.guard.accept_frame(frame.metadata, now=self.now())
            self._status_check(frame.metadata)
            self._frame = frame
            self.state = "observed"
            return frame
        except BaseException as error:
            self._abort(error)

    def calibrate(self, calibration: Calibration) -> None:
        try:
            if self.state != "observed":
                raise LoopError("Calibrate only a newly observed, unqueued scene")
            self.guard.set_calibration(calibration, now=self.now())
            self._pin_current()
        except BaseException as error:
            self._abort(error)

    def arm(self, confirmation: str) -> None:
        try:
            if self.state != "observed":
                raise LoopError("Observe and calibrate before arming")
            self._pin_current()
            self.controller.arm(confirmation)
        except BaseException as error:
            self._abort(error)

    def propose(self, task: str) -> BoundProposal:
        try:
            if self.state not in {"observed", "awaiting_approval", "approved"}:
                raise LoopError("A fresh post-action observation is required before another proposal")
            self._revoke()
            if self.controller.state != "armed":
                raise LoopError("Operator arming is required before proposing input")
            if not isinstance(task, str) or not 1 <= len(task) <= 1024:
                raise LoopError("Task must be a bounded string")
            pin, mapping = self._pin_current()
            context = ModelContext(task, pin, self._previous_receipt)
            raw = self.model.propose(self._frame, context)
            actions, expected_result = parse_proposal(raw, pin)
            # Model latency must not silently age or rebind the proposal.
            current_pin, _ = self._pin_current()
            if current_pin != pin:
                raise LoopError("Observation changed while the model was proposing")
            self.guard.verify_mapping(mapping, now=self.now())
            preview = self.controller.preview(actions)
            digest = hashlib.sha256(canonical({"observation": pin.as_dict(),
                "controller_preview": preview.digest, "expected_result": expected_result})).hexdigest()
            proposal = BoundProposal(pin, expected_result, preview, mapping, digest)
            self._proposal = proposal
            self.state = "awaiting_approval"
            return proposal
        except BaseException as error:
            self._abort(error)

    def approve(self, proposal: BoundProposal, confirmation: str) -> LoopApproval:
        try:
            self._approval = None
            self._core_approval = None
            if self.state != "awaiting_approval" or proposal is not self._proposal:
                raise LoopError("Only the current proposal can be approved")
            self._check_proposal(proposal)
            if confirmation != proposal.confirmation:
                raise LoopError("Confirmation must match the complete observation-bound preview")
            self._core_approval = self.controller.approve(proposal.preview, proposal.preview.confirmation)
            approval = LoopApproval(proposal, secrets.token_hex(16))
            self._approval = approval
            self.state = "approved"
            return approval
        except BaseException as error:
            self._abort(error)

    def dispatch(self, approval: LoopApproval) -> ExecutionReceipt:
        try:
            if self.state != "approved" or approval is not self._approval or self._core_approval is None:
                raise LoopError("An unused observation-bound approval is required")
            proposal = approval.proposal
            self._check_proposal(proposal)
            core_approval = self._core_approval
            self._revoke()  # Consume coordinator permission before input I/O.
            self.state = "executing"
            reply = self.controller.execute(core_approval)
            receipt = ExecutionReceipt(secrets.token_hex(16), proposal.observation,
                reply.seq, reply.body["action_count"], self.now(), proposal.expected_result)
            self._receipt = receipt
            self._frame = None
            self.state = "awaiting_observation"
            return receipt
        except BaseException as error:
            self._abort(error)

    def observe_after(self, receipt: ExecutionReceipt) -> ObservedResult:
        try:
            if self.state != "awaiting_observation" or receipt is not self._receipt:
                raise LoopError("A pending input receipt is required")
            self._status_check(receipt.observation.frame, exact_frame=False)
            frame = self.source.capture()
            if not isinstance(frame, FrozenFrame):
                raise LoopError("Source did not return an immutable frame")
            self.guard.accept_frame(frame.metadata, now=self.now())
            if (frame.metadata.captured_at.clock_id != receipt.completed_at.clock_id or
                    frame.metadata.captured_at.monotonic_ns < receipt.completed_at.monotonic_ns):
                raise LoopError("Post-action observation predates input completion")
            self._status_check(frame.metadata)
            self._frame = frame
            self._pin_current()
            self._previous_receipt = receipt
            self._receipt = None
            self.state = "observed"
            return ObservedResult(receipt, frame)
        except BaseException as error:
            self._abort(error)

    def poll(self) -> None:
        """Explicit event-loop check; no asynchronous watcher is installed."""
        try:
            if self.state in {"fault", "stopped", "closed"}:
                raise LoopError("The observation session is no longer active")
            if self._proposal is not None:
                self._check_proposal(self._proposal)
            elif self._frame is not None:
                self._pin_current()
            elif self._receipt is not None:
                self._status_check(self._receipt.observation.frame, exact_frame=False)
        except BaseException as error:
            self._abort(error)

    def stop(self) -> bool:
        self._revoke()
        self._frame = None
        self.guard.close()
        self.state = "fault"
        confirmed = self.controller.stop()
        if confirmed:
            self.state = "stopped"
        return confirmed

    def close(self) -> None:
        self._revoke()
        self._frame = None
        self.guard.close()
        self.state = "closed"
        first_error = None
        try:
            self.controller.close()
        except BaseException as error:
            first_error = error
        finally:
            try:
                self.source.close()
            except BaseException as cleanup_error:
                if first_error is not None:
                    raise first_error from cleanup_error
                raise
        if first_error is not None:
            raise first_error
