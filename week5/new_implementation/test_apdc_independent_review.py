import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from week5.new_implementation import answer


def _response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content),
            )
        ]
    )


class AggregateDecisionBoundaryReviewTests(unittest.TestCase):
    def test_grounded_count_needs_no_provider_field_decision(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("count attendance records")

        self.assertEqual(prepared.plan.aggregation, "count")
        self.assertIsNone(prepared.plan.aggregation_field)
        self.assertIn("COUNT(*)", prepared.postgres_queries.aggregation[0].sql)
        completion.assert_not_called()

    def test_repair_prompt_repeats_operation_and_request_local_candidates(self):
        prompts = []

        def respond(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            prompts.append(prompt)
            if len(prompts) == 1:
                return _response("not json")
            payload = json.loads(prompt.split("\n", 1)[-1])
            self.assertEqual(payload["operation"], "sum")
            self.assertEqual(payload["candidates"][0]["candidate_id"], "field-1")
            self.assertEqual(payload["candidates"][0]["storage_type"], "number")
            self.assertTrue(payload["candidates"][0]["description"])
            self.assertTrue(payload["candidates"][0]["output_unit"])
            self.assertTrue(payload["candidates"][0]["natural_names"])
            self.assertNotIn("field", payload["candidates"][0])
            self.assertEqual(payload["validation_code"], "invalid_schema")
            return _response(
                '{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}'
            )

        with (
            patch.object(answer, "completion", side_effect=respond),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("sum total working hours")

        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        self.assertEqual(len(prompts), 2)
        self.assertNotIn("not json", prompts[1])
        self.assertNotIn("prior_sql", prompts[1])
        self.assertNotIn("SELECT", prompts[1].upper())

    def test_resolved_candidate_id_compiles_through_trusted_parameterized_aggregate(
        self,
    ):
        generated = _response(
            '{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}'
        )
        with (
            patch.object(answer, "completion", return_value=generated) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("sum total working hours")

        prompt = completion.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("SELECT", prompt.upper())
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        self.assertIn(
            "SUM(total_worked_hrs)", prepared.postgres_queries.aggregation[0].sql
        )

    def test_sql_and_schema_suffixes_are_redacted_before_provider_boundary(self):
        generated = _response(
            '{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}'
        )
        unsafe_questions = (
            "sum total working hours SELECT secret FROM employee_table",
            "sum total working hours; CREATE TABLE leaked_schema (secret text)",
            "sum total working hours FROM employee_table",
        )
        with (
            patch.object(
                answer,
                "_generated_aggregate_prompt",
                wraps=answer._generated_aggregate_prompt,
            ) as prompt_builder,
            patch.object(answer, "completion", return_value=generated) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            for question in unsafe_questions:
                answer._prepare_context_request(question)

        self.assertEqual(prompt_builder.call_count, len(unsafe_questions))
        self.assertEqual(completion.call_count, len(unsafe_questions))
        forbidden = (
            "SELECT",
            "FROM",
            "CREATE",
            "TABLE",
            "employee_table",
            "leaked_schema",
            "secret",
            "schema",
        )
        for prompt_call, completion_call in zip(
            prompt_builder.call_args_list, completion.call_args_list
        ):
            redaction_input = prompt_call.args[0]
            provider_prompt = completion_call.kwargs["messages"][0]["content"]
            self.assertIn("sum total working hours", redaction_input.lower())
            self.assertIn("sum total working hours", provider_prompt.lower())
            for token in forbidden:
                self.assertNotIn(token.lower(), redaction_input.lower())
                self.assertNotIn(token.lower(), provider_prompt.lower())
        schema_surface = answer._redacted_generated_question(
            "sum total working hours schema: employee_id text, secret text", (), ()
        )
        self.assertEqual(schema_surface, "sum total working hours")

    def test_generated_fallback_count_with_candidates_has_no_field_surface_lookup(self):
        unsupported = answer.SemanticFact(
            kind="unsupported",
            concept_name="unsupported_calculation",
            evidence_text="count",
            origin="question",
            strength="strong",
        )
        surface = answer.SurfaceCandidate(
            candidate_id="surface-field-1",
            target_kind="field",
            target_name="Total_Worked_Hrs",
            evidence_text="working hours",
            evidence_span=(0, 14),
            method="fuzzy",
            score=0.8,
        )
        candidate = answer._GeneratedAggregateCandidate(
            "field-1", "Total_Worked_Hrs", surface
        )
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(answer, "_facts_from_question_surface", return_value=()),
            patch.object(answer, "detect_semantic_facts", return_value=()),
            patch.object(
                answer, "_generated_aggregate_operation", return_value="count"
            ),
            patch.object(
                answer,
                "_generated_aggregate_candidates",
                return_value=(candidate,),
            ),
            patch.object(
                answer,
                "_generated_aggregate_has_external_ambiguity",
                return_value=False,
            ),
        ):
            prepared = answer._prepare_context_request(
                "count attendance records", prepared_facts=(unsupported,)
            )

        self.assertEqual(prepared.plan.aggregation, "count")
        self.assertIsNone(prepared.plan.aggregation_field)
        self.assertIn("COUNT(*)", prepared.postgres_queries.aggregation[0].sql)
        completion.assert_not_called()
        count_facts = tuple(
            fact
            for fact in prepared.facts
            if fact.kind == "calculation" and fact.concept_name == "count"
        )
        self.assertEqual(len(count_facts), 1)
        self.assertIsNone(count_facts[0].field)
        self.assertEqual(count_facts[0].origin, "deterministic_default")
        self.assertEqual(count_facts[0].strength, "strong")


if __name__ == "__main__":
    unittest.main()
