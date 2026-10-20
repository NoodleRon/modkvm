"""Composition tests use only in-memory adapters and a deterministic clock."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest

from digit_observation import Calibration, ClockSample, Dimensions, ObservationState, Point
from digit.actions import ValidationError
from digit.controller import ExecutionUncertain, SafetyError
from digit.loop import FrozenFrame, LoopError, parse_proposal
from digit.loop_demo import run_demo
from digit.loop_fakes import make_fake_loop
from digit.protocol import canonical


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.bundle = make_fake_loop()
        self.loop = self.bundle.loop
        self.frame = self.loop.observe()
        self.calibration = self.make_calibration()
        self.loop.calibrate(self.calibration)
        self.bundle.device.local_arm()
        self.loop.arm("ARM")

    def make_calibration(self, name="calibration-1"):
        f = self.frame.metadata
        w, h = f.dimensions.width - 1, f.dimensions.height - 1
        return Calibration(name, f, f.target_dimensions,
                           (Point(0, 0), Point(w, 0), Point(w, h), Point(0, h)), 0.0)

    def proposal(self):
        return self.loop.propose("Type A")

    def approved(self):
        p = self.proposal()
        return self.loop.approve(p, p.confirmation)

    def assert_fault_without_input(self):
        self.assertEqual(self.loop.state, "fault")
        self.assertEqual(self.bundle.device.executed_batches, 0)
        self.assertFalse(self.bundle.device.armed)
        self.assertFalse(self.bundle.device.pressed)
        self.assertIsNone(self.bundle.guard.current_frame)

    def test_full_synthetic_loop_returns_new_observation_without_success_claim(self):
        approval = self.approved()
        receipt = self.loop.dispatch(approval)
        self.assertEqual(receipt.status, "input_acknowledged")
        self.assertEqual(self.loop.state, "awaiting_observation")
        result = self.loop.observe_after(receipt)
        self.assertEqual(result.outcome, "unverified")
        self.assertGreater(result.frame.metadata.frame_id.sequence, self.frame.metadata.frame_id.sequence)
        self.assertEqual(json.loads(result.frame.payload), {"screen_text": "A"})
        self.assertEqual(self.bundle.device.executed_batches, 1)
        self.assertEqual(self.loop.state, "observed")

    def test_model_receives_immutable_exact_frame_and_context(self):
        p = self.proposal()
        self.assertIs(self.bundle.model.last_frame, self.frame)
        context = self.bundle.model.last_context
        self.assertEqual(context.observation, p.observation)
        self.assertEqual(context.observation.payload_sha256, hashlib.sha256(self.frame.payload).hexdigest())
        with self.assertRaises(FrozenInstanceError):
            context.task = "something else"
        with self.assertRaises(FrozenInstanceError):
            self.frame.payload = b"changed"
        with self.assertRaises(TypeError):
            self.frame.payload[0] = 0

    def test_loop_confirmation_binds_more_than_core_actions(self):
        p = self.proposal()
        self.assertNotEqual(p.confirmation, p.preview.confirmation)
        with self.assertRaises(LoopError):
            self.loop.approve(p, p.preview.confirmation)
        self.assert_fault_without_input()

    def test_wrong_confirmation_revokes_queue(self):
        p = self.proposal()
        with self.assertRaises(LoopError):
            self.loop.approve(p, "REJECT")
        with self.assertRaises(LoopError):
            self.loop.approve(p, p.confirmation)
        self.assert_fault_without_input()

    def test_forged_or_copied_proposal_rejected(self):
        p = self.proposal()
        with self.assertRaises(LoopError):
            self.loop.approve(replace(p), p.confirmation)
        self.assert_fault_without_input()

    def test_forged_or_copied_approval_rejected(self):
        approval = self.approved()
        with self.assertRaises(LoopError):
            self.loop.dispatch(replace(approval))
        self.assert_fault_without_input()

    def test_stale_before_human_approval(self):
        p = self.proposal()
        self.bundle.clock.sleep(0.501)
        with self.assertRaises(ValueError):
            self.loop.approve(p, p.confirmation)
        self.assert_fault_without_input()

    def test_stale_after_approval_before_dispatch(self):
        approval = self.approved()
        self.bundle.clock.sleep(0.501)
        with self.assertRaises(ValueError):
            self.loop.dispatch(approval)
        self.assert_fault_without_input()

    def test_model_latency_is_revalidated(self):
        original = self.bundle.model.propose
        def slow(frame, context):
            result = original(frame, context)
            self.bundle.clock.sleep(0.501)
            return result
        self.bundle.model.propose = slow
        with self.assertRaises(ValueError):
            self.proposal()
        self.assert_fault_without_input()

    def test_resolution_target_disconnect_and_uncertainty_invalidate_at_both_gates(self):
        def source_resolution(b):
            b.source.dimensions = Dimensions(65, 48)
        def target_resolution(b):
            b.source.target_dimensions = Dimensions(800, 600)
        def source_target(b):
            b.source.binding = replace(b.source.binding, target_id="other-target")
        def source_epoch(b):
            b.source.binding = replace(b.source.binding, target_epoch="layout-2")
        def capture_session(b):
            b.source.binding = replace(b.source.binding, session_id="new-capture-session")
        def source_disconnect(b):
            b.source.state = ObservationState.DISCONNECTED
        def uncertain(b):
            b.source.state = ObservationState.UNCERTAIN
        def input_disconnect(b):
            b.transport.close()
        def input_target(b):
            b.transport.target_id = "wrong-target"
        def input_epoch(b):
            b.transport.target_epoch = "other-epoch"
        def newer_frame(b):
            b.source.capture()
        mutations = [source_resolution, target_resolution, source_target, source_epoch,
                     capture_session, source_disconnect, uncertain, input_disconnect,
                     input_target, input_epoch, newer_frame]
        for mutation in mutations:
            for gate in ("approve", "dispatch"):
                with self.subTest(mutation=mutation.__name__, gate=gate):
                    self.setUp()
                    p = self.proposal()
                    approval = self.loop.approve(p, p.confirmation) if gate == "dispatch" else None
                    mutation(self.bundle)
                    with self.assertRaises((LoopError, ValueError)):
                        if gate == "approve":
                            self.loop.approve(p, p.confirmation)
                        else:
                            self.loop.dispatch(approval)
                    self.assert_fault_without_input()

    def test_calibration_revision_change_invalidates_even_when_id_is_reused(self):
        approval = self.approved()
        self.bundle.guard.set_calibration(self.calibration, now=self.loop.now())
        with self.assertRaises(LoopError):
            self.loop.dispatch(approval)
        self.assert_fault_without_input()

    def test_poll_revokes_on_disconnect(self):
        approval = self.approved()
        self.bundle.source.close()
        with self.assertRaises(LoopError):
            self.loop.poll()
        with self.assertRaises(LoopError):
            self.loop.dispatch(approval)
        self.assert_fault_without_input()

    def test_model_binding_changes_and_boolean_alias_are_rejected(self):
        mutations = [lambda d: d["observation"]["binding"].update(target_id="other"),
                     lambda d: d["observation"]["frame_id"].update(sequence=1),
                     lambda d: d["observation"].update(calibration_revision=True),
                     lambda d: d["observation"].update(payload_sha256="0" * 64),
                     lambda d: d.update(unexpected="data")]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.setUp()
                original = self.bundle.model.propose
                def changed(frame, context):
                    value = json.loads(original(frame, context))
                    mutation(value)
                    return canonical(value)
                self.bundle.model.propose = changed
                with self.assertRaises((LoopError, ValueError)):
                    self.proposal()
                self.assert_fault_without_input()

    def test_model_cannot_return_arbitrary_code_as_an_action(self):
        original = self.bundle.model.propose
        def shell(frame, context):
            value = json.loads(original(frame, context))
            value["actions"] = [{"type": "shell", "command": "print('not executed')"}]
            return canonical(value)
        self.bundle.model.propose = shell
        with self.assertRaises(ValidationError):
            self.proposal()
        self.assert_fault_without_input()

    def test_malformed_oversized_and_duplicate_model_output_rejected(self):
        for raw in [b"x" * 8193, b"[]", b"not json", b'{"actions":[],"actions":[]}', b"\xff"]:
            with self.subTest(raw=raw[:50]):
                self.setUp()
                self.bundle.model.propose = lambda frame, context: raw
                with self.assertRaises((LoopError, ValueError)):
                    self.proposal()
                self.assert_fault_without_input()

    def test_receipt_does_not_allow_next_proposal_without_new_observation(self):
        self.loop.dispatch(self.approved())
        with self.assertRaises(LoopError):
            self.proposal()
        self.assertEqual(self.bundle.device.executed_batches, 1)
        self.assertEqual(self.loop.state, "fault")

    def test_receipt_cannot_be_used_twice_or_forged(self):
        receipt = self.loop.dispatch(self.approved())
        with self.assertRaises(LoopError):
            self.loop.observe_after(replace(receipt))
        self.assertEqual(self.loop.state, "fault")

    def test_replayed_pre_action_frame_is_not_a_post_action_observation(self):
        receipt = self.loop.dispatch(self.approved())
        self.bundle.source.capture = lambda: self.frame
        with self.assertRaises(ValueError):
            self.loop.observe_after(receipt)
        self.assertEqual(self.loop.state, "fault")

    def test_backdated_new_id_is_not_a_post_action_observation(self):
        receipt = self.loop.dispatch(self.approved())
        original = self.bundle.source.capture
        def old_timestamp():
            frame = original()
            return replace(frame, metadata=replace(frame.metadata, captured_at=ClockSample("synthetic-monotonic", 0)))
        self.bundle.source.capture = old_timestamp
        with self.assertRaises(LoopError):
            self.loop.observe_after(receipt)
        self.assertEqual(self.loop.state, "fault")

    def test_post_action_disconnect_is_not_success(self):
        receipt = self.loop.dispatch(self.approved())
        self.bundle.source.close()
        with self.assertRaises(LoopError):
            self.loop.observe_after(receipt)
        self.assertEqual(self.bundle.device.executed_batches, 1)
        self.assertEqual(self.loop.state, "fault")

    def test_post_action_observation_flows_back_to_next_model_context(self):
        receipt = self.loop.dispatch(self.approved())
        result = self.loop.observe_after(receipt)
        p = self.proposal()
        self.assertEqual(p.observation.frame, result.frame.metadata)
        self.assertEqual(self.bundle.model.last_context.previous_receipt, receipt)
        self.assertEqual(len(p.actions), 1)
        self.assertEqual(type(p.actions[0]).__name__, "ReleaseAll")

    def test_lost_ack_never_replays_or_claims_outcome(self):
        approval = self.approved()
        self.bundle.transport.drop_next_ack = True
        with self.assertRaises(ExecutionUncertain):
            self.loop.dispatch(approval)
        self.assertEqual(self.bundle.device.executed_batches, 1)
        self.assertEqual(self.loop.state, "fault")
        with self.assertRaises(LoopError):
            self.loop.dispatch(approval)
        self.assertEqual(self.bundle.device.executed_batches, 1)

    def test_stop_revokes_queue_and_close_cleans_up_both_adapters(self):
        approval = self.approved()
        self.assertTrue(self.loop.stop())
        with self.assertRaises(LoopError):
            self.loop.dispatch(approval)
        self.loop.close()
        self.assertTrue(self.bundle.transport.closed)
        self.assertTrue(self.bundle.source.closed)
        self.assertEqual(self.loop.state, "closed")
        self.assertEqual(self.bundle.device.executed_batches, 0)

    def test_model_interrupt_propagates_and_revokes(self):
        def interrupted(frame, context):
            raise KeyboardInterrupt("fake model interrupted")
        self.bundle.model.propose = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.proposal()
        self.assert_fault_without_input()

    def test_frame_payload_must_be_bounded_immutable_bytes(self):
        for value in [bytearray(b"a"), b"", b"a" * (1024 * 1024 + 1)]:
            with self.assertRaises(ValueError):
                FrozenFrame(self.frame.metadata, value, "test/synthetic")

    def test_demo_reports_fixture_results_only(self):
        result = run_demo()
        self.assertEqual(result["before_payload"]["screen_text"], "")
        self.assertEqual(result["after_payload"]["screen_text"], "A")
        self.assertEqual(result["outcome"], "unverified")
        self.assertEqual(result["executed_batches"], 1)
