"""The SQL planner answers and reviews its executed result."""

from unittest.mock import patch

import pytest

from week5.new_implementation.online.context import SharedModelContext
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
)
from week5.new_implementation.online.provider import CallBudget
from week5.new_implementation.online.provider import ProviderFailure
from week5.new_implementation.online.reference import Employee
from week5.new_implementation.tests.online.test_query import database_context


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
