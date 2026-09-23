from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from week5.new_evaluation import acceptance


class AcceptanceCliTests(unittest.TestCase):
    def test_all_named_scenarios_are_consolidated(self):
        self.assertEqual(set(acceptance.SCENARIOS), {"wail", "faris", "long"})

    def test_partial_report_is_not_marked_complete_or_successful(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            acceptance,
            "run_scenario",
            side_effect=RuntimeError("interrupted"),
        ):
            output = Path(directory) / "acceptance.json"
            with self.assertRaises(RuntimeError):
                acceptance.main(["wail", "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "running")
            self.assertNotIn("passed", report)


if __name__ == "__main__":
    unittest.main()
