from __future__ import annotations

import json
import unittest

from week5.new_implementation.online.provider import CallBudget, ProviderFailure, call_structured
from week5.new_implementation.online.reference import (
    CurrentReference,
    Employee,
    ReadyReference,
    ReferenceResponse,
    bind_references,
)
from week5.new_implementation.online.query import EvidenceSpan


class PlanningBoundaryTests(unittest.TestCase):
    def test_provider_has_zero_transport_retries_and_enforces_budget(self):
        captured = {}

        class Choice:
            class Message:
                content = json.dumps({"decision": {"status": "ready", "references": []}})

            message = Message()

        class Response:
            choices = [Choice()]

        def complete(**kwargs):
            captured.update(kwargs)
            return Response()

        budget = CallBudget(limit=1)
        call_structured(
            stage="reference",
            model="test",
            system="test",
            payload={},
            response_model=ReferenceResponse,
            budget=budget,
            timeout=1,
            max_output_tokens=100,
            completion_fn=complete,
        )
        self.assertEqual(captured["num_retries"], 0)
        with self.assertRaises(ProviderFailure):
            budget.claim("planner")

    def test_exact_name_binds_without_confirmation(self):
        question = "show Wail Ali attendance"
        response = ReferenceResponse(
            decision=ReadyReference(
                references=(
                    CurrentReference(
                        key="employee",
                        mention=EvidenceSpan(start=5, end=13, text="Wail Ali"),
                    ),
                )
            )
        )
        bound = bind_references(question, response, (Employee(employee_id="A1", name="Wail Ali"),))
        self.assertEqual(bound.employee_ids, ("A1",))
        self.assertIsNone(bound.confirmation)

    def test_unique_fuzzy_candidate_always_requires_confirmation(self):
        question = "show Wael Ali attendance"
        response = ReferenceResponse(
            decision=ReadyReference(
                references=(
                    CurrentReference(
                        key="employee",
                        mention=EvidenceSpan(start=5, end=13, text="Wael Ali"),
                    ),
                )
            )
        )
        bound = bind_references(question, response, (Employee(employee_id="A1", name="Wail Ali"),))
        self.assertEqual(bound.employee_ids, ())
        self.assertEqual(bound.confirmation.employee_id, "A1")

    def test_tied_fuzzy_candidates_fail_closed(self):
        question = "show Fare attendance"
        response = ReferenceResponse(
            decision=ReadyReference(
                references=(
                    CurrentReference(
                        key="employee",
                        mention=EvidenceSpan(start=5, end=9, text="Fare"),
                    ),
                )
            )
        )
        bound = bind_references(
            question,
            response,
            (
                Employee(employee_id="A1", name="Fares"),
                Employee(employee_id="A2", name="Fared"),
            ),
        )
        self.assertTrue(bound.ambiguous)


if __name__ == "__main__":
    unittest.main()
