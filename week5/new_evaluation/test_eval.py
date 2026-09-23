from __future__ import annotations

import unittest

from week5.new_evaluation.eval import canonical_expression, evaluate_outcome
from week5.new_evaluation.test import TestQuestion, load_tests
from week5.new_implementation.online.pipeline import Answered, Clarification, Unsupported
from week5.new_implementation.online.query import (
    All,
    AggregateMeasure,
    AggregateOutput,
    Any,
    Condition,
    EvidenceSpan,
    Not,
    Ordering,
    OutputComponent,
)
from week5.new_implementation.online.reference import PendingEmployeeConfirmation
from week5.new_implementation.online.state import ConversationState, VerifiedTurn


class EvaluatorTests(unittest.TestCase):
    def test_complete_behavior_corpus_has_311_cases(self):
        self.assertEqual(len(load_tests()), 311)

    def test_nested_all_any_not_is_preserved(self):
        expression = All(
            items=(
                Condition(field_id="date", operator="gte", value="2026-09-01"),
                Any(
                    items=(
                        Condition(field_id="status", operator="eq", value="Authorized"),
                        Not(item=Condition(field_id="exception", operator="eq", value="Absent")),
                    )
                ),
            )
        )
        rendered = canonical_expression(expression)
        self.assertEqual(rendered["items"][1]["items"][1]["kind"], "not")

    def test_grouped_values_and_record_ids_are_scored(self):
        turn = VerifiedTurn(
            turn_id="t1",
            question="show records",
            answer="Engineering",
            locale="en",
            components=(
                OutputComponent(
                    component_key="rows",
                    evidence=EvidenceSpan(start=0, end=4, text="show"),
                    output=AggregateOutput(
                        measures=(AggregateMeasure(output_id="records", function="count"),),
                        group_by=("department",),
                        ordering=(Ordering(output_id="records", direction="desc"),),
                        limit=10,
                    ),
                ),
            ),
            result={"rows": [{"department": "Engineering", "record_id": "r1"}]},
        )
        state = ConversationState(verified_turns=(turn,))
        outcome = Answered(reply="Engineering", state=state)
        case = TestQuestion(
            question="show records",
            keywords=[],
            reference_answer="Engineering",
            category="rows",
            expected_record_ids=["r1"],
            expected_group_values=[{"department": "Engineering", "record_id": "r1"}],
        )
        result = evaluate_outcome(case, outcome)
        self.assertTrue(result.record_ids_ok)
        self.assertTrue(result.group_values_ok)

    def test_confirmation_and_unsupported_capability_are_explicit(self):
        pending = PendingEmployeeConfirmation(
            original_question="show Fare",
            mention="Fare",
            employee_id="A1",
            employee_name="Faris",
        )
        clarification = Clarification(
            reply="Did you mean Faris (A1)?",
            state=ConversationState(pending_employee_confirmation=pending),
            reason="employee_confirmation",
        )
        clarify_case = TestQuestion(
            question="show Fare",
            keywords=[],
            reference_answer="",
            category="clarification",
            expected_clarification_ids=["A1"],
        )
        self.assertTrue(evaluate_outcome(clarify_case, clarification).clarification_ok)
        unsupported = Unsupported(
            reply="unsupported",
            state=ConversationState(),
            capability="window",
        )
        unsupported_case = TestQuestion(
            question="running total",
            keywords=[],
            reference_answer="",
            category="unsupported",
            expected_unsupported_capabilities=["window"],
        )
        self.assertTrue(evaluate_outcome(unsupported_case, unsupported).unsupported_capabilities_ok)


if __name__ == "__main__":
    unittest.main()
