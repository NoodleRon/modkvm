"""Current-source acceptance journeys; fake input/model/source, no real devices."""
import json
import unittest
from digit_observation import Calibration, Point
from digit.controller import ExecutionUncertain
from digit.loop import LoopError
from digit.loop_fakes import make_fake_loop


class OfflineAcceptance(unittest.TestCase):
    def setUp(self):
        self.bundle = make_fake_loop()
        self.loop = self.bundle.loop
        self.before = self.loop.observe()
        m = self.before.metadata
        w, h = m.dimensions.width - 1, m.dimensions.height - 1
        self.loop.calibrate(Calibration('portfolio-fixture', m, m.target_dimensions,
            (Point(0, 0), Point(w, 0), Point(w, h), Point(0, h)), 0.0))
        self.bundle.device.local_arm()
        self.loop.arm('ARM')
        self.addCleanup(self.loop.close)

    def approve_current(self):
        proposal = self.loop.propose('Type A')
        return proposal, self.loop.approve(proposal, proposal.confirmation)

    def first_cycle(self):
        _, approval = self.approve_current()
        receipt = self.loop.dispatch(approval)
        result = self.loop.observe_after(receipt)
        self.assertEqual(json.loads(result.frame.payload), {'screen_text': 'A'})
        self.assertEqual(result.outcome, 'unverified')
        return receipt, result

    def test_two_cycles_have_new_receipts_observations_and_no_duplicate_text(self):
        first, observed = self.first_cycle()
        second_proposal, approval = self.approve_current()
        self.assertIs(self.bundle.model.last_context.previous_receipt, first)
        self.assertEqual(type(second_proposal.actions[0]).__name__, 'ReleaseAll')
        self.assertEqual(second_proposal.observation.frame, observed.frame.metadata)
        second = self.loop.dispatch(approval)
        result = self.loop.observe_after(second)
        self.assertIsNot(second, first)
        self.assertGreater(second.input_sequence, first.input_sequence)
        self.assertGreater(result.frame.metadata.frame_id.sequence,
                           observed.frame.metadata.frame_id.sequence)
        self.assertEqual(json.loads(result.frame.payload), {'screen_text': 'A'})
        self.assertEqual(result.outcome, 'unverified')
        self.assertEqual(self.bundle.device.executed_batches, 2)
        self.assertTrue(self.loop.stop())
        self.assertFalse(self.bundle.device.armed)
        self.assertFalse(self.bundle.device.pressed)

    def test_replacing_approved_plan_rejects_old_token_without_input(self):
        _, old_approval = self.approve_current()
        newer, _ = self.approve_current()
        self.assertIs(self.bundle.model.last_context.observation, newer.observation)
        with self.assertRaises(LoopError):
            self.loop.dispatch(old_approval)
        self.assertEqual(self.bundle.device.executed_batches, 0)
        self.assertFalse(self.bundle.device.armed)
        self.assertEqual(self.loop.state, 'fault')

    def test_stop_with_pending_receipt_prevents_followup_capture(self):
        _, approval = self.approve_current()
        receipt = self.loop.dispatch(approval)
        frame_sequence = self.bundle.source.sequence
        self.assertTrue(self.loop.stop())
        with self.assertRaises(LoopError):
            self.loop.observe_after(receipt)
        self.assertEqual(self.bundle.source.sequence, frame_sequence)
        self.assertEqual(self.bundle.device.executed_batches, 1)
        self.assertFalse(self.bundle.device.armed)

    def test_second_cycle_lost_ack_does_not_replay_or_accept_old_receipt(self):
        first, _ = self.first_cycle()
        _, approval = self.approve_current()
        self.bundle.transport.drop_next_ack = True
        with self.assertRaises(ExecutionUncertain):
            self.loop.dispatch(approval)
        self.assertEqual(self.bundle.device.executed_batches, 2)
        self.assertFalse(self.bundle.device.armed)
        with self.assertRaises(LoopError):
            self.loop.dispatch(approval)
        with self.assertRaises(LoopError):
            self.loop.observe_after(first)
        self.assertEqual(self.bundle.device.executed_batches, 2)
        self.assertEqual(self.loop.state, 'fault')
