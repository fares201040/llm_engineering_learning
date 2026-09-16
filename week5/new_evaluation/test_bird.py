import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from week5.new_evaluation import eval as evaluation
from week5.new_evaluation.test import TestQuestion
from week5.new_implementation import answer


def bird_case(**overrides):
    values = {
        "question": "How many verified records are there?",
        "keywords": ["records"],
        "reference_answer": "There are two records.",
        "category": "count",
    }
    values.update(overrides)
    return TestQuestion(**values)


class BirdCaseEvaluationTests(unittest.TestCase):
    def test_generated_fallback_runs_through_real_fetch_context_without_private_detail(
        self,
    ):
        question = "how many total working hours for A10001"
        raw_sql = "SELECT SUM(Total_Worked_Hrs) AS value FROM attendance_scope"
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=json.dumps({"status": "ready", "sql": raw_sql})
                    ),
                )
            ]
        )
        case = bird_case(
            question=question,
            expected_matched_count=1,
            expected_calculation={
                "operation": "sum",
                "field": "Total_Worked_Hrs",
                "value": 8.0,
            },
        )
        employee = answer.EmployeeCandidate(employee_id="A10001", name="Private Name")
        calculation = {
            "operation": "sum",
            "field": "Total_Worked_Hrs",
            "value": 8.0,
        }

        with (
            patch.object(answer, "completion", return_value=response) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(answer, "load_employee_directory", return_value=[employee]),
            patch.object(
                answer,
                "execute_exact_postgres",
                return_value=([], calculation, 1),
            ) as execute,
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.status, "evaluated")
        self.assertEqual(result.execution_accuracy, 1.0)
        self.assertEqual(
            result.components,
            {"matched_count": True, "calculation": True},
        )
        completion.assert_called_once()
        execute.assert_called_once()
        plan = execute.call_args.args[0]
        self.assertEqual(plan.aggregation, "sum")
        self.assertEqual(plan.aggregation_field, "Total_Worked_Hrs")
        self.assertEqual(case.question, question)
        rendered = result.model_dump_json()
        for private in (raw_sql, "A10001", "Private Name", "attendance_scope"):
            self.assertNotIn(private, rendered)

    def test_exact_execution_requires_every_applicable_verified_output(self):
        case = bird_case(
            expected_matched_count=2,
            expected_calculation={"value": 2},
            expected_record_ids=["record-1", "record-2"],
        )
        chunks = [SimpleNamespace(metadata={"record_id": "record-1"})]
        plan = SimpleNamespace(model_dump=lambda: {})
        calculation = {"value": 2}

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(chunks, plan, calculation, 2),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.status, "evaluated")
        self.assertEqual(result.execution_accuracy, 0.0)
        self.assertEqual(
            result.components,
            {
                "matched_count": True,
                "calculation": True,
                "record_ids": False,
            },
        )

    def test_case_without_verified_output_contract_is_skipped_without_execution(self):
        case = bird_case()

        with patch.object(evaluation, "fetch_context") as fetch:
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.status, "skipped")
        self.assertIsNone(result.execution_accuracy)
        self.assertEqual(result.components, {})
        self.assertEqual(result.reason, "no_verified_output_contract")
        fetch.assert_not_called()

    def test_zero_matched_count_is_an_eligible_verified_output(self):
        case = bird_case(expected_matched_count=0)
        plan = SimpleNamespace(model_dump=lambda: {})

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=([], plan, None, 0),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 1.0)
        self.assertEqual(result.components, {"matched_count": True})

    def test_execution_failure_propagates_instead_of_becoming_a_scored_zero(self):
        case = bird_case(expected_matched_count=2)

        with patch.object(
            evaluation,
            "fetch_context",
            side_effect=RuntimeError("database unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                evaluation.evaluate_bird_case(case)

    def test_controlled_product_nonexecution_scores_all_applicable_checks_zero(self):
        case = bird_case(
            expected_matched_count=2,
            expected_record_ids=["record-1"],
        )
        controlled_failures = (
            answer.PlanningClarificationRequired(None, ()),
            answer.MissingIntentRequired([]),
            answer.SurfaceMeaningClarificationRequired((), ()),
            answer.SemanticPlanValidationError(()),
            answer.PlanValidationError("private SQL and parameter details"),
        )

        for failure in controlled_failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(
                    evaluation,
                    "fetch_context",
                    side_effect=failure,
                ):
                    result = evaluation.evaluate_bird_case(case)

                self.assertEqual(result.status, "evaluated")
                self.assertEqual(result.execution_accuracy, 0.0)
                self.assertEqual(
                    result.components,
                    {"matched_count": False, "record_ids": False},
                )
                self.assertEqual(result.reason, "controlled_nonexecution")
                self.assertNotIn("private SQL", str(result))

    def test_controlled_nonexecution_case_is_skipped_without_execution(self):
        case = bird_case(
            expected_matched_count=2,
            expected_clarification_outcome="ambiguous",
        )

        with patch.object(evaluation, "fetch_context") as fetch:
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "unsupported_nonexecution_case")
        fetch.assert_not_called()

    def test_none_clarification_outcome_does_not_prevent_verified_execution(self):
        case = bird_case(
            expected_matched_count=0,
            expected_clarification_outcome="none",
        )

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                SimpleNamespace(model_dump=lambda: {}),
                None,
                0,
            ),
        ) as fetch:
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.status, "evaluated")
        self.assertEqual(result.execution_accuracy, 1.0)
        self.assertEqual(result.components, {"matched_count": True})
        fetch.assert_called_once_with(case.question)

    def test_record_identity_rejects_unexpected_extra_records(self):
        case = bird_case(expected_record_ids=["record-1"])
        chunks = [
            SimpleNamespace(metadata={"record_id": "record-1"}),
            SimpleNamespace(metadata={"record_id": "unexpected"}),
        ]

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                chunks,
                SimpleNamespace(model_dump=lambda: {}),
                None,
                2,
            ),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 0.0)
        self.assertEqual(result.components, {"record_ids": False})

    def test_group_values_reject_unexpected_extra_groups(self):
        case = bird_case(expected_group_values=[{"group": ["A"], "value": 2}])
        calculation = {
            "rows": [
                {"group": ["A"], "value": 2},
                {"group": ["unexpected"], "value": 99},
            ]
        }

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                SimpleNamespace(model_dump=lambda: {}),
                calculation,
                2,
            ),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 0.0)
        self.assertEqual(result.components, {"group_values": False})

    def test_group_values_reject_duplicate_expected_group_keys(self):
        case = bird_case(
            expected_group_values=[
                {"group": ["A"], "value": 2},
                {"group": ["A"], "value": 2},
            ]
        )
        calculation = {
            "rows": [
                {"group": ["A"], "value": 2},
                {"group": ["unexpected"], "value": 99},
            ]
        }

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                SimpleNamespace(model_dump=lambda: {}),
                calculation,
                2,
            ),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 0.0)
        self.assertEqual(result.components, {"group_values": False})

    def test_numeric_expected_group_identity_rejects_boolean_actual_identity(self):
        case = bird_case(expected_group_values=[{"group": [1], "value": 2}])
        calculation = {"rows": [{"group": [True], "value": 2}]}

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                SimpleNamespace(model_dump=lambda: {}),
                calculation,
                1,
            ),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 0.0)
        self.assertEqual(result.components, {"group_values": False})

    def test_float_group_identity_requires_exact_numeric_equality(self):
        case = bird_case(expected_group_values=[{"group": [1.0], "value": 2}])
        calculation = {"rows": [{"group": [1.004], "value": 2}]}

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                SimpleNamespace(model_dump=lambda: {}),
                calculation,
                1,
            ),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 0.0)
        self.assertEqual(result.components, {"group_values": False})

    def test_explicit_empty_group_contract_requires_an_empty_rows_list(self):
        case = bird_case(expected_group_values=[])
        calculations = (
            (None, False),
            (0, False),
            ({}, False),
            ({"rows": [{"group": ["A"], "value": 1}]}, False),
            ({"rows": []}, True),
        )

        for calculation, expected in calculations:
            with self.subTest(calculation=calculation):
                with patch.object(
                    evaluation,
                    "fetch_context",
                    return_value=(
                        [],
                        SimpleNamespace(model_dump=lambda: {}),
                        calculation,
                        0,
                    ),
                ):
                    result = evaluation.evaluate_bird_case(case)

                self.assertEqual(result.execution_accuracy, float(expected))
                self.assertEqual(result.components, {"group_values": expected})

    def test_explicit_empty_record_contract_is_evaluated(self):
        case = bird_case(expected_record_ids=[])

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                SimpleNamespace(model_dump=lambda: {}),
                None,
                0,
            ),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.status, "evaluated")
        self.assertEqual(result.execution_accuracy, 1.0)
        self.assertEqual(result.components, {"record_ids": True})

    def test_output_only_bird_scoring_does_not_generate_an_answer(self):
        case = bird_case(expected_calculation={"operation": "count", "value": 2})
        calculation = {"operation": "count", "value": 2}

        with (
            patch.object(
                evaluation,
                "fetch_context",
                return_value=(
                    [],
                    SimpleNamespace(model_dump=lambda: {}),
                    calculation,
                    2,
                ),
            ),
            patch.object(evaluation, "answer_question") as answer,
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 1.0)
        answer.assert_not_called()

    def test_calculation_business_predicates_are_order_insensitive(self):
        case = bird_case(
            expected_calculation={
                "operation": "distinct_count",
                "business_predicates": ["scheduled_working_day", "not_worked"],
                "value": 1,
            }
        )
        calculation = {
            "operation": "distinct_count",
            "business_predicates": ["not_worked", "scheduled_working_day"],
            "value": 1,
        }

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                SimpleNamespace(model_dump=lambda: {}),
                calculation,
                1,
            ),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 1.0)
        self.assertEqual(result.components, {"calculation": True})

    def test_numeric_contracts_reject_boolean_actual_values(self):
        for expected_value in (1, 1.0):
            with self.subTest(expected_value=expected_value):
                case = bird_case(expected_calculation={"value": expected_value})
                with patch.object(
                    evaluation,
                    "fetch_context",
                    return_value=(
                        [],
                        SimpleNamespace(model_dump=lambda: {}),
                        {"value": True},
                        0,
                    ),
                ):
                    result = evaluation.evaluate_bird_case(case)

                self.assertEqual(result.execution_accuracy, 0.0)
                self.assertEqual(result.components, {"calculation": False})

    def test_matched_count_contract_rejects_boolean_actual_value(self):
        case = bird_case(expected_matched_count=1)
        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                SimpleNamespace(model_dump=lambda: {}),
                None,
                True,
            ),
        ):
            result = evaluation.evaluate_bird_case(case)

        self.assertEqual(result.execution_accuracy, 0.0)
        self.assertEqual(result.components, {"matched_count": False})

    def test_empty_mapping_expectations_do_not_create_a_verified_contract(self):
        for expectation_name in (
            "expected_calculation",
            "expected_normalized_result",
        ):
            with self.subTest(expectation_name=expectation_name):
                case = bird_case(**{expectation_name: {}})
                with patch.object(evaluation, "fetch_context") as fetch:
                    result = evaluation.evaluate_bird_case(case)

                self.assertEqual(result.status, "skipped")
                self.assertIsNone(result.execution_accuracy)
                self.assertEqual(result.components, {})
                self.assertEqual(result.reason, "no_verified_output_contract")
                fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
