"""Schema-grounded comparisons of a previously verified grouped aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .context import DatabaseContext
from .execution import SqlExecutionResult


@dataclass(frozen=True)
class GroupedMonthComparison:
    sql: str
    group_column: str
    metric_label: str
    previous_period: tuple[str, str]
    current_period: tuple[str, str]
    available_period: tuple[str | None, str | None]


_COMPARISON_WORDS = frozenset(
    {
        "a",
        "against",
        "and",
        "between",
        "calendar",
        "compare",
        "comparison",
        "contrast",
        "difference",
        "did",
        "does",
        "from",
        "how",
        "is",
        "it",
        "last",
        "month",
        "of",
        "previous",
        "result",
        "same",
        "that",
        "the",
        "them",
        "this",
        "those",
        "to",
        "versus",
        "vs",
        "was",
        "what",
        "with",
    }
)


def is_grouped_month_comparison_question(question: str) -> bool:
    words = re.findall(r"[a-z]+", question.casefold())
    values = set(words)
    return bool(
        words
        and values <= _COMPARISON_WORDS
        and values.intersection(
            {"compare", "comparison", "contrast", "difference", "versus", "vs"}
        )
        and values.intersection(
            {"that", "this", "those", "them", "it", "same", "result"}
        )
        and "month" in values
        and values.intersection({"last", "previous"})
    )


def build_grouped_month_comparison(
    *,
    question: str,
    previous_sql: str,
    previous_date_scope: tuple[str, str] | None,
    as_of_date: str,
    database_context: DatabaseContext,
) -> GroupedMonthComparison | None:
    """Build a period comparison only when the prior SQL has an unambiguous shape."""

    if previous_date_scope is not None or not is_grouped_month_comparison_question(
        question
    ):
        return None
    try:
        statements = parse(previous_sql, read="postgres")
        today = date.fromisoformat(as_of_date)
    except (ParseError, ValueError):
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
        or query.args.get("distinct")
    ):
        return None
    table = from_clause.this
    schema_name = table.db or "public"
    schema = next(
        (
            item
            for item in database_context.tables
            if item.schema_name == schema_name and item.table_name == table.name
        ),
        None,
    )
    if schema is None or schema.date_coverage is None:
        return None
    columns = {column.name for column in schema.columns}
    if "attendance_date" not in columns:
        return None
    if any(column.name not in columns for column in query.find_all(exp.Column)):
        return None
    where_clause = query.args.get("where")
    if where_clause is not None and any(
        column.name == "attendance_date" for column in where_clause.find_all(exp.Column)
    ):
        return None

    group_column = group_clause.expressions[0]
    matching_group = [
        item
        for item in query.expressions
        if (item.this if isinstance(item, exp.Alias) else item) == group_column
    ]
    aggregates = [
        item
        for item in query.expressions
        if isinstance(item, exp.Alias) and isinstance(item.this, (exp.Sum, exp.Count))
    ]
    if len(query.expressions) != 2 or len(matching_group) != 1 or len(aggregates) != 1:
        return None
    if any(isinstance(item, exp.Window) for item in query.find_all(exp.Window)):
        return None
    aggregate = aggregates[0].this
    group_name = matching_group[0].alias_or_name
    if not group_name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", group_name):
        return None

    first_current = today.replace(day=1)
    previous_end = first_current - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    group_sql = group_column.sql(dialect="postgres")
    aggregate_sql = aggregate.sql(dialect="postgres")
    source_sql = from_clause.sql(dialect="postgres")
    where_sql = f" {where_clause.sql(dialect='postgres')}" if where_clause else ""
    group_identifier = '"' + group_name + '"'
    previous_filter = (
        f"attendance_date >= DATE '{previous_start.isoformat()}' "
        f"AND attendance_date <= DATE '{previous_end.isoformat()}'"
    )
    current_filter = (
        f"attendance_date >= DATE '{first_current.isoformat()}' "
        f"AND attendance_date <= DATE '{today.isoformat()}'"
    )
    sql = (
        f"WITH eligible_groups AS ({query.sql(dialect='postgres')}), "
        "period_values AS ("
        f"SELECT {group_sql} AS group_key, "
        f"COALESCE({aggregate_sql} FILTER (WHERE {previous_filter}), 0) "
        "AS previous_period_value, "
        f"COALESCE({aggregate_sql} FILTER (WHERE {current_filter}), 0) "
        f"AS current_period_value {source_sql}{where_sql} "
        f"GROUP BY {group_sql}) "
        f"SELECT eligible_groups.{group_identifier} AS {group_identifier}, "
        "period_values.previous_period_value, period_values.current_period_value, "
        "period_values.current_period_value - period_values.previous_period_value "
        "AS difference, COUNT(*) OVER() AS matched_count "
        "FROM eligible_groups LEFT JOIN period_values "
        f"ON eligible_groups.{group_identifier} = period_values.group_key "
        f"ORDER BY eligible_groups.{group_identifier} LIMIT 100"
    )
    return GroupedMonthComparison(
        sql=sql,
        group_column=group_name,
        metric_label=aggregates[0].alias_or_name.replace("_", " "),
        previous_period=(previous_start.isoformat(), previous_end.isoformat()),
        current_period=(first_current.isoformat(), today.isoformat()),
        available_period=(
            schema.date_coverage.available_start,
            schema.date_coverage.available_end,
        ),
    )


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("comparison value is not numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("comparison value is not numeric") from exc
    if not number.is_finite():
        raise ValueError("comparison value is not finite")
    return number


def _display_number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def render_grouped_month_comparison(
    plan: GroupedMonthComparison,
    result: SqlExecutionResult,
    *,
    locale: str,
) -> str:
    """Verify typed results and render only values supplied by PostgreSQL."""

    if not result.coverage.complete or not result.rows:
        raise ValueError("grouped comparison result is incomplete")
    previous_start, previous_end = plan.previous_period
    current_start, current_end = plan.current_period
    previous_label = date.fromisoformat(previous_start).strftime("%B %Y")
    current_label = date.fromisoformat(current_start).strftime("%B %Y")
    available_start, available_end = plan.available_period
    known_coverage = available_start is not None and available_end is not None
    if locale == "ar":
        lines = [
            f"مقارنة {plan.metric_label} حسب {plan.group_column} للمجموعات المؤهلة في النتيجة السابقة.",
            f"الشهر السابق: {previous_label} ({previous_start} إلى {previous_end}). "
            f"الشهر الحالي: {current_label} ({current_start} إلى {current_end}).",
        ]
        if known_coverage:
            lines.append(
                f"نطاق تواريخ السجلات المتاحة إجمالاً: {available_start} إلى {available_end}. "
                "قد لا تتوفر سجلات لكل يوم ضمن الفترتين."
            )
        else:
            lines.append("تغطية التواريخ المتاحة غير معروفة.")
    else:
        lines = [
            f"Comparison of {plan.metric_label} by {plan.group_column} for groups eligible in the previous verified result.",
            f"Previous month: {previous_label} ({previous_start} to {previous_end}). "
            f"Current month: {current_label} ({current_start} to {current_end}).",
        ]
        if known_coverage:
            lines.append(
                f"Overall recorded date range: {available_start} to {available_end}. "
                "Records may be missing for individual days in either period."
            )
        else:
            lines.append("The available date range is unknown.")

    seen: set[str] = set()
    for row in result.rows:
        group = row.get(plan.group_column)
        if not isinstance(group, str) or not group.strip() or group in seen:
            raise ValueError("grouped comparison has an invalid or duplicate group")
        seen.add(group)
        previous = _number(row.get("previous_period_value"))
        current = _number(row.get("current_period_value"))
        difference = _number(row.get("difference"))
        if current - previous != difference:
            raise ValueError("grouped comparison difference is inconsistent")
        if _number(row.get("matched_count")) != len(result.rows):
            raise ValueError("grouped comparison result is truncated")
        group_label = escape(" ".join(group.split()))
        change = ("+" if difference > 0 else "-") + _display_number(abs(difference))
        if difference == 0:
            change = "0"
        if locale == "ar":
            lines.append(
                f"{group_label}: {previous_label} {_display_number(previous)}؛ "
                f"{current_label} {_display_number(current)}؛ التغير {change} "
                "(الحالي ناقص السابق)."
            )
        else:
            lines.append(
                f"{group_label}: {previous_label} {_display_number(previous)}; "
                f"{current_label} {_display_number(current)}; change {change} "
                "(current minus previous)."
            )
    return "\n".join(lines)


__all__ = [
    "GroupedMonthComparison",
    "build_grouped_month_comparison",
    "is_grouped_month_comparison_question",
    "render_grouped_month_comparison",
]
