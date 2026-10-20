"""Local-only CLI. There is intentionally no real-hardware option yet."""
import argparse
import json
from pathlib import Path
import sys
from .actions import action_dict, duration_ms, parse_batch, ValidationError
from .controller import Controller, SafetyError
from .protocol import ProtocolError, unique_object
from .simulator import LoopbackTransport, Simulator, SystemClock

MAX_PLAN_BYTES = 8192


def load_plan(path: str):
    with Path(path).open("rb") as file:
        raw = file.read(MAX_PLAN_BYTES + 1)
    if len(raw) > MAX_PLAN_BYTES:
        raise ValidationError("Plan exceeds 8192 bytes")
    try:
        plan = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValidationError("Plan is not valid bounded JSON") from exc
    if not isinstance(plan, dict) or set(plan) != {"actions"}:
        raise ValidationError("Plan must be an object containing actions only")
    return parse_batch(plan["actions"])


def main(argv=None, *, input_fn=input, output=None) -> int:
    output = output or sys.stdout
    parser = argparse.ArgumentParser(description="Digit's local keyboard simulator; never sends real input")
    parser.add_argument("command", choices=("validate", "simulate"))
    parser.add_argument("plan", help="JSON plan file")
    args = parser.parse_args(argv)
    controller = None
    try:
        actions = load_plan(args.plan)
        print(json.dumps({"actions": [action_dict(action) for action in actions]}, indent=2), file=output)
        print(f"Valid: {len(actions)} actions, {sum(duration_ms(a) for a in actions)} ms simulated input", file=output)
        if args.command == "validate":
            return 0
        print("SIMULATOR ONLY. No USB, KVM, target computer, camera, or model is connected.", file=output)
        print("Review every action above. Input could have consequences on a real target.", file=output)
        if input_fn("Type ARM to enable this simulation, or anything else to cancel: ") != "ARM":
            print("Cancelled", file=output)
            return 1
        device = Simulator(SystemClock())
        controller = Controller(LoopbackTransport(device), now=device.clock.now)
        device.local_arm()
        controller.arm("ARM")
        preview = controller.preview(actions)
        print(f"Batch fingerprint: {preview.digest}", file=output)
        confirmation = input_fn(f"Type {preview.confirmation} to run this exact batch: ")
        approval = controller.approve(preview, confirmation)
        controller.execute(approval)
        print("Simulated events:", file=output)
        for event in device.events:
            print(f"  {event.kind}: {json.dumps(event.keys)}", file=output)
        if not controller.stop():
            raise SafetyError("Final release was not confirmed")
        print("Completed in simulator; all simulated keys released and device disarmed", file=output)
        return 0
    except (OSError, ValueError, SafetyError) as exc:
        print(f"Stopped: {exc}", file=output)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("Cancelled; attempting release and disarm", file=output)
        return 130
    finally:
        if controller is not None:
            controller.close()
