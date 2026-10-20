import io
from pathlib import Path
import tempfile
import unittest
from digit.cli import load_plan, main
from digit.actions import ValidationError
from digit.protocol import ProtocolError

EXAMPLE = str(Path(__file__).resolve().parents[1] / "examples" / "hello.json")


class CLITests(unittest.TestCase):
    def test_validate(self):
        output = io.StringIO()
        self.assertEqual(main(["validate", EXAMPLE], output=output), 0)
        self.assertIn("Valid: 2 actions", output.getvalue())

    def test_simulate_with_explicit_confirmations(self):
        def operator(prompt):
            if prompt.startswith("Type ARM"):
                return "ARM"
            return prompt.removeprefix("Type ").split(" to run")[0]
        output = io.StringIO()
        self.assertEqual(main(["simulate", EXAMPLE], input_fn=operator, output=output), 0)
        self.assertIn("all simulated keys released", output.getvalue())

    def test_cancel(self):
        output = io.StringIO()
        self.assertEqual(main(["simulate", EXAMPLE], input_fn=lambda _: "no", output=output), 1)
        self.assertNotIn("Simulated events", output.getvalue())

    def test_wrong_approval(self):
        answers = iter(["ARM", "APPROVE wrong"])
        output = io.StringIO()
        self.assertEqual(main(["simulate", EXAMPLE], input_fn=lambda _: next(answers), output=output), 2)
        self.assertNotIn("Simulated events", output.getvalue())

    def test_interrupt_after_arm(self):
        def operator(prompt):
            if prompt.startswith("Type ARM"):
                return "ARM"
            raise KeyboardInterrupt()
        self.assertEqual(main(["simulate", EXAMPLE], input_fn=operator, output=io.StringIO()), 130)

    def test_plan_bounds_and_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for raw in [b"x" * 8193, b'{"actions":[],"actions":[]}', b'[]', b'{']:
                path.write_bytes(raw)
                with self.assertRaises((ValidationError, ProtocolError)):
                    load_plan(str(path))
