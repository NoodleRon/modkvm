"""Regression tests for error/interrupt cleanup; no real devices are involved."""
import unittest
from digit.actions import Text, ValidationError
from digit.controller import Controller, SafetyError
from digit.protocol import decode
from digit.simulator import LoopbackTransport, Simulator


class InterruptingReleaseTransport(LoopbackTransport):
    def __init__(self, device):
        super().__init__(device)
        self.interrupt_release = False
        self.interrupt_after_delivery = False
        self.interrupt_exception = KeyboardInterrupt
        self.close_attempted = False
        self.fail_close = False

    def exchange(self, request, timeout):
        if self.interrupt_release and decode(request).kind == "release_all":
            self.interrupt_release = False
            if self.interrupt_after_delivery:
                super().exchange(request, timeout)
            raise self.interrupt_exception("Simulated interruption during release")
        return super().exchange(request, timeout)

    def close(self):
        self.close_attempted = True
        if self.fail_close:
            raise OSError("Simulated close failure")
        return super().close()


class FailClosedTests(unittest.TestCase):
    def setUp(self):
        self.device = Simulator()
        self.transport = InterruptingReleaseTransport(self.device)
        self.controller = Controller(self.transport, now=self.device.clock.now)
        self.device.local_arm()
        self.controller.arm("ARM")
        self.preview = self.controller.preview((Text("A"),))
        self.approval = self.controller.approve(self.preview, self.preview.confirmation)

    def assert_old_approval_invalid(self):
        with self.assertRaises(SafetyError):
            self.controller.execute(self.approval)
        self.assertEqual(self.device.executed_batches, 0)

    def test_interrupted_stop_faults_before_io_and_preserves_interrupt(self):
        for exception_type in (KeyboardInterrupt, SystemExit):
            for after_delivery in (False, True):
                with self.subTest(exception=exception_type, after_delivery=after_delivery):
                    self.setUp()
                    self.transport.interrupt_release = True
                    self.transport.interrupt_exception = exception_type
                    self.transport.interrupt_after_delivery = after_delivery
                    with self.assertRaises(exception_type):
                        self.controller.stop()
                    self.assertEqual(self.controller.state, "fault")
                    self.assert_old_approval_invalid()

    def test_interrupted_close_still_closes_transport_and_preserves_interrupt(self):
        for exception_type in (KeyboardInterrupt, SystemExit):
            for after_delivery in (False, True):
                with self.subTest(exception=exception_type, after_delivery=after_delivery):
                    self.setUp()
                    self.transport.interrupt_release = True
                    self.transport.interrupt_exception = exception_type
                    self.transport.interrupt_after_delivery = after_delivery
                    with self.assertRaises(exception_type):
                        self.controller.close()
                    self.assertTrue(self.transport.close_attempted)
                    self.assertTrue(self.transport.closed)
                    self.assertFalse(self.device.armed)
                    self.assertFalse(self.device.pressed)
                    self.assertEqual(self.controller.state, "closed")
                    self.assert_old_approval_invalid()

    def test_interrupted_close_not_masked_by_cleanup_failure(self):
        self.transport.interrupt_release = True
        self.transport.fail_close = True
        with self.assertRaises(KeyboardInterrupt):
            self.controller.close()
        self.assertTrue(self.transport.close_attempted)
        self.assertEqual(self.controller.state, "closed")
        self.assert_old_approval_invalid()

    def test_bad_replacement_preview_invalidates_approval_and_disarms(self):
        with self.assertRaises(ValidationError):
            self.controller.preview((Text("\n"),))
        self.assertEqual(self.controller.state, "fault")
        self.assertFalse(self.device.armed)
        self.assert_old_approval_invalid()

    def test_bad_confirmation_invalidates_prior_approval_and_disarms(self):
        with self.assertRaises(SafetyError):
            self.controller.approve(self.preview, "wrong")
        self.assertEqual(self.controller.state, "fault")
        self.assertFalse(self.device.armed)
        self.assert_old_approval_invalid()

    def test_expired_approval_disarms(self):
        self.device.clock.sleep(45)
        with self.assertRaises(SafetyError):
            self.controller.execute(self.approval)
        self.assertEqual(self.controller.state, "fault")
        self.assertFalse(self.device.armed)

    def test_close_failure_is_reported_after_local_permission_is_revoked(self):
        self.transport.fail_close = True
        with self.assertRaises(OSError):
            self.controller.close()
        self.assertTrue(self.transport.close_attempted)
        self.assertEqual(self.controller.state, "closed")
        self.assertFalse(self.device.armed)
        self.assert_old_approval_invalid()

    def test_stale_token_rejection_keeps_a_different_current_approval(self):
        newer = self.controller.approve(self.preview, self.preview.confirmation)
        with self.assertRaises(SafetyError):
            self.controller.execute(self.approval)
        self.controller.execute(newer)
        self.assertEqual(self.device.executed_batches, 1)
