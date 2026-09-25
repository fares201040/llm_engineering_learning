from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from week5.new_evaluation import acceptance
from week5.new_implementation.online.pipeline import Answered, Unsupported
from week5.new_implementation.online.state import ConversationState, VerifiedTurn


def answered_for(request):
    previous = request.state
    turn = VerifiedTurn(
        turn_id=f"turn-{len(previous.verified_turns) + 1}",
        original_question=request.question,
        rewritten_request=f"Request: {request.question}",
        answer="Synthetic Employee worked 4 days.",
        locale="en",
        executed_sql="SELECT 4 AS worked_days",
        result={"rows": [{"worked_days": 4}]},
    )
    state = previous.model_copy(
        update={"verified_turns": previous.verified_turns + (turn,)}
    )
    return Answered(reply=turn.answer, state=state)


class AcceptanceCliTests(unittest.TestCase):
    def test_report_write_falls_back_when_windows_denies_atomic_replace(self):
        output = Path("acceptance.json")
        with (
            patch.object(Path, "write_text") as write_text,
            patch.object(Path, "replace", side_effect=PermissionError),
            patch.object(Path, "unlink") as unlink,
        ):
            acceptance._write(output, {"status": "running"})

        self.assertEqual(write_text.call_count, 2)
        self.assertEqual(write_text.call_args_list[0], write_text.call_args_list[1])
        unlink.assert_called_once_with(missing_ok=True)

    def test_all_named_scenarios_are_consolidated(self):
        self.assertEqual(
            set(acceptance.SCENARIOS),
            {"wail", "faris", "generic-subject", "long"},
        )

    def test_each_scenario_step_declares_its_expected_outcome(self):
        self.assertTrue(
            all(
                step.expected_outcome in {"answered", "clarification", "unsupported"}
                for steps in acceptance.SCENARIOS.values()
                for step in steps
            )
        )
        payroll_step = acceptance.SCENARIOS["long"][5]
        self.assertEqual(payroll_step.expected_outcome, "unsupported")
        self.assertEqual(payroll_step.expected_capability, "schema")

    def test_long_answered_steps_declare_semantic_answer_facts(self):
        answered_steps = [
            step
            for step in acceptance.SCENARIOS["long"]
            if step.expected_outcome == "answered"
        ]

        self.assertTrue(answered_steps)
        self.assertTrue(
            all(
                step.expected_answer_groups and step.expected_result_groups
                for step in answered_steps
            )
        )

    def test_turn_without_semantic_oracle_is_rejected_before_model_call(self):
        with patch.object(acceptance, "run_turn") as run:
            with self.assertRaisesRegex(ValueError, "no semantic answer expectations"):
                acceptance.run_scenario_turn("wail", 0)

        run.assert_not_called()

    def test_step_runner_executes_one_turn_and_checks_verified_state(self):
        step = acceptance.AcceptanceStep(
            "Synthetic acceptance question.",
            "answered",
            expected_answer_groups=(("synthetic employee",), ("4 days",)),
            expected_result_groups=(("4",),),
        )
        with (
            patch.dict(acceptance.SCENARIOS, {"long": (step,)}),
            patch.object(acceptance, "run_turn", side_effect=answered_for) as run,
        ):
            record, state, history = acceptance.run_scenario_turn("long", 0)

        self.assertEqual(run.call_count, 1)
        self.assertTrue(record["passed"])
        self.assertTrue(record["state_continuity_ok"])
        self.assertEqual(record["outcome"], "answered")
        self.assertEqual(len(state.verified_turns), 1)
        self.assertEqual(len(history), 2)

    def test_unexpected_outcome_does_not_pass_a_scenario_turn(self):
        previous = ConversationState(session_id="acceptance-long")
        with patch.object(
            acceptance,
            "run_turn",
            return_value=Unsupported(
                reply="unsupported", state=previous, capability="schema"
            ),
        ):
            record, state, _history = acceptance.run_scenario_turn("long", 0)

        self.assertFalse(record["passed"])
        self.assertFalse(record["outcome_ok"])
        self.assertTrue(record["state_continuity_ok"])
        self.assertEqual(state, previous)

    def test_answer_missing_an_expected_fact_does_not_pass(self):
        step = acceptance.AcceptanceStep(
            "Count synthetic work days.",
            "answered",
            expected_answer_groups=(("5",),),
            expected_result_groups=(("4",),),
        )
        previous = ConversationState(session_id="acceptance-long")
        outcome = answered_for(
            type("Request", (), {"state": previous, "question": step.question})()
        )
        with (
            patch.dict(acceptance.SCENARIOS, {"long": (step,)}),
            patch.object(acceptance, "run_turn", return_value=outcome),
        ):
            record, _state, _history = acceptance.run_scenario_turn("long", 0)

        self.assertTrue(record["outcome_ok"])
        self.assertTrue(record["state_continuity_ok"])
        self.assertFalse(record["semantic_ok"])
        self.assertFalse(record["passed"])
        self.assertEqual(record["missing_answer_facts"], ["5"])

    def test_answer_fact_matching_uses_word_boundaries_and_is_case_insensitive(self):
        step = acceptance.AcceptanceStep(
            "Summarize synthetic work days.",
            "answered",
            expected_answer_groups=(("engineering",), ("5",)),
            expected_result_groups=(("4",),),
        )
        previous = ConversationState(session_id="acceptance-long")
        base = answered_for(
            type("Request", (), {"state": previous, "question": step.question})()
        )
        turn = base.state.verified_turns[-1].model_copy(
            update={"answer": "ENGINEERING had 5 days."}
        )
        outcome = Answered(
            reply=turn.answer,
            state=base.state.model_copy(update={"verified_turns": (turn,)}),
        )
        with (
            patch.dict(acceptance.SCENARIOS, {"long": (step,)}),
            patch.object(acceptance, "run_turn", return_value=outcome),
        ):
            record, _state, _history = acceptance.run_scenario_turn("long", 0)

        self.assertTrue(record["semantic_ok"])
        self.assertEqual(record["missing_answer_facts"], [])

    def test_answered_turn_with_wrong_database_value_does_not_pass(self):
        step = acceptance.AcceptanceStep(
            "Count synthetic work days.",
            "answered",
            expected_answer_groups=(("synthetic employee",), ("4 days",)),
            expected_result_groups=(("5",),),
        )
        previous = ConversationState(session_id="acceptance-long")
        outcome = answered_for(
            type("Request", (), {"state": previous, "question": step.question})()
        )
        with (
            patch.dict(acceptance.SCENARIOS, {"long": (step,)}),
            patch.object(acceptance, "run_turn", return_value=outcome),
        ):
            record, _state, _history = acceptance.run_scenario_turn("long", 0)

        self.assertTrue(record["outcome_ok"])
        self.assertFalse(record["semantic_ok"])
        self.assertEqual(record["missing_result_facts"], ["5"])
        self.assertFalse(record["passed"])

    def test_answered_turn_without_state_publication_does_not_pass(self):
        previous = ConversationState(session_id="acceptance-long")
        with patch.object(
            acceptance,
            "run_turn",
            return_value=Answered(reply="An answer", state=previous),
        ):
            record, state, _history = acceptance.run_scenario_turn("long", 0)

        self.assertTrue(record["outcome_ok"])
        self.assertFalse(record["state_continuity_ok"])
        self.assertFalse(record["passed"])
        self.assertEqual(state, previous)

    def test_expected_schema_rejection_requires_the_schema_capability(self):
        previous = ConversationState(session_id="acceptance-long")
        with patch.object(
            acceptance,
            "run_turn",
            return_value=Unsupported(
                reply="unsupported", state=previous, capability="malformed_value"
            ),
        ):
            record, _state, _history = acceptance.run_scenario_turn(
                "long", 5, state=previous
            )

        self.assertFalse(record["outcome_ok"])
        self.assertFalse(record["passed"])

    def test_step_cli_checkpoints_then_resumes_exactly_one_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "long.json"
            steps = (
                acceptance.AcceptanceStep(
                    "First synthetic turn.",
                    "answered",
                    expected_answer_groups=(("synthetic employee",), ("4 days",)),
                    expected_result_groups=(("4",),),
                ),
                acceptance.AcceptanceStep(
                    "Second synthetic turn.",
                    "answered",
                    expected_answer_groups=(("synthetic employee",), ("4 days",)),
                    expected_result_groups=(("4",),),
                ),
            )
            with (
                patch.dict(acceptance.SCENARIOS, {"long": steps}),
                patch.object(acceptance, "run_turn", side_effect=answered_for),
            ):
                self.assertEqual(
                    acceptance.main(["long", "--turn", "1", "--output", str(output)]),
                    0,
                )
                first = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(first["status"], "running")
                self.assertEqual(first["completed_turns"], 1)
                self.assertNotIn("passed", first)

                self.assertEqual(
                    acceptance.main(["long", "--turn", "2", "--output", str(output)]),
                    0,
                )

            second = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(second["completed_turns"], 2)
            self.assertEqual(len(second["history"]), 4)
            self.assertEqual(len(second["state"]["verified_turns"]), 2)
            self.assertEqual(second["turns"][-1]["turn_number"], 2)

    def test_step_cli_rejects_skipped_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "long.json"
            with patch.object(acceptance, "run_turn") as run:
                with self.assertRaises(SystemExit):
                    acceptance.main(["long", "--turn", "2", "--output", str(output)])

        run.assert_not_called()

    def test_partial_report_is_not_marked_complete_or_successful(self):
        writes = []
        step = acceptance.AcceptanceStep(
            "Synthetic interruption case.",
            "answered",
            expected_answer_groups=(("synthetic",),),
            expected_result_groups=(("4",),),
        )
        with (
            patch.dict(acceptance.SCENARIOS, {"wail": (step,)}),
            patch.object(
                acceptance,
                "run_turn",
                side_effect=RuntimeError("interrupted"),
            ),
            patch.object(
                acceptance,
                "_write",
                side_effect=lambda _path, payload: writes.append(dict(payload)),
            ),
        ):
            output = Path("acceptance.json")
            with self.assertRaises(RuntimeError):
                acceptance.main(["wail", "--turn", "1", "--output", str(output)])
            report = writes[-1]
            self.assertEqual(report["status"], "running")
            self.assertNotIn("passed", report)


if __name__ == "__main__":
    unittest.main()
