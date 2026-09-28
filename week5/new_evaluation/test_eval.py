from __future__ import annotations

import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

from week5.new_evaluation.eval import (
    BehaviorEval,
    _answer_fact_matches,
    _expected_subset,
    _group_values_by_shape,
    _plan_matches,
    _write,
    evaluate_outcome,
    main,
)
from week5.new_evaluation.test import TestQuestion, load_tests as load_evaluation_tests
from week5.new_implementation.online.execution import ExecutionCoverage
from week5.new_implementation.online.pipeline import (
    Answered,
    Clarification,
    Unsupported,
)
from week5.new_implementation.online.reference import (
    EmployeeOption,
    PendingEmployeeConfirmation,
    PendingResolution,
)
from week5.new_implementation.online.state import ConversationState, VerifiedTurn


class EvaluatorTests(unittest.TestCase):
    def test_plan_matcher_accepts_quoted_postgres_identifiers(self):
        sql = (
            'SELECT COUNT(DISTINCT "attendance_date") FROM '
            '"public"."attendance_records" WHERE "day_type" = \'Working Day\''
        )
        expected = {
            "aggregation": "distinct_count",
            "aggregation_field": "Date",
            "required_filter": {
                "field": "Day_Type",
                "operator": "eq",
                "value": "Working Day",
            },
        }
        self.assertTrue(_plan_matches(sql, expected))

    def test_plan_matcher_accepts_postgres_date_literal_bounds(self):
        sql = (
            'SELECT COUNT(DISTINCT "attendance_date") '
            'FROM "public"."attendance_records" '
            "WHERE \"attendance_date\" BETWEEN DATE '2026-09-01' "
            "AND DATE '2026-09-30'"
        )
        expected = {
            "aggregation": "distinct_count",
            "required_filters": [
                {"field": "Date", "operator": "gte", "value": "2026-09-01"},
                {"field": "Date", "operator": "lte", "value": "2026-09-30"},
            ],
        }
        self.assertTrue(_plan_matches(sql, expected))

    def test_grouped_numeric_measure_allows_equivalent_sql_alias(self):
        shaped = _group_values_by_shape(
            [
                {"department": "Engineering", "total_worked_hours": 56},
                {"department": "Finance", "total_worked_hours": 36},
            ],
            [
                {"department": "Engineering", "worked_hours": 56},
                {"department": "Finance", "worked_hours": 36},
            ],
        )
        self.assertIsNotNone(shaped)
        self.assertTrue(_expected_subset(*shaped))

    @staticmethod
    def _write_cases(path: Path, count: int = 2) -> None:
        path.write_text(
            "".join(
                json.dumps(
                    {
                        "question": f"Question {index}",
                        "keywords": [],
                        "reference_answer": f"Answer {index}",
                        "category": "resume_test",
                    }
                )
                + "\n"
                for index in range(count)
            ),
            encoding="utf-8",
        )

    def test_interrupted_report_resumes_remaining_prefix_and_preserves_failures(self):
        with TemporaryDirectory() as directory:
            case_file = Path(directory) / "cases.jsonl"
            output = Path(directory) / "report.json"
            self._write_cases(case_file)
            with patch(
                "week5.new_evaluation.eval.evaluate_behavior",
                side_effect=[BehaviorEval(outcome_ok=False), RuntimeError("stop")],
            ):
                with self.assertRaisesRegex(RuntimeError, "stop"):
                    main(
                        [
                            "--all",
                            "--test-file",
                            str(case_file),
                            "--output",
                            str(output),
                        ]
                    )
            interrupted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(interrupted["status"], "running")
            self.assertEqual(interrupted["selected_indices"], [0, 1])
            self.assertEqual(interrupted["completed"], 1)
            self.assertEqual(interrupted["failures"][0]["index"], 0)
            self.assertFalse(interrupted["failures"][0]["result"]["outcome_ok"])

            with patch(
                "week5.new_evaluation.eval.evaluate_behavior",
                return_value=BehaviorEval(),
            ) as evaluate:
                return_code = main(
                    [
                        "--all",
                        "--test-file",
                        str(case_file),
                        "--output",
                        str(output),
                        "--resume",
                    ]
                )

            self.assertEqual(return_code, 1)
            self.assertEqual(evaluate.call_count, 1)
            complete = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(complete["status"], "complete")
            self.assertEqual(complete["completed"], 2)
            self.assertEqual(complete["failures"], interrupted["failures"])

    def test_resume_rejects_mismatched_fingerprint_or_selected_indices(self):
        with TemporaryDirectory() as directory:
            case_file = Path(directory) / "cases.jsonl"
            output = Path(directory) / "report.json"
            self._write_cases(case_file)
            with patch(
                "week5.new_evaluation.eval.evaluate_behavior",
                side_effect=RuntimeError("stop"),
            ):
                with self.assertRaisesRegex(RuntimeError, "stop"):
                    main(
                        [
                            "--all",
                            "--test-file",
                            str(case_file),
                            "--output",
                            str(output),
                        ]
                    )
            original = json.loads(output.read_text(encoding="utf-8"))
            variants = (
                {
                    **original,
                    "fingerprints": {**original["fingerprints"], "cases": "bad"},
                },
                {**original, "selected_indices": [1, 0]},
            )
            for variant in variants:
                output.write_text(json.dumps(variant), encoding="utf-8")
                with self.subTest(variant=variant):
                    with patch(
                        "week5.new_evaluation.eval.evaluate_behavior"
                    ) as evaluate:
                        with self.assertRaises(ValueError):
                            main(
                                [
                                    "--all",
                                    "--test-file",
                                    str(case_file),
                                    "--output",
                                    str(output),
                                    "--resume",
                                ]
                            )
                        evaluate.assert_not_called()

    def test_batch_size_checkpoints_a_prefix_without_marking_complete(self):
        with TemporaryDirectory() as directory:
            case_file = Path(directory) / "cases.jsonl"
            output = Path(directory) / "report.json"
            self._write_cases(case_file, count=3)
            with patch(
                "week5.new_evaluation.eval.evaluate_behavior",
                return_value=BehaviorEval(),
            ) as evaluate:
                self.assertEqual(
                    main(
                        [
                            "--all",
                            "--test-file",
                            str(case_file),
                            "--output",
                            str(output),
                            "--batch-size",
                            "2",
                        ]
                    ),
                    0,
                )
                self.assertEqual(evaluate.call_count, 2)
            partial = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(partial["status"], "running")
            self.assertEqual(partial["completed"], 2)

            with patch(
                "week5.new_evaluation.eval.evaluate_behavior",
                return_value=BehaviorEval(),
            ) as evaluate:
                self.assertEqual(
                    main(
                        [
                            "--all",
                            "--test-file",
                            str(case_file),
                            "--output",
                            str(output),
                            "--batch-size",
                            "2",
                            "--resume",
                        ]
                    ),
                    0,
                )
                self.assertEqual(evaluate.call_count, 1)
            complete = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(complete["status"], "complete")
            self.assertEqual(complete["completed"], 3)

    def test_wrong_unsupported_capability_does_not_pass_as_schema(self):
        case = TestQuestion(
            question="Count records on September 31, 2026.",
            keywords=[],
            reference_answer="The date is invalid.",
            category="synthetic_invalid_date",
            expected_unsupported_capabilities=["malformed_value"],
        )
        outcome = Unsupported(
            reply="The schema does not include this field.",
            state=ConversationState(),
            capability="schema",
        )

        result = evaluate_outcome(case, outcome)

        self.assertFalse(result.unsupported_capabilities_ok)

    def test_cli_can_use_explicit_synthetic_case_file(self):
        with TemporaryDirectory() as directory:
            case_file = Path(directory) / "synthetic.jsonl"
            case_file.write_text(
                json.dumps(
                    {
                        "question": "Join attendance to a missing table.",
                        "keywords": [],
                        "reference_answer": "The join target is absent.",
                        "category": "synthetic_schema",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("week5.new_evaluation.eval.evaluate_behavior") as evaluate:
                evaluate.return_value.passed = True
                self.assertEqual(main(["--all", "--test-file", str(case_file)]), 0)
            self.assertEqual(evaluate.call_count, 1)

    def test_expected_rows_cannot_reuse_one_actual_row(self):
        actual = [{"group": ["Engineering"], "value": 56}]
        expected = [
            {"group": ["Engineering"], "value": 56},
            {"group": ["Engineering"], "value": 56},
        ]
        self.assertFalse(_expected_subset(actual, expected))

    def test_required_sql_filter_must_be_in_a_predicate_clause(self):
        expected = {
            "required_filter": {
                "field": "Employee_ID",
                "operator": "eq",
                "value": "A1",
            }
        }
        self.assertFalse(
            _plan_matches(
                "SELECT CASE WHEN employee_id = 'A1' THEN 1 ELSE 0 END AS marker "
                "FROM attendance_records",
                expected,
            )
        )
        self.assertTrue(
            _plan_matches(
                "SELECT COUNT(*) FROM attendance_records WHERE employee_id = 'A1'",
                expected,
            )
        )

    def test_evaluation_record_model_is_not_collected_as_a_pytest_test_class(self):
        self.assertFalse(getattr(TestQuestion, "__test__", True))

    def test_answer_fact_date_range_accepts_equivalent_same_month_wording(self):
        answer = (
            "Available data covers September 1 to September 7, 2026, "
            "not the full requested period."
        )

        self.assertTrue(_answer_fact_matches(answer, "September 1-7, 2026"))
        self.assertTrue(_answer_fact_matches(answer, "not the full requested period"))

    def test_answer_facts_accept_equivalent_business_wording_but_not_wrong_value(self):
        answer = (
            "A11017 was absent for 1 day. The available data covers September 1 "
            "to September 7, 2026 and does not encompass the entire month."
        )

        self.assertTrue(_answer_fact_matches(answer, "1 recorded absent day"))
        self.assertTrue(_answer_fact_matches(answer, "September 1-7, 2026"))
        self.assertTrue(_answer_fact_matches(answer, "not the full requested period"))
        self.assertFalse(_answer_fact_matches(answer, "2 recorded absent days"))

    def test_answer_fact_accepts_thousands_separator_without_changing_value(self):
        self.assertTrue(
            _answer_fact_matches(
                "There are 1,076 matching attendance records.",
                "1076",
            )
        )
        self.assertFalse(
            _answer_fact_matches(
                "There are 1,075 matching attendance records.",
                "1076",
            )
        )

    def test_answer_fact_date_range_accepts_iso_coverage_dates(self):
        answer = (
            "The table covers 2026-09-01 to 2026-09-07, not the full requested period."
        )

        self.assertTrue(_answer_fact_matches(answer, "September 1-7, 2026"))

    def test_answer_fact_recognizes_working_day_as_scheduled_day(self):
        answer = "A11017 did not attend work on 1 working day."

        self.assertTrue(
            _answer_fact_matches(answer, "1 scheduled working day was not attended")
        )

    def test_plan_filter_matches_null_safe_coalesce_comparison(self):
        turn = VerifiedTurn(
            turn_id="zero-hours",
            original_question="zero worked hours",
            rewritten_request="Request:\nzero worked hours",
            answer="3 days",
            locale="en",
            executed_sql=(
                "SELECT COUNT(DISTINCT attendance_date) AS zero_days "
                "FROM attendance_records "
                "WHERE COALESCE(total_worked_hrs, 0) <= 0"
            ),
            result={"columns": [], "rows": [{"zero_days": 3}], "coverage": {}},
        )
        case = TestQuestion(
            question="zero worked hours",
            keywords=[],
            reference_answer="3 days",
            category="zero_hours",
            expected_plan={
                "required_filter": {
                    "field": "Total_Worked_Hrs",
                    "operator": "lte",
                    "value": 0.0,
                }
            },
        )

        result = evaluate_outcome(
            case,
            Answered(reply="3 days", state=ConversationState(verified_turns=(turn,))),
        )

        self.assertTrue(result.plan_ok)

    def test_plan_filter_accepts_equivalent_exclusive_date_end(self):
        turn = VerifiedTurn(
            turn_id="month-range",
            original_question="September records",
            rewritten_request="Request:\nSeptember records",
            answer="7 records",
            locale="en",
            executed_sql=(
                "SELECT COUNT(*) FROM attendance_records "
                "WHERE attendance_date >= '2026-09-01' "
                "AND attendance_date < DATE '2026-10-01'"
            ),
            result={"columns": [], "rows": [{"count": 7}], "coverage": {}},
        )
        case = TestQuestion(
            question="September records",
            keywords=[],
            reference_answer="7 records",
            category="date_range",
            expected_plan={
                "required_filter": {
                    "field": "Date",
                    "operator": "lte",
                    "value": "2026-09-30",
                }
            },
        )

        result = evaluate_outcome(
            case,
            Answered(
                reply="7 records", state=ConversationState(verified_turns=(turn,))
            ),
        )

        self.assertTrue(result.plan_ok)

    def test_report_write_falls_back_when_windows_denies_atomic_replace(self):
        output = Path("report.json")
        temporary = Path("report.json.tmp")
        with (
            patch.object(Path, "write_text") as write_text,
            patch.object(Path, "replace", side_effect=PermissionError),
            patch.object(Path, "unlink") as unlink,
        ):
            _write(output, {"status": "running"})

        serialized = '{\n  "status": "running"\n}\n'
        self.assertEqual(
            write_text.call_args_list,
            [
                call(serialized, encoding="utf-8"),
                call(serialized, encoding="utf-8"),
            ],
        )
        unlink.assert_called_once_with(missing_ok=True)
        self.assertEqual(temporary.suffix, ".tmp")

    def test_complete_behavior_corpus_has_311_cases(self):
        self.assertEqual(len(load_evaluation_tests()), 311)

    def test_physical_sql_and_typed_rows_are_scored(self):
        coverage = ExecutionCoverage(
            fetched_rows=1, result_limit=100, response_bytes=40
        )
        turn = VerifiedTurn(
            turn_id="t1",
            original_question="show totals",
            rewritten_request="Request:\nShow totals.",
            answer="Engineering: 8",
            locale="en",
            executed_sql="SELECT department, SUM(total_worked_hrs) FROM attendance_records GROUP BY department",
            result={
                "columns": [],
                "rows": [{"department": "Engineering", "hours": 8, "record_id": "r1"}],
                "coverage": coverage.model_dump(mode="json"),
            },
        )
        outcome = Answered(
            reply="Engineering: 8", state=ConversationState(verified_turns=(turn,))
        )
        case = TestQuestion(
            question="show totals",
            keywords=[],
            reference_answer="Engineering: 8",
            category="aggregate",
            expected_plan={
                "aggregation": "sum",
                "aggregation_field": "Total_Worked_Hrs",
                "group_by": ["Department"],
            },
            expected_record_ids=["r1"],
            expected_group_values=[
                {"department": "Engineering", "hours": 8, "record_id": "r1"}
            ],
        )
        result = evaluate_outcome(case, outcome)
        self.assertTrue(result.plan_ok)
        self.assertTrue(result.record_ids_ok)
        self.assertTrue(result.group_values_ok)

    def test_percentage_result_does_not_require_unrequested_intermediate_counts(self):
        turn = VerifiedTurn(
            turn_id="percentage",
            original_question="percentage authorized",
            rewritten_request="Request:\npercentage authorized",
            answer="19.55%",
            locale="en",
            executed_sql=(
                "SELECT (COUNT(CASE WHEN status = 'Authorized' THEN 1 END) "
                "* 100.0) / COUNT(*) AS percentage FROM attendance_records"
            ),
            result={
                "columns": [],
                "rows": [{"percentage": 19.55095862764884}],
                "coverage": {},
            },
        )
        case = TestQuestion(
            question="percentage authorized",
            keywords=[],
            reference_answer="19.55%",
            category="percentage",
            expected_plan={"aggregation": "percentage"},
            expected_calculation={
                "operation": "percentage",
                "field": "attendance_records",
                "numerator": 775,
                "denominator": 3964,
                "value": 19.55,
            },
        )

        result = evaluate_outcome(
            case,
            Answered(reply="19.55%", state=ConversationState(verified_turns=(turn,))),
        )

        self.assertTrue(result.calculation_ok)
        self.assertTrue(result.plan_ok)

    def test_calculation_ignores_non_observable_expectation_metadata(self):
        turn = VerifiedTurn(
            turn_id="worked-days",
            original_question="How many days did A11017 work?",
            rewritten_request="Request:\nHow many days did A11017 work?",
            answer="4 days",
            locale="en",
            executed_sql=(
                "SELECT COUNT(DISTINCT attendance_date) AS worked_days "
                "FROM attendance_records WHERE employee_id = 'A11017' "
                "AND attendance_date BETWEEN '2026-09-01' AND '2026-09-30' "
                "AND total_worked_hrs > 0"
            ),
            result={
                "columns": [],
                "rows": [{"worked_days": 4}],
                "coverage": {},
            },
        )
        case = TestQuestion(
            question="How many days did A11017 work?",
            keywords=[],
            reference_answer="4 days",
            category="worked_days",
            expected_plan={
                "business_predicates": ["worked"],
                "required_filter": {
                    "field": "Total_Worked_Hrs",
                    "operator": "gt",
                    "value": 0.0,
                },
                "required_filters": [
                    {
                        "field": "Date",
                        "operator": "gte",
                        "value": "2026-09-01",
                    },
                    {
                        "field": "Date",
                        "operator": "lte",
                        "value": "2026-09-30",
                    },
                ],
            },
            expected_calculation={
                "operation": "count_distinct",
                "field": "Date",
                "value": 4,
                "measure": "days",
                "business_predicates": ["worked"],
                "coverage": {"start": "2026-09-01", "end": "2026-09-30"},
            },
        )

        result = evaluate_outcome(
            case,
            Answered(reply="4 days", state=ConversationState(verified_turns=(turn,))),
        )

        self.assertTrue(result.calculation_ok)
        self.assertTrue(result.plan_ok)

    def test_plan_filter_matches_all_values_in_sql_in_list(self):
        turn = VerifiedTurn(
            turn_id="off-days",
            original_question="How many off days?",
            rewritten_request="Request:\nHow many off days?",
            answer="2 off days",
            locale="en",
            executed_sql=(
                "SELECT COUNT(DISTINCT attendance_date) AS off_days "
                "FROM attendance_records "
                "WHERE day_type IN ('OFF Day', 'OFF Day (ZAS)')"
            ),
            result={"columns": [], "rows": [{"off_days": 2}], "coverage": {}},
        )
        case = TestQuestion(
            question="How many off days?",
            keywords=[],
            reference_answer="2 off days",
            category="off_days",
            expected_plan={
                "required_filter": {
                    "field": "Day_Type",
                    "operator": "in",
                    "value": ["OFF Day", "OFF Day (ZAS)"],
                }
            },
        )

        result = evaluate_outcome(
            case,
            Answered(
                reply="2 off days", state=ConversationState(verified_turns=(turn,))
            ),
        )

        self.assertTrue(result.plan_ok)

    def test_window_total_is_used_as_matched_count_for_bounded_detail_rows(self):
        turn = VerifiedTurn(
            turn_id="bounded-list",
            original_question="show attendance before date",
            rewritten_request="Request:\nshow attendance before date",
            answer="Showing a bounded sample of 2260 matching records.",
            locale="en",
            executed_sql=(
                "SELECT record_id, COUNT(*) OVER() AS matched_count "
                "FROM attendance_records WHERE attendance_date < '2026-09-05' LIMIT 100"
            ),
            result={
                "columns": [],
                "rows": [{"record_id": "r1", "matched_count": 2260}],
                "coverage": {},
            },
        )
        case = TestQuestion(
            question="show attendance before date",
            keywords=[],
            reference_answer="2260 matches",
            category="date_filter",
            expected_plan={"aggregation": "none"},
            expected_matched_count=2260,
        )

        result = evaluate_outcome(
            case,
            Answered(
                reply="Showing a bounded sample of 2260 matching records.",
                state=ConversationState(verified_turns=(turn,)),
            ),
        )

        self.assertTrue(result.matched_count_ok)
        self.assertTrue(result.plan_ok)

    def test_grouped_sql_rows_are_normalized_and_compared_with_rounding_tolerance(self):
        turn = VerifiedTurn(
            turn_id="grouped",
            original_question="average lateness by department",
            rewritten_request="Request:\naverage lateness by department",
            answer="Engineering: 0.916071",
            locale="en",
            executed_sql=(
                "SELECT department, AVG (lateness_hrs) AS average_lateness "
                "FROM attendance_records GROUP BY department"
            ),
            result={
                "columns": [],
                "rows": [{"department": "Engineering", "average_lateness": 0.9160714}],
                "coverage": {},
            },
        )
        case = TestQuestion(
            question="average lateness by department",
            keywords=[],
            reference_answer="Engineering: 0.916071",
            category="grouped_aggregate",
            expected_plan={
                "aggregation": "average",
                "aggregation_field": "Lateness_Hrs",
                "group_by": ["Department"],
            },
            expected_calculation={
                "operation": "average",
                "field": "Lateness_Hrs",
                "group_by": ["Department"],
                "total_groups": 1,
            },
            expected_group_values=[{"group": ["Engineering"], "value": 0.916071}],
        )

        result = evaluate_outcome(
            case,
            Answered(
                reply="Engineering: 0.916071",
                state=ConversationState(verified_turns=(turn,)),
            ),
        )

        self.assertTrue(result.plan_ok)
        self.assertTrue(result.calculation_ok)
        self.assertTrue(result.group_values_ok)

    def test_confirmation_and_unsupported_outcomes_remain_explicit(self):
        pending = PendingEmployeeConfirmation(
            original_question="show Fare",
            mention="Fare",
            options=(EmployeeOption(employee_id="A1", employee_name="Faris"),),
            resolution=PendingResolution(
                rewritten_request="Show Fare attendance.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
            ),
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
            reply="unsupported", state=ConversationState(), capability="field"
        )
        unsupported_case = TestQuestion(
            question="unsupported concept",
            keywords=[],
            reference_answer="",
            category="unsupported",
            expected_unsupported_capabilities=["field"],
        )
        unsupported_result = evaluate_outcome(unsupported_case, unsupported)
        self.assertTrue(unsupported_result.outcome_ok)
        self.assertTrue(unsupported_result.unsupported_capabilities_ok)

    def test_legacy_safe_stop_expectation_accepts_explicit_unsupported_outcome(self):
        outcome = Unsupported(
            reply="The request contains an invalid value.",
            state=ConversationState(),
            capability="malformed_value",
        )
        case = TestQuestion(
            question="Show lateness greater than NaN hours.",
            keywords=[],
            reference_answer="Reject non-finite numeric input.",
            category="malformed_input",
            expected_error="finite number",
            expected_exception_type="PlanValidationError",
        )

        result = evaluate_outcome(case, outcome)

        self.assertTrue(result.outcome_ok)
        self.assertTrue(result.clarification_ok)
        self.assertTrue(result.unsupported_capabilities_ok)

    def test_unsupported_constraint_expectation_accepts_safe_clarification(self):
        outcome = Clarification(
            reply="Please clarify the date constraint.",
            state=ConversationState(),
            reason="ambiguous_reference",
        )
        case = TestQuestion(
            question="Show records from not-a-date to tomorrow.",
            keywords=[],
            reference_answer="Clarify the date constraint.",
            category="malformed_input",
            expected_unsupported_capabilities=["unsupported_constraint"],
        )

        result = evaluate_outcome(case, outcome)

        self.assertTrue(result.outcome_ok)
        self.assertTrue(result.clarification_ok)
        self.assertTrue(result.unsupported_capabilities_ok)

    def test_expected_unsupported_capability_does_not_pass_as_answered(self):
        case = TestQuestion(
            question="Show attendance on an impossible date.",
            keywords=[],
            reference_answer="Reject the invalid date.",
            category="malformed_input",
            expected_unsupported_capabilities=["malformed_value"],
        )

        result = evaluate_outcome(
            case,
            Answered(reply="There were no records.", state=ConversationState()),
        )

        self.assertFalse(result.outcome_ok)
        self.assertFalse(result.unsupported_capabilities_ok)


if __name__ == "__main__":
    unittest.main()
