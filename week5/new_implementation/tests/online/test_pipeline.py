from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import patch

import psycopg

from week5.new_implementation.online.context import DatabaseColumn
from week5.new_implementation.online.execution import (
    AccessContext,
    AttendanceRowScope,
    ExecutionCoverage,
    LOCAL_DEMO_ACCESS,
    SqlExecutionResult,
    search_employee_directory_postgres,
)
from week5.new_implementation.online.pipeline import (
    Answered,
    Clarification,
    Failed,
    RuntimeDependencies,
    TurnRequest,
    Unsupported,
    _locale,
    _has_required_date_scope,
    _mentions_time_period,
    _pending_response,
    _previous_having,
    _request_value_issue,
    _repair_group_order,
    _sql_date_scope,
    _sql_semantic_issue,
    run_turn,
)
from week5.new_implementation.online import pipeline
from week5.new_implementation.online.planner import ReplanRequest
from week5.new_implementation.online.provider import ProviderFailure
from week5.new_implementation.online.reference import (
    AmbiguousReference,
    Employee,
    EmployeeOption,
    PendingEmployeeConfirmation,
    PendingResolution,
    ReadyReference,
    ReferenceResponse,
    UnsupportedReference,
    bind_references,
)
from week5.new_implementation.online.state import ConversationState, VerifiedTurn
from week5.new_implementation.tests.online.test_query import database_context
from week5.new_implementation.tests.online.test_comparison import attendance_schema
from week5.new_implementation.config import settings


def ready_reference(*_args, **_kwargs):
    return ReferenceResponse(
        decision=ReadyReference(
            rewritten_request="Show absence dates for employee A1.",
            locale="en",
            request_relationship="new",
            subject_relationship="employees",
            employee_ids=("A1",),
        )
    )


def sql_result():
    return SqlExecutionResult(
        columns=(),
        rows=({"attendance_date": "2026-09-03"},),
        coverage=ExecutionCoverage(
            fetched_rows=1,
            result_limit=100,
            response_bytes=34,
        ),
    )


def database_context_with_departments():
    schema = database_context()
    table = schema.tables[0]
    return schema.model_copy(
        update={
            "tables": (
                table.model_copy(
                    update={
                        "columns": table.columns
                        + (
                            DatabaseColumn(
                                name="department",
                                data_type="text",
                                nullable=False,
                                description="Department assigned to the employee.",
                                standard_values=(
                                    "Finance",
                                    "Human Resource",
                                    "Information Technology",
                                    "Operations",
                                ),
                            ),
                        )
                    }
                ),
            )
        }
    )


def database_context_with_locations():
    schema = database_context()
    table = schema.tables[0]
    return schema.model_copy(
        update={
            "tables": (
                table.model_copy(
                    update={
                        "columns": table.columns
                        + (
                            DatabaseColumn(
                                name="work_location",
                                data_type="text",
                                nullable=True,
                                description="Assigned work location.",
                                standard_values=("MES-Eng", "N/NA"),
                            ),
                        )
                    }
                ),
            )
        }
    )


class PipelineTests(unittest.TestCase):
    def test_independent_clauses_do_not_get_one_global_status_filter(self):
        sql = (
            "WITH auth AS (SELECT country, COUNT(*) AS n FROM attendance_records "
            "WHERE status = 'Authorized' GROUP BY country), "
            "hr AS (SELECT work_location, COUNT(DISTINCT employee_id) AS n "
            "FROM attendance_records WHERE department = 'Human Resource' "
            "GROUP BY work_location) "
            "SELECT country AS group_name, n FROM auth UNION ALL "
            "SELECT work_location AS group_name, n FROM hr"
        )
        self.assertIsNone(
            _sql_semantic_issue(
                "Authorized by country; HR people by location",
                sql,
                independent_clauses=True,
            )
        )
        self.assertIsNone(
            _sql_semantic_issue(
                "Authorized by country; HR people by location on Sep 5 2026",
                sql.replace(
                    "WHERE department = 'Human Resource'",
                    "WHERE department = 'Human Resource' "
                    "AND attendance_date = '2026-09-05'",
                ),
                required_date_scope=("2026-09-05", "2026-09-05"),
                independent_clauses=True,
            )
        )

    def test_extra_sql_sources_cannot_erase_single_scope_requirements(self):
        sql = (
            "WITH first AS (SELECT COUNT(*) AS n FROM attendance_records), "
            "second AS (SELECT COUNT(*) AS n FROM attendance_records) "
            "SELECT n FROM first UNION ALL SELECT n FROM second"
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Count Draft records on September 5, 2026",
                sql,
                required_date_scope=("2026-09-05", "2026-09-05"),
                independent_clauses=True,
            ),
            "date_scope_mismatch",
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Count Draft records",
                sql,
                independent_clauses=True,
            ),
            "missing_workflow_status_filter",
        )

    def test_grouped_summary_is_not_forced_into_detail_rows(self):
        self.assertIsNone(
            _sql_semantic_issue(
                "Show attendance summary by department",
                "SELECT department, COUNT(*) AS record_count "
                "FROM attendance_records GROUP BY department",
            )
        )

    def test_explicit_day_and_range_are_not_promoted_to_full_month(self):
        self.assertEqual(
            pipeline._requested_date_scope("Show 4 September 2026."),
            ("2026-09-04", "2026-09-04"),
        )
        self.assertEqual(
            pipeline._requested_date_scope("Show September 1-7, 2026."),
            ("2026-09-01", "2026-09-07"),
        )
        self.assertEqual(
            pipeline._requested_date_scope("Show September 2026."),
            ("2026-09-01", "2026-09-30"),
        )

    def test_iso_and_written_endpoint_ranges_are_single_intervals(self):
        self.assertEqual(
            pipeline._requested_date_scope(
                "Show records from 2026-09-01 to 2026-09-07."
            ),
            ("2026-09-01", "2026-09-07"),
        )
        self.assertEqual(
            pipeline._requested_date_scope(
                "Show records from September 1 to September 7, 2026."
            ),
            ("2026-09-01", "2026-09-07"),
        )

    def test_distinct_explicit_periods_have_no_single_global_date_scope(self):
        self.assertIsNone(
            pipeline._requested_date_scope(
                "Compare September 1, 2026 and September 4, 2026."
            )
        )
        self.assertIsNone(
            pipeline._requested_date_scope("Compare September and October 2026.")
        )
        self.assertIsNone(
            pipeline._required_date_scope(
                "Compare Sep & Oct 2026.",
                request_relationship="new",
                subject_relationship="all_authorized",
                previous_scope=None,
                rewritten_request="Compare attendance in October 2026.",
            )
        )

    def test_compound_union_does_not_inherit_one_global_period(self):
        prior = ("2026-09-01", "2026-09-07")
        self.assertIsNone(
            pipeline._required_date_scope(
                "For that employee, list Draft dates. Separately rank Positions globally.",
                request_relationship="follow_up",
                subject_relationship="union",
                previous_scope=prior,
            )
        )
        self.assertEqual(
            pipeline._required_date_scope(
                "Which dates had Status Draft?",
                request_relationship="follow_up",
                subject_relationship="employees",
                previous_scope=prior,
            ),
            prior,
        )

    def test_previous_sql_date_is_not_a_hard_requirement_for_follow_up(self):
        prior = VerifiedTurn(
            turn_id="prior",
            original_question="Show status totals across all records.",
            rewritten_request="Show status totals across all records.",
            answer="Done.",
            locale="en",
            executed_sql="SELECT COUNT(*) FROM attendance_records WHERE attendance_date = '2026-09-05'",
            date_scope=("2026-09-05", "2026-09-05"),
        )
        self.assertIsNone(pipeline._verified_requested_date_scope(prior))

    def test_resolved_relative_date_can_be_carried_to_follow_up(self):
        prior = VerifiedTurn(
            turn_id="prior",
            original_question="Show yesterday's attendance.",
            rewritten_request="Show yesterday's attendance.",
            requested_date_scope=("2026-09-28", "2026-09-28"),
            answer="Done.",
            locale="en",
            executed_sql="SELECT attendance_date FROM attendance_records WHERE attendance_date = '2026-09-28'",
            date_scope=("2026-09-28", "2026-09-28"),
        )
        self.assertEqual(
            pipeline._verified_requested_date_scope(prior),
            ("2026-09-28", "2026-09-28"),
        )

    def test_employee_union_keeps_one_explicit_shared_date(self):
        self.assertEqual(
            pipeline._required_date_scope(
                "Compare A1 and A2 on September 1, 2026.",
                request_relationship="new",
                subject_relationship="union",
                previous_scope=None,
            ),
            ("2026-09-01", "2026-09-01"),
        )
        self.assertIsNone(
            pipeline._required_date_scope(
                "A1 dates on September 1, 2026; all departments across all dates.",
                request_relationship="new",
                subject_relationship="union",
                union_has_criteria=True,
                previous_scope=None,
            )
        )

    def test_short_date_follow_up_uses_resolved_current_date(self):
        self.assertEqual(
            pipeline._required_date_scope(
                "same on sep 2",
                request_relationship="follow_up",
                subject_relationship="employees",
                previous_scope=("2026-09-01", "2026-09-01"),
                rewritten_request="Show the same measures on September 2, 2026.",
            ),
            ("2026-09-02", "2026-09-02"),
        )

    def test_ordinal_day_follow_up_replaces_previous_range(self):
        self.assertEqual(
            pipeline._required_date_scope(
                "him on 2nd, shift + status?",
                request_relationship="follow_up",
                subject_relationship="employees",
                previous_scope=("2026-09-01", "2026-09-07"),
                rewritten_request="For that employee on September 2, 2026, show shift and status.",
            ),
            ("2026-09-02", "2026-09-02"),
        )
        self.assertEqual(
            pipeline._required_date_scope(
                "what about 5th?",
                request_relationship="follow_up",
                subject_relationship="employees",
                previous_scope=("2026-09-02", "2026-09-02"),
                rewritten_request="For that employee on September 5, 2026, show shift and status.",
            ),
            ("2026-09-05", "2026-09-05"),
        )
        self.assertFalse(_mentions_time_period("How many worked a 2nd Shift?"))

    def test_verified_mixed_sql_preserves_previous_employee_antecedent(self):
        employee = Employee(employee_id="A11026", name="Wail Saleh Awadh")
        sql = (
            "WITH person_rows AS (SELECT attendance_date FROM attendance_records "
            "WHERE employee_id = 'A11026' AND status = 'Draft'), "
            "global_counts AS (SELECT position, COUNT(*) AS n FROM "
            "attendance_records GROUP BY position) "
            "SELECT CAST(attendance_date AS text) AS value FROM person_rows "
            "UNION ALL SELECT position FROM global_counts"
        )
        self.assertEqual(
            pipeline._carried_verified_employees(
                sql, (employee,), request_relationship="follow_up"
            ),
            (employee,),
        )
        self.assertEqual(
            pipeline._carried_verified_employees(
                sql, (employee,), request_relationship="new"
            ),
            (),
        )

    def test_scalar_lookup_employee_does_not_become_active_subject(self):
        employee = Employee(employee_id="A1", name="Wail Ali")
        sql = (
            "SELECT COUNT(*) FROM attendance_records WHERE position = "
            "(SELECT position FROM attendance_records "
            "WHERE employee_id = 'A1' LIMIT 1)"
        )
        self.assertEqual(
            pipeline._carried_verified_employees(
                sql, (employee,), request_relationship="follow_up"
            ),
            (),
        )

    def test_mixed_follow_up_publishes_employee_used_by_verified_sql(self):
        employee = Employee(employee_id="A1", name="Wail Ali")
        prior = ConversationState(
            verified_turns=(
                VerifiedTurn(
                    turn_id="prior",
                    original_question="Show A1 attendance.",
                    rewritten_request="Show A1 attendance.",
                    answer="A1 has records.",
                    locale="en",
                    employees=(employee,),
                    executed_sql="SELECT employee_id FROM attendance_records WHERE employee_id = 'A1'",
                ),
            ),
            active_employee_ids=("A1",),
            active_employees=(employee,),
        )
        deps = self.dependencies()
        deps.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show A1 dates and all Positions.",
                locale="en",
                request_relationship="follow_up",
                subject_relationship="all_authorized",
            )
        )
        deps.planner = lambda **_kwargs: (
            "SELECT CAST(attendance_date AS text) AS value FROM attendance_records "
            "WHERE employee_id = 'A1' UNION ALL "
            "SELECT position AS value FROM attendance_records"
        )
        deps.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"value": "2026-09-01"},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=30
            ),
        )
        deps.answerer = lambda **_kwargs: "A1 had attendance; a Position was found."
        outcome = run_turn(
            TurnRequest(
                question="Show those dates and all Positions.",
                state=prior,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=deps,
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(outcome.state.active_employee_ids, ("A1",))
        self.assertEqual(outcome.state.verified_turns[-1].employees, (employee,))

    def test_two_fuzzy_name_parts_offer_one_employee_for_confirmation(self):
        dependencies = self.dependencies()
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A10218", name="Wael Nageeb Mahmoud"),
            Employee(employee_id="A11026", name="Wail Saleh Awadh"),
            Employee(employee_id="A10771", name="Waheeb Saleh Mohammed"),
            Employee(employee_id="A10044", name="Adel Abdulla Saleh"),
        )
        dependencies.reference_writer = lambda question, **_kwargs: ReferenceResponse(
            decision=AmbiguousReference(
                rewritten_request=question,
                locale="en",
                reason="ambiguous_reference",
                employee_mention="wael saleh",
            )
        )
        dependencies.employee_fuzzy_search = search_employee_directory_postgres

        outcome = run_turn(
            TurnRequest(
                question=(
                    "Tell me the attendance from 1 to 7 September for employee "
                    "wael saleh."
                ),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Clarification)
        self.assertEqual(outcome.reply, "Did you mean Wail Saleh Awadh (A11026)?")
        self.assertEqual(
            outcome.state.pending_employee_confirmation.options,
            (EmployeeOption(employee_id="A11026", employee_name="Wail Saleh Awadh"),),
        )

    def test_adjusted_swipe_lookup_keeps_selected_employee_without_prior_location(self):
        employee = Employee(employee_id="A1", name="Wail Ali")
        prior = VerifiedTurn(
            turn_id="prior",
            original_question="Who is A1?",
            rewritten_request="Who is A1?",
            answer="A1 is at N/NA; another candidate is at MES-Eng.",
            locale="en",
            employees=(employee,),
            executed_sql=(
                "SELECT employee_id, name, work_location "
                "FROM attendance_records WHERE employee_id = 'A1'"
            ),
            result={"rows": [{"employee_id": "A1", "work_location": "N/NA"}]},
        )
        dependencies = self.dependencies()
        dependencies.context_loader = (
            lambda **_kwargs: database_context_with_locations()
        )
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=(
                    "Tell me if A1 has a manually adjusted swipe during September 2026."
                ),
                locale="en",
                request_relationship="follow_up",
                subject_relationship="employees",
                employee_ids=("A1",),
            )
        )
        comparisons = (
            f"(record_json ->> '{effective}') IS DISTINCT FROM "
            f"(record_json ->> '{actual}')"
            for effective, actual in (
                ("From_Date", "Actual_From_Date"),
                ("From_Time", "Actual_From_Time"),
                ("To_Date", "Actual_To_Date"),
                ("To_Time", "Actual_To_Time"),
            )
        )
        sql = (
            "SELECT employee_id, work_location, attendance_date, "
            "COUNT(*) OVER() AS matched_count FROM attendance_records "
            "WHERE employee_id = 'A1' "
            "AND attendance_date >= '2026-09-01' "
            "AND attendance_date < '2026-10-01' AND ("
            + " OR ".join(comparisons)
            + ") LIMIT 100"
        )
        dependencies.planner = lambda **_kwargs: sql
        executed = []
        dependencies.executor = lambda query, **_kwargs: (
            executed.append(query)
            or SqlExecutionResult(
                columns=(),
                rows=(
                    {
                        "employee_id": "A1",
                        "work_location": "N/NA",
                        "attendance_date": "2026-09-01",
                        "matched_count": 1,
                    },
                ),
                coverage=ExecutionCoverage(
                    fetched_rows=1, result_limit=100, response_bytes=100
                ),
            )
        )
        dependencies.answerer = lambda **_kwargs: (
            "A1 has a manually adjusted swipe on September 1."
        )
        outcome = run_turn(
            TurnRequest(
                question="Please tell me if he has manual swipe during September 2026.",
                state=ConversationState(
                    verified_turns=(prior,),
                    active_employee_ids=("A1",),
                    active_employees=(employee,),
                ),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(executed, [sql])
        self.assertNotIn("MES-Eng", outcome.state.verified_turns[-1].rewritten_request)
        self.assertNotIn("work_location =", sql)

    def test_plain_language_does_not_force_a_database_location(self):
        sql = (
            "SELECT employee_id, attendance_date FROM attendance_records "
            "WHERE employee_id = 'A1' AND attendance_date >= '2026-09-01' "
            "AND attendance_date < '2026-10-01'"
        )
        for question in (
            "Please tell me about A1 during September 2026.",
            "Now give me A1's details during September 2026.",
        ):
            with self.subTest(question=question):
                self.assertIsNone(
                    _sql_semantic_issue(
                        question,
                        sql,
                        database_context=database_context_with_locations(),
                        required_date_scope=("2026-09-01", "2026-09-30"),
                    )
                )

    def test_general_request_preserves_location_explicit_in_current_question(self):
        question = "Compare departments at South Dock during September 2026."
        bound = bind_references(
            ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="new",
                    subject_relationship="criteria",
                    employee_criteria=("South Dock",),
                )
            ),
            (Employee(employee_id="A1", name="Wail Ali"),),
            original_question=question,
        )

        self.assertEqual(bound.request_relationship, "new")
        self.assertEqual(bound.subject_relationship, "criteria")
        self.assertEqual(bound.updated_request, f"Request:\n{question}")

    def test_new_department_comparison_ignores_old_employee_and_location(self):
        dependencies = self.dependencies()
        prior = VerifiedTurn(
            turn_id="prior",
            original_question="Show A1's manual swipes at North Yard.",
            rewritten_request="Show A1's manual swipes at North Yard.",
            answer="A1 has no manual swipes at North Yard.",
            locale="en",
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            executed_sql=(
                "SELECT employee_id FROM attendance_records "
                "WHERE employee_id = 'A1' AND work_location = 'North Yard'"
            ),
            result={},
        )
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=(
                    "Now compare overtime between departments during September 2026."
                ),
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        seen = []

        def planner(**kwargs):
            seen.append(kwargs["shared_context"])
            return (
                "SELECT department, SUM(total_ot) AS overtime_hours, "
                "COUNT(*) OVER() AS matched_count FROM attendance_records "
                "WHERE attendance_date >= '2026-09-01' "
                "AND attendance_date < '2026-10-01' "
                "GROUP BY department ORDER BY overtime_hours DESC LIMIT 100"
            )

        dependencies.planner = planner
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=(
                {"department": "Operations", "overtime_hours": 30, "matched_count": 2},
                {"department": "Engineering", "overtime_hours": 20, "matched_count": 2},
            ),
            coverage=ExecutionCoverage(
                fetched_rows=2, result_limit=100, response_bytes=120
            ),
        )
        dependencies.answerer = lambda **_kwargs: "Operations 30; Engineering 20."
        question = "Now compare overtime between departments during September 2026."
        outcome = run_turn(
            TurnRequest(
                question=question,
                state=ConversationState(
                    verified_turns=(prior,),
                    active_employee_ids=("A1",),
                    active_employees=(Employee(employee_id="A1", name="Wail Ali"),),
                ),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].request_relationship, "new")
        self.assertEqual(seen[0].subject_relationship, "all_authorized")
        self.assertEqual(seen[0].resolved_employee_ids, ())
        self.assertEqual(seen[0].updated_request, f"Request:\n{question}")
        self.assertEqual(seen[0].required_date_scope, ("2026-09-01", "2026-09-30"))
        self.assertEqual(
            seen[0].scope_provenance["current_original_question"], question
        )
        self.assertEqual(
            seen[0].scope_provenance["previous_original_question"],
            prior.original_question,
        )
        self.assertEqual(seen[0].scope_provenance["carried_employee_ids_source"], [])
        self.assertEqual(outcome.state.active_employee_ids, ())

    def test_arbitrary_new_topic_uses_current_request_even_with_date(self):
        dependencies = self.dependencies()
        question = "Analyze overtime distribution across units during September 2026."
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=(
                    "Analyze overtime distribution across units at North Yard "
                    "during September 2026."
                ),
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        seen = []

        def planner(**kwargs):
            seen.append(kwargs["shared_context"])
            return (
                "SELECT department, SUM(total_ot) AS overtime_hours, "
                "COUNT(*) OVER() AS matched_count FROM attendance_records "
                "WHERE attendance_date >= '2026-09-01' "
                "AND attendance_date < '2026-10-01' "
                "GROUP BY department ORDER BY overtime_hours DESC LIMIT 100"
            )

        dependencies.planner = planner
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=(
                {"department": "Operations", "overtime_hours": 30, "matched_count": 1},
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=60
            ),
        )
        dependencies.answerer = lambda **_kwargs: "Operations: 30 hours."
        outcome = run_turn(
            TurnRequest(question=question, access_context=LOCAL_DEMO_ACCESS),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(seen[0].updated_request, f"Request:\n{question}")
        self.assertEqual(seen[0].request_relationship, "new")

    def test_unsupported_plan_is_reconsidered_against_schema(self):
        dependencies = self.dependencies()
        calls = []

        def planner(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return (
                    "SELECT 'The requested concept is not represented by the "
                    "attendance schema.'::text AS unsupported_capability"
                )
            return "SELECT attendance_date FROM attendance_records WHERE employee_id = 'A1'"

        dependencies.planner = planner
        outcome = run_turn(
            TurnRequest(question="show A1 dates", access_context=LOCAL_DEMO_ACCESS),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[1]["sql_execution_failure"]["error_type"],
            "capability_reconsideration",
        )

    def test_invalid_sql_reports_syntax_to_planner_retry(self):
        dependencies = self.dependencies()
        calls = []

        def planner(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return (
                    "SELECT ARRAY_AGG(ROW(attendance_date)) ORDER BY employee_id FROM"
                )
            return "SELECT attendance_date FROM attendance_records WHERE employee_id = 'A1'"

        dependencies.planner = planner
        outcome = run_turn(
            TurnRequest(question="show A1 dates", access_context=LOCAL_DEMO_ACCESS),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(
            calls[1]["sql_execution_failure"]["error_type"],
            "invalid_sql_syntax",
        )

    def test_follow_up_name_search_is_not_rejected_as_employee_scope(self):
        dependencies = self.dependencies()
        seen = []
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Search for close name matches to Raed Ans.",
                locale="en",
                request_relationship="follow_up",
                subject_relationship="all_authorized",
            )
        )
        dependencies.planner = lambda **kwargs: (
            seen.append(kwargs)
            or "SELECT employee_id, name FROM attendance_records "
            "WHERE name ILIKE '%Raed%' LIMIT 10"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"employee_id": "A1", "name": "Raed Ansi"},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=64
            ),
        )
        dependencies.answerer = lambda **_kwargs: "Raed Ansi (A1)."
        prior = VerifiedTurn(
            turn_id="prior",
            original_question="Who is Raed Ans?",
            rewritten_request="Who is Raed Ans?",
            answer="No exact match. I can search for close matches.",
            locale="en",
            executed_sql="SELECT name FROM attendance_records WHERE name = 'Raed Ans'",
            result={},
        )
        outcome = run_turn(
            TurnRequest(
                question="Search for close matches",
                state=ConversationState(verified_turns=(prior,)),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(outcome.reply, "Raed Ansi (A1).")
        self.assertEqual(len(seen), 1)

    def test_multiple_choice_words_preserve_ambiguous_employee_options(self):
        pending = PendingEmployeeConfirmation(
            original_question="What was the overtime?",
            mention="shared name",
            options=(
                EmployeeOption(employee_id="A1", employee_name="First Person"),
                EmployeeOption(employee_id="A2", employee_name="Second Person"),
            ),
            resolution=PendingResolution(
                rewritten_request="What was the overtime?",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
            ),
        )
        for reply in ("all", "both"):
            self.assertEqual(
                _pending_response(reply, pending), ("invalid_selection", None)
            )

    def test_new_question_replaces_pending_employee_confirmation(self):
        candidates = tuple(
            EmployeeOption(
                employee_id=f"A{index:05d}", employee_name=f"Candidate {index}"
            )
            for index in range(1, 21)
        )
        pending = PendingEmployeeConfirmation(
            original_question="What is the full name of Muktar Ahmd?",
            mention="Muktar Ahmd",
            options=candidates,
            resolution=PendingResolution(
                rewritten_request="What is the full name of Muktar Ahmd?",
                locale="ar",
                request_relationship="new",
                subject_relationship="employees",
            ),
        )
        state = ConversationState(pending_employee_confirmation=pending)
        dependencies = self.dependencies()
        seen_questions = []
        seen_plans = []

        def reference_writer(question, *_args, **_kwargs):
            seen_questions.append(question)
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="new",
                    subject_relationship="all_authorized",
                )
            )

        def planner(**kwargs):
            seen_plans.append(kwargs["shared_context"].current_question)
            return (
                "SELECT employee_id, name, COUNT(*) AS manual_swipe_records, "
                "COUNT(*) OVER() AS matched_count FROM attendance_records WHERE "
                "department = 'HR' AND attendance_date >= '2026-09-01' AND "
                "attendance_date < '2026-10-01' AND ("
                "(record_json ->> 'From_Date') IS DISTINCT FROM "
                "(record_json ->> 'Actual_From_Date') OR "
                "(record_json ->> 'From_Time') IS DISTINCT FROM "
                "(record_json ->> 'Actual_From_Time') OR "
                "(record_json ->> 'To_Date') IS DISTINCT FROM "
                "(record_json ->> 'Actual_To_Date') OR "
                "(record_json ->> 'To_Time') IS DISTINCT FROM "
                "(record_json ->> 'Actual_To_Time')) "
                "GROUP BY employee_id, name ORDER BY employee_id LIMIT 100"
            )

        dependencies.reference_writer = reference_writer
        dependencies.planner = planner
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"employee_id": "A1", "name": "Wail Ali"},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=50
            ),
        )
        dependencies.answerer = lambda **_kwargs: "Wail Ali matched."
        question = (
            "Please list the employees who have manual swipe in HR during "
            "September 2026."
        )

        outcome = run_turn(
            TurnRequest(
                question=question,
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(seen_questions, [question])
        self.assertEqual(seen_plans, [question])
        self.assertIsNone(outcome.state.pending_employee_confirmation)

    def test_verified_turn_preserves_complete_bounded_result_answer(self):
        answer = "employee row\n" * 1000

        turn = VerifiedTurn(
            turn_id="complete-list",
            original_question="List every matching employee.",
            rewritten_request="List every matching employee.",
            answer=answer,
            locale="en",
            executed_sql="SELECT employee_id FROM attendance_records",
        )

        self.assertEqual(turn.answer, answer.strip())

    def test_implicit_yesterday_request_can_use_a_date_predicate(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Count A1's attendance records from yesterday.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_ids=("A1",),
            )
        )
        sql = (
            "SELECT COUNT(*) AS record_count FROM attendance_records "
            "WHERE employee_id = 'A1' "
            "AND attendance_date = CURRENT_DATE - INTERVAL '1 day'"
        )
        captured = []
        dependencies.planner = lambda **_kwargs: sql
        dependencies.executor = lambda query, **_kwargs: (
            captured.append(query)
            or SqlExecutionResult(
                columns=(),
                rows=({"record_count": 0},),
                coverage=ExecutionCoverage(
                    fetched_rows=1, result_limit=100, response_bytes=20
                ),
            )
        )
        dependencies.answerer = lambda **_kwargs: "No attendance records yesterday."
        outcome = run_turn(
            TurnRequest(
                question="How many attendance records did A1 have yesterday?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(captured, [sql])

    def test_resolved_relative_scope_is_saved_for_follow_up(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=f"Show A1 attendance on {yesterday}.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_ids=("A1",),
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT attendance_date FROM attendance_records "
            f"WHERE employee_id = 'A1' AND attendance_date = '{yesterday}'"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"attendance_date": yesterday},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=34
            ),
        )
        dependencies.answerer = lambda **_kwargs: f"A1 attended on {yesterday}."
        outcome = run_turn(
            TurnRequest(
                question="Show A1's attendance yesterday.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(
            outcome.state.verified_turns[0].requested_date_scope,
            (yesterday, yesterday),
        )

    def test_detail_request_retries_scalar_count_before_execution(self):
        dependencies = self.dependencies()
        attempts = []
        executed = []

        def planner(**kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                return (
                    "SELECT COUNT(*) AS attendance_count FROM attendance_records "
                    "WHERE employee_id = 'A1' AND attendance_date = '2026-09-05'"
                )
            return (
                "SELECT record_id, employee_id, attendance_date, "
                "COUNT(*) OVER() AS matched_count FROM attendance_records "
                "WHERE employee_id = 'A1' AND attendance_date = '2026-09-05' "
                "ORDER BY record_id LIMIT 100"
            )

        dependencies.planner = planner
        dependencies.executor = lambda sql, **_kwargs: (
            executed.append(sql)
            or SqlExecutionResult(
                columns=(),
                rows=(
                    {
                        "record_id": 17,
                        "employee_id": "A1",
                        "attendance_date": "2026-09-05",
                        "matched_count": 1,
                    },
                ),
                coverage=ExecutionCoverage(
                    fetched_rows=1, result_limit=100, response_bytes=90
                ),
            )
        )
        dependencies.answerer = lambda **_kwargs: (
            "Wail Ali has record 17 on 2026-09-05."
        )

        outcome = run_turn(
            TurnRequest(
                question="Show attendance for A1 on 2026-09-05.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(executed), 1)
        self.assertIn("record_id", executed[0])
        self.assertEqual(
            attempts[1]["sql_execution_failure"]["error_type"], "sql_semantics"
        )
        self.assertEqual(
            attempts[1]["sql_execution_failure"]["database_error"],
            "The user requested attendance detail rows, but this SQL returns only "
            "an aggregate. Return bounded matching rows with record_id and "
            "COUNT(*) OVER() AS matched_count.",
        )

    def test_rewritten_manual_swipe_request_uses_planner_sql_without_contract_check(
        self,
    ):
        dependencies = self.dependencies()
        attempts = []
        executed = []
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=(
                    "List employees who have a manual swipe from September."
                ),
                locale="en",
                request_relationship="follow_up",
                subject_relationship="all_authorized",
            )
        )

        incomplete_sql = (
            "SELECT employee_id, name FROM attendance_records WHERE "
            "(record_json ->> 'From_Time') IS DISTINCT FROM "
            "(record_json ->> 'Actual_From_Time')"
        )

        def planner_call(**kwargs):
            attempts.append(kwargs)
            return incomplete_sql

        dependencies.planner = planner_call
        dependencies.executor = lambda sql, **_kwargs: (
            executed.append(sql)
            or SqlExecutionResult(
                columns=(),
                rows=({"employee_id": "A1", "name": "Wail Ali"},),
                coverage=ExecutionCoverage(
                    fetched_rows=1, result_limit=100, response_bytes=45
                ),
            )
        )
        dependencies.answerer = lambda **_kwargs: "Wail Ali has manual swipes."

        outcome = run_turn(
            TurnRequest(
                question="Only those from September.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(executed, [incomplete_sql])

    def test_over_dates_is_not_a_malformed_numeric_comparison(self):
        self.assertIsNone(_request_value_issue("Show a running total over dates."))
        self.assertIsNone(
            _request_value_issue("Show attendance between September 3 and September 8.")
        )
        self.assertEqual(
            _request_value_issue("Show lateness greater than nonsense hours."),
            "malformed_value",
        )
        self.assertEqual(
            _request_value_issue("Who was late at least ?. hours?"),
            "malformed_value",
        )
        self.assertEqual(
            _request_value_issue("Show records with lateness at most none."),
            "malformed_value",
        )
        self.assertEqual(
            _request_value_issue("Show records from not-a-date to tomorrow."),
            "unsupported_constraint",
        )
        self.assertEqual(
            _request_value_issue(
                "Show attendance between September 8 and September 3."
            ),
            "reversed_temporal_range",
        )
        self.assertEqual(
            _request_value_issue("Count records between 2026-09-07 and 2026-09-01."),
            "reversed_temporal_range",
        )

    def test_unsupported_reference_cannot_veto_schema_grounded_aggregate(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request="Rank departments by worked hours.",
                locale="en",
                capability="outside_attendance_domain",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT department, SUM(total_worked_hrs) AS worked_hours "
            "FROM attendance_records GROUP BY department ORDER BY worked_hours DESC"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"department": "Engineering", "worked_hours": 56},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=50
            ),
        )
        dependencies.answerer = lambda **_kwargs: "Engineering: 56 hours."

        outcome = run_turn(
            TurnRequest(
                question="Rank departments by worked hours.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(outcome.state.verified_turns[-1].employee_ids, ())

    def test_unsupported_reference_cannot_veto_explicit_schema_count(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request=(
                    'How many attendance records have Department equal to "Operations"?'
                ),
                locale="en",
                capability="outside_attendance_domain",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT COUNT(*) AS matched_count FROM attendance_records "
            "WHERE department = 'Operations'"
        )
        dependencies.executor = lambda sql, *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"matched_count": 1743},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=24
            ),
        )

        outcome = run_turn(
            TurnRequest(
                question=(
                    'How many attendance records have Department equal to "Operations"?'
                ),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertIn(
            "department = 'Operations'",
            outcome.state.verified_turns[-1].executed_sql,
        )

    def test_general_grouped_schema_aggregate_reaches_planner(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Sum Total_Worked_Hrs by Department.",
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT department, SUM(total_worked_hrs) AS total_total_worked_hrs, "
            "COUNT(*) OVER() AS matched_count FROM attendance_records "
            "GROUP BY department LIMIT 100"
        )
        dependencies.executor = lambda sql, *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=(
                {
                    "department": "Engineering",
                    "total_total_worked_hrs": 56,
                    "matched_count": 1,
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=70
            ),
        )

        outcome = run_turn(
            TurnRequest(
                question="Sum Total_Worked_Hrs by Department.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertIn(
            "SUM(total_worked_hrs)",
            outcome.state.verified_turns[-1].executed_sql,
        )

    def test_general_month_attendance_details_reach_planner(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show attendance in September 2026.",
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT record_id, attendance_date, COUNT(*) OVER() AS matched_count "
            "FROM attendance_records WHERE attendance_date >= '2026-09-01' "
            "AND attendance_date < '2026-10-01' ORDER BY record_id LIMIT 100"
        )
        dependencies.executor = lambda sql, *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"record_id": "one", "matched_count": 3964},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=55
            ),
        )

        outcome = run_turn(
            TurnRequest(
                question="Show attendance in September 2026.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertIn(
            "attendance_date >= '2026-09-01'",
            outcome.state.verified_turns[-1].executed_sql,
        )

    def test_general_scope_is_delegated_to_sql_planner(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=(
                    "list all employees in the hr department who have manual swipe access"
                ),
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        planned_contexts = []
        manual_swipe_sql = (
            "SELECT employee_id, name, COUNT(*) AS manual_swipe_records, "
            "COUNT(*) OVER() AS matched_count FROM attendance_records WHERE "
            "department = 'HR' AND ((record_json ->> 'From_Date') "
            "IS DISTINCT FROM (record_json ->> 'Actual_From_Date') OR "
            "(record_json ->> 'From_Time') IS DISTINCT FROM "
            "(record_json ->> 'Actual_From_Time') OR "
            "(record_json ->> 'To_Date') IS DISTINCT FROM "
            "(record_json ->> 'Actual_To_Date') OR "
            "(record_json ->> 'To_Time') IS DISTINCT FROM "
            "(record_json ->> 'Actual_To_Time')) GROUP BY employee_id, name "
            "ORDER BY employee_id LIMIT 100"
        )

        def planner(**kwargs):
            planned_contexts.append(kwargs["shared_context"])
            return manual_swipe_sql

        dependencies.planner = planner
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=(
                {
                    "employee_id": "A1",
                    "name": "Wail Ali",
                    "manual_swipe_records": 1,
                    "matched_count": 1,
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=95
            ),
        )
        dependencies.answerer = lambda **_kwargs: (
            "Wail Ali (A1) has one manual-swipe record in HR."
        )

        outcome = run_turn(
            TurnRequest(
                question="list all in hr department which has manual swipe",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(planned_contexts), 1)
        self.assertEqual(
            planned_contexts[0].current_question,
            "list all in hr department which has manual swipe",
        )
        self.assertIn(
            "list all in hr department which has manual swipe",
            planned_contexts[0].updated_request,
        )
        self.assertEqual(planned_contexts[0].subject_relationship, "all_authorized")

    def test_business_criterion_misread_as_person_is_reconsidered_by_planner_model(
        self,
    ):
        dependencies = self.dependencies()
        reference_models = []

        def reference_writer(question, **kwargs):
            reference_models.append(kwargs["model"])
            if len(reference_models) == 1:
                return ReferenceResponse(
                    decision=AmbiguousReference(
                        rewritten_request=question,
                        locale="en",
                        reason="ambiguous_reference",
                        employee_mention="Finance department",
                    )
                )
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="new",
                    subject_relationship="criteria",
                    employee_criteria=("Finance department",),
                )
            )

        dependencies.reference_writer = reference_writer
        dependencies.employee_fuzzy_search = lambda *_args, **_kwargs: ()
        dependencies.employee_fallback_search = lambda *_args, **_kwargs: self.fail(
            "unmatched business wording must not be mapped to arbitrary employees"
        )
        planned_contexts = []

        def planner(**kwargs):
            planned_contexts.append(kwargs["shared_context"])
            return (
                "SELECT SUM(total_worked_hrs) AS total_worked_hours "
                "FROM attendance_records WHERE department = 'Finance'"
            )

        dependencies.planner = planner
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"total_worked_hours": 116.75},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=38
            ),
        )

        outcome = run_turn(
            TurnRequest(
                question="How many total worked hours are in the Finance department?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(reference_models), 2)
        self.assertEqual(len(planned_contexts), 1)
        self.assertEqual(planned_contexts[0].subject_relationship, "criteria")
        self.assertIn("Finance department", planned_contexts[0].updated_request)

    def test_reference_decision_limit_prevents_reconsideration(self):
        dependencies = self.dependencies()
        calls = []

        def reference_writer(question, **_kwargs):
            calls.append(question)
            return ReferenceResponse(
                decision=AmbiguousReference(
                    rewritten_request=question,
                    locale="en",
                    reason="ambiguous_reference",
                    employee_mention="Finance department",
                )
            )

        dependencies.reference_writer = reference_writer
        dependencies.employee_fuzzy_search = lambda *_args, **_kwargs: ()
        outcome = run_turn(
            TurnRequest(
                question="How many total worked hours are in the Finance department?",
                access_context=LOCAL_DEMO_ACCESS,
                max_reference_calls=1,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Clarification)
        self.assertEqual(len(calls), 1)

    def test_inconsistent_reference_is_reconsidered_when_models_are_the_same(self):
        dependencies = self.dependencies()
        reference_calls = []

        def reference_writer(question, **kwargs):
            reference_calls.append(kwargs)
            if len(reference_calls) == 1:
                return ReferenceResponse(
                    decision=ReadyReference(
                        rewritten_request=question,
                        locale="en",
                        request_relationship="follow_up",
                        subject_relationship="intersection",
                    )
                )
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="follow_up",
                    subject_relationship="criteria",
                    employee_criteria=("workflow status Authorized",),
                )
            )

        dependencies.reference_writer = reference_writer
        dependencies.planner = lambda **_kwargs: (
            "SELECT COUNT(DISTINCT employee_id) AS employee_count "
            "FROM attendance_records WHERE status = 'Authorized'"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"employee_count": 370},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=22
            ),
        )
        dependencies.answerer = lambda **_kwargs: "370 employees."

        with patch(
            "week5.new_implementation.online.pipeline.settings",
            replace(settings, llm_reference_model=settings.llm_planner_model),
        ):
            outcome = run_turn(
                TurnRequest(
                    question="How many of those employees have Authorized status?",
                    access_context=LOCAL_DEMO_ACCESS,
                ),
                dependencies=dependencies,
            )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(reference_calls), 2)
        self.assertEqual(reference_calls[0]["model"], reference_calls[1]["model"])
        self.assertIn(
            "subject_relationship", reference_calls[1]["reconsideration_feedback"]
        )

    def test_unknown_grouped_metric_reaches_planner_capability_protocol(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="What is the average of banana by department?",
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT 'Metric is not in the schema.'::text AS unsupported_capability"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"unsupported_capability": "Metric is not in the schema."},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=40
            ),
        )

        outcome = run_turn(
            TurnRequest(
                question="What is the average of banana by department?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "schema")

    def test_unsupported_reference_cannot_veto_named_employee_aggregate(self):
        dependencies = self.dependencies()
        reference_calls = []

        def reference_writer(_question, **kwargs):
            reference_calls.append(kwargs["model"])
            if len(reference_calls) == 1:
                return ReferenceResponse(
                    decision=UnsupportedReference(
                        rewritten_request="What were Wail Ali's total worked hours?",
                        locale="en",
                        capability="outside_attendance_domain",
                    )
                )
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request="What were Wail Ali's total worked hours?",
                    locale="en",
                    request_relationship="new",
                    subject_relationship="employees",
                    employee_names=("Wail Ali",),
                )
            )

        dependencies.reference_writer = reference_writer
        dependencies.planner = lambda **_kwargs: (
            "SELECT SUM(total_worked_hrs) AS worked_hours "
            "FROM attendance_records WHERE employee_id = 'A1'"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"worked_hours": 32.41},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=25
            ),
        )
        dependencies.answerer = lambda **_kwargs: "Wail Ali worked 32.41 hours."

        outcome = run_turn(
            TurnRequest(
                question="What were Wail Ali's total worked hours?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(reference_calls), 2)
        self.assertEqual(outcome.state.verified_turns[-1].employee_ids, ("A1",))

    def test_unsupported_reference_cannot_veto_employee_status_count(self):
        dependencies = self.dependencies()
        schema = attendance_schema()
        table = schema.tables[0]
        dependencies.context_loader = lambda **_kwargs: schema.model_copy(
            update={
                "tables": (
                    table.model_copy(
                        update={
                            "columns": table.columns
                            + (
                                DatabaseColumn(
                                    name="status",
                                    data_type="text",
                                    nullable=False,
                                    description="Authorization status.",
                                ),
                            )
                        }
                    ),
                )
            }
        )
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request=(
                    "How many Authorized attendance records does A1 have?"
                ),
                locale="en",
                capability="outside_attendance_domain",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT COUNT(*) AS matched_count FROM attendance_records "
            "WHERE employee_id = 'A1' AND status = 'Authorized'"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"matched_count": 2},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=20
            ),
        )

        outcome = run_turn(
            TurnRequest(
                question="How many Authorized attendance records does A1 have?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertIn(
            "status = 'Authorized'", outcome.state.verified_turns[-1].executed_sql
        )

    def test_unsupported_reference_cannot_veto_attendance_pattern_request(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request="Find problematic attendance behavior.",
                locale="en",
                capability="outside_attendance_domain",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT exception, COUNT(*) AS occurrence_count "
            "FROM attendance_records GROUP BY exception"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"exception": "Lateness", "occurrence_count": 8},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=50
            ),
        )
        dependencies.answerer = lambda **_kwargs: "Lateness: 8 records."

        outcome = run_turn(
            TurnRequest(
                question="Find potentially problematic attendance behavior.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(outcome.state.verified_turns[-1].employee_ids, ())

    def test_missing_join_target_is_assessed_by_planner_schema(self):
        state = ConversationState()
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Join attendance to payroll.",
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT 'Payroll is not in the schema.'::text AS unsupported_capability"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"unsupported_capability": "Payroll is not in the schema."},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=40
            ),
        )

        outcome = run_turn(
            TurnRequest(
                question="Join attendance to payroll.",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "schema")
        self.assertEqual(outcome.state, state)

    def test_grouped_month_followup_uses_verified_sql_without_model_replanning(self):
        previous_sql = (
            "SELECT department, SUM(total_worked_hrs) AS total_hours "
            "FROM attendance_records GROUP BY department "
            "HAVING SUM(total_worked_hrs) > 10"
        )
        previous_turn = VerifiedTurn(
            turn_id="prior-grouped",
            original_question="Group worked hours by department over 10.",
            rewritten_request="Group worked hours by department over 10.",
            answer="Engineering: 56 hours.",
            locale="en",
            executed_sql=previous_sql,
            result={"rows": [{"department": "Engineering", "total_hours": 56}]},
        )
        state = ConversationState(verified_turns=(previous_turn,))
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda question, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=question,
                locale="en",
                request_relationship="follow_up",
                subject_relationship="all_authorized",
            )
        )
        dependencies.planner = lambda **_kwargs: self.fail(
            "clear grouped comparison must not be model-replanned"
        )
        dependencies.answerer = lambda **_kwargs: (
            "Engineering: August 16 hours; September 40 hours; difference 24 hours."
        )
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        executed = []

        def executor(sql, **_kwargs):
            executed.append(sql)
            return SqlExecutionResult(
                rows=(
                    {
                        "department": "Engineering",
                        "previous_period_value": 16,
                        "current_period_value": 40,
                        "difference": 24,
                        "matched_count": 1,
                    },
                ),
                coverage=ExecutionCoverage(
                    fetched_rows=1, result_limit=100, response_bytes=100
                ),
            )

        dependencies.executor = executor
        outcome = run_turn(
            TurnRequest(
                question="Compare that with last month.",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(executed), 1)
        self.assertIn("HAVING SUM(total_worked_hrs) > 10", executed[0])
        self.assertIn("Engineering: August", outcome.reply)
        self.assertEqual(len(outcome.state.verified_turns), 2)
        self.assertIsNone(outcome.state.verified_turns[-1].date_scope)

    def test_running_total_followup_uses_verified_metric_and_checks_series(self):
        previous_turn = VerifiedTurn(
            turn_id="prior-ranking",
            original_question="Rank departments by worked hours.",
            rewritten_request="Rank departments by worked hours.",
            answer="Engineering: 56 hours.",
            locale="en",
            executed_sql=(
                "SELECT department, SUM(total_worked_hrs) AS worked_hours "
                "FROM attendance_records GROUP BY department ORDER BY worked_hours DESC"
            ),
            result={"rows": [{"department": "Engineering", "worked_hours": 56}]},
        )
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        reference_questions = []

        def reference_writer(question, **_kwargs):
            reference_questions.append(question)
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="follow_up",
                    subject_relationship="all_authorized",
                )
            )

        dependencies.reference_writer = reference_writer
        dependencies.planner = lambda **_kwargs: self.fail(
            "verified running total must not be model-replanned"
        )
        dependencies.answerer = lambda **_kwargs: (
            "2026-08-03: 14 hours; 2026-08-04: 36 hours running total."
        )

        def executor(sql, **_kwargs):
            self.assertIn("SUM(daily_value) OVER", sql)
            return SqlExecutionResult(
                rows=(
                    {
                        "attendance_date": "2026-08-03",
                        "daily_value": 14,
                        "running_total": 14,
                        "matched_count": 2,
                    },
                    {
                        "attendance_date": "2026-08-04",
                        "daily_value": 22,
                        "running_total": 36,
                        "matched_count": 2,
                    },
                ),
                coverage=ExecutionCoverage(
                    fetched_rows=2, result_limit=100, response_bytes=100
                ),
            )

        dependencies.executor = executor
        outcome = run_turn(
            TurnRequest(
                question="Show a running total over dates.",
                state=ConversationState(verified_turns=(previous_turn,)),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(reference_questions, ["Show a running total over dates."])
        self.assertIn("2026-08-04: 36", outcome.reply)
        self.assertIsNone(outcome.state.verified_turns[-1].date_scope)

    def test_previous_group_eligibility_is_extracted_from_verified_sql(self):
        self.assertEqual(
            _previous_having(
                "SELECT department, SUM(hours) FROM records GROUP BY department "
                "HAVING SUM(hours) > 10 ORDER BY department"
            ),
            "HAVING SUM(hours) > 10",
        )

    def test_trusted_followup_context_uses_latest_verified_turn(self):
        turns = tuple(
            VerifiedTurn(
                turn_id=str(index),
                original_question=question,
                rewritten_request=question,
                answer="Verified answer.",
                locale="en",
                executed_sql="SELECT 1",
                scope_clauses=(
                    {
                        "request": question,
                        "current_question_basis": question,
                        "carried_from_previous": (),
                    },
                ),
            )
            for index, question in enumerate(
                ("Show A1 absences.", "Group hours by department.")
            )
        )
        context = ConversationState(verified_turns=turns).trusted_context()

        self.assertEqual(len(context["verified_turns"]), 1)
        self.assertEqual(
            context["verified_turns"][0]["original_question"],
            "Group hours by department.",
        )
        self.assertNotIn("executed_sql", context["verified_turns"][0])
        self.assertNotIn("answer", context["verified_turns"][0])
        self.assertNotIn("result", context["verified_turns"][0])
        self.assertNotIn("scope_clauses", context["verified_turns"][0])
        self.assertEqual(
            turns[-1].scope_clauses[0].request, "Group hours by department."
        )

    def test_question_language_controls_output_locale(self):
        self.assertEqual(_locale("كم يوم اشتغل A1؟"), "ar")
        self.assertEqual(_locale("How many days for A1?"), "en")
        self.assertEqual(_locale("How many days for A1? Reply in Arabic."), "ar")
        self.assertEqual(_locale("كم يوم اشتغل A1؟ جاوب بالإنجليزية"), "en")

    def test_bounded_grouped_result_has_distinct_result_count(self):
        repaired = pipeline._repair_group_matched_count(
            "SELECT country, COUNT(*) AS record_count, "
            "COUNT(*) OVER() AS matched_group_count "
            "FROM attendance_records GROUP BY country LIMIT 100"
        )
        self.assertIn("AS matched_count", repaired)
        self.assertIsNone(_sql_semantic_issue("by country", repaired))

    def test_named_draft_request_requires_status_filter_on_every_source_path(self):
        missing = (
            "SELECT day_type, COUNT(*) AS record_count FROM attendance_records "
            "WHERE attendance_date = '2026-09-05' GROUP BY day_type"
        )
        filtered = (
            "SELECT day_type, COUNT(*) AS record_count FROM attendance_records "
            "WHERE attendance_date = '2026-09-05' AND status = 'Draft' "
            "GROUP BY day_type"
        )
        self.assertEqual(
            _sql_semantic_issue("sep 5 draft by day type pls", missing),
            "missing_workflow_status_filter",
        )
        self.assertIsNone(_sql_semantic_issue("sep 5 draft by day type pls", filtered))
        self.assertIsNone(_sql_semantic_issue("Show counts by status", missing))
        leaked_branch = (
            "SELECT COUNT(*) FROM attendance_records WHERE "
            "(status = 'Draft' AND day_type = 'Working Day') OR "
            "day_type = 'OFF Day'"
        )
        self.assertEqual(
            _sql_semantic_issue("draft counts by day type", leaked_branch),
            "missing_workflow_status_filter",
        )
        pending = (
            "SELECT COUNT(*) FROM attendance_records "
            "WHERE status = 'Pending For Authorization'"
        )
        self.assertIsNone(_sql_semantic_issue("pending recs?", pending))
        mixed_statuses = (
            "SELECT COUNT(*) FROM attendance_records "
            "WHERE status IN ('Draft', 'Authorized')"
        )
        self.assertEqual(
            _sql_semantic_issue("Draft recs?", mixed_statuses),
            "missing_workflow_status_filter",
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Draft recs?",
                "SELECT COUNT(*) FROM attendance_records WHERE status = 'Drafted'",
            ),
            "missing_workflow_status_filter",
        )
        self.assertIsNone(_sql_semantic_issue("authorized overtime hours", missing))
        self.assertIsNone(
            _sql_semantic_issue(
                "Authorized by country; separately all HR employees by location",
                missing,
            )
        )
        self.assertIsNone(
            _sql_semantic_issue(
                "how many Authorized, and countries count all",
                "SELECT (SELECT COUNT(*) FROM attendance_records "
                "WHERE status = 'Authorized') AS authorized_count, "
                "(SELECT COUNT(DISTINCT country) FROM attendance_records) "
                "AS country_count",
            )
        )
        self.assertIsNone(_sql_semantic_issue("not Draft records", missing))
        partitioned = (
            "SELECT country, COUNT(*) AS record_count, "
            "COUNT(*) OVER(PARTITION BY country) AS local_count "
            "FROM attendance_records GROUP BY country LIMIT 100"
        )
        self.assertEqual(pipeline._repair_group_matched_count(partitioned), partitioned)
        self.assertEqual(
            _sql_semantic_issue(
                "Show absence dates.",
                "SELECT ARRAY_AGG(attendance_date) FROM attendance_records",
            ),
            "nested_result_shape",
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Find attendance indicators by employee.",
                "SELECT employee_id, COUNT(*) AS matched_count "
                "FROM attendance_records GROUP BY employee_id LIMIT 100",
            ),
            "invalid_grouped_matched_count",
        )
        self.assertIsNone(
            _sql_semantic_issue(
                "Find attendance indicators by employee.",
                "SELECT employee_id, COUNT(*) AS indicator_count, "
                "COUNT(*) OVER() AS matched_count FROM attendance_records "
                "GROUP BY employee_id LIMIT 100",
            )
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Find attendance indicators by employee.",
                "SELECT employee_id, COUNT(*) OVER() AS matched_count "
                "FROM attendance_records GROUP BY employee_id LIMIT 100",
            ),
            "missing_group_measure",
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Find attendance indicators by employee.",
                "SELECT employee_id, COUNT(*) AS indicator_count, "
                "COUNT(*) OVER() AS matched_count FROM attendance_records "
                "GROUP BY employee_id ORDER BY matched_count DESC LIMIT 100",
            ),
            "group_order_uses_total_count",
        )
        repaired = _repair_group_order(
            "SELECT employee_id, COUNT(*) AS indicator_count, "
            "COUNT(*) OVER() AS matched_count FROM attendance_records "
            "GROUP BY employee_id ORDER BY matched_count DESC LIMIT 100"
        )
        self.assertIn("ORDER BY indicator_count DESC", repaired)
        self.assertIsNone(_sql_semantic_issue("Find indicators by employee.", repaired))

    def test_grouped_cte_limit_does_not_need_final_result_count(self):
        self.assertIsNone(
            _sql_semantic_issue(
                "Count the leading departments.",
                "WITH leaders AS ("
                "SELECT department FROM attendance_records "
                "GROUP BY department ORDER BY SUM(total_worked_hrs) DESC LIMIT 3"
                ") SELECT COUNT(*) AS leading_count FROM leaders",
            )
        )

    def test_running_total_alias_requires_an_ordered_window(self):
        self.assertEqual(
            _sql_semantic_issue(
                "Show a running total over dates.",
                "SELECT attendance_date, SUM(total_worked_hrs) AS daily_value "
                "FROM attendance_records GROUP BY attendance_date",
            ),
            "missing_running_total_expression",
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Show a running total over dates.",
                "SELECT attendance_date, SUM(total_worked_hrs) AS running_total "
                "FROM attendance_records GROUP BY attendance_date",
            ),
            "invalid_running_total_expression",
        )
        self.assertIsNone(
            _sql_semantic_issue(
                "Show a running total over dates.",
                "WITH daily_values AS (SELECT attendance_date, "
                "SUM(total_worked_hrs) AS daily_value FROM attendance_records "
                "GROUP BY attendance_date) SELECT attendance_date, "
                "SUM(daily_value) OVER (ORDER BY attendance_date) "
                "AS running_total FROM daily_values",
            )
        )
        self.assertIsNone(
            _sql_semantic_issue(
                "Show a running total over dates.",
                "WITH daily_values AS (SELECT attendance_date, "
                "SUM(total_worked_hrs) AS daily_value FROM attendance_records "
                "GROUP BY attendance_date), running AS (SELECT attendance_date, "
                "SUM(daily_value) OVER (ORDER BY attendance_date) AS running_total "
                "FROM daily_values) SELECT attendance_date, running_total FROM running",
            )
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Show a running total over dates.",
                "SELECT attendance_date, SUM(total_worked_hrs) OVER "
                "(ORDER BY attendance_date) AS running_total "
                "FROM attendance_records",
            ),
            "running_total_requires_daily_grouping",
        )

    def test_schema_meaning_is_not_enforced_by_request_wording_checks(self):
        self.assertIsNone(
            _sql_semantic_issue(
                "Show dates that are not absent.",
                "SELECT attendance_date FROM attendance_records WHERE exception = 'Absent'",
            )
        )
        self.assertIsNone(
            _sql_semantic_issue(
                "List employees with manual swipes.",
                "SELECT employee_id FROM attendance_records WHERE "
                "(record_json ->> 'From_Time') IS DISTINCT FROM "
                "(record_json ->> 'Actual_From_Time')",
            )
        )

    def test_negated_category_does_not_require_positive_filter(self):
        self.assertIsNone(
            _sql_semantic_issue(
                "Show dates that are not absent.",
                "SELECT attendance_date FROM attendance_records "
                "WHERE exception IS DISTINCT FROM 'Absent'",
                database_context=database_context(),
            )
        )

    def test_unfiltered_self_membership_predicate_is_rejected(self):
        self.assertEqual(
            _sql_semantic_issue(
                "Group worked hours by department.",
                "SELECT department, SUM(total_worked_hrs) "
                "FROM attendance_records WHERE employee_id IN "
                "(SELECT employee_id FROM attendance_records) GROUP BY department",
            ),
            "self_membership_filter",
        )

    def test_common_word_is_not_treated_as_a_category_acronym(self):
        self.assertIsNone(
            _sql_semantic_issue(
                "Show it by attendance date.",
                "SELECT attendance_date FROM attendance_records",
                database_context=database_context_with_departments(),
            )
        )

    def test_required_date_scope_must_apply_to_every_or_branch(self):
        question = "List attendance records during September 2026."
        leaky_sql = (
            "SELECT employee_id FROM attendance_records WHERE "
            "attendance_date >= '2026-09-01' AND "
            "attendance_date < '2026-10-01' AND exception = 'Late' "
            "OR exception = 'Missing In'"
        )
        scoped_sql = (
            "SELECT employee_id FROM attendance_records WHERE "
            "attendance_date >= '2026-09-01' AND "
            "attendance_date < '2026-10-01' AND "
            "(exception = 'Late' OR exception = 'Missing In')"
        )

        self.assertEqual(
            _sql_semantic_issue(
                question,
                leaky_sql,
                required_date_scope=("2026-09-01", "2026-09-30"),
            ),
            "date_scope_mismatch",
        )
        self.assertIsNone(
            _sql_semantic_issue(
                question,
                scoped_sql,
                required_date_scope=("2026-09-01", "2026-09-30"),
            )
        )

    def test_sql_date_scope_normalizes_inclusive_and_exclusive_bounds(self):
        self.assertEqual(
            _sql_date_scope(
                "WHERE attendance_date >= '2026-09-01' "
                "AND attendance_date < DATE '2026-10-01'"
            ),
            ("2026-09-01", "2026-09-30"),
        )
        self.assertEqual(
            _sql_date_scope(
                "WHERE attendance_date BETWEEN '2026-09-01' AND '2026-09-30'"
            ),
            ("2026-09-01", "2026-09-30"),
        )
        self.assertIsNone(
            _sql_date_scope(
                "SELECT SUM(CASE WHEN attendance_date BETWEEN '2026-08-01' "
                "AND '2026-08-31' THEN hours ELSE 0 END) FROM attendance_records "
                "GROUP BY department"
            )
        )
        self.assertFalse(_mentions_time_period("Show the absence dates instead."))
        self.assertTrue(_mentions_time_period("Compare with last month."))

    def test_postgres_date_literals_survive_scope_validation(self):
        sql = (
            'SELECT COUNT(DISTINCT "attendance_date") FROM public.attendance_records '
            "WHERE \"employee_id\" = 'A11017' "
            "AND \"attendance_date\" BETWEEN DATE '2026-09-01' "
            "AND DATE '2026-09-30' AND total_worked_hrs > 0"
        )
        self.assertEqual(_sql_date_scope(sql), ("2026-09-01", "2026-09-30"))
        self.assertIsNone(
            _sql_semantic_issue(
                "How many days did A11017 attend during September 2026?",
                sql,
                required_date_scope=("2026-09-01", "2026-09-30"),
            )
        )

    def test_unused_cte_cannot_satisfy_date_or_category_scope(self):
        sql = (
            "WITH scoped AS (SELECT employee_id FROM attendance_records "
            "WHERE attendance_date BETWEEN DATE '2026-09-01' "
            "AND DATE '2026-09-30' AND department = 'Engineering'), "
            "raw AS (SELECT COUNT(*) AS n FROM attendance_records) "
            "SELECT n FROM raw"
        )
        self.assertFalse(_has_required_date_scope(sql, ("2026-09-01", "2026-09-30")))
        self.assertIsNone(_sql_date_scope(sql))

    def test_outer_cte_filter_scopes_contributing_rows(self):
        sql = (
            "WITH raw AS (SELECT attendance_date, department "
            "FROM attendance_records) SELECT COUNT(*) FROM raw "
            "WHERE attendance_date BETWEEN DATE '2026-09-01' "
            "AND DATE '2026-09-30' AND department = 'Engineering'"
        )
        self.assertTrue(_has_required_date_scope(sql, ("2026-09-01", "2026-09-30")))

    def test_inherited_date_scope_must_survive_a_short_follow_up(self):
        employee = Employee(employee_id="A1", name="Wail Ali")
        previous = ConversationState(
            verified_turns=(
                VerifiedTurn(
                    turn_id="prior",
                    original_question="Show A1 in September 2026.",
                    rewritten_request="Show A1 in September 2026.",
                    answer="Wail Ali had one date.",
                    locale="en",
                    employees=(employee,),
                    executed_sql=(
                        "SELECT attendance_date FROM attendance_records "
                        "WHERE employee_id = 'A1' "
                        "AND attendance_date >= '2026-09-01' "
                        "AND attendance_date <= '2026-09-30'"
                    ),
                    date_scope=("2026-09-01", "2026-09-30"),
                ),
            ),
            active_employee_ids=("A1",),
            active_employees=(employee,),
        )
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show A1 absence dates in September 2026.",
                locale="en",
                request_relationship="follow_up",
                subject_relationship="employees",
                employee_ids=("A1",),
            )
        )
        seen = []

        def planner(**kwargs):
            seen.append(kwargs["shared_context"])
            return (
                "SELECT attendance_date FROM attendance_records "
                "WHERE employee_id = 'A1' AND exception = 'Absent'"
            )

        dependencies.planner = planner
        dependencies.answerer = lambda **_kwargs: self.fail(
            "out-of-period SQL must not reach the answer writer"
        )
        outcome = run_turn(
            TurnRequest(
                question="Show the absence dates instead.",
                state=previous,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.code, "date_scope_mismatch")
        self.assertEqual(outcome.state, previous)
        self.assertIn(
            "Show A1 absence dates in September 2026.", seen[0].updated_request
        )
        self.assertEqual(
            seen[0].previous_verified_turn["request"],
            "Show A1 in September 2026.",
        )
        self.assertIn("attendance_records", seen[0].previous_verified_turn["sql"])
        self.assertEqual(seen[0].subject_relationship, "employees")
        self.assertEqual(seen[0].resolved_employee_ids, ("A1",))

    def dependencies(self):
        return RuntimeDependencies(
            reference_writer=ready_reference,
            directory_loader=lambda **_kwargs: (
                Employee(employee_id="A1", name="Wail Ali"),
            ),
            employee_fuzzy_search=lambda *_args, **_kwargs: (),
            context_loader=lambda **_kwargs: database_context(),
            planner=lambda **_kwargs: (
                'SELECT attendance_date FROM "attendance_records" '
                "WHERE employee_id = 'A1' AND exception = 'Absent'"
            ),
            executor=lambda *_args, **_kwargs: sql_result(),
            answerer=lambda **_kwargs: "Wail Ali was absent on 2026-09-03.",
        )

    def test_answered_turn_publishes_rewrite_sql_result_and_answer_atomically(self):
        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=self.dependencies(),
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(outcome.state.verified_turns), 1)
        turn = outcome.state.verified_turns[0]
        self.assertIn("Resolved employees", turn.rewritten_request)
        self.assertTrue(turn.executed_sql.startswith("SELECT"))
        self.assertEqual(turn.result["rows"][0]["attendance_date"], "2026-09-03")
        self.assertEqual(outcome.state.active_employee_ids, ("A1",))
        self.assertIsNone(turn.requested_date_scope)

    def test_requested_date_scope_is_saved_from_the_verified_turn(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show A1 absences on September 3, 2026.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_ids=("A1",),
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT attendance_date FROM attendance_records "
            "WHERE employee_id = 'A1' AND attendance_date = '2026-09-03'"
        )
        outcome = run_turn(
            TurnRequest(
                question="Show A1 absences on September 3, 2026.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(
            outcome.state.verified_turns[0].requested_date_scope,
            ("2026-09-03", "2026-09-03"),
        )
        self.assertEqual(
            outcome.state.trusted_context()["verified_turns"][0][
                "requested_date_scope"
            ],
            ("2026-09-03", "2026-09-03"),
        )

    def test_extra_reference_clauses_cannot_disable_single_source_date_guard(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show A1 absences on September 5, 2026.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_ids=("A1",),
                scope_clauses=(
                    {
                        "request": "Show A1 absences.",
                        "current_question_basis": "Show A1 absences",
                    },
                    {
                        "request": "On September 5, 2026.",
                        "current_question_basis": "Sep 5 2026",
                    },
                ),
            )
        )
        outcome = run_turn(
            TurnRequest(
                question="Show A1 absences on Sep 5 2026",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.code, "date_scope_mismatch")

    def test_new_criteria_union_keeps_reference_interpretation_separate(self):
        dependencies = self.dependencies()
        interpretation = (
            "Count Authorized records by country. Separately count distinct "
            "Human Resource employees by work location across all HR rows."
        )
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=interpretation,
                locale="en",
                request_relationship="new",
                subject_relationship="union",
                employee_criteria=("Authorized records", "Human Resource employees"),
                scope_clauses=(
                    {
                        "request": "Count Authorized records by country.",
                        "current_question_basis": "auth by country",
                        "carried_from_previous": (),
                    },
                    {
                        "request": "Count all HR people by work location.",
                        "current_question_basis": "hr work loc ppl count, all hr",
                        "carried_from_previous": (),
                    },
                ),
            )
        )
        seen = []
        dependencies.planner = lambda **kwargs: (
            seen.append(kwargs["shared_context"])
            or "SELECT COUNT(*) AS record_count FROM attendance_records"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"record_count": 1},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=20
            ),
        )
        dependencies.answerer = lambda **_kwargs: "One record."
        outcome = run_turn(
            TurnRequest(
                question="auth by country; hr work loc ppl count, all hr",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(
            seen[0].updated_request,
            "Request:\nauth by country; hr work loc ppl count, all hr",
        )
        self.assertEqual(
            seen[0].scope_provenance["reference_interpretation"],
            interpretation,
        )
        self.assertEqual(len(seen[0].scope_provenance["reference_scope_clauses"]), 2)
        self.assertEqual(
            outcome.state.verified_turns[0].scope_clauses[1].request,
            "Count all HR people by work location.",
        )

    def test_new_request_rewrite_cannot_invent_an_unrequested_date(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request=("Count A1 working days starting from 2026-09-26."),
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_ids=("A1",),
            )
        )
        seen = []

        def planner(**kwargs):
            seen.append(kwargs["shared_context"])
            return (
                "SELECT COUNT(DISTINCT attendance_date) AS worked_days "
                "FROM attendance_records WHERE employee_id = 'A1' "
                "AND day_type = 'Working Day'"
            )

        dependencies.planner = planner
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"worked_days": 5},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=20
            ),
        )
        dependencies.answerer = lambda **_kwargs: "Wail Ali worked 5 days."

        outcome = run_turn(
            TurnRequest(
                question="How many days did employee A1 work?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(
            seen[0].current_question, "How many days did employee A1 work?"
        )
        self.assertNotIn("2026-09-26", seen[0].updated_request)
        self.assertIn("How many days did employee A1 work?", seen[0].updated_request)
        self.assertIn(
            "2026-09-26", seen[0].scope_provenance["reference_interpretation"]
        )
        self.assertIsNone(outcome.state.verified_turns[0].requested_date_scope)

    def test_follow_up_recovers_active_employee_when_rewriter_calls_it_missing(self):
        dependencies = self.dependencies()
        reference_calls = []

        def reference_writer(_question, **kwargs):
            reference_calls.append(kwargs["model"])
            if len(reference_calls) == 1:
                return ReferenceResponse(
                    decision=AmbiguousReference(
                        rewritten_request="Show the absence dates for Wail Ali (A1).",
                        locale="en",
                        reason="missing_employee",
                        employee_mention="Wail Ali",
                    )
                )
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request="Show the absence dates for Wail Ali (A1).",
                    locale="en",
                    request_relationship="follow_up",
                    subject_relationship="employees",
                    employee_ids=("A1",),
                )
            )

        dependencies.reference_writer = reference_writer
        employee = Employee(employee_id="A1", name="Wail Ali")
        previous = ConversationState(
            active_employee_ids=("A1",), active_employees=(employee,)
        )
        seen = []

        def planner(**kwargs):
            seen.append(kwargs["shared_context"])
            return (
                "SELECT attendance_date FROM attendance_records "
                "WHERE employee_id = 'A1' AND exception = 'Absent'"
            )

        dependencies.planner = planner
        outcome = run_turn(
            TurnRequest(
                question="Show the absence dates instead.",
                history=(
                    {"role": "user", "content": "Show A1 attendance."},
                    {"role": "assistant", "content": "One record."},
                ),
                state=previous,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(reference_calls), 2)
        self.assertEqual(seen[0].request_relationship, "follow_up")
        self.assertEqual(
            seen[0].conversation_history,
            (
                {"role": "user", "content": "Show A1 attendance."},
                {"role": "assistant", "content": "One record."},
            ),
        )
        self.assertEqual(outcome.state.active_employee_ids, ("A1",))
        self.assertIn(
            "Wail Ali (A1)", outcome.state.verified_turns[-1].rewritten_request
        )

    def test_follow_up_does_not_inherit_employee_outside_current_access_scope(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=AmbiguousReference(
                rewritten_request="Show the absence dates instead.",
                locale="en",
                reason="missing_employee",
            )
        )
        employee = Employee(employee_id="A1", name="Wail Ali")
        previous = ConversationState(
            active_employee_ids=("A1",), active_employees=(employee,)
        )
        access = AccessContext(
            principal_id="limited",
            domain="attendance",
            allowed_domains=frozenset({"attendance"}),
            attendance_scope=AttendanceRowScope("employee_ids", ("A2",)),
        )
        outcome = run_turn(
            TurnRequest(
                question="Show the absence dates instead.",
                state=previous,
                access_context=access,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Clarification)
        self.assertEqual(outcome.state, previous)

    def test_all_downstream_stages_receive_one_shared_context_instance(self):
        seen = {}
        dependencies = self.dependencies()

        def planner(**kwargs):
            seen["planner"] = kwargs["shared_context"]
            return "SELECT 1"

        def answerer(**kwargs):
            seen["answer"] = kwargs["shared_context"]
            return "Wail Ali has one matching row."

        dependencies.planner = planner
        dependencies.answerer = answerer
        outcome = run_turn(
            TurnRequest(
                question="show A1 dates",
                history=({"role": "user", "content": "attendance for A1"},),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertIs(seen["planner"], seen["answer"])
        self.assertEqual(
            set(seen["planner"].model_payload()),
            {
                "current_question",
                "latest_user_message",
                "as_of_date",
                "last_calendar_month",
                "updated_request",
                "previous_verified_turn",
                "request_relationship",
                "subject_relationship",
                "resolved_employee_ids",
                "required_date_scope",
                "requested_period_vs_observed_rows",
                "request_has_date_period",
                "conversation_history",
                "trusted_context",
                "scope_provenance",
                "database_type",
                "database_context",
                "observed_date_ranges",
                "calendar_month_date_extent",
                "resolution_statement",
            },
        )
        self.assertEqual(seen["planner"].current_question, "show A1 dates")
        self.assertEqual(seen["planner"].latest_user_message, "show A1 dates")
        self.assertEqual(
            seen["planner"].conversation_history,
            ({"role": "user", "content": "attendance for A1"},),
        )
        self.assertEqual(seen["planner"].trusted_context, {})

    def test_pipeline_logs_each_application_layer_output(self):
        with self.assertLogs(
            "week5.new_implementation.online.layers", level="DEBUG"
        ) as captured:
            outcome = run_turn(
                TurnRequest(
                    question="show A1 absence dates",
                    access_context=LOCAL_DEMO_ACCESS,
                ),
                dependencies=self.dependencies(),
            )

        self.assertIsInstance(outcome, Answered)
        combined = "\n".join(captured.output)
        for layer in (
            "employee_directory",
            "employee_resolution",
            "database_context",
            "sql_planner",
            "sql_execution",
            "answer",
            "publication",
        ):
            self.assertIn(f"layer={layer}", combined)

    def test_restricted_access_scopes_schema_observations(self):
        dependencies = self.dependencies()
        seen = []

        def load_context(**kwargs):
            seen.append(kwargs["allowed_employee_ids"])
            return database_context()

        dependencies.context_loader = load_context
        outcome = run_turn(
            TurnRequest(
                question="Show A1 absence dates.",
                access_context=AccessContext(
                    principal_id="restricted",
                    domain="attendance",
                    allowed_domains=frozenset({"attendance"}),
                    attendance_scope=AttendanceRowScope("employee_ids", ("A1",)),
                ),
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(seen, [("A1",)])

    def test_provider_or_verifier_failure_preserves_exact_prior_state(self):
        state = ConversationState(active_employee_ids=("A1",))
        dependencies = self.dependencies()
        dependencies.answerer = lambda **_kwargs: (_ for _ in ()).throw(
            ProviderFailure("sql_answer_review", "answer_review_failed", "rejected")
        )

        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.state, state)

    def test_review_can_replan_sql_and_publish_only_corrected_evidence(self):
        state = ConversationState()
        dependencies = self.dependencies()
        plans = []
        executed = []
        reviewed = []

        def planner(**kwargs):
            plans.append(kwargs)
            return f"SELECT attempt_{len(plans)}"

        def executor(sql, **_kwargs):
            executed.append(sql)
            return sql_result()

        def answerer(**kwargs):
            reviewed.append(kwargs["sql"])
            if len(reviewed) == 1:
                return ReplanRequest(
                    reason="The first SQL omitted a requested measure. Include it in a new query."
                )
            return "The corrected result answers both requested measures."

        dependencies.planner = planner
        dependencies.executor = executor
        dependencies.answerer = answerer

        outcome = run_turn(
            TurnRequest(
                question="Show A1 absence dates and hours",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(executed, ["SELECT attempt_1", "SELECT attempt_2"])
        self.assertEqual(reviewed, executed)
        self.assertEqual(
            plans[1]["sql_execution_failure"]["error_type"], "answer_review_requery"
        )
        self.assertIn(
            "omitted a requested measure",
            plans[1]["sql_execution_failure"]["database_error"],
        )
        self.assertEqual(
            outcome.state.verified_turns[-1].executed_sql, "SELECT attempt_2"
        )

    def test_review_requery_stops_at_sql_attempt_limit_without_publication(self):
        state = ConversationState()
        dependencies = self.dependencies()
        plans = []

        def planner(**kwargs):
            plans.append(kwargs)
            return f"SELECT attempt_{len(plans)}"

        dependencies.planner = planner
        dependencies.answerer = lambda **_kwargs: ReplanRequest(
            reason="The query still lacks required evidence."
        )
        outcome = run_turn(
            TurnRequest(
                question="Show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.state, state)
        self.assertLessEqual(len(plans), 4)

    def test_review_requery_respects_shared_provider_call_budget(self):
        state = ConversationState()
        dependencies = self.dependencies()
        plans = []

        def planner(**kwargs):
            kwargs["budget"].claim("sql_planner")
            plans.append(kwargs)
            return f"SELECT attempt_{len(plans)}"

        def answerer(**kwargs):
            kwargs["budget"].claim("sql_final_answer")
            kwargs["budget"].claim("sql_answer_review")
            return ReplanRequest(reason="The evidence still omits a requested field.")

        dependencies.planner = planner
        dependencies.answerer = answerer
        outcome = run_turn(
            TurnRequest(
                question="Show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.code, "call_budget_exceeded")
        self.assertEqual(outcome.state, state)
        self.assertLessEqual(len(plans), 4)

    def test_database_failure_preserves_exact_prior_state(self):
        state = ConversationState()
        dependencies = self.dependencies()
        dependencies.executor = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("statement timeout")
        )

        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.state, state)

    def test_postgres_query_rejection_is_replanned_once_then_succeeds(self):
        dependencies = self.dependencies()
        planner_calls = []
        execution_calls = []

        def planner(**kwargs):
            planner_calls.append(kwargs)
            return f"SELECT attempt_{len(planner_calls)}"

        def executor(sql, **_kwargs):
            execution_calls.append(sql)
            if len(execution_calls) == 1:
                raise psycopg.errors.UndefinedColumn(
                    f"column attempt_{len(execution_calls)} does not exist"
                )
            return sql_result()

        dependencies.planner = planner
        dependencies.executor = executor

        outcome = run_turn(
            TurnRequest(
                question="show A1 dates",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(planner_calls), 2)
        self.assertEqual(len(execution_calls), 2)
        self.assertNotIn("sql_execution_failure", planner_calls[0])
        for retry_number, call in enumerate(planner_calls[1:], start=1):
            failure = call["sql_execution_failure"]
            self.assertEqual(failure["retry_number"], retry_number)
            self.assertEqual(failure["failed_sql"], f"SELECT attempt_{retry_number}")
            self.assertEqual(failure["error_type"], "UndefinedColumn")
            self.assertIn("does not exist", failure["database_error"])
        self.assertEqual(
            outcome.state.verified_turns[-1].executed_sql, "SELECT attempt_2"
        )

    def test_row_bound_replans_with_bounded_result_contract(self):
        dependencies = self.dependencies()
        calls = []

        def planner(**kwargs):
            calls.append(kwargs)
            return f"SELECT attempt_{len(calls)}"

        executions = []

        def executor(sql, **_kwargs):
            executions.append(sql)
            if len(executions) == 1:
                raise RuntimeError("result row bound exceeded")
            return sql_result()

        dependencies.planner = planner
        dependencies.executor = executor
        outcome = run_turn(
            TurnRequest(question="show A1 dates", access_context=LOCAL_DEMO_ACCESS),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(calls), 2)
        self.assertIn(
            "COUNT(*) OVER() AS matched_count",
            calls[1]["sql_execution_failure"]["database_error"],
        )

    def test_unavailable_table_stops_before_meaning_changing_retry(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Join attendance to payroll.",
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        planner_calls = []

        def planner(**kwargs):
            planner_calls.append(kwargs)
            return "SELECT * FROM payroll WHERE attendance_date >= '2026-09-01'"

        dependencies.planner = planner
        dependencies.executor = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unavailable relation reached execution")
        )
        state = ConversationState()
        outcome = run_turn(
            TurnRequest(
                question="Join attendance to payroll.",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "schema")
        self.assertEqual(outcome.state, state)
        self.assertEqual(len(planner_calls), 1)

    def test_two_postgres_query_rejections_allow_third_planner_attempt(self):
        dependencies = self.dependencies()
        planner_calls = []
        execution_calls = []

        def planner(**kwargs):
            planner_calls.append(kwargs)
            return f"SELECT attempt_{len(planner_calls)}"

        def executor(sql, **_kwargs):
            execution_calls.append(sql)
            if len(execution_calls) < 3:
                raise psycopg.errors.UndefinedColumn("column does not exist")
            return sql_result()

        dependencies.planner = planner
        dependencies.executor = executor

        outcome = run_turn(
            TurnRequest(
                question="show A1 dates",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(
            execution_calls,
            ["SELECT attempt_1", "SELECT attempt_2", "SELECT attempt_3"],
        )
        self.assertEqual(planner_calls[2]["sql_execution_failure"]["retry_number"], 2)
        self.assertEqual(
            planner_calls[2]["sql_execution_failure"]["failed_sql"], "SELECT attempt_2"
        )
        self.assertEqual(
            outcome.state.verified_turns[-1].executed_sql, "SELECT attempt_3"
        )

    def test_fourth_postgres_query_rejection_fails_without_publishing_state(self):
        state = ConversationState()
        dependencies = self.dependencies()
        planner_calls = []
        execution_calls = []

        def planner(**kwargs):
            planner_calls.append(kwargs)
            return f"SELECT attempt_{len(planner_calls)}"

        def executor(sql, **_kwargs):
            execution_calls.append(sql)
            raise psycopg.errors.UndefinedFunction("function does not exist")

        dependencies.planner = planner
        dependencies.executor = executor

        outcome = run_turn(
            TurnRequest(
                question="show A1 records",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.state, state)
        self.assertEqual(len(planner_calls), 4)
        self.assertEqual(len(execution_calls), 4)

    def test_missing_access_fails_before_planning_and_preserves_state(self):
        dependencies = self.dependencies()
        dependencies.planner = lambda **_kwargs: self.fail("planning must not run")
        state = ConversationState()

        outcome = run_turn(
            TurnRequest(question="show A1 absence dates", state=state),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.code, "authorization_failed")
        self.assertEqual(outcome.state, state)

    def test_malformed_employee_identifier_is_unsupported_before_planning(self):
        dependencies = self.dependencies()
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A1", name="Wail Ali"),
        )
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show employee 12345 attendance.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_ids=("12345",),
            )
        )
        dependencies.planner = lambda **_kwargs: self.fail("planning must not run")

        outcome = run_turn(
            TurnRequest(
                question="Show employee 12345 attendance.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "malformed_identifier")

    def test_unknown_id_is_classified_by_reference_before_directory_rejection(self):
        dependencies = self.dependencies()
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A11017", name="Known Person"),
        )
        seen = []

        def reference_writer(question, **_kwargs):
            seen.append(question)
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="new",
                    subject_relationship="employees",
                    employee_ids=("A99999",),
                )
            )

        dependencies.reference_writer = reference_writer
        dependencies.planner = lambda **_kwargs: self.fail("unresolved ID reached SQL")
        outcome = run_turn(
            TurnRequest(
                question="Show attendance for employee A99999.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertEqual(seen, ["Show attendance for employee A99999."])
        self.assertIsInstance(outcome, Clarification)
        self.assertEqual(outcome.reason, "unknown_employee_id")

    def test_planner_model_rechecks_uncertain_reference_before_clarification(self):
        dependencies = self.dependencies()
        models = []

        def reference_writer(question, **kwargs):
            models.append(kwargs["model"])
            if len(models) == 1:
                return ReferenceResponse(
                    decision=AmbiguousReference(
                        rewritten_request=question,
                        locale="en",
                        reason="missing_employee",
                    )
                )
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="new",
                    subject_relationship="criteria",
                    employee_criteria=("absent employees",),
                )
            )

        dependencies.reference_writer = reference_writer
        dependencies.planner = lambda **_kwargs: (
            "SELECT COUNT(*) AS absent_count FROM attendance_records "
            "WHERE exception = 'Absent'"
        )
        outcome = run_turn(
            TurnRequest(
                question="Count absent employees.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0], models[1])

    def test_planner_model_rechecks_conflicting_reference_fields(self):
        dependencies = self.dependencies()
        models = []

        def reference_writer(question, **kwargs):
            models.append(kwargs["model"])
            if len(models) == 1:
                return ReferenceResponse(
                    decision=ReadyReference(
                        rewritten_request=question,
                        locale="en",
                        request_relationship="new",
                        subject_relationship="criteria",
                        employee_names=("Wail Ali",),
                    )
                )
            return ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="new",
                    subject_relationship="employees",
                    employee_ids=("A1",),
                )
            )

        dependencies.reference_writer = reference_writer
        outcome = run_turn(
            TurnRequest(
                question="Show attendance for Wail Ali A1.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0], models[1])
        self.assertEqual(outcome.state.active_employee_ids, ("A1",))

    def test_unclassified_reference_leaves_subject_for_sql_planner(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda question, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request=question,
                locale="en",
                capability="outside_attendance_domain",
            )
        )
        subjects = []

        def planner(**kwargs):
            subjects.append(kwargs["shared_context"].subject_relationship)
            return "SELECT COUNT(*) AS record_count FROM attendance_records"

        dependencies.planner = planner
        outcome = run_turn(
            TurnRequest(
                question="How many attendance records exist?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(subjects, [None])

    def test_confirmed_ambiguous_follow_up_reaches_planner_with_prior_period(self):
        pending = PendingEmployeeConfirmation(
            original_question="Show Wail Ali's absence dates instead.",
            mention="Wail Ali",
            options=(EmployeeOption(employee_id="A1", employee_name="Wail Ali"),),
            resolution=PendingResolution(
                rewritten_request="Show Wail Ali's absence dates instead.",
                locale="en",
                request_relationship="follow_up",
                subject_relationship="employees",
            ),
        )
        state = ConversationState(
            verified_turns=(
                VerifiedTurn(
                    turn_id="prior",
                    original_question="Show September 2026 attendance.",
                    rewritten_request="Show September 2026 attendance.",
                    answer="One date.",
                    locale="en",
                    executed_sql=(
                        "SELECT attendance_date FROM attendance_records "
                        "WHERE attendance_date >= '2026-09-01' "
                        "AND attendance_date < '2026-10-01'"
                    ),
                    date_scope=("2026-09-01", "2026-09-30"),
                ),
            ),
            pending_employee_confirmation=pending,
        )
        dependencies = self.dependencies()
        seen = []

        def planner(**kwargs):
            seen.append(kwargs["shared_context"])
            return (
                "SELECT attendance_date FROM attendance_records "
                "WHERE employee_id = 'A1' AND exception = 'Absent' "
                "AND attendance_date >= '2026-09-01' "
                "AND attendance_date < '2026-10-01'"
            )

        dependencies.planner = planner
        outcome = run_turn(
            TurnRequest(
                question="1",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].current_question, pending.original_question)
        self.assertEqual(seen[0].latest_user_message, "1")
        self.assertEqual(seen[0].required_date_scope, ("2026-09-01", "2026-09-30"))

    def test_unresolved_person_without_candidates_does_not_reach_sql_planner(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=AmbiguousReference(
                rewritten_request="Show overtime for Unknown Human.",
                locale="en",
                reason="ambiguous_reference",
                employee_mention="Unknown Human",
            )
        )
        dependencies.employee_fuzzy_search = lambda *_args, **_kwargs: ()
        dependencies.planner = lambda **_kwargs: self.fail("person reached SQL")
        outcome = run_turn(
            TurnRequest(
                question="Show overtime for Unknown Human.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Clarification)
        self.assertEqual(outcome.reason, "ambiguous_reference")

    def test_unknown_written_person_stops_after_empty_directory_search(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=AmbiguousReference(
                rewritten_request="Count authorized records for Unknown Human.",
                locale="en",
                reason="ambiguous_reference",
                employee_mention="Unknown Human",
            )
        )
        dependencies.employee_fuzzy_search = lambda *_args, **_kwargs: ()
        dependencies.planner = lambda **_kwargs: self.fail(
            "unknown written person must not become an all-records query"
        )
        outcome = run_turn(
            TurnRequest(
                question="Count authorized records for Unknown Human.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )
        self.assertIsInstance(outcome, Clarification)
        self.assertEqual(outcome.reason, "ambiguous_reference")

    def test_non_finite_numeric_comparison_is_unsupported_before_reference(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "reference resolution must not run"
        )

        outcome = run_turn(
            TurnRequest(
                question="Show lateness greater than NaN hours.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "malformed_value")

    def test_numeric_threshold_before_metric_name_is_not_rejected(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show records with more than 1 lateness hour.",
                locale="en",
                request_relationship="new",
                subject_relationship="criteria",
                employee_criteria=("more than 1 lateness hour",),
            )
        )

        outcome = run_turn(
            TurnRequest(
                question="Show concerning records with more than 1 lateness hour.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)

    def test_impossible_calendar_date_is_unsupported_before_reference(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "reference resolution must not run"
        )

        outcome = run_turn(
            TurnRequest(
                question="Count records on September 31, 2026.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "malformed_value")

    def test_planner_unsupported_protocol_returns_explicit_unsupported_outcome(self):
        dependencies = self.dependencies()
        dependencies.planner = lambda **_kwargs: (
            "SELECT 'requested concept is not represented by the attendance schema' "
            "AS unsupported_capability"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=(
                {
                    "unsupported_capability": (
                        "requested concept is not represented by the attendance schema"
                    )
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=90
            ),
        )
        dependencies.answerer = lambda **_kwargs: (
            "The attendance schema has no loan balance field."
        )

        outcome = run_turn(
            TurnRequest(
                question="How much loan balance does A1 have?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "schema")
        self.assertEqual(
            outcome.reply, "The attendance schema has no loan balance field."
        )

    def test_planner_clarification_protocol_returns_explicit_clarification(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="List HR employees with an unclear manual swipe meaning.",
                locale="en",
                request_relationship="new",
                subject_relationship="all_authorized",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT 'Please clarify which attendance category you mean.'::text "
            "AS clarification_required"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=(
                {
                    "clarification_required": (
                        "Please clarify which attendance category you mean."
                    )
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=76
            ),
        )
        dependencies.answerer = lambda **_kwargs: (
            "Which attendance category do you mean?"
        )

        previous = ConversationState()
        outcome = run_turn(
            TurnRequest(
                question="List HR employees with the unclear manual swipe category.",
                state=previous,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Clarification)
        self.assertEqual(outcome.reason, "planner_clarification")
        self.assertEqual(outcome.reply, "Which attendance category do you mean?")
        self.assertEqual(outcome.state, previous)

    def test_reference_unsupported_domain_reaches_planner(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request="List repayment records for employee A1.",
                locale="en",
                capability="outside_attendance_domain",
            )
        )
        dependencies.planner = lambda **_kwargs: (
            "SELECT 'Repayment is not in the schema.'::text AS unsupported_capability"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=({"unsupported_capability": "Repayment is not in the schema."},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=40
            ),
        )

        outcome = run_turn(
            TurnRequest(
                question="List repayment records for A1.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "schema")

    def test_unresolved_name_uses_confirm_only_postgres_options(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show attendance for XX.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_names=("XX",),
            )
        )
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A1", name="Wail Ali"),
            Employee(employee_id="A2", name="Faris Hassan"),
        )
        dependencies.employee_fuzzy_search = lambda *_args, **_kwargs: (
            EmployeeOption(employee_id="A2", employee_name="Faris Hassan"),
        )
        dependencies.employee_fallback_search = lambda *_args, **_kwargs: self.fail(
            "semantic nearest-neighbor names must not be offered for confirmation"
        )

        first = run_turn(
            TurnRequest(
                question="show XX attendance", access_context=LOCAL_DEMO_ACCESS
            ),
            dependencies=dependencies,
        )
        second = run_turn(
            TurnRequest(
                question="yes", state=first.state, access_context=LOCAL_DEMO_ACCESS
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(first, Clarification)
        self.assertIsInstance(second, Answered)
        self.assertEqual(second.state.active_employee_ids, ("A2",))
        self.assertIn(
            "Faris Hassan (A2)", second.state.verified_turns[0].rewritten_request
        )

    def test_incompatible_state_resets_instead_of_migrating(self):
        reset = ConversationState.from_untrusted(
            {"runtime_version": "attendance-state/v3", "session_id": "old"}
        )
        self.assertEqual(reset.runtime_version, "attendance-online/v1")
        self.assertNotEqual(reset.session_id, "old")


if __name__ == "__main__":
    unittest.main()
