from __future__ import annotations

import pytest

from week5.new_implementation.online.execution import (
    ExecutionCoverage,
    SqlExecutionResult,
)
from week5.new_implementation.online.running_total import (
    build_running_total,
    is_running_total_question,
    render_running_total,
)
from week5.new_implementation.tests.online.test_comparison import attendance_schema


PRIOR_SQL = (
    "SELECT department, SUM(CASE WHEN total_worked_hrs > 0 "
    "THEN total_worked_hrs ELSE 0 END) AS worked_hours "
    "FROM attendance_records GROUP BY department ORDER BY worked_hours DESC"
)


def test_builds_cumulative_plan_from_verified_aggregate():
    assert is_running_total_question("Show a running total over dates.")
    plan = build_running_total(
        question="Show a running total over dates.",
        previous_sql=PRIOR_SQL,
        previous_date_scope=None,
        database_context=attendance_schema(),
    )

    assert plan is not None
    assert "SUM(daily_value) OVER (ORDER BY attendance_date" in plan.sql
    assert "SUM(CASE WHEN total_worked_hrs > 0" in plan.sql
    assert "GROUP BY attendance_date" in plan.sql


def test_running_total_renderer_checks_every_cumulative_value():
    plan = build_running_total(
        question="Show a running total over dates.",
        previous_sql=PRIOR_SQL,
        previous_date_scope=None,
        database_context=attendance_schema(),
    )
    assert plan is not None
    rows = (
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
    )
    result = SqlExecutionResult(
        columns=(),
        rows=rows,
        coverage=ExecutionCoverage(
            fetched_rows=2, result_limit=100, response_bytes=100
        ),
    )

    answer = render_running_total(plan, result, locale="en")

    assert "2026-08-04: 36" in answer
    assert "2026-08-03 to 2026-09-06" in answer
    bad_rows = (rows[0], {**rows[1], "running_total": 22})
    with pytest.raises(ValueError, match="inconsistent"):
        render_running_total(
            result=result.model_copy(update={"rows": bad_rows}), plan=plan, locale="en"
        )
