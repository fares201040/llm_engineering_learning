from __future__ import annotations

import unittest

import psycopg

from week5.new_implementation.online.context import DatabaseColumn
from week5.new_implementation.online.execution import (
    AccessContext,
    AttendanceRowScope,
    ExecutionCoverage,
    LOCAL_DEMO_ACCESS,
    SqlExecutionResult,
)
from week5.new_implementation.online.pipeline import (
    Answered,
    Clarification,
    Failed,
    RuntimeDependencies,
    TurnRequest,
    Unsupported,
    _locale,
    _missing_join_target,
    _schema_grounded_analytic_question,
    _mentions_time_period,
    _previous_having,
    _request_value_issue,
    _build_native_attendance_observation_sql,
    _build_native_employee_day_count_sql,
    _build_native_schema_aggregate_sql,
    _sql_date_scope,
    _sql_semantic_issue,
    _unrequested_date_filter,
    run_turn,
)
from week5.new_implementation.online.provider import ProviderFailure
from week5.new_implementation.online.reference import (
    AmbiguousReference,
    Employee,
    EmployeeOption,
    ReadyReference,
    ReferenceResponse,
    UnsupportedReference,
)
from week5.new_implementation.online.state import ConversationState, VerifiedTurn
from week5.new_implementation.tests.online.test_query import database_context
from week5.new_implementation.tests.online.test_comparison import attendance_schema


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


class PipelineTests(unittest.TestCase):
    def test_unrequested_date_filter_detects_predicate_not_date_grouping(self):
        self.assertTrue(
            _unrequested_date_filter(
                "SELECT department, SUM(CASE WHEN attendance_date >= '2026-09-01' "
                "THEN total_worked_hrs ELSE 0 END) AS worked_hours "
                "FROM attendance_records GROUP BY department"
            )
        )
        self.assertFalse(
            _unrequested_date_filter(
                "SELECT attendance_date, SUM(total_worked_hrs) AS worked_hours "
                "FROM attendance_records GROUP BY attendance_date"
            )
        )
        self.assertTrue(
            _unrequested_date_filter(
                "SELECT department, SUM(total_worked_hrs) FROM attendance_records "
                "WHERE attendance_date = (SELECT MAX(attendance_date) "
                "FROM attendance_records) GROUP BY department"
            )
        )
        self.assertTrue(
            _unrequested_date_filter(
                "SELECT department, SUM(total_worked_hrs) FROM attendance_records "
                "WHERE attendance_date IS NULL GROUP BY department"
            )
        )

    def test_independent_aggregate_retries_unrequested_date_filter_before_execution(
        self,
    ):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        attempts = []
        executed = []

        def planner(**kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                return (
                    "SELECT department, SUM(CASE WHEN attendance_date >= '2026-09-01' "
                    "THEN total_worked_hrs ELSE 0 END) AS worked_hours "
                    "FROM attendance_records GROUP BY department"
                )
            return (
                "SELECT department, SUM(total_worked_hrs) AS worked_hours "
                "FROM attendance_records GROUP BY department"
            )

        dependencies.planner = planner
        dependencies.executor = lambda sql, **_kwargs: (
            executed.append(sql)
            or SqlExecutionResult(
                columns=(),
                rows=({"department": "Engineering", "worked_hours": 56},),
                coverage=ExecutionCoverage(
                    fetched_rows=1, result_limit=100, response_bytes=50
                ),
            )
        )
        dependencies.answer_writer = lambda **_kwargs: "Engineering: 56 hours."

        outcome = run_turn(
            TurnRequest(
                question="Rank departments by worked hours.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(executed), 1)
        self.assertEqual(
            attempts[1]["sql_execution_failure"]["error_type"], "sql_semantics"
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
        dependencies.answer_writer = lambda **_kwargs: (
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

    def test_native_semantic_employee_plan_uses_observable_indicators(self):
        sql = _build_native_attendance_observation_sql(
            "Which employees show unusual attendance patterns?",
            attendance_schema(),
            (),
        )

        self.assertIsNotNone(sql)
        self.assertIn("GROUP BY employee_id, name", sql)
        self.assertIn("NULLIF(BTRIM(exception), '') IS NOT NULL", sql)
        self.assertIn("COUNT(*) OVER() AS matched_count", sql)

    def test_native_semantic_detail_plan_keeps_employee_date_and_threshold(self):
        sql = _build_native_attendance_observation_sql(
            "Show concerning records with more than 1 lateness hour on 2026-09-05.",
            attendance_schema(),
            (Employee(employee_id="A1", name="Wail Ali"),),
        )

        self.assertIsNotNone(sql)
        self.assertIn("employee_id IN ('A1')", sql)
        self.assertIn("attendance_date = '2026-09-05'", sql)
        self.assertIn("lateness_hrs > 1", sql)
        self.assertIn("record_id", sql)

    def test_native_status_detail_plan_handles_schema_values_without_model(self):
        sql = _build_native_attendance_observation_sql(
            "Show Draft records.", attendance_schema(), ()
        )

        self.assertIsNotNone(sql)
        self.assertIn("status = 'Draft'", sql)
        self.assertIn("COUNT(*) OVER() AS matched_count", sql)

    def test_native_status_detail_accepts_attendance_between_status_and_records(self):
        sql = _build_native_attendance_observation_sql(
            "List Authorized attendance records for A10029.",
            attendance_schema(),
            (Employee(employee_id="A10029", name="Suhail Mustafa Yousuf"),),
        )

        self.assertIn("status = 'Authorized'", sql)
        self.assertIn("employee_id IN ('A10029')", sql)

    def test_native_month_detail_plan_supports_general_attendance_scope(self):
        sql = _build_native_attendance_observation_sql(
            "Show attendance in September 2026.", attendance_schema(), ()
        )

        self.assertIn("attendance_date >= '2026-09-01'", sql)
        self.assertIn("attendance_date <= '2026-09-30'", sql)
        self.assertIn("COUNT(*) OVER() AS matched_count", sql)

    def test_native_employee_day_counts_preserve_business_meaning(self):
        employee = (Employee(employee_id="A11017", name="Faris Nasser Ali"),)
        schema = attendance_schema()
        table = schema.tables[0]
        schema = schema.model_copy(
            update={
                "tables": (
                    table.model_copy(
                        update={
                            "columns": table.columns
                            + (
                                DatabaseColumn(
                                    name="day_type",
                                    data_type="text",
                                    nullable=False,
                                    description="Scheduled day classification.",
                                ),
                            )
                        }
                    ),
                )
            }
        )
        worked_sql = _build_native_employee_day_count_sql(
            "How many days did A11017 attend during September 2026?",
            schema,
            employee,
        )
        missed_sql = _build_native_employee_day_count_sql(
            "Tell me how many days employee A11017 did not attend during September 2026.",
            schema,
            employee,
        )

        self.assertIn("COUNT(DISTINCT attendance_date)", worked_sql)
        self.assertIn("COALESCE(total_worked_hrs, 0) > 0", worked_sql)
        self.assertIn("day_type = 'Working Day'", missed_sql)
        self.assertIn("COALESCE(total_worked_hrs, 0) <= 0", missed_sql)

    def test_native_semantic_plan_covers_hr_review_and_early_departures(self):
        hr_sql = _build_native_attendance_observation_sql(
            "Describe attendance behavior that may need HR review.",
            attendance_schema(),
            (),
        )
        early_sql = _build_native_attendance_observation_sql(
            "Find attendance patterns involving early departures.",
            attendance_schema(),
            (),
        )

        self.assertIsNotNone(hr_sql)
        self.assertIn("attendance_indicator", hr_sql)
        self.assertIn("UPPER(BTRIM(exception)) <> 'OK'", hr_sql)
        self.assertIsNotNone(early_sql)
        self.assertIn("COALESCE(early_out_hrs, 0) > 0", early_sql)

    def test_native_exact_count_and_percentage_use_schema_columns(self):
        count_sql = _build_native_schema_aggregate_sql(
            'How many attendance records have Department equal to "Operations"?',
            attendance_schema(),
            (),
        )
        percentage_sql = _build_native_schema_aggregate_sql(
            'What percentage of all attendance records have Department equal to "Finance"?',
            attendance_schema(),
            (),
        )

        self.assertEqual(
            count_sql,
            'SELECT COUNT(*) AS matched_count FROM "public"."attendance_records" '
            "WHERE department = 'Operations'",
        )
        self.assertIn("COUNT(*) FILTER (WHERE department = 'Finance')", percentage_sql)
        self.assertIn("AS percentage", percentage_sql)

    def test_native_distinct_employee_status_count_preserves_aggregation(self):
        schema = attendance_schema()
        table = schema.tables[0]
        schema = schema.model_copy(
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
        sql = _build_native_schema_aggregate_sql(
            "Count distinct employees with Authorized attendance records.",
            schema,
            (),
        )

        self.assertIn("COUNT(DISTINCT employee_id)", sql)
        self.assertIn("status = 'Authorized'", sql)

        employee_sql = _build_native_schema_aggregate_sql(
            "How many Authorized attendance records does A10055 have?",
            schema,
            (Employee(employee_id="A10055", name="Example Employee"),),
        )
        self.assertIn("COUNT(*) AS matched_count", employee_sql)
        self.assertIn("employee_id IN ('A10055')", employee_sql)
        self.assertIn("status = 'Authorized'", employee_sql)

        combined_sql = _build_native_schema_aggregate_sql(
            (
                "How many Authorized attendance records belong to A10017 and "
                "A10029 combined?"
            ),
            schema,
            (
                Employee(employee_id="A10017", name="Employee One"),
                Employee(employee_id="A10029", name="Employee Two"),
            ),
        )
        self.assertIn("employee_id IN ('A10017', 'A10029')", combined_sql)
        self.assertIn("status = 'Authorized'", combined_sql)
        self.assertIsNone(
            _build_native_schema_aggregate_sql(
                "How many Authorized attendance records belong to A99999?",
                schema,
                (),
            )
        )

    def test_native_threshold_counts_validate_columns_and_comparators(self):
        greater_sql = _build_native_schema_aggregate_sql(
            "How many attendance records have Total_Worked_Hrs greater than 0?",
            attendance_schema(),
            (),
        )
        at_least_sql = _build_native_schema_aggregate_sql(
            "How many attendance records have Total_Worked_Hrs at least 8?",
            attendance_schema(),
            (),
        )

        self.assertIn("total_worked_hrs > 0", greater_sql)
        self.assertIn("total_worked_hrs >= 8", at_least_sql)

    def test_native_grouped_average_and_sum_preserve_metric_and_group(self):
        average_sql = _build_native_schema_aggregate_sql(
            "What is average Total_Worked_Hrs by Department?",
            attendance_schema(),
            (),
        )
        sum_sql = _build_native_schema_aggregate_sql(
            "Sum Total_Worked_Hrs by Department.", attendance_schema(), ()
        )

        self.assertIn(
            "department, AVG(total_worked_hrs) AS average_total_worked_hrs",
            average_sql,
        )
        self.assertIn("GROUP BY department", average_sql)
        self.assertIn(
            "department, SUM(total_worked_hrs) AS total_total_worked_hrs", sum_sql
        )
        self.assertIn("GROUP BY department", sum_sql)

    def test_schema_grounded_analytic_detection_rejects_unknown_concepts(self):
        schema = attendance_schema()
        table = schema.tables[0]
        schema = schema.model_copy(
            update={
                "tables": (
                    table.model_copy(
                        update={
                            "columns": tuple(
                                column.model_copy(
                                    update={
                                        "description": "Payroll-effective worked hours."
                                    }
                                )
                                if column.name == "total_worked_hrs"
                                else column
                                for column in table.columns
                            )
                        }
                    ),
                )
            }
        )
        self.assertTrue(
            _schema_grounded_analytic_question(
                "Rank departments by worked hours.", schema
            )
        )
        self.assertFalse(
            _schema_grounded_analytic_question("Rank departments by salary.", schema)
        )
        self.assertFalse(
            _schema_grounded_analytic_question(
                "Rank departments by payroll worked hours.", schema
            )
        )
        self.assertFalse(
            _schema_grounded_analytic_question("Compare that with last month.", schema)
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
        dependencies.answer_writer = lambda **_kwargs: "Engineering: 56 hours."

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

    def test_missing_employee_cannot_veto_explicit_grouped_schema_aggregate(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=AmbiguousReference(
                rewritten_request="Sum Total_Worked_Hrs by Department.",
                locale="en",
                reason="missing_employee",
            )
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

    def test_missing_employee_cannot_veto_general_month_attendance_details(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=AmbiguousReference(
                rewritten_request="Show attendance in September 2026.",
                locale="en",
                reason="missing_employee",
            )
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

    def test_unknown_grouped_metric_is_typed_unsupported_calculation(self):
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "invalid schema calculations should stop before model classification"
        )

        outcome = run_turn(
            TurnRequest(
                question="What is the average of banana by department?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "unsupported_calculation")

    def test_unsupported_reference_cannot_veto_named_employee_aggregate(self):
        dependencies = self.dependencies()
        employee = Employee(employee_id="A1", name="Wail Ali")
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request="What were Wail Ali's total worked hours?",
                locale="en",
                capability="outside_attendance_domain",
            )
        )
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
        dependencies.answer_writer = lambda **_kwargs: "Wail Ali worked 32.41 hours."

        outcome = run_turn(
            TurnRequest(
                question="What were Wail Ali's total worked hours?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(outcome.state.verified_turns[-1].employee_ids, ("A1",))

    def test_unsupported_reference_cannot_veto_employee_status_count(self):
        dependencies = self.dependencies()
        employee = Employee(employee_id="A1", name="Wail Ali")
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
        dependencies.answer_writer = lambda **_kwargs: "Lateness: 8 records."

        outcome = run_turn(
            TurnRequest(
                question="Find potentially problematic attendance behavior.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(outcome.state.verified_turns[-1].employee_ids, ())

    def test_join_preflight_defers_when_both_or_neither_targets_are_known(self):
        schema = attendance_schema()
        self.assertFalse(
            _missing_join_target("Join attendance to attendance_records.", schema)
        )
        self.assertFalse(_missing_join_target("Join payroll to benefits.", schema))

    def test_missing_join_target_is_schema_unsupported_before_reference_model(self):
        state = ConversationState()
        dependencies = self.dependencies()
        dependencies.context_loader = lambda **_kwargs: attendance_schema()
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "schema-declared join target absence should be resolved before the model"
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
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "clear grouped comparison must use verified state"
        )
        dependencies.planner = lambda **_kwargs: self.fail(
            "clear grouped comparison must not be model-replanned"
        )
        dependencies.answer_writer = lambda **_kwargs: self.fail(
            "typed grouped comparison result must be rendered consistently"
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
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "verified running total must not use reference model"
        )
        dependencies.planner = lambda **_kwargs: self.fail(
            "verified running total must not be model-replanned"
        )
        dependencies.answer_writer = lambda **_kwargs: self.fail(
            "verified running total must be rendered from checked results"
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
        self.assertEqual(context["verified_turns"][0]["executed_sql"], "SELECT 1")

    def test_question_language_controls_output_locale(self):
        self.assertEqual(_locale("كم يوم اشتغل A1؟"), "ar")
        self.assertEqual(_locale("How many days for A1?"), "en")
        self.assertEqual(_locale("How many days for A1? Reply in Arabic."), "ar")
        self.assertEqual(_locale("كم يوم اشتغل A1؟ جاوب بالإنجليزية"), "en")

    def test_negated_absence_requires_the_distinct_predicate(self):
        question = "Show dates that are not absent."
        self.assertEqual(
            _sql_semantic_issue(
                question,
                "SELECT attendance_date FROM attendance_records WHERE exception = 'Absent'",
            ),
            "wrong_absence_polarity",
        )
        self.assertIsNone(
            _sql_semantic_issue(
                question,
                "SELECT attendance_date FROM attendance_records WHERE exception IS DISTINCT FROM 'Absent'",
            )
        )
        self.assertIsNone(
            _sql_semantic_issue(
                "Compare absence counts by department.",
                "SELECT department, SUM(CASE WHEN exception = 'Absent' THEN 1 ELSE 0 END) "
                "FROM attendance_records GROUP BY department",
            )
        )
        self.assertIsNone(
            _sql_semantic_issue(
                'How many attendance records have Exception equal to "Absence Hours"?',
                "SELECT COUNT(*) FROM attendance_records "
                "WHERE exception = 'Absence Hours'",
            )
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Show the absence dates instead.",
                "SELECT attendance_date FROM attendance_records "
                "WHERE exception = 'Absent' OR COALESCE(total_worked_hrs, 0) <= 0",
            ),
            "wrong_absence_semantics",
        )
        self.assertEqual(
            _sql_semantic_issue(
                "Show the absence dates instead.",
                "SELECT attendance_date FROM attendance_records "
                "WHERE exception = 'Absent' AND day_type = 'Working Day'",
            ),
            "wrong_absence_semantics",
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
        seen = []

        def planner(**kwargs):
            seen.append(kwargs["shared_context"])
            return (
                "SELECT attendance_date FROM attendance_records "
                "WHERE employee_id = 'A1' AND exception = 'Absent'"
            )

        dependencies.planner = planner
        dependencies.answer_writer = lambda **_kwargs: self.fail(
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
        self.assertIn("Immediately previous verified request:", seen[0].updated_request)
        self.assertIn("Show A1 in September 2026.", seen[0].updated_request)
        self.assertIn(
            "Its verified SQL defines the grouping and eligibility:",
            seen[0].updated_request,
        )
        self.assertEqual(seen[0].subject_relationship, "employees")
        self.assertEqual(seen[0].resolved_employee_ids, ("A1",))

    def test_wrong_absence_polarity_does_not_publish_state(self):
        dependencies = self.dependencies()
        dependencies.planner = lambda **_kwargs: (
            "SELECT attendance_date FROM attendance_records "
            "WHERE employee_id = 'A1' AND exception = 'Absent'"
        )
        dependencies.answer_writer = lambda **_kwargs: self.fail(
            "wrong SQL must not reach the answer writer"
        )
        previous = ConversationState()
        outcome = run_turn(
            TurnRequest(
                question="Show A1 dates that are not absent.",
                state=previous,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.code, "wrong_absence_polarity")
        self.assertEqual(outcome.state, previous)

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
            answer_writer=lambda **_kwargs: "Wail Ali was absent on 2026-09-03.",
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
        dependencies.answer_writer = lambda **_kwargs: "Wail Ali worked 5 days."

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

    def test_follow_up_recovers_active_employee_when_rewriter_calls_it_missing(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=AmbiguousReference(
                rewritten_request="Show the absence dates for Wail Ali (A1).",
                locale="en",
                reason="missing_employee",
                employee_mention="Wail Ali",
            )
        )
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
        self.assertEqual(seen[0].request_relationship, "follow_up")
        self.assertEqual(
            seen[0].conversation_history,
            ({"role": "user", "content": "Show A1 attendance."},),
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

        def answer_writer(**kwargs):
            seen["answer"] = kwargs["shared_context"]
            return "Wail Ali has one matching row."

        dependencies.planner = planner
        dependencies.answer_writer = answer_writer
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
                "as_of_date",
                "last_calendar_month",
                "updated_request",
                "request_relationship",
                "subject_relationship",
                "resolved_employee_ids",
                "required_date_scope",
                "request_has_date_period",
                "attendance_meaning",
                "conversation_history",
                "trusted_context",
                "database_type",
                "database_context",
                "resolution_statement",
            },
        )
        self.assertEqual(seen["planner"].current_question, "show A1 dates")
        self.assertEqual(
            seen["planner"].conversation_history,
            (),
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

    def test_provider_or_verifier_failure_preserves_exact_prior_state(self):
        state = ConversationState(active_employee_ids=("A1",))
        dependencies = self.dependencies()
        dependencies.answer_writer = lambda **_kwargs: (_ for _ in ()).throw(
            ProviderFailure("answer_verifier", "answer_verdict_failed", "rejected")
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

    def test_third_postgres_query_rejection_fails_without_publishing_state(self):
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
                question="show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.state, state)
        self.assertEqual(len(planner_calls), 3)
        self.assertEqual(len(execution_calls), 3)

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

    def test_malformed_id_is_rejected_before_reference_or_fuzzy_search(self):
        dependencies = self.dependencies()
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A10017", name="Iftikhar Hasson Ismail"),
        )
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "reference resolution must not run"
        )
        dependencies.employee_fuzzy_search = lambda *_args, **_kwargs: self.fail(
            "fuzzy search must not run"
        )

        outcome = run_turn(
            TurnRequest(
                question="Show attendance for A10.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "malformed_identifier")

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
        dependencies.answer_writer = lambda **_kwargs: self.fail(
            "unsupported results must not reach answer writing"
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

    def test_reference_unsupported_domain_stops_before_planning(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request="List repayment records for employee A1.",
                locale="en",
                capability="outside_attendance_domain",
            )
        )
        dependencies.planner = lambda **_kwargs: self.fail("planning must not run")

        outcome = run_turn(
            TurnRequest(
                question="List repayment records for A1.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "outside_attendance_domain")

    def test_unresolved_name_uses_confirm_only_chroma_options(self):
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
        dependencies.employee_fallback_search = lambda *_args, **_kwargs: (
            EmployeeOption(employee_id="A2", employee_name="Faris Hassan"),
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
