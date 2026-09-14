import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

from week5.new_implementation.answer import EmployeeCandidate, FilterCondition, Result
from week5.new_evaluation import eval as evaluation
from week5.new_evaluation import test as evaluation_cases
from week5.new_evaluation.test import TestQuestion, load_tests as load_evaluation_tests

PRIVATE_ATTENDANCE_PATH = (
    Path(__file__).parents[1] / "new-knowledge-base" / "attendance" / "attendance.jsonl"
)
PRIVATE_FIXTURES_AVAILABLE = (
    Path(evaluation_cases.TEST_FILE).is_file() and PRIVATE_ATTENDANCE_PATH.is_file()
)


class PrivacySafeDiagnosticTests(unittest.TestCase):
    def test_case_diagnostic_contains_structure_without_private_text(self):
        diagnostic = evaluation.CaseDiagnostic.from_failure(
            index=7,
            category="worked_days",
            stage="semantic_validation",
            violation_codes=("uncovered_fact",),
            fact_kind_counts={"measure": 1, "predicate": 1},
        )

        payload = diagnostic.model_dump(mode="json")

        self.assertEqual(payload["cause"], "missing_deterministic_fact")
        self.assertEqual(payload["index"], 7)
        self.assertEqual(payload["fact_kind_counts"], {"measure": 1, "predicate": 1})
        for forbidden in (
            "question",
            "reference_answer",
            "generated_answer",
            "evidence_text",
            "record_ids",
            "employee_ids",
            "provider_payload",
            "exception",
        ):
            self.assertNotIn(forbidden, payload)

    def test_failure_classifier_uses_stable_architectural_precedence(self):
        cases = (
            (
                {"stage": "provider_response_validation"},
                "provider_structural_failure",
            ),
            (
                {
                    "stage": "semantic_validation",
                    "unsupported_capabilities": ("grouped_percentage",),
                },
                "unsupported_plan_shape",
            ),
            (
                {
                    "stage": "semantic_validation",
                    "violation_codes": ("uncovered_fact",),
                },
                "missing_deterministic_fact",
            ),
            (
                {
                    "stage": "semantic_validation",
                    "violation_codes": ("ungrounded_constraint",),
                },
                "excess_or_ungrounded_fact",
            ),
            (
                {
                    "stage": "semantic_validation",
                    "violation_codes": ("answer_contract_mismatch",),
                },
                "answer_contract_mismatch",
            ),
            (
                {"stage": "result_validation", "failed_checks": ("calculation_ok",)},
                "retrieval_or_calculation_mismatch",
            ),
            (
                {"stage": "answer_rendering", "failed_checks": ("answer_facts_ok",)},
                "renderer_incomplete",
            ),
            (
                {"stage": "session_state", "failed_checks": ("multi_turn_ok",)},
                "session_state_failure",
            ),
            (
                {
                    "stage": "answer_judging",
                    "sub_five_dimensions": ("relevance",),
                    "evidence_count": 4,
                },
                "irrelevant_evidence",
            ),
            (
                {
                    "stage": "answer_judging",
                    "sub_five_dimensions": ("accuracy",),
                },
                "evaluator_expectation_drift",
            ),
        )

        for inputs, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    evaluation.classify_case_diagnostic(**inputs), expected
                )

    def test_case_diagnostic_rejects_arbitrary_private_fields(self):
        with self.assertRaises(Exception):
            evaluation.CaseDiagnostic.model_validate(
                {
                    "index": 0,
                    "category": "synthetic",
                    "stage": "answer_judging",
                    "cause": "evaluator_expectation_drift",
                    "question": "private text",
                }
            )

    def test_behavior_diagnostic_classifies_failed_checks_without_case_text(self):
        behavior = evaluation.BehaviorEval(
            plan_ok=True,
            employee_ids_ok=True,
            matched_count_ok=False,
            calculation_ok=True,
            clarification_ok=True,
        )

        diagnostic = evaluation.diagnose_behavior_result(
            index=3,
            category="synthetic_exact",
            result=behavior,
        )

        self.assertEqual(diagnostic.cause, "retrieval_or_calculation_mismatch")
        self.assertEqual(diagnostic.failed_checks, ("matched_count_ok",))
        self.assertNotIn("question", diagnostic.model_dump())

    def test_sub_five_answer_diagnostic_uses_the_scored_execution_documents(self):
        judged = evaluation.AnswerEval(
            feedback="Synthetic feedback",
            accuracy=5,
            completeness=5,
            relevance=4,
        )
        documents = [Result(page_content="Synthetic evidence", metadata={})]
        test = TestQuestion(
            question="Synthetic question",
            keywords=[],
            reference_answer="Synthetic reference",
            category="semantic",
        )

        with patch.object(
            evaluation,
            "evaluate_answer",
            return_value=(judged, "Synthetic answer", documents),
        ):
            result, diagnostic = evaluation.evaluate_answer_with_diagnostic(
                test, index=9
            )

        self.assertIs(result, judged)
        self.assertEqual(diagnostic.cause, "irrelevant_evidence")
        self.assertEqual(diagnostic.evidence_count, 1)
        self.assertEqual(diagnostic.sub_five_dimensions, ("relevance",))


class BaselineCapabilityParityTests(unittest.TestCase):
    def test_projection_employee_and_date_compose_through_evaluator_fixture(self):
        questions = (
            "Show Date and Status from records for Morgan River on 2026-09-01",
            "On 2026-09-01, show Date and Status from records for Morgan River",
        )
        for question in questions:
            with self.subTest(question=question):
                chunks, plan, calculation, count = self.answer.fetch_context(question)

            self.assertEqual(plan.projection, ["Date", "Status"])
            self.assertIsNone(calculation)
            self.assertEqual(count, 2)
            self.assertEqual(len(chunks), 2)
            self.assertIn(
                ("Employee_ID", "eq", "A10001"),
                {(item.field, item.operator, item.value) for item in plan.filters},
            )
            self.assertIn(
                ("Date", "eq", "2026-09-01"),
                {(item.field, item.operator, item.value) for item in plan.filters},
            )

    def test_temporal_filters_preserve_projection_and_returned_rows(self):
        for fields, projection in (
            (("Date", "Status"), ["Date", "Status"]),
            (("Date", "Total_OT"), ["Date", "Total_OT"]),
            (("Status", "Total_OT"), ["Status", "Total_OT"]),
            (("Status", "Date"), ["Date", "Status"]),
            (("Total_OT", "Date"), ["Date", "Total_OT"]),
        ):
            for scope, indexes, temporal_filters in (
                ("on 2026-09-01", (0, 1), {("eq", "2026-09-01")}),
                ("before 2026-09-04", (0, 1, 2, 3), {("lt", "2026-09-04")}),
                ("after 2026-09-03", (4,), {("gt", "2026-09-03")}),
                ("on or before 2026-09-01", (0, 1), {("lte", "2026-09-01")}),
                ("on or after 2026-09-04", (4,), {("gte", "2026-09-04")}),
                (
                    "from 2026-09-02 to 2026-09-03",
                    (2, 3),
                    {("gte", "2026-09-02"), ("lte", "2026-09-03")},
                ),
                (
                    "between September 1, 2026 and 3",
                    (0, 1, 2, 3),
                    {("gte", "2026-09-01"), ("lte", "2026-09-03")},
                ),
                (
                    "in September 2026",
                    (0, 1, 2, 3, 4),
                    {("gte", "2026-09-01"), ("lte", "2026-09-30")},
                ),
            ):
                with self.subTest(fields=fields, scope=scope):
                    chunks, plan, result, count = self.answer.fetch_context(
                        f"Show {' and '.join(fields)} from records {scope}"
                    )
                    self.assertEqual(plan.projection, projection)
                    self.assertEqual(plan.answer_contract.grain, projection)
                    self.assertEqual(
                        (plan.aggregation, result, count), ("none", None, len(indexes))
                    )
                    self.assertEqual(
                        {
                            (item.field, item.operator, item.value)
                            for item in plan.filters
                            if item.field != "chunk_type"
                        },
                        {
                            ("Date", operator, value)
                            for operator, value in temporal_filters
                        },
                    )
                    self.assertEqual(
                        [
                            tuple(chunk.metadata[field] for field in fields)
                            for chunk in chunks
                        ],
                        [
                            tuple(self.rows[index][field] for field in fields)
                            for index in indexes
                        ],
                    )

    def test_repeated_date_constraint_does_not_consume_projection(self):
        for clause, expected, numeric in (
            ("Date equals 2026-09-01", (0, 1), set()),
            ("Date on or before 2026-09-01", (0, 1), set()),
            ("Total_OT equals 2 on 2026-09-01", (0,), {("Total_OT", "eq", 2.0)}),
        ):
            with self.subTest(clause=clause):
                chunks, plan, _, count = self.answer.fetch_context(
                    f"Show Date and Status and Total_OT from records where {clause}"
                )
                self.assertEqual(plan.projection, ["Date", "Status", "Total_OT"])
                self.assertEqual(count, len(expected))
                self.assertEqual(
                    [chunk.metadata["Total_OT"] for chunk in chunks],
                    [self.rows[index]["Total_OT"] for index in expected],
                )
                self.assertEqual(
                    {
                        (item.field, item.operator, item.value)
                        for item in plan.filters
                        if item.field not in {"chunk_type", "Date"}
                    },
                    numeric,
                )

    def test_projection_cannot_hide_invalid_temporal_or_numeric_constraints(self):
        for clause in (
            "on 2026-02-30",
            "between 2026-09-04 and 2026-09-01",
            "where Date approximately 2026-09-01",
            "where Total_OT equals 2026-09-01",
            "where Total_OT greater than 2026-09-01",
        ):
            with self.subTest(clause=clause):
                self.store.get.reset_mock()
                with self.assertRaises(self.answer.PlanValidationError):
                    self.answer.fetch_context(
                        f"Show Date and Status from records {clause}"
                    )
                self.store.get.assert_not_called()

    def test_date_qualified_sums_keep_temporal_filters_executable(self):
        cases = (
            ("on 2026-09-01", 3.0, {("eq", "2026-09-01")}),
            ("before 2026-09-04", 3.0, {("lt", "2026-09-04")}),
            ("after 2026-09-01", 4.0, {("gt", "2026-09-01")}),
            ("on or before 2026-09-01", 3.0, {("lte", "2026-09-01")}),
            ("on or after 2026-09-04", 4.0, {("gte", "2026-09-04")}),
            ("since 2026-09-01", 7.0, {("gte", "2026-09-01")}),
            ("until 2026-09-03", 3.0, {("lte", "2026-09-03")}),
            ("in September 2026", 7.0, {("gte", "2026-09-01"), ("lte", "2026-09-30")}),
            (
                "from 2026-09-01 to 2026-09-03",
                3.0,
                {("gte", "2026-09-01"), ("lte", "2026-09-03")},
            ),
            (
                "between 2026-09-02 and 2026-09-04",
                4.0,
                {("gte", "2026-09-02"), ("lte", "2026-09-04")},
            ),
            (
                "between September 1, 2026 and 3",
                3.0,
                {("gte", "2026-09-01"), ("lte", "2026-09-03")},
            ),
        )
        for scope, expected, temporal_filters in cases:
            with self.subTest(scope=scope):
                _, plan, result, _ = self.answer.fetch_context(f"Sum overtime {scope}")
                self.assertEqual(
                    (plan.aggregation, plan.aggregation_field, result["value"]),
                    ("sum", "Total_OT", expected),
                )
                self.assertEqual(
                    {
                        (item.field, item.operator, item.value)
                        for item in plan.filters
                        if item.field != "chunk_type"
                    },
                    {("Date", operator, value) for operator, value in temporal_filters},
                )

    def test_numeric_operands_end_at_independent_temporal_clauses(self):
        for clause, expected, numeric in (
            (
                "Total_Worked_Hrs greater than 2 on 2026-09-01",
                2.0,
                {("Total_Worked_Hrs", "gt", 2.0)},
            ),
            (
                "Total_Worked_Hrs greater than 2 before 2026-09-04",
                2.0,
                {("Total_Worked_Hrs", "gt", 2.0)},
            ),
            (
                "Total_Worked_Hrs greater than 2 in September 2026",
                6.0,
                {("Total_Worked_Hrs", "gt", 2.0)},
            ),
            (
                "Total_Worked_Hrs greater than 2 and Total_OT less than 4 on 2026-09-01",
                2.0,
                {("Total_Worked_Hrs", "gt", 2.0), ("Total_OT", "lt", 4.0)},
            ),
            (
                "above 2 Total_Worked_Hrs on 2026-09-01",
                2.0,
                {("Total_Worked_Hrs", "gt", 2.0)},
            ),
        ):
            with self.subTest(clause=clause):
                _, plan, result, _ = self.answer.fetch_context(
                    f"Sum overtime where {clause}"
                )
                self.assertEqual(
                    (plan.aggregation, plan.aggregation_field, result["value"]),
                    ("sum", "Total_OT", expected),
                )
                self.assertEqual(
                    {
                        (item.field, item.operator, item.value)
                        for item in plan.filters
                        if item.field not in {"chunk_type", "Date"}
                    },
                    numeric,
                )
                self.assertTrue(any(item.field == "Date" for item in plan.filters))

    def test_invalid_date_scopes_and_numeric_date_operands_reject_before_retrieval(
        self,
    ):
        for question in (
            "Sum overtime on 2026-02-30",
            "Sum overtime between 2026-09-04 and 2026-09-01",
            "Sum overtime where Total_Worked_Hrs equals 2026-09-01",
            "Sum overtime where Total_Worked_Hrs greater than 2026-09-01",
            "Sum overtime where Total_Worked_Hrs 2026-09-01",
            "Sum overtime on 2026-09-01 or after 2026-09-03",
        ):
            with self.subTest(question=question):
                self.store.get.reset_mock()
                with self.assertRaises(self.answer.PlanValidationError):
                    self.answer.fetch_context(question)
                self.store.get.assert_not_called()

    def test_cross_field_numeric_aggregates_execute_only_requested_filter(self):
        for target, constrained, value, expected in (
            ("Total_OT", "Total_Worked_Hrs", 2, 1.0),
            ("Total_Worked_Hrs", "Total_OT", 2, 8.0),
            ("Total_OT", "Total_OT", 2, 2.0),
            ("Total_Worked_Hrs", "Total_Worked_Hrs", 2, 2.0),
        ):
            with self.subTest(target=target, constrained=constrained):
                _, plan, result, _ = self.answer.fetch_context(
                    f"Sum {target} where {constrained} equals {value}"
                )
                self.assertEqual(
                    (plan.aggregation, plan.aggregation_field, result["value"]),
                    ("sum", target, expected),
                )
                self.assertEqual(
                    [
                        (item.field, item.operator, item.value)
                        for item in plan.filters
                        if item.field != "chunk_type"
                    ],
                    [(constrained, "eq", float(value))],
                )

    def test_multiple_numeric_clauses_execute_independently(self):
        for question in (
            "Count records where Total_OT greater than 1 and Total_Worked_Hrs less than 8",
            "Count records with above 1 Total_OT and below 8 Total_Worked_Hrs",
        ):
            with self.subTest(question=question):
                _, plan, result, _ = self.answer.fetch_context(question)
                self.assertEqual((plan.aggregation, result["value"]), ("count", 1))
                self.assertEqual(
                    {
                        (item.field, item.operator, item.value)
                        for item in plan.filters
                        if item.field != "chunk_type"
                    },
                    {("Total_OT", "gt", 1.0), ("Total_Worked_Hrs", "lt", 8.0)},
                )

    def test_numeric_operand_rejection_precedes_retrieval(self):
        for question in (
            "Count records where Total_OT equals 2 extra",
            "Count records where Total_OT sounds like 2",
            "Count records where Total_OT equals 2026-09-01",
            "Count records where Total_OT and Total_Worked_Hrs equals 2",
            "Count records with above 2 Total_OT below 5",
            "Count records with above bananas Total_OT",
            "Count records with above 2 extra Total_OT",
            "Count records with equals 2 extra Total_OT",
        ):
            with self.subTest(question=question):
                self.store.get.reset_mock()
                with self.assertRaises(self.answer.PlanValidationError):
                    self.answer.fetch_context(question)
                self.store.get.assert_not_called()

    def test_bound_field_alias_matrix_executes_count_filters(self):
        from week5.new_implementation.test_semantic_resolution import (
            ROLE_ALIAS_CASES,
            registered_test_alias,
        )

        for field, alias in ROLE_ALIAS_CASES:
            for introducer in ("", "where "):
                for operator in ("equals", "is"):
                    with (
                        self.subTest(
                            field=field,
                            alias=alias,
                            introducer=introducer,
                            operator=operator,
                        ),
                        registered_test_alias(field, alias),
                    ):
                        _, plan, result, _ = self.answer.fetch_context(
                            f"Count records {introducer}{alias} {operator} 2"
                        )
                        self.assertEqual(
                            (plan.aggregation, result["value"]), ("count", 1)
                        )
                        self.assertEqual(
                            [
                                (condition.field, condition.operator, condition.value)
                                for condition in plan.filters
                                if condition.field != "chunk_type"
                            ],
                            [(field, "eq", 2.0)],
                        )
                        self.assertEqual(
                            (plan.group_by, plan.order_by, plan.limit, plan.projection),
                            ([], None, None, []),
                        )

    def test_bound_measure_and_value_words_execute_as_catalog_literals(self):
        from week5.new_implementation.attendance_schema import (
            FIELD_DEFINITIONS,
            MEASURE_DEFINITIONS,
            VALUE_CONCEPT_DEFINITIONS,
        )

        phrases = {
            phrase
            for registry in (MEASURE_DEFINITIONS, VALUE_CONCEPT_DEFINITIONS)
            for definition in registry.values()
            for phrase in definition.natural_names
        }
        phrases.update(
            value
            for definition in FIELD_DEFINITIONS.values()
            for value in (
                *definition.closed_values,
                *(alias.natural_name for alias in definition.value_aliases),
            )
        )
        for phrase in sorted(phrases):
            with self.subTest(phrase=phrase):
                for row, position in zip(
                    self.rows, (phrase, phrase, "Other", "Other", "Other")
                ):
                    row["Position"] = position
                with patch.object(
                    self.answer,
                    "load_attendance_catalog_candidates",
                    return_value={"Position": (phrase, "Other")},
                ):
                    _, plan, result, _ = self.answer.fetch_context(
                        f"Count records where Position equals {phrase}"
                    )
                self.assertEqual((plan.aggregation, result["value"]), ("count", 2))
                self.assertEqual(
                    {condition.field for condition in plan.filters},
                    {"Position", "chunk_type"},
                )

    def test_independent_operations_survive_same_field_constraint_alias(self):
        for row, department in zip(
            self.rows, ("Sales", "Sales", "Support", "Support", "Support")
        ):
            row["Department"] = department
        for question, expected in (
            ("What is total overtime?", 7.0),
            ("What is total overtime where Total OT equals 2?", 2.0),
        ):
            with self.subTest(question=question):
                _, plan, result, _ = self.answer.fetch_context(question)
                self.assertEqual(
                    (plan.aggregation, plan.aggregation_field, result["value"]),
                    ("sum", "Total_OT", expected),
                )
        _, plan, result, _ = self.answer.fetch_context(
            "Which department has the highest total overtime where Total OT equals 2?"
        )
        self.assertEqual(
            (
                plan.aggregation,
                plan.aggregation_field,
                plan.group_by,
                plan.order_by,
                plan.limit,
            ),
            ("sum", "Total_OT", ["Department"], "value", 1),
        )
        self.assertEqual(result["rows"], [{"group": ["Sales"], "value": 2.0}])
        _, plan, _, _ = self.answer.fetch_context(
            "Show latest 3 records where Total OT equals 2"
        )
        self.assertEqual(
            (plan.order_by, plan.order_direction, plan.limit), ("Date", "desc", 3)
        )
        _, plan, _, _ = self.answer.fetch_context(
            "Show Date and Status from records where Total OT equals 2"
        )
        self.assertEqual(plan.projection, ["Date", "Status"])

    def test_independent_unsupported_calculation_is_not_masked_by_constraint(self):
        for question in (
            "Median overtime where Total OT equals 2",
            "Count records where Total OT equals Unknown",
        ):
            with self.subTest(question=question):
                self.store.get.reset_mock()
                with self.assertRaises(self.answer.PlanValidationError):
                    self.answer.fetch_context(question)
                self.store.get.assert_not_called()

    def test_implicit_typed_suffixes_execute_the_same_constraints_as_explicit_eq(self):
        catalog = {
            "Department": ("Sales", "Sales and Job Services", "Support"),
            "Position": ("Engineer", "Officer"),
        }
        for department in ("Sales", "Sales and Job Services"):
            for row, location, position in zip(
                self.rows,
                (department, department, "Support", department, department),
                ("Engineer", "Officer", "Engineer", "Engineer", "Officer"),
            ):
                row.update(Department=location, Position=position)
            for field, operand, expected, count in (
                ("Position", "Engineer", "Engineer", 2),
                ("Status", "Authorized", "Authorized", 3),
                ("Day_Type", "Working Day", "Working Day", 3),
                ("Employee_ID", "A10001", "A10001", 2),
                ("Date", "2026-09-01", "2026-09-01", 2),
                ("Total_OT", "2", 2.0, 1),
            ):
                for introducer in ("", "where "):
                    for operator_text in ("", "equals "):
                        field_phrase = (
                            "overtime"
                            if field == "Total_OT"
                            else field.replace("_", " ")
                        )
                        with self.subTest(
                            department=department,
                            field=field,
                            introducer=introducer,
                            operator_text=operator_text,
                        ):
                            with patch.object(
                                self.answer,
                                "load_attendance_catalog_candidates",
                                return_value=catalog,
                            ):
                                _, plan, result, _ = self.answer.fetch_context(
                                    f"Count records {introducer}Department equals {department} and {field_phrase} {operator_text}{operand}"
                                )
                            self.assertEqual(result["value"], count)
                            condition = next(
                                item for item in plan.filters if item.field == field
                            )
                            self.assertEqual(
                                (condition.operator, condition.value), ("eq", expected)
                            )
                            self.assertEqual(
                                next(
                                    item.value
                                    for item in plan.filters
                                    if item.field == "Department"
                                ),
                                department,
                            )
            with patch.object(
                self.answer, "load_attendance_catalog_candidates", return_value=catalog
            ):
                _, plan, result, _ = self.answer.fetch_context(
                    f"Count records Department equals {department} and Position Engineer and Status Authorized and Date 2026-09-01"
                )
            self.assertEqual(result["value"], 1)
            self.assertEqual(
                {item.field for item in plan.filters},
                {"Department", "Position", "Status", "Date", "chunk_type"},
            )

    def test_malformed_implicit_suffix_never_authorizes_retrieval(self):
        catalog = {"Department": ("Sales",), "Position": ("Engineer",)}
        for introducer in ("", "where "):
            for suffix in (
                "Position Unknown",
                "Position Engineer North",
                "Position sounds like Engineer",
                "Status Authorizd",
                "Employee_ID A1",
                "Employee_ID Unknown",
                "Employee_ID sounds like A10001",
                "Employee_ID A10001 extra",
                "Date 2026-02-30",
                "Date 2026-09-01 extra",
                "Date sounds like 2026-09-01",
                "Total_OT 2 extra",
                "Total_OT sounds like 2",
            ):
                with self.subTest(introducer=introducer, suffix=suffix):
                    self.store.get.reset_mock()
                    with patch.object(
                        self.answer,
                        "load_attendance_catalog_candidates",
                        return_value=catalog,
                    ):
                        with self.assertRaises(self.answer.PlanValidationError):
                            self.answer.fetch_context(
                                f"Count records {introducer}Department equals Sales and {suffix}"
                            )
                    self.store.get.assert_not_called()

    def test_catalog_alias_conjunctions_execute_as_complete_literals(self):
        for field, value, prefix in (
            ("Department", "Sales and Job Services", "Sales"),
            ("Position", "Engineer and Department Services", "Engineer"),
            ("Job", "Analyst and Status Services", "Analyst"),
        ):
            population = (value, value + " East", prefix, "Field " + value, "Other")
            for row, entry in zip(self.rows, population):
                row[field] = entry
            for grammar, operator, scalar_count, restricted_count in (
                ("equals", "eq", 1, 1),
                ("is not", "ne", 4, 2),
                ("in", "in", 1, 1),
                ("contains", "contains", 3, 2),
                ("starts with", "starts_with", 2, 2),
            ):
                for introducer in ("", "where "):
                    for suffix, expected in (
                        ("", scalar_count),
                        (" and Status is Authorized", restricted_count),
                    ):
                        with self.subTest(
                            field=field,
                            grammar=grammar,
                            introducer=introducer,
                            suffix=suffix,
                        ):
                            with patch.object(
                                self.answer,
                                "load_attendance_catalog_candidates",
                                return_value={field: population},
                            ):
                                _, plan, result, _ = self.answer.fetch_context(
                                    f"Count records {introducer}{field} {grammar} {value}{suffix}"
                                )
                            self.assertEqual(result["value"], expected)
                            condition = next(
                                item for item in plan.filters if item.field == field
                            )
                            self.assertEqual(
                                (condition.operator, condition.value),
                                (operator, [value] if operator == "in" else value),
                            )
                            self.assertEqual(
                                {item.field for item in plan.filters},
                                {field, "chunk_type", "Status"}
                                if suffix
                                else {field, "chunk_type"},
                            )

    def test_atomic_catalog_members_compose_with_real_following_clauses(self):
        value = "Sales and Job Services"
        for row, department in zip(
            self.rows, (value, value, "Sales", "Other", "Other")
        ):
            row["Department"] = department
            row["Job"] = "Services"
        catalog = {"Department": (value, "Sales", "Other"), "Job": ("Services",)}
        for clause, expected in (
            (f"Department in {value} and Sales and Status is Authorized", 2),
            (
                f"Department equals {value} and Job equals Services and Status is Authorized",
                2,
            ),
            (f"Department equals {value} and Total_OT greater than 1", 1),
            (f"Department equals {value} and Date before 2026-09-02", 2),
        ):
            with self.subTest(clause=clause):
                with patch.object(
                    self.answer,
                    "load_attendance_catalog_candidates",
                    return_value=catalog,
                ):
                    _, plan, result, _ = self.answer.fetch_context(
                        f"Count records {clause}"
                    )
                self.assertEqual(result["value"], expected)
                condition = next(
                    item for item in plan.filters if item.field == "Department"
                )
                self.assertEqual(
                    condition.value,
                    [value, "Sales"] if condition.operator == "in" else value,
                )

    def test_invalid_suffix_after_atomic_catalog_member_never_retrieves(self):
        catalog = {
            "Department": ("Sales", "Sales and Job Services"),
            "Job": ("Services",),
        }
        for introducer in ("", "where "):
            for clause in (
                "Department in Sales and Job Services and Unknown",
                "Department equals Sales and Job Services and Job sounds like Services",
                "Department equals Sales and Job Services and Job equals Unknown",
            ):
                with self.subTest(introducer=introducer, clause=clause):
                    self.store.get.reset_mock()
                    with patch.object(
                        self.answer,
                        "load_attendance_catalog_candidates",
                        return_value=catalog,
                    ):
                        with self.assertRaises(self.answer.SemanticPlanValidationError):
                            self.answer.fetch_context(
                                f"Count records {introducer}{clause}"
                            )
                    self.store.get.assert_not_called()

    def test_incomplete_or_unsupported_catalog_clause_never_retrieves(self):
        for introducer in ("", "where "):
            for clause in (
                "Department equals Sales North",
                "Department in Sales and Unknown",
                "Department contains Sales North",
                "Department starts with Sales North",
                "Department sounds like Sales",
                "Department resembles Sales",
                "Department is approximately Sales",
            ):
                with self.subTest(introducer=introducer, clause=clause):
                    self.store.get.reset_mock()
                    with patch.object(
                        self.answer,
                        "load_attendance_catalog_candidates",
                        return_value={"Department": ("Sales", "Support")},
                    ):
                        with self.assertRaises(self.answer.SemanticPlanValidationError):
                            self.answer.fetch_context(
                                f"Count records {introducer}{clause}"
                            )
                    self.store.get.assert_not_called()

    def test_complete_lists_and_multiple_clauses_execute_all_constraints(self):
        for row, department in zip(
            self.rows, ("Sales", "Support", "Sales", "Sales North", "Operations")
        ):
            row["Department"] = department
        catalog = {"Department": ("Sales", "Support", "Sales North", "Operations")}
        for clause, expected in (
            ("Department in Sales and Support and Status is Authorized", 2),
            ("Department in Sales, Support and Status is Authorized", 2),
            ("Department in Sales and Support; Status is Authorized", 2),
            ("Department equals Sales North", 1),
            ("Department contains Sales North", 1),
            ("Department equals Sales and Status is Authorized", 1),
            ("Department in Sales and Support and Total_OT greater than 0", 2),
        ):
            with self.subTest(clause=clause):
                with patch.object(
                    self.answer,
                    "load_attendance_catalog_candidates",
                    return_value=catalog,
                ):
                    _, plan, result, _ = self.answer.fetch_context(
                        f"Count records {clause}"
                    )
                self.assertEqual(result["value"], expected)
                department = next(
                    item for item in plan.filters if item.field == "Department"
                )
                if clause.startswith("Department in"):
                    self.assertEqual(
                        (department.operator, department.value),
                        ("in", ["Sales", "Support"]),
                    )

    def test_list_members_can_contain_the_conjunction_word(self):
        for row, department in zip(
            self.rows,
            (
                "Research and Development",
                "Research and Development",
                "Sales",
                "Sales",
                "Support",
            ),
        ):
            row["Department"] = department
        with patch.object(
            self.answer,
            "load_attendance_catalog_candidates",
            return_value={
                "Department": ("Research and Development", "Sales", "Support")
            },
        ):
            _, plan, result, _ = self.answer.fetch_context(
                "Count records Department in Research and Development and Sales"
            )
        department = next(item for item in plan.filters if item.field == "Department")
        self.assertEqual(
            (department.operator, department.value),
            ("in", ["Research and Development", "Sales"]),
        )
        self.assertEqual(result["value"], 4)

    def test_unbound_catalog_operator_rejects_instead_of_counting_every_record(self):
        for prefix in ("Count records where", "Count records"):
            for grammar in (
                "in",
                "contains",
                "starts with",
                "is not",
                "does not contain",
            ):
                with self.subTest(prefix=prefix, grammar=grammar):
                    self.store.get.reset_mock()
                    with patch.object(
                        self.answer,
                        "load_attendance_catalog_candidates",
                        return_value={"Department": ("Sales",)},
                    ):
                        with self.assertRaises(self.answer.SemanticPlanValidationError):
                            self.answer.fetch_context(
                                f"{prefix} Department {grammar} Unknown"
                            )
                    self.store.get.assert_not_called()

    def test_categorical_operator_matrix_executes_restricted_populations(self):
        for field, value, population, counts in (
            (
                "Department",
                "Sales",
                ("Sales", "Sales", "Sales East", "Field Sales", "Support"),
                (2, 3, 2, 4, 3),
            ),
            (
                "Position",
                "Engineer",
                ("Engineer", "Engineer", "Engineer Lead", "Lead Engineer", "Officer"),
                (2, 3, 2, 4, 3),
            ),
            (
                "Status",
                "Authorized",
                ("Authorized", "Authorized", "Authorized", "Draft", "Draft"),
                (3, 2, 3, 3, 3),
            ),
            (
                "Day_Type",
                "OFF Day",
                ("OFF Day", "OFF Day", "OFF Day (ZAS)", "OFF Day (ZAS)", "Working Day"),
                (2, 3, 2, 4, 4),
            ),
        ):
            for row, entry in zip(self.rows, population):
                row[field] = entry
            for (grammar, operator), expected in zip(
                (
                    ("is", "eq"),
                    ("is not", "ne"),
                    ("in", "in"),
                    ("contains", "contains"),
                    ("starts with", "starts_with"),
                ),
                counts,
            ):
                with self.subTest(field=field, grammar=grammar):
                    with patch.object(
                        self.answer,
                        "load_attendance_catalog_candidates",
                        return_value={field: tuple(dict.fromkeys(population))},
                    ):
                        _, plan, result, _ = self.answer.fetch_context(
                            f"Count records where {field} {grammar} {value}"
                        )
                    filters = [item for item in plan.filters if item.field == field]
                    self.assertEqual(
                        [(item.operator, item.value) for item in filters],
                        [(operator, [value] if operator == "in" else value)],
                    )
                    self.assertEqual(result["value"], expected)

    def test_categorical_text_pattern_uses_literal_not_guessed_catalog_member(self):
        for row, value in zip(
            self.rows, ("Sales East", "Sales West", "Field Sales", "Support", "Support")
        ):
            row["Department"] = value
        for grammar, operator, expected in (
            ("contains", "contains", 3),
            ("starts with", "starts_with", 2),
        ):
            with self.subTest(grammar=grammar):
                with patch.object(
                    self.answer,
                    "load_attendance_catalog_candidates",
                    return_value={
                        "Department": (
                            "Sales East",
                            "Sales West",
                            "Field Sales",
                            "Support",
                        )
                    },
                ):
                    _, plan, result, _ = self.answer.fetch_context(
                        f"Count records where Department {grammar} Sales"
                    )
                self.assertIn(
                    ("Department", operator, "Sales"),
                    {(item.field, item.operator, item.value) for item in plan.filters},
                )
                self.assertEqual(result["value"], expected)

    def test_unrepresentable_categorical_operator_rejects_before_retrieval(self):
        for field, value in (("Department", "Sales"), ("Status", "Authorized")):
            for grammar in (
                "does not contain",
                "does not start with",
                "not in",
                "ends with",
                "sounds like",
                "greater than",
            ):
                with self.subTest(field=field, grammar=grammar):
                    self.store.get.reset_mock()
                    with patch.object(
                        self.answer,
                        "load_attendance_catalog_candidates",
                        return_value={"Department": ("Sales",)},
                    ):
                        rejection = (
                            self.answer.PlanValidationError
                            if grammar == "greater than"
                            else self.answer.SemanticPlanValidationError
                        )
                        with self.assertRaises(rejection):
                            self.answer.fetch_context(
                                f"Count records where {field} {grammar} {value}"
                            )
                    self.store.get.assert_not_called()

    def test_bare_unsupported_catalog_operator_rejects_before_retrieval(self):
        for grammar in (
            "does not contain",
            "does not start with",
            "not in",
            "ends with",
            "regex",
        ):
            with self.subTest(grammar=grammar):
                self.store.get.reset_mock()
                with patch.object(
                    self.answer,
                    "load_attendance_catalog_candidates",
                    return_value={"Department": ("Sales",)},
                ):
                    with self.assertRaises(self.answer.SemanticPlanValidationError):
                        self.answer.fetch_context(
                            f"Count records Department {grammar} Sales"
                        )
                self.store.get.assert_not_called()

    def test_explicit_distributive_totals_remain_grouped(self):
        for index, row in enumerate(self.rows):
            row["Department"] = "Alpha" if index < 3 else "Beta"
        for field, subject in (
            ("Department", "department"),
            ("Employee_ID", "employee"),
        ):
            for quantifier in ("each", "every"):
                for question in (
                    f"How many records does {quantifier} {subject} have in total?",
                    f"In total, how many records does {quantifier} {subject} have?",
                ):
                    with self.subTest(question=question):
                        _, plan, result, _ = self.answer.fetch_context(question)
                        self.assertEqual(plan.group_by, [field])
                        self.assertEqual(plan.answer_contract.shape, "grouped")
                        self.assertEqual(
                            sorted(row["value"] for row in result["rows"]), [2, 3]
                        )

    def test_collective_totals_defer_only_to_explicit_grouping(self):
        self.stack.enter_context(
            patch.object(
                self.answer,
                "load_attendance_catalog_candidates",
                return_value={"Department": ("Total", "Alpha", "Beta")},
            )
        )
        for index, row in enumerate(self.rows):
            row["Department"] = "Alpha" if index < 3 else "Beta"
        for quantifier, subject, verb in (
            ("all", "departments", "do"),
            ("any", "department", "does"),
        ):
            for modifier in ("combined", "in total"):
                with self.subTest(quantifier=quantifier, modifier=modifier):
                    _, scalar, result, _ = self.answer.fetch_context(
                        f"How many records {verb} {quantifier} {subject} have {modifier}?"
                    )
                    self.assertEqual(scalar.group_by, [])
                    self.assertEqual(result["value"], 5)
                    _, grouped, result, _ = self.answer.fetch_context(
                        f"Count records by {quantifier} {subject} {modifier}"
                    )
                    self.assertEqual(grouped.group_by, ["Department"])
                    self.assertEqual(
                        sorted(row["value"] for row in result["rows"]), [2, 3]
                    )

    def test_catalog_operation_word_does_not_filter_ranked_dimension(self):
        for index, row in enumerate(self.rows):
            row["Position"] = "Highest" if index < 3 else "Engineer"
        with patch.object(
            self.answer,
            "load_attendance_catalog_candidates",
            return_value={"Position": ("Highest", "Engineer")},
        ):
            _, plan, result, _ = self.answer.fetch_context(
                "Which Position has the highest total overtime?"
            )
        self.assertEqual(plan.group_by, ["Position"])
        self.assertEqual(
            (plan.order_by, plan.order_direction, plan.limit), ("value", "desc", 1)
        )
        self.assertFalse([item for item in plan.filters if item.field == "Position"])
        self.assertEqual(result["rows"], [{"group": ["Engineer"], "value": 4.0}])

    def test_categorical_constraint_matrix_preserves_implicit_and_explicit_counts(self):
        catalog = {
            "Department": ("Sales", "Support"),
            "Position": ("Engineer", "Officer"),
        }
        for field, value, other in (
            ("Status", "Authorized", "Draft"),
            ("Department", "Sales", "Support"),
            ("Position", "Engineer", "Officer"),
            ("Day_Type", "Working Day", "OFF Day"),
        ):
            for index, row in enumerate(self.rows):
                row[field] = value if index < 3 else other
            for question in (
                f"Count records where {field} equal to {value}",
                f"Count records in {value}",
                f"Count {value} records",
            ):
                with self.subTest(field=field, question=question):
                    with patch.object(
                        self.answer,
                        "load_attendance_catalog_candidates",
                        return_value=catalog,
                    ):
                        _, plan, result, _ = self.answer.fetch_context(question)
                    self.assertEqual(plan.aggregation, "count")
                    self.assertIn(
                        (field, "eq", value),
                        {
                            (item.field, item.operator, item.value)
                            for item in plan.filters
                        },
                    )
                    self.assertEqual(result["value"], 3)
        with patch.object(
            self.answer, "load_attendance_catalog_candidates", return_value=catalog
        ):
            _, plan, result, _ = self.answer.fetch_context("Count employees in Sales")
        self.assertEqual(plan.aggregation, "distinct_count")
        self.assertEqual(result["value"], 1)

    def test_collective_quantifier_modifiers_execute_scalar_totals(self):
        for index, row in enumerate(self.rows):
            row["Department"] = "Alpha" if index < 3 else "Beta"
        for subject in ("departments", "employees"):
            for question in (
                f"How many records do all {subject} have combined?",
                f"How many records do all {subject} have in total?",
                f"In total, how many records do all {subject} have?",
                f"Count records that belong to all {subject} combined",
            ):
                with self.subTest(question=question):
                    _, plan, calculation, _ = self.answer.fetch_context(question)
                    self.assertEqual(plan.group_by, [])
                    self.assertEqual(plan.answer_contract.shape, "scalar")
                    self.assertEqual(calculation["value"], 5)

    def test_preposed_bound_value_preserves_ranked_result(self):
        for index, row in enumerate(self.rows):
            row.update(Position="Highest", Department="Alpha" if index < 3 else "Beta")
        with patch.object(
            self.answer,
            "load_attendance_catalog_candidates",
            return_value={"Position": ("Highest",)},
        ):
            _, plan, calculation, _ = self.answer.fetch_context(
                "Among employees in the Highest position, which department has the highest total overtime?"
            )
        self.assertEqual(plan.group_by, ["Department"])
        self.assertEqual(
            (plan.order_by, plan.order_direction, plan.limit), ("value", "desc", 1)
        )
        self.assertIn(
            ("Position", "eq", "Highest"),
            {(item.field, item.operator, item.value) for item in plan.filters},
        )
        self.assertEqual(calculation["rows"], [{"group": ["Beta"], "value": 4.0}])

    def test_tied_value_binding_does_not_retrieve_a_guessed_plan(self):
        with patch.object(
            self.answer,
            "load_attendance_catalog_candidates",
            return_value={"Position": ("Highest",)},
        ):
            with self.assertRaises(self.answer.SemanticPlanValidationError):
                self.answer.fetch_context(
                    "Which department has the highest total overtime where Highest Position Highest?"
                )

    def test_quantified_grouping_relations_execute_grouped_counts(self):
        for index, row in enumerate(self.rows):
            row["Department"] = "Alpha" if index < 3 else "Beta"
        for field, singular, plural in (
            ("Department", "department", "departments"),
            ("Employee_ID", "employee", "employees"),
        ):
            for quantifier, noun, verb in (
                ("each", singular, "does"),
                ("every", singular, "does"),
                ("all", plural, "do"),
                ("any", singular, "does"),
            ):
                for question in (
                    f"How many records {verb} {quantifier} {noun} have?",
                    f"Count records by {quantifier} {noun}",
                ):
                    with self.subTest(question=question):
                        _, plan, calculation, _ = self.answer.fetch_context(question)
                        self.assertEqual(plan.group_by, [field])
                        self.assertEqual(plan.answer_contract.shape, "grouped")
                        self.assertEqual(
                            sorted(row["value"] for row in calculation["rows"]),
                            [2, 3],
                        )

    def test_collection_population_without_grouping_stays_scalar(self):
        for question in (
            "Count records for all employees",
            "Count records for any department",
        ):
            with self.subTest(question=question):
                _, plan, calculation, _ = self.answer.fetch_context(question)
                self.assertEqual(plan.group_by, [])
                self.assertEqual(plan.answer_contract.shape, "scalar")
                self.assertEqual(calculation["value"], 5)

    def test_bound_value_provenance_preserves_independent_ranked_result(self):
        for index, row in enumerate(self.rows):
            row.update(Position="Highest", Department="Alpha" if index < 3 else "Beta")
        for connector in ("is exactly", "has the value"):
            with self.subTest(connector=connector):
                with patch.object(
                    self.answer,
                    "load_attendance_catalog_candidates",
                    return_value={"Position": ("Highest",)},
                ):
                    _, plan, calculation, _ = self.answer.fetch_context(
                        "Which department has the highest total overtime "
                        f"where Position {connector} Highest?"
                    )
                self.assertEqual(plan.group_by, ["Department"])
                self.assertEqual(
                    (plan.order_by, plan.order_direction, plan.limit),
                    ("value", "desc", 1),
                )
                self.assertIn(
                    ("Position", "eq", "Highest"),
                    {(item.field, item.operator, item.value) for item in plan.filters},
                )
                self.assertEqual(
                    calculation["rows"], [{"group": ["Beta"], "value": 4.0}]
                )

    def test_bound_catalog_superlative_preserves_all_groups(self):
        for title in ("Highest Officer", "Lowest Officer", "Latest Officer"):
            with self.subTest(title=title):
                for index, row in enumerate(self.rows):
                    row.update(
                        Position=title, Department="Alpha" if index < 3 else "Beta"
                    )
                with patch.object(
                    self.answer,
                    "load_attendance_catalog_candidates",
                    return_value={"Position": (title,)},
                ):
                    _, plan, calculation, _ = self.answer.fetch_context(
                        f"Count attendance records by Department where Position equal to {title}"
                    )
                self.assertIsNone(plan.order_by)
                self.assertIsNone(plan.limit)
                self.assertEqual(len(calculation["rows"]), 2)
                self.assertEqual(
                    sorted(row["value"] for row in calculation["rows"]), [2, 3]
                )

    def test_behavior_compares_predicate_conjunctions_without_order(self):
        test = TestQuestion(
            question="How many days did not attend?",
            keywords=[],
            reference_answer="One scheduled date was not worked.",
            category="non_attended_days",
            expected_calculation={
                "value": 1,
                "business_predicates": ["scheduled_working_day", "not_worked"],
            },
        )
        result = evaluation.evaluate_behavior(test)
        self.assertTrue(result.calculation_ok)
        test.expected_calculation["business_predicates"] = ["scheduled_working_day"]
        self.assertFalse(evaluation.evaluate_behavior(test).calculation_ok)

    """Synthetic baseline behaviors through proposal, gate, retrieval and evaluator."""

    def setUp(self):
        from contextlib import ExitStack
        from types import SimpleNamespace
        from week5.new_implementation import answer

        self.answer = answer
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "status": "ready",
                                "answer_contract": {"shape": "rows", "unit": "value"},
                            }
                        )
                    )
                )
            ]
        )
        for name, kwargs in (
            ("completion", {"return_value": response}),
            ("_postgres_enabled", {"return_value": False}),
            ("load_attendance_catalog_candidates", {"return_value": {}}),
            (
                "load_employee_directory",
                {
                    "return_value": [
                        EmployeeCandidate(employee_id="A10001", name="Morgan River"),
                        EmployeeCandidate(employee_id="A10002", name="Morgan Lake"),
                    ]
                },
            ),
        ):
            self.stack.enter_context(patch.object(answer, name, **kwargs))
        self.store = self.stack.enter_context(patch.object(answer, "collection"))
        self.rows = [
            {
                "record_id": "r1",
                "Employee_ID": "A10001",
                "Name": "Morgan River",
                "Date": "2026-09-01",
                "Total_Worked_Hrs": 8.0,
                "Total_OT": 2.0,
                "Day_Type": "Working Day",
                "Status": "Authorized",
            },
            {
                "record_id": "r2",
                "Employee_ID": "A10001",
                "Name": "Morgan River",
                "Date": "2026-09-01",
                "Total_Worked_Hrs": 2.0,
                "Total_OT": 1.0,
                "Day_Type": "Working Day",
                "Status": "Authorized",
            },
            {
                "record_id": "r3",
                "Employee_ID": "A10001",
                "Name": "Morgan River",
                "Date": "2026-09-02",
                "Total_Worked_Hrs": 0.0,
                "Total_OT": 0.0,
                "Day_Type": "Working Day",
                "Status": "Draft",
            },
            {
                "record_id": "r4",
                "Employee_ID": "A10002",
                "Name": "Morgan Lake",
                "Date": "2026-09-03",
                "Total_Worked_Hrs": 0.0,
                "Total_OT": 0.0,
                "Day_Type": "OFF Day",
                "Status": "Draft",
            },
            {
                "record_id": "r5",
                "Employee_ID": "A10002",
                "Name": "Morgan Lake",
                "Date": "2026-09-04",
                "Total_Worked_Hrs": 7.0,
                "Total_OT": 4.0,
                "Day_Type": "Working Day",
                "Status": "Authorized",
            },
        ]
        for row in self.rows:
            row.update(domain="attendance", chunk_type="attendance_record")
        self.store.get.return_value = {
            "documents": ["Synthetic attendance evidence"] * len(self.rows),
            "metadatas": self.rows,
        }

    def test_baseline_counts_and_percentage_use_verified_plans(self):
        cases = (
            ("How many days were worked?", "distinct_count", "Date", 2, "dates"),
            ("How many days did not work?", "distinct_count", "Date", 2, "dates"),
            ("How many days did not attend?", "distinct_count", "Date", 1, "dates"),
            ("How many zero worked hours dates?", "distinct_count", "Date", 2, "dates"),
            ("Count working day schedule dates", "distinct_count", "Date", 3, "dates"),
            ("Count scheduled work dates", "distinct_count", "Date", 3, "dates"),
            (
                "How many scheduled days did not attend?",
                "distinct_count",
                "Date",
                1,
                "dates",
            ),
            ("Count attendance records", "count", None, 5, "records"),
            ("Count Authorized records", "count", None, 3, "records"),
            (
                "What percentage of records are Authorized?",
                "percentage",
                None,
                60.0,
                "percentage",
            ),
            ("Count records on 2026-09-01", "count", None, 2, "records"),
            ("Count records before 2026-09-03", "count", None, 3, "records"),
        )
        for question, operation, field, value, unit in cases:
            with self.subTest(question=question):
                chunks, plan, calculation, _count = self.answer.fetch_context(question)
                self.assertIsInstance(plan, self.answer.ExecutableQueryPlan)
                self.assertEqual(
                    (plan.aggregation, plan.aggregation_field), (operation, field)
                )
                self.assertEqual(plan.answer_contract.unit, unit)
                self.assertEqual(calculation["value"], value)
                self.assertTrue(chunks)

    def test_equivalent_worked_wording_compiles_the_same_operation_and_scope(self):
        plans = [
            self.answer.fetch_context(question)[1]
            for question in (
                "How many days were worked?",
                "How many dates attended?",
                "Count dates with positive worked hours",
            )
        ]
        for plan in plans:
            self.assertIsInstance(plan, self.answer.ExecutableQueryPlan)
            self.assertEqual(plan.aggregation, "distinct_count")
            self.assertEqual(plan.aggregation_field, "Date")
            self.assertEqual(
                {(f.field, f.operator, f.value) for f in plan.filters},
                {
                    ("Total_Worked_Hrs", "gt", 0.0),
                    ("chunk_type", "eq", "attendance_record"),
                },
            )

    def test_latest_records_and_projection_preserve_requested_rows(self):
        chunks, plan, _, _ = self.answer.fetch_context(
            "Show latest 2 attendance records"
        )
        self.assertIsInstance(plan, self.answer.ExecutableQueryPlan)
        self.assertEqual(
            (plan.order_by, plan.order_direction, plan.limit), ("Date", "desc", 2)
        )
        self.assertEqual(
            [chunk.metadata["record_id"] for chunk in chunks], ["r5", "r4"]
        )
        _, projection, _, _ = self.answer.fetch_context("Show employee ID and overtime")
        self.assertEqual(projection.projection, ["Employee_ID", "Total_OT"])
        self.assertEqual(projection.answer_contract.grain, ["Employee_ID", "Total_OT"])

    def test_grouped_highest_aggregate_is_evaluated_through_current_pipeline(self):
        case = TestQuestion(
            question="Which employee has the highest total overtime?",
            keywords=[],
            reference_answer="Synthetic grouped overtime result.",
            category="grouped_aggregate",
            expected_plan={
                "aggregation": "sum",
                "aggregation_field": "Total_OT",
                "order_by": "value",
                "order_direction": "desc",
                "limit": 1,
            },
            expected_group_values=[{"group": ["A10002"], "value": 4.0}],
        )
        result = evaluation.evaluate_behavior(case)
        self.assertTrue(all(result.model_dump().values()), result.model_dump())

    def test_employee_clarification_and_followup_preserve_scope(self):
        text, chunks, state = self.answer.answer_question_with_state(
            "Count Morgan's records", [], self.answer.ConversationState()
        )
        self.assertEqual(chunks, [])
        self.assertEqual(len(state.pending_candidates), 2)
        self.store.get.assert_not_called()
        text, _, state = self.answer.answer_question_with_state("A10001", [], state)
        self.assertIn("3", text)
        self.assertEqual([e.employee_id for e in state.selected_employees], ["A10001"])
        _, plan, calculation, _ = self.answer.fetch_context(
            "How many of his days were worked?",
            default_employees=state.selected_employees,
        )
        self.assertIsInstance(plan, self.answer.ExecutableQueryPlan)
        self.assertIn(
            ("Employee_ID", "A10001"), {(f.field, f.value) for f in plan.filters}
        )
        self.assertEqual(calculation["value"], 1)


class EvaluationWiringTests(unittest.TestCase):
    def test_semantic_rejection_cannot_pass_via_legacy_message_substring(self):
        from week5.new_implementation.plan_compiler import PlanViolation

        test = TestQuestion(
            question="Synthetic malformed attendance request",
            keywords=[],
            reference_answer="A controlled rejection.",
            category="malformed_input",
            expected_error="not supported",
            expected_exception_type="PlanValidationError",
        )
        rejection = evaluation.SemanticPlanValidationError(
            (
                PlanViolation(
                    "unsupported_capability",
                    "unsupported_calculation",
                    "This request is not supported.",
                ),
            )
        )

        with patch.object(evaluation, "fetch_context", side_effect=rejection):
            result = evaluation.evaluate_behavior(test)

        self.assertFalse(result.plan_ok)
        self.assertFalse(result.expected_error_ok)
        self.assertFalse(result.unsupported_capabilities_ok)

    def test_preflight_error_requires_exact_controlled_exception_type(self):
        from week5.new_implementation.answer import PlanValidationError

        test = TestQuestion(
            question="Synthetic malformed attendance request",
            keywords=[],
            reference_answer="A controlled rejection.",
            category="malformed_input",
            expected_error="invalid numeric comparison",
            expected_exception_type="PlanValidationError",
        )

        with patch.object(
            evaluation,
            "fetch_context",
            side_effect=PlanValidationError("Invalid numeric comparison"),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertTrue(result.expected_error_ok)
        self.assertTrue(result.plan_ok)

        wrong_type = test.model_copy(
            update={"expected_exception_type": "DomainAccessDeniedError"}
        )
        with patch.object(
            evaluation,
            "fetch_context",
            side_effect=PlanValidationError("Invalid numeric comparison"),
        ):
            rejected = evaluation.evaluate_behavior(wrong_type)
        self.assertFalse(rejected.expected_error_ok)

    def test_missing_private_corpus_has_an_actionable_error(self):
        missing = Path(__file__).with_name("missing-private-corpus.jsonl")
        with (
            patch.object(evaluation_cases, "TEST_FILE", str(missing)),
            self.assertRaisesRegex(FileNotFoundError, "private evaluation corpus"),
        ):
            evaluation_cases.load_tests()

    def test_missing_private_manifest_has_an_actionable_error(self):
        missing = Path(__file__).with_name("missing-private-manifest.json")
        with (
            patch.object(evaluation, "DATASET_MANIFEST_PATH", missing),
            self.assertRaisesRegex(FileNotFoundError, "private dataset manifest"),
        ):
            evaluation.verify_dataset()

    def test_behavior_checks_every_required_filter(self):
        test = TestQuestion(
            question="Show September records.",
            keywords=[],
            reference_answer="September records.",
            category="date_filter",
            expected_plan={
                "required_filters": [
                    {"field": "Date", "operator": "gte", "value": "2026-09-01"},
                    {"field": "Date", "operator": "lte", "value": "2026-09-30"},
                ]
            },
        )
        plan = evaluation.QueryPlan(
            mode="exact",
            search_query="September",
            filters=[FilterCondition(field="Date", operator="gte", value="2026-09-01")],
        )

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=([], plan, None, 0),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertFalse(result.plan_ok)

    def test_behavior_accepts_all_required_filters_when_present(self):
        required_filters = [
            {"field": "Date", "operator": "gte", "value": "2026-09-01"},
            {"field": "Date", "operator": "lte", "value": "2026-09-30"},
        ]
        test = TestQuestion(
            question="Show September records.",
            keywords=[],
            reference_answer="September records.",
            category="date_filter",
            expected_plan={"required_filters": required_filters},
        )
        plan = evaluation.QueryPlan(
            mode="exact",
            search_query="September",
            filters=[FilterCondition(**condition) for condition in required_filters],
        )

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=([], plan, None, 0),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertTrue(result.plan_ok)

    def test_behavior_checks_grouped_values_not_only_group_count(self):
        test = TestQuestion(
            question="Count attendance records by Status.",
            keywords=[],
            reference_answer="Grouped status counts.",
            category="grouped_aggregate",
            expected_group_values=[
                {"group": ["Authorized"], "value": 775},
            ],
        )
        plan = evaluation.QueryPlan(
            mode="exact", search_query="attendance", aggregation="count"
        )
        calculation = {
            "operation": "count",
            "group_by": ["Status"],
            "total_groups": 1,
            "rows": [{"group": ["Authorized"], "value": 1}],
        }

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=([], plan, calculation, 3964),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertFalse(result.group_values_ok)

    def test_behavior_checks_expected_record_ids(self):
        test = TestQuestion(
            question="Show attendance on 2026-09-05.",
            keywords=[],
            reference_answer="The September 5 record.",
            category="exact_filter",
            expected_record_ids=["expected-record"],
        )
        plan = evaluation.QueryPlan(mode="exact", search_query="attendance")
        documents = [Result(page_content="", metadata={"record_id": "wrong-record"})]

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(documents, plan, None, 1),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertFalse(result.record_ids_ok)

    def test_successful_selection_requires_pending_clarification_to_clear(self):
        candidate = EmployeeCandidate(employee_id="A11000", name="Example")
        pending = evaluation.ConversationState(
            pending_question="Which Example?",
            pending_candidates=[candidate],
            selected_employees=[candidate],
        )
        test = TestQuestion(
            question="Show Example attendance.",
            keywords=[],
            reference_answer="Clarify then execute.",
            category="employee_ambiguity",
            expected_clarification_ids=["A11000"],
            turns=[{"user": "1", "expected_employee_ids": ["A11000"]}],
        )
        with patch.object(
            evaluation,
            "answer_question_with_state",
            side_effect=[
                ("Choose A11000", [], pending),
                ("Choose A11000", [], pending),
            ],
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertFalse(result.multi_turn_ok)

    def test_successful_selection_rejects_a_stale_pending_question(self):
        candidate = EmployeeCandidate(employee_id="A11000", name="Example")
        clarification = evaluation.ConversationState(
            pending_question="Show Example attendance.",
            pending_candidates=[candidate],
        )
        stale_success = evaluation.ConversationState(
            pending_question="Show Example attendance.",
            selected_employees=[candidate],
        )
        test = TestQuestion(
            question="Show Example attendance.",
            keywords=[],
            reference_answer="Clarify then execute.",
            category="employee_ambiguity",
            expected_clarification_ids=["A11000"],
            turns=[{"user": "1", "expected_employee_ids": ["A11000"]}],
        )
        with patch.object(
            evaluation,
            "answer_question_with_state",
            side_effect=[
                ("Choose A11000", [], clarification),
                ("Done", [], stale_success),
            ],
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertFalse(result.multi_turn_ok)

    @unittest.skipUnless(
        evaluation.DATASET_MANIFEST_PATH.is_file(),
        "private APDC dataset manifest is not available in this checkout",
    )
    def test_direct_evaluator_dataset_verification_is_supported(self):
        evaluation_dir = Path(__file__).parent
        completed = subprocess.run(
            [sys.executable, "eval.py", "--verify-dataset"],
            cwd=evaluation_dir,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_expected_subset_accepts_display_precision_for_numeric_results(self):
        self.assertTrue(
            evaluation._expected_subset(
                {"operation": "percentage", "value": 19.5509586276},
                {"operation": "percentage", "value": 19.55},
            )
        )

    def test_retrieval_evaluation_uses_documents_from_fetch_context_tuple(self):
        test = TestQuestion(
            question="Who attended?",
            keywords=["Sample"],
            reference_answer="Sample attended.",
            category="direct_fact",
        )
        documents = [
            Result(
                page_content="Name: Sample",
                metadata={"Name": "Sample"},
            )
        ]

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(documents, None, None, 1),
        ):
            result = evaluation.evaluate_retrieval(test)

        self.assertEqual(result.keywords_found, 1)
        self.assertEqual(result.total_keywords, 1)

    def test_behavior_evaluation_checks_normalized_identity_and_calculation(self):
        test = TestQuestion(
            question="How many days did A10029 work?",
            keywords=["A10029"],
            reference_answer="5 worked days.",
            category="worked_days",
            expected_employee_ids=["A10029"],
            expected_calculation={
                "operation": "distinct_count",
                "field": "Date",
                "value": 5,
            },
        )
        plan = evaluation.QueryPlan(
            mode="exact",
            search_query="worked days",
            filters=[
                FilterCondition(field="Employee_ID", operator="eq", value="A10029")
            ],
            aggregation="distinct_count",
            aggregation_field="Date",
        )

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                plan,
                {"operation": "distinct_count", "field": "Date", "value": 5},
                5,
            ),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertTrue(result.employee_ids_ok)
        self.assertTrue(result.calculation_ok)

    def test_behavior_evaluation_compares_business_predicates_as_a_set(self):
        test = TestQuestion(
            question="How many days did A11017 not attend?",
            keywords=[],
            reference_answer="One scheduled working day was not attended.",
            category="non_attended_days",
            expected_plan={
                "measure": "distinct_dates",
                "business_predicates": [
                    "not_worked",
                    "scheduled_working_day",
                ],
            },
        )
        normalized = evaluation.QueryPlan(
            mode="exact",
            search_query="not attended",
            measure="distinct_dates",
            business_predicates=["scheduled_working_day", "not_worked"],
        )

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                normalized,
                {"operation": "distinct_count", "field": "Date", "value": 1},
                1,
            ),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertTrue(result.plan_ok)

    def test_answer_facts_are_checked_against_rendered_answer_not_dict_repr(self):
        test = TestQuestion(
            question="How many records?",
            keywords=[],
            reference_answer="Seven records.",
            category="exact_filter",
            expected_answer_facts=["secret_internal_marker"],
        )
        plan = evaluation.QueryPlan(
            mode="exact", search_query="attendance", aggregation="count"
        )
        with (
            patch.object(
                evaluation,
                "fetch_context",
                return_value=(
                    [],
                    plan,
                    {"operation": "count", "value": 7, "secret_internal_marker": 1},
                    7,
                ),
            ),
            patch.object(
                evaluation,
                "_format_aggregation_answer",
                return_value="7 attendance records matched.",
            ),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertFalse(result.answer_facts_ok)

    def test_behavior_evaluation_renders_calculation_with_retrieved_plan(self):
        test = TestQuestion(
            question="How many employees worked?",
            keywords=[],
            reference_answer="Two employees worked.",
            category="exact_filter",
            expected_answer_facts=["employees", "worked"],
        )
        plan = evaluation.QueryPlan(
            mode="exact",
            search_query="employees worked",
            measure="employees",
            business_predicates=["worked"],
            aggregation="distinct_count",
            aggregation_field="Employee_ID",
        )

        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                plan,
                {
                    "operation": "distinct_count",
                    "field": "Employee_ID",
                    "value": 2,
                },
                2,
            ),
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertTrue(result.answer_facts_ok)

    def test_semantic_category_requires_semantic_plan_mode(self):
        test = TestQuestion(
            question="Find unusual patterns.",
            keywords=[],
            reference_answer="Semantic evidence.",
            category="semantic",
        )
        with patch.object(
            evaluation,
            "fetch_context",
            return_value=(
                [],
                evaluation.QueryPlan(mode="exact", search_query="patterns"),
                None,
                0,
            ),
        ):
            result = evaluation.evaluate_behavior(test)
        self.assertFalse(result.plan_ok)

    def test_multi_turn_clarification_asserts_selected_employee(self):
        candidates = [
            EmployeeCandidate(employee_id="A11000", name="First Example"),
            EmployeeCandidate(employee_id="A10651", name="Second Example"),
        ]
        pending = evaluation.ConversationState(
            pending_question="Which Example?",
            pending_candidates=[
                *candidates,
            ],
        )
        selected = evaluation.ConversationState(selected_employees=[candidates[0]])
        test = TestQuestion(
            question="Show Example attendance.",
            keywords=[],
            reference_answer="Clarify then select.",
            category="employee_ambiguity",
            expected_clarification_ids=["A11000", "A10651"],
            turns=[{"user": "1", "expected_employee_ids": ["A11000"]}],
        )
        with patch.object(
            evaluation,
            "answer_question_with_state",
            side_effect=[
                ("Choose A11000 or A10651", [], pending),
                ("Selected A11000", [], selected),
            ],
        ):
            result = evaluation.evaluate_behavior(test)

        self.assertTrue(result.clarification_ok)
        self.assertTrue(result.multi_turn_ok)


@unittest.skipUnless(
    PRIVATE_FIXTURES_AVAILABLE,
    "private APDC evaluation fixtures are not available in this checkout",
)
class AttendanceCorpusTests(unittest.TestCase):
    @staticmethod
    def _attendance_records():
        return [
            json.loads(line)
            for line in PRIVATE_ATTENDANCE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_corpus_is_fully_replaced_with_attendance_cases(self):
        tests = load_evaluation_tests()
        combined = " ".join(
            " ".join([test.question, test.reference_answer, test.category])
            for test in tests
        )

        self.assertGreaterEqual(len(tests), 300)
        self.assertNotIn("insurellm", combined.casefold())
        self.assertFalse(
            {"insurance_product", "contract", "salary"}
            & {test.category for test in tests}
        )
        self.assertTrue(
            {
                "worked_days",
                "scheduled_days",
                "authorized_records",
                "employee_ambiguity",
                "malformed_input",
                "exact_filter",
                "semantic",
                "hybrid",
                "grouped_aggregate",
                "percentage",
                "date_filter",
                "field_filter",
                "multi_employee",
            }.issubset({test.category for test in tests})
        )
        self.assertEqual(
            sum(
                bool(test.turns)
                for test in tests
                if test.category == "employee_ambiguity"
            ),
            20,
        )
        self.assertGreaterEqual(
            sum(bool(test.expected_record_ids) for test in tests), 5
        )
        self.assertGreaterEqual(
            sum(bool(test.expected_normalized_result) for test in tests), 5
        )

    def test_corpus_expectations_are_well_formed_and_reference_live_records(self):
        tests = load_evaluation_tests()
        records = self._attendance_records()
        employee_ids = {record["Employee_ID"] for record in records}

        questions = [test.question.casefold().strip() for test in tests]
        self.assertEqual(len(questions), len(set(questions)))
        self.assertTrue(all(test.question.strip() for test in tests))
        self.assertTrue(all(test.reference_answer.strip() for test in tests))
        self.assertTrue(all(test.category.strip() for test in tests))

        for test in tests:
            referenced_ids = {
                *test.expected_employee_ids,
                *test.expected_clarification_ids,
                *(
                    employee_id
                    for turn in test.turns
                    for employee_id in turn.get("expected_employee_ids", [])
                ),
                *(
                    employee_id
                    for turn in test.turns
                    for employee_id in turn.get("expected_pending_ids", [])
                ),
            }
            self.assertTrue(
                referenced_ids <= employee_ids,
                f"{test.question!r} references unknown IDs: "
                f"{sorted(referenced_ids - employee_ids)}",
            )

            calculation = test.expected_calculation
            if calculation and "value" in calculation:
                rendered_value = str(calculation["value"])
                self.assertTrue(
                    rendered_value in test.reference_answer
                    or rendered_value in test.expected_answer_facts,
                    f"{test.question!r} does not expose its expected value",
                )

    def test_deterministic_calculation_expectations_match_source_data(self):
        records = self._attendance_records()

        for test in load_evaluation_tests():
            expected = test.expected_calculation
            if not expected:
                continue

            selected = records
            if test.expected_employee_ids:
                selected = [
                    record
                    for record in selected
                    if record["Employee_ID"] in test.expected_employee_ids
                ]

            expected_plan = test.expected_plan or {}
            required_filters = list(expected_plan.get("required_filters", []))
            if expected_plan.get("required_filter"):
                required_filters.append(expected_plan["required_filter"])
            for required_filter in required_filters:
                field = required_filter["field"]
                operator = required_filter["operator"]
                value = required_filter["value"]

                def comparable(actual):
                    if isinstance(value, (int, float)):
                        return float(actual or 0), float(value)
                    return str(actual), str(value)

                predicates = {
                    "eq": lambda actual: actual == value,
                    "ne": lambda actual: actual != value,
                    "gt": lambda actual: comparable(actual)[0] > comparable(actual)[1],
                    "gte": lambda actual: comparable(actual)[0]
                    >= comparable(actual)[1],
                    "lt": lambda actual: comparable(actual)[0] < comparable(actual)[1],
                    "lte": lambda actual: comparable(actual)[0]
                    <= comparable(actual)[1],
                    "in": lambda actual: actual in value,
                }
                selected = [
                    record
                    for record in selected
                    if predicates[operator](record.get(field))
                ]
            if not required_filters and test.category == "worked_days":
                selected = [
                    record
                    for record in selected
                    if float(record.get("Total_Worked_Hrs") or 0) > 0
                ]

            operation = expected["operation"]
            field = expected.get("field")
            if operation == "percentage":
                match = re.search(
                    r'have ([A-Za-z_]+) equal to "([^"]+)"', test.question
                )
                self.assertIsNotNone(match, test.question)
                numerator = sum(
                    record.get(match.group(1)) == match.group(2) for record in records
                )
                actual = {
                    "numerator": numerator,
                    "denominator": len(records),
                    "value": round(numerator / len(records) * 100, 2),
                }
                for key, value in actual.items():
                    self.assertEqual(expected[key], value, test.question)
                continue

            group_by = expected.get("group_by")
            if group_by:
                grouped_records = {}
                for record in selected:
                    key = tuple(record.get(group_field) for group_field in group_by)
                    grouped_records.setdefault(key, []).append(record)
                self.assertEqual(
                    expected["total_groups"], len(grouped_records), test.question
                )
                actual_group_values = []
                for key, group_records in grouped_records.items():
                    if operation == "count":
                        value = len(group_records)
                    elif operation == "distinct_count":
                        value = len({record.get(field) for record in group_records})
                    elif operation == "sum":
                        value = round(
                            sum(
                                float(record.get(field) or 0)
                                for record in group_records
                            ),
                            6,
                        )
                    elif operation == "average":
                        present_values = [
                            float(record[field])
                            for record in group_records
                            if record.get(field) is not None
                        ]
                        value = round(
                            sum(present_values) / len(present_values),
                            6,
                        )
                    else:
                        self.fail(f"Unsupported grouped operation {operation!r}")
                    actual_group_values.append({"group": list(key), "value": value})
                self.assertTrue(
                    evaluation._expected_group_values_match(
                        {"rows": actual_group_values}, test.expected_group_values
                    ),
                    test.question,
                )
                continue

            if "value" not in expected:
                continue
            if operation == "count":
                actual_value = len(selected)
            elif operation == "distinct_count":
                actual_value = len({record.get(field) for record in selected})
            elif operation == "sum":
                actual_value = round(
                    sum(float(record.get(field) or 0) for record in selected), 2
                )
            else:
                self.fail(f"Unsupported source-truth operation {operation!r}")
            self.assertEqual(expected["value"], actual_value, test.question)


if __name__ == "__main__":
    unittest.main()
