from __future__ import annotations

import unittest

from week5.new_implementation.online.comparison import (
    build_grouped_month_comparison,
    render_grouped_month_comparison,
)
from week5.new_implementation.online.context import (
    DatabaseColumn,
    DatabaseContext,
    DatabaseDateCoverage,
    DatabaseTable,
)
from week5.new_implementation.online.execution import (
    ExecutionCoverage,
    SqlExecutionResult,
)


def attendance_schema():
    return DatabaseContext(
        server_version="17",
        tables=(
            DatabaseTable(
                schema_name="public",
                table_name="attendance_records",
                object_type="BASE TABLE",
                description="Synthetic attendance records.",
                date_coverage=DatabaseDateCoverage(
                    field="attendance_date",
                    available_start="2026-08-03",
                    available_end="2026-09-06",
                ),
                columns=tuple(
                    DatabaseColumn(
                        name=name,
                        data_type=kind,
                        nullable=False,
                        description=name,
                    )
                    for name, kind in (
                        ("department", "text"),
                        ("employee_id", "text"),
                        ("attendance_date", "date"),
                        ("total_worked_hrs", "numeric"),
                    )
                ),
            ),
        ),
    )


class GroupedComparisonTests(unittest.TestCase):
    def test_accepts_zero_coalesced_grouped_measure(self):
        plan = build_grouped_month_comparison(
            question="Compare that with last month.",
            previous_sql=(
                "SELECT department, COALESCE(SUM(total_worked_hrs), 0) "
                "AS total_worked_hours FROM attendance_records "
                "GROUP BY department "
                "HAVING COALESCE(SUM(total_worked_hrs), 0) > 10"
            ),
            previous_date_scope=None,
            as_of_date="2026-09-27",
            database_context=attendance_schema(),
        )
        self.assertIsNotNone(plan)
        self.assertIn("eligible_groups", plan.sql)
        self.assertIn("previous_period_record_count", plan.sql)
        self.assertIn("current_period_record_count", plan.sql)
        self.assertIn("CASE WHEN COUNT(*) FILTER", plan.sql)
        self.assertNotIn("COALESCE(SUM(total_worked_hrs) FILTER", plan.sql)

    def test_accepts_verified_aggregate_alias_in_order_by(self):
        plan = build_grouped_month_comparison(
            question="Compare that with last month.",
            previous_sql=(
                "SELECT department, SUM(CASE WHEN COALESCE(total_worked_hrs, 0) > 0 "
                "THEN total_worked_hrs ELSE 0 END) AS total_hours, "
                "COUNT(*) OVER() AS matched_count FROM attendance_records "
                "GROUP BY department HAVING SUM(CASE WHEN "
                "COALESCE(total_worked_hrs, 0) > 0 THEN total_worked_hrs "
                "ELSE 0 END) > 10 ORDER BY total_hours DESC"
            ),
            previous_date_scope=None,
            as_of_date="2026-09-27",
            database_context=attendance_schema(),
        )
        self.assertIsNotNone(plan)
        self.assertIn("eligible_groups", plan.sql)
        self.assertIn("2026-08-31", plan.sql)

    def test_accepts_grouped_result_with_standard_match_count(self):
        plan = build_grouped_month_comparison(
            question="Compare that with last month.",
            previous_sql=(
                "SELECT department, SUM(CASE WHEN total_worked_hrs > 0 THEN "
                "total_worked_hrs ELSE 0 END) AS total_hours, "
                "COUNT(*) OVER() AS matched_count FROM attendance_records "
                "GROUP BY department HAVING SUM(CASE WHEN total_worked_hrs > 0 "
                "THEN total_worked_hrs ELSE 0 END) > 10"
            ),
            previous_date_scope=None,
            as_of_date="2026-09-25",
            database_context=attendance_schema(),
        )
        self.assertIsNotNone(plan)
        self.assertIn("eligible_groups", plan.sql)
        self.assertIn("2026-08-31", plan.sql)
        self.assertIn("2026-09-01", plan.sql)

    def test_renders_verified_period_values_and_partial_coverage(self):
        plan = build_grouped_month_comparison(
            question="Compare that with last month.",
            previous_sql=(
                "SELECT department, SUM(total_worked_hrs) AS total_hours "
                "FROM attendance_records GROUP BY department "
                "HAVING SUM(total_worked_hrs) > 10"
            ),
            previous_date_scope=None,
            as_of_date="2026-09-25",
            database_context=attendance_schema(),
        )
        result = SqlExecutionResult(
            rows=(
                {
                    "department": "Engineering",
                    "previous_period_value": 16,
                    "current_period_value": 40,
                    "difference": 24,
                    "matched_count": 2,
                },
                {
                    "department": "Finance",
                    "previous_period_value": 12,
                    "current_period_value": 24,
                    "difference": 12,
                    "matched_count": 2,
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=2, result_limit=100, response_bytes=100
            ),
        )

        answer = render_grouped_month_comparison(plan, result, locale="en")
        self.assertIn("August 2026", answer)
        self.assertIn("September 2026", answer)
        self.assertIn("2026-08-31", answer)
        self.assertIn("2026-08-03 to 2026-09-06", answer)
        self.assertIn(
            "Engineering: August 2026 16; September 2026 40; change +24", answer
        )
        self.assertIn("Finance: August 2026 12; September 2026 24; change +12", answer)
        self.assertNotIn("fully covered", answer)

    def test_rejects_result_with_wrong_difference(self):
        plan = build_grouped_month_comparison(
            question="Compare that with last month.",
            previous_sql=(
                "SELECT department, SUM(total_worked_hrs) AS total_hours "
                "FROM attendance_records GROUP BY department"
            ),
            previous_date_scope=None,
            as_of_date="2026-09-25",
            database_context=attendance_schema(),
        )
        result = SqlExecutionResult(
            rows=(
                {
                    "department": "Engineering",
                    "previous_period_value": 16,
                    "current_period_value": 40,
                    "difference": -24,
                    "matched_count": 1,
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=50
            ),
        )
        with self.assertRaisesRegex(ValueError, "difference"):
            render_grouped_month_comparison(plan, result, locale="en")

    def test_preserves_trailing_zero_in_whole_number_difference(self):
        plan = build_grouped_month_comparison(
            question="Compare that with last month.",
            previous_sql=(
                "SELECT department, SUM(total_worked_hrs) AS total_hours "
                "FROM attendance_records GROUP BY department"
            ),
            previous_date_scope=None,
            as_of_date="2026-09-25",
            database_context=attendance_schema(),
        )
        result = SqlExecutionResult(
            rows=(
                {
                    "department": "Engineering",
                    "previous_period_value": 16,
                    "current_period_value": 56,
                    "difference": 40,
                    "matched_count": 1,
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=50
            ),
        )
        self.assertIn(
            "change +40",
            render_grouped_month_comparison(plan, result, locale="en"),
        )

    def test_builds_two_periods_from_verified_grouped_aggregate(self):
        plan = build_grouped_month_comparison(
            question="Compare that with last month.",
            previous_sql=(
                "SELECT department, SUM(total_worked_hrs) AS total_hours "
                "FROM attendance_records GROUP BY department "
                "HAVING SUM(total_worked_hrs) > 10"
            ),
            previous_date_scope=None,
            as_of_date="2026-09-25",
            database_context=attendance_schema(),
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.group_column, "department")
        self.assertEqual(plan.previous_period, ("2026-08-01", "2026-08-31"))
        self.assertEqual(plan.current_period, ("2026-09-01", "2026-09-25"))
        self.assertIn("HAVING SUM(total_worked_hrs) > 10", plan.sql)
        self.assertIn("2026-08-01", plan.sql)
        self.assertIn("2026-08-31", plan.sql)
        self.assertIn("2026-09-01", plan.sql)
        self.assertIn("2026-09-25", plan.sql)
        self.assertNotIn("employee_id", plan.sql)

    def test_preserves_verified_employee_filter_for_grouped_comparison(self):
        plan = build_grouped_month_comparison(
            question="Compare this with the previous month.",
            previous_sql=(
                "SELECT department, SUM(total_worked_hrs) AS total_hours "
                "FROM public.attendance_records WHERE employee_id = 'A1' "
                "GROUP BY department HAVING SUM(total_worked_hrs) >= 5"
            ),
            previous_date_scope=None,
            as_of_date="2026-09-25",
            database_context=attendance_schema(),
        )

        self.assertIsNotNone(plan)
        self.assertGreaterEqual(plan.sql.count("employee_id = 'A1'"), 2)

    def test_declines_comparison_when_prior_request_has_a_date_scope(self):
        plan = build_grouped_month_comparison(
            question="Compare that with last month.",
            previous_sql=(
                "SELECT department, SUM(total_worked_hrs) AS total_hours "
                "FROM attendance_records WHERE attendance_date >= '2026-09-01' "
                "GROUP BY department HAVING SUM(total_worked_hrs) > 10"
            ),
            previous_date_scope=("2026-09-01", "2026-09-25"),
            as_of_date="2026-09-25",
            database_context=attendance_schema(),
        )
        self.assertIsNone(plan)

    def test_declines_new_metric_or_unknown_schema_identifier(self):
        self.assertIsNone(
            build_grouped_month_comparison(
                question="Compare that with last month's overtime.",
                previous_sql=(
                    "SELECT department, SUM(total_worked_hrs) AS total_hours "
                    "FROM attendance_records GROUP BY department"
                ),
                previous_date_scope=None,
                as_of_date="2026-09-25",
                database_context=attendance_schema(),
            )
        )
        self.assertIsNone(
            build_grouped_month_comparison(
                question="Compare that with last month.",
                previous_sql=(
                    "SELECT department, SUM(private_salary) AS total "
                    "FROM attendance_records GROUP BY department"
                ),
                previous_date_scope=None,
                as_of_date="2026-09-25",
                database_context=attendance_schema(),
            )
        )
