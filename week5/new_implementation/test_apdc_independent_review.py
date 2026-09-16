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


if __name__ == "__main__":
    unittest.main()
