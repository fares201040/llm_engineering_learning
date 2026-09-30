"""The SQL planner answers and reviews its executed result."""

from unittest.mock import patch

import pytest

from week5.new_implementation.online.context import DatabaseColumn, SharedModelContext
from week5.new_implementation.online.execution import (
    ExecutionCoverage,
    ResultColumn,
    SqlExecutionResult,
)
from week5.new_implementation.online.planner import (
    PlannerAnswer,
    ReplanRequest,
    ReviewedAnswer,
    answer_result,
    _count_reconciliation_answer,
    _reconciliation_group,
    build_count_reconciliation_sql,
    is_count_reconciliation_plan,
    request_sql,
)
from week5.new_implementation.online.provider import CallBudget
from week5.new_implementation.online.provider import ProviderFailure
from week5.new_implementation.online.reference import Employee
from week5.new_implementation.tests.online.test_query import database_context


def _count_database_context():
    context = database_context()
    table = context.tables[0]
    status = DatabaseColumn(
        name="status", data_type="text", nullable=True,
        description="Workflow approval status.",
    )
    return context.model_copy(update={
        "tables": (table.model_copy(update={"columns": table.columns + (status,)}),)
    })


@patch("week5.new_implementation.online.planner.call_structured")
def test_planner_reviews_and_corrects_answer_using_full_context(call):
    call.side_effect = [
        PlannerAnswer(answer="Mukhtar Ahmed Meer has seven attendance records."),
        ReviewedAnswer(
            answer="Mukhtar Ahmed Meer (A11000) is in Human Resource and his position is Human Resource Executive."
        ),
    ]
    shared = SharedModelContext(
        current_question="What is his department and position?",
        updated_request="Mukhtar Ahmed Meer (A11000): department and position",
        conversation_history=(
            {"role": "user", "content": "Who is Mukhtar Ahmd?"},
            {"role": "assistant", "content": "Choose a number."},
            {"role": "user", "content": "1"},
        ),
        required_date_scope=("2026-09-01", "2026-09-30"),
        database_context=database_context(),
    )
    rows = tuple(
        {
            "record_id": f"attendance:{index}",
            "employee_id": "A11000",
            "name": "Mukhtar Ahmed Meer",
            "department": "Human Resource",
            "position": "Human Resource Executive",
        }
        for index in range(7)
    )
    result = SqlExecutionResult(
        columns=tuple(ResultColumn(name=name) for name in rows[0]),
        rows=rows,
        coverage=ExecutionCoverage(
            fetched_rows=7, result_limit=100, response_bytes=700
        ),
    )
    answer = answer_result(
        shared_context=shared,
        sql="SELECT record_id, employee_id, name, department, position FROM attendance_records",
        result=result,
        employees=(Employee(employee_id="A11000", name="Mukhtar Ahmed Meer"),),
        locale="en",
        model="openai/gpt-5-nano",
        budget=CallBudget(),
        timeout=30,
        max_output_tokens=3000,
    )

    assert answer == (
        "Mukhtar Ahmed Meer (A11000) is in Human Resource and his position is Human Resource Executive."
    )
    assert [item.kwargs["stage"] for item in call.call_args_list] == [
        "sql_final_answer",
        "sql_answer_review",
    ]
    assert all(
        item.kwargs["model"] == "openai/gpt-5-nano" for item in call.call_args_list
    )
    assert all(
        "Examples of turning executed rows" in item.kwargs["system"]
        for item in call.call_args_list
    )
    for item in call.call_args_list:
        payload = item.kwargs["payload"]
        assert payload["latest_user_message"] == shared.current_question
        assert payload["conversation_history"] == list(shared.conversation_history)
        assert payload["database_result"]["rows"] == list(rows)
        assert payload["authoritative_employees"][0]["employee_id"] == "A11000"
        assert "database_context" in payload
        assert payload["requested_period_vs_observed_rows"][0][
            "request_extends_after_observed_rows"
        ]
    assert call.call_args_list[1].kwargs["payload"]["proposed_answer"] == (
        "Mukhtar Ahmed Meer has seven attendance records."
    )


@patch("week5.new_implementation.online.planner.call_structured")
def test_review_requests_new_evidence_when_executed_sql_cannot_answer(call):
    call.side_effect = [
        PlannerAnswer(answer="Engineering has 10 overtime hours."),
        ReviewedAnswer(
            answer="Engineering has 10 overtime hours.",
            requires_new_query=True,
            query_issue="The SQL restricts work_location to a prior result; the current request asks for every department.",
        ),
    ]
    shared = SharedModelContext(
        current_question="Compare overtime between departments in September.",
        updated_request="Compare overtime between departments in September.",
        conversation_history=(
            {"role": "user", "content": "Who is employee A1?"},
            {"role": "assistant", "content": "Employee A1 works at MES-Eng."},
        ),
        database_context=database_context(),
    )
    result = SqlExecutionResult(
        columns=(ResultColumn(name="department"), ResultColumn(name="hours")),
        rows=({"department": "Engineering", "hours": 10},),
        coverage=ExecutionCoverage(fetched_rows=1, result_limit=100, response_bytes=40),
    )

    decision = answer_result(
        shared_context=shared,
        sql="SELECT department, SUM(overtime) AS hours FROM attendance_records WHERE work_location = 'MES-Eng' GROUP BY department",
        result=result,
        employees=(),
        locale="en",
        model="openai/gpt-5-nano",
        budget=CallBudget(),
        timeout=30,
        max_output_tokens=3000,
    )

    assert isinstance(decision, ReplanRequest)
    assert "work_location" in decision.reason
    review_system = call.call_args_list[1].kwargs["system"]
    assert "current_question" in review_system
    assert "proposed_answer" in review_system
    assert "new query" in review_system.lower()


@patch("week5.new_implementation.online.planner.call_structured")
def test_review_requery_requires_actionable_feedback(call):
    call.side_effect = [
        PlannerAnswer(answer="The records show one department."),
        ReviewedAnswer(
            answer="The records show one department.",
            requires_new_query=True,
            query_issue="",
        ),
    ]
    shared = SharedModelContext(
        current_question="Compare departments.",
        updated_request="Compare departments.",
        database_context=database_context(),
    )
    result = SqlExecutionResult(
        columns=(),
        rows=({"department": "Engineering"},),
        coverage=ExecutionCoverage(fetched_rows=1, result_limit=100, response_bytes=30),
    )

    with pytest.raises(ProviderFailure) as error:
        answer_result(
            shared_context=shared,
            sql="SELECT department FROM attendance_records",
            result=result,
            employees=(),
            locale="en",
            model="openai/gpt-5-nano",
            budget=CallBudget(),
            timeout=30,
            max_output_tokens=3000,
        )
    assert error.value.code == "missing_replan_reason"


@patch("week5.new_implementation.online.planner.call_structured")
def test_comparison_report_requests_a_supported_markdown_table(call):
    call.side_effect = [
        PlannerAnswer(answer="August has 16 hours; September has 40 hours."),
        ReviewedAnswer(
            answer=(
                "| Period | Worked hours |\n"
                "| --- | ---: |\n"
                "| August | 16 |\n"
                "| September | 40 |\n\n"
                "Available records do not cover either full month."
            )
        ),
    ]
    shared = SharedModelContext(
        current_question="Give me a comparison report of August and September hours.",
        updated_request="Compare August and September worked hours.",
        database_context=database_context(),
    )
    result = SqlExecutionResult(
        columns=(ResultColumn(name="period"), ResultColumn(name="worked_hours")),
        rows=(
            {"period": "August", "worked_hours": 16},
            {"period": "September", "worked_hours": 40},
        ),
        coverage=ExecutionCoverage(fetched_rows=2, result_limit=100, response_bytes=90),
    )

    answer = answer_result(
        shared_context=shared,
        sql="SELECT period, SUM(worked_hours) AS worked_hours FROM attendance_records GROUP BY period",
        result=result,
        employees=(),
        locale="en",
        model="openai/gpt-5-nano",
        budget=CallBudget(),
        timeout=30,
        max_output_tokens=3000,
    )

    assert answer.startswith("| Period | Worked hours |\n| --- | ---: |")
    assert "do not cover either full month" in answer
    assert all(
        "Markdown table" in item.kwargs["system"] for item in call.call_args_list
    )


@patch("week5.new_implementation.online.planner.call_structured")
def test_count_explanation_requeries_when_intermediate_counts_are_missing(call):
    shared = SharedModelContext(
        current_question="Explain why all records exceed people with positive hours by status.",
        updated_request="Explain the count difference by status.",
        count_reconciliation=True,
        database_context=_count_database_context(),
    )
    result = SqlExecutionResult(
        columns=(
            ResultColumn(name="status"),
            ResultColumn(name="all_record_count"),
            ResultColumn(name="qualifying_people_count"),
        ),
        rows=(
            {"status": "Draft", "all_record_count": 6, "qualifying_people_count": 2},
        ),
        coverage=ExecutionCoverage(fetched_rows=1, result_limit=100, response_bytes=80),
    )

    decision = answer_result(
        shared_context=shared,
        sql=(
            "SELECT status, COUNT(*) AS all_record_count, "
            "COUNT(DISTINCT employee_id) FILTER (WHERE total_worked_hrs > 0) "
            "AS qualifying_people_count FROM attendance_records GROUP BY status"
        ),
        result=result,
        employees=(),
        locale="en",
        model="openai/gpt-5-nano",
        budget=CallBudget(),
        timeout=30,
        max_output_tokens=3000,
    )

    assert isinstance(decision, ReplanRequest)
    assert "qualifying_record_count" in decision.reason
    call.assert_not_called()


@patch("week5.new_implementation.online.planner.call_structured")
def test_count_explanation_publishes_grounded_review_with_coverage_caveat(call):
    reviewed_text = (
        "| Status | People | All records |\n| --- | ---: | ---: |\n"
        "| Authorized | 5 | 78 |\n| Draft | 119 | 280 |\n\n"
        "These are counts in the observed rows, not proof that the full requested period is covered."
    )
    call.return_value = ReviewedAnswer(answer=reviewed_text)
    shared = SharedModelContext(
        current_question="Compare attending people and all records by status, then explain the difference in two tables.",
        updated_request="Compare the two measures by status and explain the gap.",
        count_reconciliation=True,
        database_context=_count_database_context(),
    )
    result = SqlExecutionResult(
        columns=tuple(
            ResultColumn(name=name)
            for name in (
                "status",
                "all_record_count",
                "all_people_count",
                "qualifying_record_count",
                "qualifying_people_count",
            )
        ),
        rows=(
            {
                "status": "Authorized",
                "all_record_count": 78,
                "all_people_count": 78,
                "qualifying_record_count": 5,
                "qualifying_people_count": 5,
            },
            {
                "status": "Draft",
                "all_record_count": 280,
                "all_people_count": 280,
                "qualifying_record_count": 119,
                "qualifying_people_count": 119,
            },
        ),
        coverage=ExecutionCoverage(fetched_rows=2, result_limit=100, response_bytes=160),
    )
    sql = (
        "SELECT status, COUNT(*) AS all_record_count, "
        "COUNT(DISTINCT employee_id) AS all_people_count, "
        "COUNT(*) FILTER (WHERE total_worked_hrs > 0) AS qualifying_record_count, "
        "COUNT(DISTINCT employee_id) FILTER (WHERE total_worked_hrs > 0) "
        "AS qualifying_people_count FROM attendance_records GROUP BY status"
    )

    answer = answer_result(
        shared_context=shared,
        sql=sql,
        result=result,
        employees=(),
        locale="en",
        model="openai/gpt-5-nano",
        budget=CallBudget(),
        timeout=30,
        max_output_tokens=3000,
    )

    assert isinstance(answer, str)
    assert answer == reviewed_text
    assert "| Status |" in call.call_args.kwargs["payload"]["proposed_answer"]
    assert [item.kwargs["stage"] for item in call.call_args_list] == [
        "sql_answer_review"
    ]


def test_count_reconciliation_accepts_equivalent_positive_hour_sql():
    shared = SharedModelContext(
        current_question="Explain the count difference.",
        updated_request="Explain the count difference.",
        count_reconciliation=True,
        database_context=_count_database_context(),
    )
    sql = (
        "SELECT COALESCE(status, '') AS status, "
        "COUNT(*) AS all_record_count, "
        "COUNT(DISTINCT employee_id) AS all_people_count, "
        "COUNT(*) FILTER (WHERE COALESCE(total_worked_hrs, 0) > 0) "
        "AS qualifying_record_count, "
        "COUNT(DISTINCT CASE WHEN COALESCE(total_worked_hrs, 0) > 0 "
        "THEN employee_id END) AS qualifying_people_count "
        "FROM attendance_records GROUP BY status"
    )
    assert _reconciliation_group(shared, sql) == "status"


def test_count_reconciliation_rewrites_ordered_two_population_plan():
    shared = SharedModelContext(
        current_question="Explain the count difference by status.",
        updated_request="Explain the count difference by status.",
        count_reconciliation=True,
        database_context=_count_database_context(),
    )
    proposed = (
        "WITH attendees AS (SELECT status AS approval_status, "
        "COUNT(DISTINCT employee_id) AS people FROM public.attendance_records "
        "WHERE attendance_date = DATE '2026-09-06' "
        "AND COALESCE(total_worked_hrs, 0) > 0 GROUP BY status), "
        "records AS (SELECT status AS status_group, COUNT(*) AS records "
        "FROM public.attendance_records WHERE attendance_date = DATE '2026-09-06' "
        "GROUP BY status) "
        "SELECT 'attendees' AS source_type, approval_status AS group_value, "
        "people AS value FROM attendees UNION ALL "
        "SELECT 'records' AS source_type, status_group AS group_value, "
        "records AS value FROM records ORDER BY source_type, group_value"
    )
    sql = build_count_reconciliation_sql(shared, proposed)
    assert isinstance(sql, str)
    assert "ORDER BY \"status\"" in sql
    assert _reconciliation_group(shared, sql) == "status"
    assert isinstance(_reconciliation_group(shared, sql + " LIMIT 1"), ReplanRequest)


def test_count_reconciliation_builds_measured_sql_from_matching_source_scopes():
    shared = SharedModelContext(
        current_question="Explain the count difference.",
        updated_request="Explain the count difference.",
        count_reconciliation=True,
        database_context=_count_database_context(),
    )
    proposed = (
        "WITH attendees AS (SELECT status, COUNT(DISTINCT employee_id) AS people "
        "FROM attendance_records WHERE attendance_date = DATE '2026-09-06' "
        "AND COALESCE(total_worked_hrs, 0) > 0 GROUP BY status), "
        "records AS (SELECT status, COUNT(*) AS records FROM attendance_records "
        "WHERE attendance_date = DATE '2026-09-06' GROUP BY status) "
        "SELECT status, people AS value FROM attendees UNION ALL "
        "SELECT status, records AS value FROM records"
    )
    sql = build_count_reconciliation_sql(shared, proposed)
    assert isinstance(sql, str)
    assert "all_record_count" in sql
    assert "qualifying_record_count" in sql
    assert "2026-09-06" in sql
    assert _reconciliation_group(shared, sql) == "status"


def test_count_reconciliation_rejects_mismatched_source_scopes():
    shared = SharedModelContext(
        current_question="Explain the count difference.",
        updated_request="Explain the count difference.",
        count_reconciliation=True,
        database_context=_count_database_context(),
    )
    proposed = (
        "WITH attendees AS (SELECT status, COUNT(DISTINCT employee_id) AS people "
        "FROM attendance_records WHERE attendance_date = DATE '2026-09-06' "
        "AND total_worked_hrs > 0 GROUP BY status), "
        "records AS (SELECT status, COUNT(*) AS records FROM attendance_records "
        "WHERE attendance_date = DATE '2026-09-06' AND status = 'Draft' GROUP BY status) "
        "SELECT status, people AS value FROM attendees UNION ALL "
        "SELECT status, records AS value FROM records"
    )
    assert build_count_reconciliation_sql(shared, proposed) is None


def test_count_reconciliation_preserves_matching_having_and_replans_windowed_branches():
    shared = SharedModelContext(
        current_question="Explain the count difference for groups with more than one row.",
        updated_request="Explain the count difference for groups with more than one row.",
        count_reconciliation=True, database_context=_count_database_context(),
    )
    branches = (
        "WITH attendees AS (SELECT status, COUNT(DISTINCT employee_id) AS people "
        "FROM attendance_records WHERE total_worked_hrs > 0 GROUP BY status "
        "HAVING COUNT(*) > 1), "
        "records AS (SELECT status, COUNT(*) AS records FROM attendance_records "
        "GROUP BY status HAVING COUNT(*) > 1) "
        "SELECT status, people AS value FROM attendees UNION ALL "
        "SELECT status, records AS value FROM records"
    )
    rewritten = build_count_reconciliation_sql(shared, branches)
    assert rewritten is not None and "HAVING COUNT(*) > 1" in rewritten
    assert build_count_reconciliation_sql(shared, branches + " ORDER BY status LIMIT 2") is None
    assert is_count_reconciliation_plan(shared, branches + " ORDER BY status LIMIT 2")


def test_count_presentation_labels_null_group_and_uses_actual_arabic_group():
    shared = SharedModelContext(
        current_question="اعرض الفرق حسب القسم في جدولين",
        updated_request="اعرض الفرق حسب القسم في جدولين",
        count_reconciliation=True, request_relationship="follow_up",
        database_context=_count_database_context(),
    )
    base = shared.database_context
    table = base.tables[0]
    shared = shared.model_copy(update={"database_context": base.model_copy(update={
        "tables": (table.model_copy(update={"columns": table.columns + (
            DatabaseColumn(name="department", data_type="text", nullable=True,
                           description="Department on the attendance row."),
        )}),),
    })})
    sql = (
        "SELECT department, COUNT(*) AS all_record_count, "
        "COUNT(DISTINCT employee_id) AS all_people_count, "
        "COUNT(*) FILTER (WHERE total_worked_hrs > 0) AS qualifying_record_count, "
        "COUNT(DISTINCT employee_id) FILTER (WHERE total_worked_hrs > 0) "
        "AS qualifying_people_count FROM attendance_records GROUP BY department"
    )
    result = SqlExecutionResult(
        columns=tuple(ResultColumn(name=name) for name in (
            "department", "all_record_count", "all_people_count",
            "qualifying_record_count", "qualifying_people_count")),
        rows=({"department": None, "all_record_count": 2, "all_people_count": 2,
               "qualifying_record_count": 1, "qualifying_people_count": 1},),
        coverage=ExecutionCoverage(fetched_rows=1, result_limit=100, response_bytes=80),
    )
    answer = _count_reconciliation_answer(
        shared_context=shared, sql=sql, result=result, locale="ar"
    )
    assert isinstance(answer, str)
    assert answer.count("| Department |") == 2
    assert "قيمة department فارغة" in answer
    assert "ضمن Department" in answer
    assert "Overall" not in answer


@patch("week5.new_implementation.online.planner.call_structured")
def test_reviewer_receives_the_planners_retry_evidence(call):
    call.side_effect = [
        PlannerAnswer(answer="Five people attended."),
        ReviewedAnswer(answer="Five people attended."),
    ]
    shared = SharedModelContext(
        current_question="How many people attended?",
        updated_request="Count attending people.",
        database_context=database_context(),
    )
    result = SqlExecutionResult(
        columns=(ResultColumn(name="people"),),
        rows=({"people": 5},),
        coverage=ExecutionCoverage(fetched_rows=1, result_limit=100, response_bytes=25),
    )
    feedback = {"error_type": "answer_review_requery", "database_error": "Missing scope"}
    answer_result(
        shared_context=shared,
        sql="SELECT COUNT(DISTINCT employee_id) AS people FROM attendance_records",
        result=result,
        employees=(),
        locale="en",
        model="openai/gpt-5-nano",
        budget=CallBudget(),
        timeout=30,
        max_output_tokens=3000,
        sql_execution_failure=feedback,
    )
    assert all(
        item.kwargs["payload"]["sql_execution_failure"] == feedback
        for item in call.call_args_list
    )


@patch("week5.new_implementation.online.planner.call_structured")
@patch("week5.new_implementation.online.planner.call_text")
def test_reviewer_receives_every_planner_input(call_text, call_structured):
    call_text.return_value = "SELECT COUNT(*) AS records FROM attendance_records"
    call_structured.side_effect = [
        PlannerAnswer(answer="Five records."),
        ReviewedAnswer(answer="Five records."),
    ]
    shared = SharedModelContext(
        current_question="How many records?",
        updated_request="Count records.",
        conversation_history=({"role": "user", "content": "Earlier question"},),
        trusted_context={"verified_turns": []},
        database_context=database_context(),
    )
    feedback = {"error_type": "retry", "database_error": "Use the same scope"}
    sql = request_sql(
        shared_context=shared, model="openai/gpt-5-nano", budget=CallBudget(),
        timeout=30, max_output_tokens=3000, sql_execution_failure=feedback,
    )
    result = SqlExecutionResult(
        columns=(ResultColumn(name="records"),), rows=({"records": 5},),
        coverage=ExecutionCoverage(fetched_rows=1, result_limit=100, response_bytes=25),
    )
    answer_result(
        shared_context=shared, sql=sql, result=result, employees=(), locale="en",
        model="openai/gpt-5-nano", budget=CallBudget(), timeout=30,
        max_output_tokens=3000, sql_execution_failure=feedback,
    )
    planner_payload = call_text.call_args.kwargs["payload"]
    reviewer_payload = call_structured.call_args_list[-1].kwargs["payload"]
    assert all(reviewer_payload[key] == value for key, value in planner_payload.items())
    assert reviewer_payload["executed_sql"] == sql
    assert reviewer_payload["database_result"] == result.model_dump(mode="json")


@patch("week5.new_implementation.online.planner.call_structured")
def test_count_correction_separates_repeated_records_from_gap_contribution(call):
    call.return_value = ReviewedAnswer(
        answer="Draft has 2 records without positive worked hours and 2 extra positive-hour records beyond one per person. Multiple attendance records do not explain the entire gap."
    )
    shared = SharedModelContext(
        current_question="Check whether anyone has multiple records within a status and revise the explanation.",
        updated_request="Check repeated records and revise the count explanation.",
        count_reconciliation=True,
        request_relationship="new",
        database_context=_count_database_context(),
    )
    result = SqlExecutionResult(
        columns=tuple(
            ResultColumn(name=name)
            for name in (
                "status",
                "all_record_count",
                "all_people_count",
                "qualifying_record_count",
                "qualifying_people_count",
            )
        ),
        rows=(
            {
                "status": "Draft",
                "all_record_count": 6,
                "all_people_count": 3,
                "qualifying_record_count": 4,
                "qualifying_people_count": 2,
            },
        ),
        coverage=ExecutionCoverage(fetched_rows=1, result_limit=100, response_bytes=80),
    )
    sql = (
        "SELECT status, COUNT(*) AS all_record_count, "
        "COUNT(DISTINCT employee_id) AS all_people_count, "
        "COUNT(*) FILTER (WHERE total_worked_hrs > 0) AS qualifying_record_count, "
        "COUNT(DISTINCT employee_id) FILTER (WHERE total_worked_hrs > 0) "
        "AS qualifying_people_count FROM attendance_records GROUP BY status"
    )

    answer = answer_result(
        shared_context=shared,
        sql=sql,
        result=result,
        employees=(),
        locale="en",
        model="openai/gpt-5-nano",
        budget=CallBudget(),
        timeout=30,
        max_output_tokens=3000,
    )

    assert isinstance(answer, str)
    assert "multiple attendance records" in answer.lower()
    assert "2 records without positive worked hours" in answer
    assert "2 extra positive-hour records" in answer
    assert [item.kwargs["stage"] for item in call.call_args_list] == [
        "sql_answer_review"
    ]
