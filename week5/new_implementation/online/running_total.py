"""Cumulative date series from a previously verified grouped aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .context import DatabaseContext
from .execution import SqlExecutionResult


@dataclass(frozen=True)
class RunningTotalPlan:
    sql: str
    date_column: str
    metric_label: str
    available_period: tuple[str | None, str | None]


_FOLLOWUP_WORDS = frozenset(
    {
        "a",
        "across",
        "by",
        "cumulative",
        "date",
        "dates",
        "each",
        "of",
        "over",
        "show",
        "the",
        "total",
        "running",
    }
)


def is_running_total_question(question: str) -> bool:
    words = re.findall(r"[a-z]+", question.casefold())
    values = set(words)
    return bool(
        words
        and values <= _FOLLOWUP_WORDS
        and values.intersection({"running", "cumulative"})
        and "total" in values
        and values.intersection({"date", "dates"})
    )


def build_running_total(
    *,
    question: str,
    previous_sql: str,
    previous_date_scope: tuple[str, str] | None,
    database_context: DatabaseContext,
) -> RunningTotalPlan | None:
    """Use the verified metric only when its SQL shape is unambiguous."""
    if previous_date_scope is not None or not is_running_total_question(question):
        return None
    try:
        statements = parse(previous_sql, read="postgres")
    except ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    query = statements[0]
    from_clause = query.args.get("from_")
    group_clause = query.args.get("group")
    if (
        from_clause is None
        or not isinstance(from_clause.this, exp.Table)
        or group_clause is None
        or len(group_clause.expressions) != 1
        or not isinstance(group_clause.expressions[0], exp.Column)
        or query.args.get("joins")
        or query.args.get("with_")
        or query.args.get("having")
        or query.args.get("distinct")
        or len(query.expressions) != 2
    ):
        return None
    table = from_clause.this
    schema = next(
        (
            item
            for item in database_context.tables
            if item.schema_name == (table.db or "public")
            and item.table_name == table.name
        ),
        None,
    )
    if schema is None or schema.date_coverage is None:
        return None
    date_column = schema.date_coverage.field
    columns = {column.name for column in schema.columns}
    if date_column not in columns:
        return None
    group_column = group_clause.expressions[0]
    aggregates = [
        item
        for item in query.expressions
        if isinstance(item, exp.Alias) and isinstance(item.this, (exp.Sum, exp.Count))
    ]
    if (
        len(aggregates) != 1
        or not any(
            (item.this if isinstance(item, exp.Alias) else item) == group_column
            for item in query.expressions
        )
        or any(isinstance(item, exp.Window) for item in query.find_all(exp.Window))
    ):
        return None
    where_clause = query.args.get("where")
    checked_parts = (group_column, aggregates[0].this, where_clause)
    if any(
        column.name not in columns
        for part in checked_parts
        if part is not None
        for column in part.find_all(exp.Column)
    ):
        return None
    if where_clause is not None and any(
        column.name == date_column for column in where_clause.find_all(exp.Column)
    ):
        return None
    date_sql = exp.column(date_column).sql(dialect="postgres")
    aggregate_sql = aggregates[0].this.sql(dialect="postgres")
    source_sql = from_clause.sql(dialect="postgres")
    where_sql = f" {where_clause.sql(dialect='postgres')}" if where_clause else ""
    sql = (
        "WITH daily_values AS ("
        f"SELECT {date_sql}, {aggregate_sql} AS daily_value "
        f"{source_sql}{where_sql} GROUP BY {date_sql}) "
        f"SELECT {date_sql}, daily_value, "
        f"SUM(daily_value) OVER (ORDER BY {date_sql} "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total, "
        "COUNT(*) OVER () AS matched_count "
        f"FROM daily_values ORDER BY {date_sql}"
    )
    return RunningTotalPlan(
        sql=sql,
        date_column=date_column,
        metric_label=aggregates[0].alias_or_name.replace("_", " "),
        available_period=(
            schema.date_coverage.available_start,
            schema.date_coverage.available_end,
        ),
    )


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("running total value is not numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("running total value is not numeric") from exc
    if not number.is_finite():
        raise ValueError("running total value is not finite")
    return number


def render_running_total(
    plan: RunningTotalPlan, result: SqlExecutionResult, *, locale: str
) -> str:
    """Render only complete date series with verified cumulative arithmetic."""
    if not result.coverage.complete or not result.rows:
        raise ValueError("running total result is incomplete")
    start, end = plan.available_period
    if locale == "ar":
        lines = [f"المجموع التراكمي لـ {plan.metric_label} حسب التاريخ."]
        lines.append(
            f"نطاق تواريخ السجلات المتاحة: {start} إلى {end}."
            if start and end
            else "نطاق تواريخ السجلات المتاحة غير معروف."
        )
    else:
        lines = [f"Running total of {plan.metric_label} over dates."]
        lines.append(
            f"Overall recorded date range: {start} to {end}."
            if start and end
            else "The available date range is unknown."
        )
    cumulative = Decimal(0)
    previous_date: date | None = None
    for row in result.rows:
        raw_date = row.get(plan.date_column)
        if not isinstance(raw_date, str):
            raise ValueError("running total date is invalid")
        current_date = date.fromisoformat(raw_date)
        if previous_date is not None and current_date <= previous_date:
            raise ValueError("running total dates are not strictly ordered")
        previous_date = current_date
        cumulative += _number(row.get("daily_value"))
        if cumulative != _number(row.get("running_total")):
            raise ValueError("running total is inconsistent")
        if _number(row.get("matched_count")) != len(result.rows):
            raise ValueError("running total result is truncated")
        value = format(cumulative.normalize(), "f")
        lines.append(f"{raw_date}: {value}" + ("." if locale == "en" else ""))
    return "\n".join(lines)
