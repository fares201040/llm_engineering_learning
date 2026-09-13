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


class BaselineCapabilityParityTests(unittest.TestCase):
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
