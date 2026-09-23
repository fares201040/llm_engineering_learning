from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from week5.new_implementation.online.audit import audit_with_one_repair
from week5.new_implementation.online.planner import PlannerResponse, ReadyPlan
from week5.new_implementation.online.provider import CallBudget, ProviderFailure, call_structured
from week5.new_implementation.online.reference import (
    CurrentReference,
    Employee,
    EmployeeOption,
    ReadyReference,
    ReferenceResponse,
    bind_references,
    search_employee_candidates,
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
        self.assertEqual(
            bound.confirmation.options,
            (EmployeeOption(employee_id="A1", employee_name="Wail Ali"),),
        )

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
        self.assertEqual(bound.unresolved_mention, "Fare")

    def test_transliterated_arabic_candidate_requires_confirmation(self):
        question = "show Waleed Ali attendance"
        response = ReferenceResponse(
            decision=ReadyReference(
                references=(
                    CurrentReference(
                        key="employee",
                        mention=EvidenceSpan(start=5, end=15, text="Waleed Ali"),
                    ),
                )
            )
        )
        bound = bind_references(
            question,
            response,
            (Employee(employee_id="A7", name="وليد علي"),),
        )
        self.assertEqual(bound.confirmation.options[0].employee_id, "A7")

    @patch("week5.new_implementation.chroma_client.create_chroma_client")
    @patch("openai.OpenAI")
    def test_semantic_candidates_are_scoped_and_cross_checked_with_postgres(self, openai, chroma):
        openai.return_value.embeddings.create.return_value.data = [MagicMock(embedding=[0.1, 0.2])]
        collection = chroma.return_value.get_collection.return_value
        collection.query.return_value = {
            "metadatas": [[
                {"Employee_ID": "A2", "Name": "Faris Ahmed"},
                {"Employee_ID": "A9", "Name": "Injected Name"},
                {"Employee_ID": "A2", "Name": "Faris Ahmed"},
            ]],
            "distances": [[0.1, 0.2, 0.3]],
        }

        options = search_employee_candidates(
            "Fares",
            (Employee(employee_id="A2", name="Faris Ahmed"),),
            embedding_model="embedding-test",
            collection_name="docs",
            allowed_employee_ids=("A2",),
        )

        self.assertEqual(options, (EmployeeOption(employee_id="A2", employee_name="Faris Ahmed"),))
        self.assertEqual(
            collection.query.call_args.kwargs["where"],
            {"$and": [{"domain": "attendance"}, {"Employee_ID": {"$in": ["A2"]}}]},
        )

    @patch(
        "week5.new_implementation.online.audit.request_audit",
        side_effect=ProviderFailure("audit", "provider_unavailable", "offline"),
    )
    def test_initial_audit_unavailability_keeps_only_the_validated_primary(self, _audit):
        primary = PlannerResponse(
            decision=ReadyPlan(
                locale="en",
                relationship="new",
                narrative_search="attendance pattern",
            )
        )
        validated = []
        result = audit_with_one_repair(
            initial=primary,
            planner_args={},
            audit_args={},
            candidate_validator=lambda candidate: validated.append(candidate),
        )
        self.assertIs(result, primary)
        self.assertEqual(validated, [])


if __name__ == "__main__":
    unittest.main()
