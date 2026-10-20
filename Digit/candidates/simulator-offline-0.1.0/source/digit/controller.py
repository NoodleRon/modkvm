"""Single-owner controller. Approval functions belong in trusted operator UI.

This Python API is an application boundary, not a sandbox. Never give a model
Python execution access, an approval token, or an unrestricted KVM client.
"""
from dataclasses import dataclass
import hashlib
import secrets
import threading
import time
from .actions import Action, action_dict, validate_batch
from .protocol import Frame, ProtocolError, canonical, decode, encode
from .transport import Transport

PREVIEW_TTL = 45.0


class SafetyError(RuntimeError):
    pass


class ExecutionUncertain(SafetyError):
    """Input may have happened. Inspect the target; never automatically replay."""


@dataclass(frozen=True)
class Preview:
    session: str
    actions: tuple[Action, ...]
    digest: str
    expires_at: float

    @property
    def confirmation(self) -> str:
        return f"APPROVE {self.digest}"


@dataclass(frozen=True)
class Approval:
    preview: Preview
    nonce: str


class Controller:
    def __init__(self, transport: Transport, now=time.monotonic) -> None:
        self.transport = transport
        self.now = now
        self.session = secrets.token_hex(16)
        self.sequence = 0
        self.state = "disarmed"
        self._pending = None
        self._approval = None
        self._lock = threading.RLock()

    def _request(self, kind: str, body: dict) -> Frame:
        self.sequence += 1
        request = Frame(self.session, self.sequence, kind, body)
        reply = decode(self.transport.exchange(encode(request), timeout=2.0))
        if reply.kind != "ack" or reply.session != self.session or reply.seq != self.sequence:
            raise ProtocolError("Acknowledgment does not match the request")
        if set(reply.body) != {"status", "detail", "state", "action_count"}:
            raise ProtocolError("Malformed acknowledgment fields")
        if reply.body["status"] not in ("ok", "rejected") or reply.body["state"] not in ("armed", "disarmed"):
            raise ProtocolError("Malformed acknowledgment status")
        if not isinstance(reply.body["detail"], str) or type(reply.body["action_count"]) is not int:
            raise ProtocolError("Malformed acknowledgment details")
        if reply.body["status"] != "ok":
            raise SafetyError(reply.body["detail"])
        return reply

    def _invalidate(self) -> None:
        self._pending = None
        self._approval = None

    def _best_effort_release(self) -> bool:
        try:
            reply = self._request("release_all", {})
            return reply.body["state"] == "disarmed"
        except Exception:
            return False

    def _fail_closed(self) -> None:
        """Revoke permission before cleanup that may itself be interrupted."""
        self._invalidate()
        if self.state == "closed":
            return
        self.state = "fault"
        self._best_effort_release()

    def arm(self, confirmation: str) -> None:
        with self._lock:
            if confirmation != "ARM":
                raise SafetyError("Explicit operator ARM confirmation is required")
            if self.state != "disarmed":
                raise SafetyError("Stop before arming a new session")
            self._invalidate()
            self.session = secrets.token_hex(16)
            self.sequence = 0
            try:
                reply = self._request("arm", {})
                if reply.body["state"] != "armed":
                    raise ProtocolError("Device did not confirm armed state")
                self.state = "armed"
            except BaseException:
                self.state = "fault"
                self._best_effort_release()
                raise

    def preview(self, actions: tuple[Action, ...]) -> Preview:
        with self._lock:
            # Starting a replacement revokes the old plan even if the new
            # actions fail validation before a new Preview can be constructed.
            self._invalidate()
            try:
                if self.state != "armed":
                    raise SafetyError("Arm before preparing an actionable preview")
                actions = validate_batch(actions)
                digest = hashlib.sha256(canonical({
                    "session": self.session, "nonce": secrets.token_hex(16),
                    "actions": [action_dict(action) for action in actions],
                })).hexdigest()
                preview = Preview(self.session, actions, digest, self.now() + PREVIEW_TTL)
                self._pending = preview
                return preview
            except BaseException:
                self._fail_closed()
                raise

    def approve(self, preview: Preview, confirmation: str) -> Approval:
        with self._lock:
            # A failed/rejected confirmation must not leave an older token live.
            self._approval = None
            try:
                if self.state != "armed" or preview is not self._pending or self.now() >= preview.expires_at:
                    raise SafetyError("Preview is stale, expired, or not owned by this controller")
                if confirmation != preview.confirmation:
                    raise SafetyError("Approval must exactly match the displayed batch fingerprint")
                approval = Approval(preview, secrets.token_hex(16))
                self._approval = approval
                return approval
            except BaseException:
                self._fail_closed()
                raise

    def execute(self, approval: Approval) -> Frame:
        with self._lock:
            if not isinstance(approval, Approval) or approval is not self._approval:
                raise SafetyError("An unused operator approval is required")
            preview = approval.preview
            if self.state != "armed" or preview is not self._pending or preview.session != self.session:
                raise SafetyError("Approval does not match the active session")
            if self.now() >= preview.expires_at:
                self._fail_closed()
                raise SafetyError("Approval has expired")
            # Consume permission BEFORE I/O, including failed or timed-out I/O.
            self._invalidate()
            try:
                reply = self._request("execute", {"actions": [action_dict(a) for a in preview.actions]})
                if reply.body["state"] != "armed" or reply.body["action_count"] != len(preview.actions):
                    raise ProtocolError("Device did not confirm the complete approved batch")
                return reply
            except BaseException as exc:
                self.state = "fault"
                self._best_effort_release()
                if not isinstance(exc, Exception):
                    raise
                raise ExecutionUncertain("Execution was not confirmed. Stop, inspect the target, and create a new approved batch; do not replay.") from exc

    def stop(self) -> bool:
        """Invalidate approvals, release all, and disarm. False means unconfirmed."""
        with self._lock:
            self._invalidate()
            # An interrupt or process-exit exception cannot leave local state
            # armed while release delivery/acknowledgment is unknown.
            self.state = "fault"
            confirmed = False
            try:
                confirmed = self._best_effort_release()
            finally:
                self.state = "disarmed" if confirmed else "fault"
            return confirmed

    def close(self) -> bool:
        with self._lock:
            confirmed = False
            stop_error = None
            try:
                confirmed = self.stop()
            except BaseException as exc:
                stop_error = exc
            finally:
                # This finally includes stop(), so an interrupted release never
                # skips disconnect. Do not hide the original termination event
                # if the cleanup path also raises.
                try:
                    self.transport.close()
                except BaseException as close_error:
                    if stop_error is not None:
                        raise stop_error from close_error
                    raise
                finally:
                    self._invalidate()
                    self.state = "closed"
            if stop_error is not None:
                raise stop_error
            return confirmed
