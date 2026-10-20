import unittest
from digit.actions import KeyChord, Text
from digit.controller import Approval, Controller, ExecutionUncertain, SafetyError
from digit.protocol import Frame, decode, encode
from digit.simulator import LoopbackTransport, Simulator


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.device = Simulator()
        self.transport = LoopbackTransport(self.device)
        self.controller = Controller(self.transport, now=self.device.clock.now)

    def arm(self):
        self.device.local_arm()
        self.controller.arm("ARM")

    def approve(self, text="Hello"):
        preview = self.controller.preview((Text(text),))
        return self.controller.approve(preview, preview.confirmation)

    def test_arm_requires_local_and_operator_confirmation(self):
        with self.assertRaises(SafetyError):
            self.controller.arm("yes")
        with self.assertRaises(SafetyError):
            self.controller.arm("ARM")
        self.assertEqual(self.controller.state, "fault")
        self.assertFalse(self.device.armed)

    def test_happy_path_and_single_use_approval(self):
        self.arm()
        approval = self.approve()
        self.controller.execute(approval)
        self.assertEqual(self.device.executed_batches, 1)
        self.assertFalse(self.device.pressed)
        with self.assertRaises(SafetyError):
            self.controller.execute(approval)
        self.assertTrue(self.controller.stop())
        self.assertFalse(self.device.armed)

    def test_wrong_or_forged_approval_rejected(self):
        self.arm()
        preview = self.controller.preview((Text("a"),))
        with self.assertRaises(SafetyError):
            self.controller.approve(preview, "APPROVE")
        with self.assertRaises(SafetyError):
            self.controller.execute(Approval(preview, "forged"))
        self.assertEqual(self.device.executed_batches, 0)

    def test_new_preview_invalidates_old_approval(self):
        self.arm()
        approval = self.approve()
        self.controller.preview((Text("changed"),))
        with self.assertRaises(SafetyError):
            self.controller.execute(approval)

    def test_approval_expiry(self):
        self.arm()
        approval = self.approve()
        self.device.clock.sleep(45)
        with self.assertRaises(SafetyError):
            self.controller.execute(approval)
        self.assertEqual(self.device.executed_batches, 0)

    def test_lost_ack_does_not_replay_and_attempts_release(self):
        self.arm()
        approval = self.approve()
        self.transport.drop_next_ack = True
        with self.assertRaises(ExecutionUncertain):
            self.controller.execute(approval)
        self.assertEqual(self.device.executed_batches, 1)
        self.assertEqual(self.controller.state, "fault")
        self.assertFalse(self.device.armed)
        self.assertFalse(self.device.pressed)
        kinds = [decode(wire).kind for wire in self.transport.requests]
        self.assertEqual(kinds, ["arm", "execute", "release_all"])
        with self.assertRaises(SafetyError):
            self.controller.execute(approval)

    def test_corrupted_ack_fails_closed(self):
        self.arm()
        self.transport.corrupt_next_ack = True
        with self.assertRaises(ExecutionUncertain):
            self.controller.execute(self.approve())
        self.assertFalse(self.device.armed)

    def test_disconnect_clears_keys_and_invalidates_execution(self):
        self.arm()
        approval = self.approve()
        self.transport.close()
        with self.assertRaises(ExecutionUncertain):
            self.controller.execute(approval)
        self.assertFalse(self.device.armed)
        self.assertFalse(self.controller.stop())

    def test_stop_invalidates_pending_approval(self):
        self.arm()
        approval = self.approve()
        self.assertTrue(self.controller.stop())
        with self.assertRaises(SafetyError):
            self.controller.execute(approval)

    def test_simulated_hold_watchdog_releases_and_faults(self):
        self.arm()
        self.device.stall_after_press = 0.3
        with self.assertRaises(ExecutionUncertain):
            self.controller.execute(self.approve("A"))
        self.assertFalse(self.device.pressed)
        self.assertFalse(self.device.armed)
        self.assertIn("hold_watchdog", [event.kind for event in self.device.events])

    def test_idle_arm_expiry(self):
        self.arm()
        self.device.clock.sleep(60)
        self.device.tick()
        self.assertFalse(self.device.armed)

    def test_arm_lease_expiry_during_final_release_gap(self):
        self.arm()
        self.device.clock.sleep(59.895)
        preview = self.controller.preview((KeyChord(("A",), 100),))
        approval = self.controller.approve(preview, preview.confirmation)
        with self.assertRaises(ExecutionUncertain):
            self.controller.execute(approval)
        self.assertFalse(self.device.armed)
        self.assertFalse(self.device.pressed)
        self.assertIn("arm_expired", [event.kind for event in self.device.events])

    def test_backend_deduplicates_identical_request(self):
        self.arm()
        self.controller.execute(self.approve())
        request = self.transport.requests[-1]
        before = len(self.device.events)
        reply = self.device.handle(request)
        self.assertEqual(decode(reply).body["status"], "ok")
        self.assertEqual(self.device.executed_batches, 1)
        self.assertEqual(len(self.device.events), before)

    def test_sequence_conflict_and_gap_fail_closed(self):
        for seq in (2, 4):
            with self.subTest(seq=seq):
                self.setUp()
                self.arm()
                self.controller.execute(self.approve())
                request = Frame(self.controller.session, seq, "execute", {"actions": [{"type": "text", "value": "different"}]})
                reply = decode(self.device.handle(encode(request)))
                self.assertEqual(reply.body["status"], "rejected")
                self.assertFalse(self.device.armed)
                self.assertEqual(self.device.executed_batches, 1)

    def test_whole_batch_validated_before_any_input(self):
        self.arm()
        request = Frame(self.controller.session, 2, "execute", {"actions": [
            {"type": "text", "value": "safe"}, {"type": "shell", "command": "unsafe"}]})
        reply = decode(self.device.handle(encode(request)))
        self.assertEqual(reply.body["status"], "rejected")
        self.assertFalse(any(event.kind == "press" for event in self.device.events))

    def test_release_accepts_stale_session_and_sequence_gap(self):
        self.arm()
        reply = decode(self.device.handle(encode(Frame("a" * 32, 999, "release_all", {}))))
        self.assertEqual(reply.body["status"], "ok")
        self.assertFalse(self.device.armed)

    def test_close_attempts_release_and_closes_transport(self):
        self.arm()
        self.assertTrue(self.controller.close())
        self.assertTrue(self.transport.closed)
        self.assertEqual(self.controller.state, "closed")
