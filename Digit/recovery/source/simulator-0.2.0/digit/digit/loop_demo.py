"""One explicit synthetic observation/action cycle with a simulated operator."""
import json
from digit_observation import Calibration, Point
from .actions import action_dict
from .loop_fakes import make_fake_loop


def run_demo() -> dict:
    bundle = make_fake_loop()
    loop = bundle.loop
    try:
        before = loop.observe()
        size = before.metadata.dimensions
        calibration = Calibration("synthetic-calibration-1", before.metadata,
            before.metadata.target_dimensions,
            (Point(0, 0), Point(size.width - 1, 0),
             Point(size.width - 1, size.height - 1), Point(0, size.height - 1)), 0.0)
        loop.calibrate(calibration)
        # Explicit fixture operator calls. Never reuse this auto-approval demo
        # pattern in a real input adapter or expose these methods to a model.
        bundle.device.local_arm()
        loop.arm("ARM")
        proposal = loop.propose("Type A")
        approval = loop.approve(proposal, proposal.confirmation)
        receipt = loop.dispatch(approval)
        state_after_dispatch = loop.state
        result = loop.observe_after(receipt)
        return {
            "mode": "synthetic fixtures and simulated operator only",
            "target": before.metadata.binding.target_id,
            "before_frame": before.metadata.frame_id.sequence,
            "before_payload": json.loads(before.payload),
            "proposal": [action_dict(action) for action in proposal.actions],
            "approval_bound_to": proposal.observation.as_dict(),
            "receipt_status": receipt.status,
            "state_after_dispatch": state_after_dispatch,
            "after_frame": result.frame.metadata.frame_id.sequence,
            "after_payload": json.loads(result.frame.payload),
            "outcome": result.outcome,
            "executed_batches": bundle.device.executed_batches,
        }
    finally:
        loop.close()


def main() -> None:
    print(json.dumps(run_demo(), indent=2))


if __name__ == "__main__":
    main()
