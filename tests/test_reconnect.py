"""Synthetic reconnect regressions: no Bluetooth, subprocess, capture or settings."""
import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch


def load_app():
    source = Path(os.environ.get("MODKVM_TEST_SOURCE", Path(__file__).resolve().parents[1] / "mk.py"))
    dbus = types.ModuleType("dbus")
    service = types.ModuleType("dbus.service")
    service.Object = object
    service.method = lambda *a, **kw: lambda f: f
    dbus.service = service
    dbus.mainloop = types.ModuleType("dbus.mainloop")
    dbus.mainloop.glib = types.ModuleType("dbus.mainloop.glib")
    gi = types.ModuleType("gi")
    gi.repository = types.ModuleType("gi.repository")
    gi.repository.GLib = types.SimpleNamespace()
    modules = {"dbus": dbus, "dbus.service": service, "dbus.mainloop": dbus.mainloop,
               "dbus.mainloop.glib": dbus.mainloop.glib, "gi": gi, "gi.repository": gi.repository}
    loader = SourceFileLoader("modkvm_reconnect_test", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    app = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules): loader.exec_module(app)
    return app


pb = load_app()
A, B = "AA:AA:AA:AA:AA:AA", "BB:BB:BB:BB:BB:BB"


class FakeHID:
    def __init__(self, app):
        self.app, self.up, self.calls, self.conns = app, set(), [], {}
        self.result = (False, "fixture sleeping")
        self.connect_hook = None

    def is_up(self, addr): return addr in self.up

    def connect(self, addr, timeout=10):
        self.calls.append((addr, timeout))
        if self.connect_hook: return self.connect_hook(addr, timeout)
        if self.result[0]:
            self.up.add(addr)
            self.app.on_change(addr, True)
        return self.result

    def drop(self, addr):
        self.up.discard(addr)
        self.conns.pop(addr, None)
        self.app.on_change(addr, False)
        return True

    def press(self, *args): raise AssertionError("reconnect sent keyboard input")
    def mouse(self, *args): raise AssertionError("reconnect sent mouse input")


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.app = pb.App.__new__(pb.App)
        a = self.app
        a.active, a.bus, a.solo = A, object(), False
        a.stop, a.target_lock = threading.Event(), threading.RLock()
        a.reconnect_enabled = True
        a.reconnect_plan = pb.ReconnectPlan(lambda: self.now)
        a.hid = FakeHID(a)
        a.name_of = lambda addr: "fixture"
        a.vpos, a.last_try = None, 0.0
        a.hint = lambda msg: None
        a.bt_disconnect = lambda addr: None
        self.paired = [{"addr": A, "name": "fixture A"}, {"addr": B, "name": "fixture B"}]
        for obj, name, value in [(pb, "bonds", lambda bus, **kw: self.paired),
                                  (pb, "load_state", lambda: {}), (pb, "save_state", lambda **kw: None),
                                  (pb, "say", lambda *args, **kw: None)]:
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        for obj, name in [(pb.socket, "socket"), (pb.subprocess, "Popen")]:
            p = patch.object(obj, name, side_effect=AssertionError("no hardware/process allowed"))
            p.start(); self.addCleanup(p.stop)

    def disconnected(self): self.app.on_change(A, False)

    def attempt(self):
        self.now = self.app.reconnect_plan.next_at
        self.app.reconnect_once()

    def test_disconnect_dials_only_selected_paired_target_without_input(self):
        self.disconnected()
        self.app.reconnect_once()
        self.assertEqual(self.app.hid.calls, [])
        self.attempt()
        self.assertEqual(self.app.hid.calls, [(A, 2.0)])
        self.assertEqual(self.app.active, A)

    def test_input_reconnect_cannot_dial_an_unpaired_target(self):
        self.paired = []
        self.assertFalse(self.app.ensure_target())
        self.assertEqual(self.app.hid.calls, [])

    def test_backoff_is_finite_and_duplicate_disconnect_cannot_extend_it(self):
        self.disconnected()
        plan = self.app.reconnect_plan
        deadline = plan.deadline
        waits = []
        for expected in range(1, len(plan.DELAYS) + 1):
            waits.append(plan.next_at - self.now)
            self.attempt()
            self.assertEqual(plan.attempts, expected)
            self.disconnected()
            self.assertEqual(plan.deadline, deadline)
        self.assertEqual(waits, list(plan.DELAYS))
        self.assertEqual(plan.state, "exhausted")
        self.now += 1000
        self.app.reconnect_once()
        self.assertEqual(len(self.app.hid.calls), len(plan.DELAYS))

    def test_window_expires_even_if_worker_was_suspended(self):
        self.disconnected()
        self.now += pb.ReconnectPlan.WINDOW
        self.app.reconnect_once()
        self.assertEqual(self.app.hid.calls, [])
        self.assertEqual(self.app.reconnect_plan.state, "exhausted")

    def test_metadata_query_cannot_start_a_dial_after_deadline(self):
        self.disconnected()
        def paired(bus, **kw):
            self.now = self.app.reconnect_plan.deadline + 1
            return self.paired
        with patch.object(pb, "bonds", side_effect=paired): self.attempt()
        self.assertEqual(self.app.hid.calls, [])
        self.assertEqual(self.app.reconnect_plan.state, "exhausted")

    def test_timeout_shrinks_near_deadline(self):
        self.disconnected()
        self.now = self.app.reconnect_plan.deadline - .5
        self.app.reconnect_once()
        self.assertEqual(self.app.hid.calls, [(A, .25)])

    def test_success_cancels_retry_and_restores_no_pointer_calibration(self):
        self.disconnected()
        self.app.vpos = [1, 2]
        self.app.hid.result = (True, None)
        self.attempt()
        self.assertIsNone(self.app.reconnect_plan.target)
        self.assertEqual(self.app.reconnect_plan.state, "connected")
        self.assertIsNone(self.app.vpos)
        self.assertEqual(self.app.active, A)
        self.app.reconnect_once()
        self.assertEqual(self.app.reconnect_plan.state, "connected")

    def test_inbound_reconnect_cancels_pending_retry(self):
        self.disconnected()
        self.app.hid.up.add(A)
        self.app.on_change(A, True)
        self.now += 10
        self.app.reconnect_once()
        self.assertEqual(self.app.hid.calls, [])

    def test_other_device_cannot_take_over_while_selected_target_is_offline(self):
        self.disconnected()
        self.app.hid.up.add(B)
        self.app.on_change(B, True)
        self.assertEqual(self.app.active, A)
        self.assertEqual(self.app.reconnect_plan.target, A)
        self.attempt()
        self.assertEqual(self.app.hid.calls[0][0], A)

    def test_other_device_disconnect_does_not_start_retry(self):
        self.app.on_change(B, False)
        self.assertIsNone(self.app.reconnect_plan.target)

    def test_unpaired_target_is_not_dialed(self):
        self.disconnected()
        self.paired = [{"addr": B}]
        self.attempt()
        self.assertEqual(self.app.hid.calls, [])
        self.assertEqual(self.app.reconnect_plan.state, "not paired")

    def test_unavailable_pairing_metadata_backs_off_without_dialing(self):
        self.disconnected()
        with patch.object(pb, "bonds", side_effect=RuntimeError("fixture unavailable")):
            self.attempt()
        self.assertEqual(self.app.hid.calls, [])
        self.assertEqual(self.app.reconnect_plan.attempts, 1)
        self.assertEqual(self.app.reconnect_plan.next_at, self.now + 2)

    def test_connect_exception_consumes_budget_and_backs_off(self):
        self.disconnected()
        def connect(addr, timeout): raise RuntimeError("fixture failure")
        self.app.hid.connect_hook = connect
        self.attempt()
        self.assertEqual(self.app.reconnect_plan.attempts, 1)
        self.assertEqual(self.app.reconnect_plan.next_at, self.now + 2)

    def test_stop_during_connection_does_not_rearm(self):
        self.disconnected()
        def connect(addr, timeout):
            self.app.stop.set()
            return False, "fixture stopped"
        self.app.hid.connect_hook = connect
        self.attempt()
        self.assertEqual(self.app.reconnect_plan.state, "stopped")
        self.assertIsNone(self.app.reconnect_plan.target)

    def test_manual_disconnect_stays_disconnected_until_explicit_use(self):
        self.disconnected()
        self.app.drop({"addr": A, "name": "fixture"})
        self.app.on_change(A, False)
        self.now += 20
        self.app.reconnect_once()
        self.assertFalse(self.app.ensure_target())
        self.assertEqual(self.app.hid.calls, [])
        self.assertFalse(self.app.reconnect_enabled)
        self.app.use({"addr": A, "name": "fixture"})
        self.assertTrue(self.app.reconnect_enabled)
        self.assertEqual(self.app.hid.calls, [(A, 10)])
        self.assertEqual(self.app.reconnect_plan.target, A)

    def test_manual_target_selection_cancels_old_retry(self):
        self.disconnected()
        self.app.set_active(B)
        self.now += 100
        self.app.reconnect_once()
        self.assertEqual(self.app.active, B)
        self.assertEqual(self.app.hid.calls, [])

    def test_manual_forget_cancels_retry_before_removing_bond(self):
        self.disconnected()
        self.app.getkey = lambda fd: "y"
        self.app.bus = types.SimpleNamespace(get_object=lambda *args: object())
        adapter = types.SimpleNamespace(RemoveDevice=lambda path: None)
        with patch.object(pb.dbus, "Interface", return_value=adapter, create=True):
            self.app.forget({"addr": A, "name": "fixture", "path": "/fixture"}, None)
        self.now += 20
        self.app.reconnect_once()
        self.assertIsNone(self.app.active)
        self.assertEqual(self.app.hid.calls, [])
        self.assertFalse(self.app.reconnect_enabled)

    def test_stopping_never_reconnects(self):
        self.disconnected()
        self.app.stop.set()
        self.now += 20
        self.app.reconnect_once()
        self.assertEqual(self.app.hid.calls, [])

    def test_startup_failed_attempt_arms_bounded_retry_for_same_target(self):
        self.app.auto_reconnect()
        self.assertEqual(self.app.hid.calls, [(A, 8)])
        self.assertEqual(self.app.reconnect_plan.target, A)

    def test_startup_cannot_connect_an_unpaired_saved_address(self):
        self.paired = []
        self.app.auto_reconnect()
        self.assertEqual(self.app.hid.calls, [])

    def test_startup_metadata_failure_is_deferred_without_crashing_app(self):
        with patch.object(pb, "bonds", side_effect=RuntimeError("fixture unavailable")):
            self.app.auto_reconnect()
        self.assertEqual(self.app.hid.calls, [])
        self.assertEqual(self.app.reconnect_plan.state, "waiting")

    def test_input_metadata_failure_is_refused_without_sending_input(self):
        with patch.object(pb, "bonds", side_effect=RuntimeError("fixture unavailable")):
            self.assertFalse(self.app.ensure_target())
        self.assertEqual(self.app.hid.calls, [])
        self.assertEqual(self.app.reconnect_plan.state, "waiting")

    def test_manual_use_rearms_exhausted_window_without_repairing(self):
        self.disconnected()
        self.now += 400
        self.app.reconnect_once()
        self.app.use({"addr": A, "name": "fixture"})
        self.assertEqual(self.app.reconnect_plan.state, "waiting")
        self.assertEqual(self.app.reconnect_plan.attempts, 0)
        self.assertEqual(self.app.reconnect_plan.deadline, self.now + 300)

    def test_manual_selection_serializes_with_inflight_retry(self):
        started, finish, selected = threading.Event(), threading.Event(), threading.Event()
        def connect(addr, timeout):
            started.set()
            if not finish.wait(2): raise AssertionError("test retry was not released")
            return False, "fixture"
        self.app.hid.connect_hook = connect
        self.disconnected(); self.now += 1
        retry = threading.Thread(target=self.app.reconnect_once)
        def select():
            self.app.set_active(B); selected.set()
        change = threading.Thread(target=select)
        retry.start()
        try:
            self.assertTrue(started.wait(1))
            change.start()
            self.assertFalse(selected.wait(.05))
        finally:
            finish.set(); retry.join(2)
            if change.ident is not None: change.join(2)
        self.assertFalse(retry.is_alive()); self.assertFalse(change.is_alive())
        self.assertEqual(self.app.hid.calls, [(A, 2.0)])
        self.assertEqual(self.app.active, B)
        self.assertIsNone(self.app.reconnect_plan.target)


if __name__ == "__main__": unittest.main()
