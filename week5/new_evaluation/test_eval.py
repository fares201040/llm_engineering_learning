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


class EvaluationWiringTests(unittest.TestCase):
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
