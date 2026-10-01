"""Direct-SQL attendance turn pipeline with atomic state publication."""

from __future__ import annotations

import calendar
from contextlib import nullcontext
from datetime import date, timedelta
import json
import math
import re
from typing import Annotated, Callable, Literal
from uuid import uuid4

import psycopg
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from ..config import settings
from .evidence import Result
from .comparison import (
    build_grouped_month_comparison,
    is_grouped_month_comparison_question,
)
from .context import (
    DatabaseContext,
    SharedModelContext,
    load_database_context,
)
from .execution import (
    AccessContext,
    AuthorizationError,
    SqlExecutionResult,
    authorize_access,
    execute_sql,
    open_read_snapshot,
    load_employee_directory,
    search_employee_directory_postgres,
    validate_read_query,
    _projects_private_source,
    _relation_names_for_select,
)
from .history import model_history
from .limits import MAX_EMPLOYEE_CANDIDATES
from .planner import (
    ExecutedPlanStep,
    MultiSqlPlan,
    SqlPlanStep,
    ReplanRequest,
    answer_multi_result,
    answer_result,
    build_count_reconciliation_sql,
    is_count_reconciliation_plan,
    request_sql,
    retry_plan_step,
)
from .query_paths import boolean_paths, contributing_where_paths
from .provider import (
    CallBudget,
    ProviderFailure,
    StageEvent,
    TurnObserver,
    log_layer_failure,
    log_layer_output,
)
from .reference import (
    AmbiguousReference,
    BoundReferences,
    Employee,
    EmployeeOption,
    PendingEmployeeConfirmation,
    ReadyReference,
    ReferenceResponse,
    UnsupportedReference,
    attach_resolved_employees,
    bind_references,
    complete_confirmation,
    request_references,
    search_employee_candidates,
)
from .running_total import (
    build_running_total,
    is_running_total_question,
)
from .state import ConversationState, VerifiedTurn


SQL_EXECUTION_ATTEMPT_LIMIT = 4
_MONTH_NUMBERS = {
    month.casefold(): number
    for number, month in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}
_NUMBER_WORDS = {
    "zero": 0.0,
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
}


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, arbitrary_types_allowed=True
    )


class TurnRequest(_Strict):
    question: str = Field(min_length=1, max_length=50000)
    history: tuple[dict[str, str], ...] = ()
    state: ConversationState = Field(default_factory=ConversationState)
    access_context: AccessContext | None = None
    max_reference_calls: Literal[1, 2] = 2


class Answered(_Strict):
    kind: Literal["answered"] = "answered"
    reply: str
    evidence: tuple[Result, ...] = ()
    state: ConversationState


class Clarification(_Strict):
    kind: Literal["clarification"] = "clarification"
    reply: str
    evidence: tuple[Result, ...] = ()
    state: ConversationState
    reason: str


class Unsupported(_Strict):
    kind: Literal["unsupported"] = "unsupported"
    reply: str
    evidence: tuple[Result, ...] = ()
    state: ConversationState
    capability: str


class Failed(_Strict):
    kind: Literal["failed"] = "failed"
    reply: str
    evidence: tuple[Result, ...] = ()
    state: ConversationState
    code: str


TurnOutcome = Annotated[
    Answered | Clarification | Unsupported | Failed, Field(discriminator="kind")
]


class RuntimeDependencies:
    """Injectable boundaries for deterministic tests and local acceptance."""

    def __init__(
        self,
        *,
        reference_writer: Callable[..., ReferenceResponse] = request_references,
        directory_loader: Callable[..., tuple[Employee, ...]] = load_employee_directory,
        employee_fallback_search: Callable[
            ..., tuple[EmployeeOption, ...]
        ] = search_employee_candidates,
        employee_fuzzy_search: Callable[
            ..., tuple[EmployeeOption, ...]
        ] = search_employee_directory_postgres,
        context_loader: Callable[..., DatabaseContext] = load_database_context,
        planner: Callable[..., str | MultiSqlPlan] = request_sql,
        executor: Callable[..., SqlExecutionResult] = execute_sql,
        answerer: Callable[..., str | ReplanRequest] = answer_result,
        snapshot_factory: Callable[..., object] | None = None,
    ):
        self.reference_writer = reference_writer
        self.directory_loader = directory_loader
        self.employee_fallback_search = employee_fallback_search
        self.employee_fuzzy_search = employee_fuzzy_search
        self.context_loader = context_loader
        self.planner = planner
        self.executor = executor
        self.answerer = answerer
        self.snapshot_factory = snapshot_factory


DEPENDENCIES = RuntimeDependencies(snapshot_factory=open_read_snapshot)


def _locale(question: str) -> Literal["en", "ar"]:
    if re.search(r"بال(?:لغة\s*)?[اإ]نجليز(?:ية|ي)", question):
        return "en"
    if re.search(
        r"\b(?:answer|reply|respond|write)\s+(?:to\s+me\s+)?(?:in\s+)?arabic\b",
        question,
        flags=re.IGNORECASE,
    ):
        return "ar"
    return "ar" if any("\u0600" <= char <= "\u06ff" for char in question) else "en"


def _unwrap_parentheses(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _has_required_date_scope(
    sql: str,
    required_date_scope: tuple[str, str] | None,
    *,
    independent_clauses: bool = False,
) -> bool:
    if required_date_scope is None:
        return True
    paths = contributing_where_paths(sql)
    matches = (_date_scope_for_path(path) == required_date_scope for path in paths)
    return (any(matches) if independent_clauses else all(matches)) if paths else False


def _has_multiple_source_scopes(sql: str) -> bool:
    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return False
    attendance_table = settings.postgres_attendance_table.rsplit(".", 1)[-1].casefold()
    return any(
        sum(
            table.name.casefold() == attendance_table
            for table in statement.find_all(exp.Table)
        )
        > 1
        or sum(1 for _ in statement.find_all(exp.Filter)) > 1
        for statement in statements
        if statement is not None
    )


def _carried_verified_employees(
    sql: str,
    previous_active: tuple[Employee, ...],
    *,
    request_relationship: str,
) -> tuple[Employee, ...]:
    """Keep a trusted antecedent only if the current SQL uses its exact ID."""

    if request_relationship != "follow_up" or not previous_active:
        return ()
    paths = contributing_where_paths(sql, include_scalar_subqueries=False)
    if not paths:
        return ()
    used_ids: set[str] = set()
    for path in paths:
        for predicate in path:
            if not isinstance(predicate, exp.EQ):
                continue
            left, right = predicate.this, predicate.expression
            for column, literal in ((left, right), (right, left)):
                if (
                    isinstance(column, exp.Column)
                    and column.name.casefold() == "employee_id"
                    and isinstance(literal, exp.Literal)
                    and literal.is_string
                ):
                    used_ids.add(str(literal.this).casefold())
    return tuple(
        employee
        for employee in previous_active
        if employee.employee_id.casefold() in used_ids
    )


def _has_self_membership_filter(sql: str) -> bool:
    """Find an unrequested column-in-itself filter with no narrowing subquery."""

    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return False
    for statement in statements:
        for node in statement.find_all(exp.In):
            if not isinstance(node.this, exp.Column):
                continue
            query = node.args.get("query")
            select = query.this if isinstance(query, exp.Subquery) else query
            if not isinstance(select, exp.Select) or select.args.get("where"):
                continue
            if len(select.expressions) != 1 or not isinstance(
                select.expressions[0], exp.Column
            ):
                continue
            if select.expressions[0].name.casefold() != node.this.name.casefold():
                continue
            outer = node.find_ancestor(exp.Select)
            if outer is None:
                continue
            outer_from = outer.args.get("from_")
            inner_from = select.args.get("from_")
            if (
                isinstance(outer_from, exp.From)
                and isinstance(inner_from, exp.From)
                and isinstance(outer_from.this, exp.Table)
                and isinstance(inner_from.this, exp.Table)
                and outer_from.this.name.casefold() == inner_from.this.name.casefold()
            ):
                return True
    return False


def _sql_semantic_issue(
    _question: str,
    sql: str,
    *,
    required_date_scope: tuple[str, str] | None = None,
    independent_clauses: bool = False,
) -> str | None:
    if _planner_control_alias(sql) is not None:
        return None
    if _has_self_membership_filter(sql):
        return "self_membership_filter"
    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        statements = ()
    for statement in statements:
        if statement is None:
            continue
        for alias in statement.find_all(exp.Alias):
            if alias.alias.casefold() != "running_total":
                continue
            if not (
                isinstance(alias.this, exp.Window)
                and isinstance(alias.this.this, exp.Sum)
                and alias.this.args.get("order") is not None
            ):
                return "invalid_running_total_expression"
        # Only the published result needs a matched_count. An inner grouped CTE
        # may use LIMIT to select an eligible cohort before the outer query
        # computes a scalar or a different result shape.
        for select_node in (statement,) if isinstance(statement, exp.Select) else ():
            if not (select_node.args.get("group") and select_node.args.get("limit")):
                continue
            matched = [
                item
                for item in select_node.expressions
                if item.alias_or_name.casefold() == "matched_count"
            ]
            if matched and not all(
                isinstance(item, exp.Alias)
                and isinstance(item.this, exp.Window)
                and isinstance(item.this.this, exp.Count)
                for item in matched
            ):
                return "invalid_grouped_matched_count"
    separate_sources = independent_clauses and _has_multiple_source_scopes(sql)
    if not _has_required_date_scope(
        sql, required_date_scope, independent_clauses=separate_sources
    ):
        return "date_scope_mismatch"
    return None


def _strip_private_projections(
    sql: str, *, public_columns: tuple[str, ...] = ()
) -> str | None:
    """Keep public attendance evidence while withholding full ingestion payloads."""

    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return sql
    if len(statements) != 1 or statements[0] is None:
        return sql
    statement = statements[0]
    changed = False
    private_names = {"record_json", "raw_row_key"}
    for select in reversed(list(statement.find_all(exp.Select))):
        relation_names = _relation_names_for_select(
            select,
            frozenset(
                {settings.postgres_attendance_table.rsplit(".", 1)[-1].casefold()}
            ),
        )
        kept: list[exp.Expression] = []
        for item in select.expressions:
            projection = item.this if isinstance(item, exp.Alias) else item
            if isinstance(projection, exp.Star) or (
                isinstance(projection, exp.Column)
                and isinstance(projection.this, exp.Star)
            ):
                source = select.args.get("from_")
                table = source.this if isinstance(source, exp.From) else None
                if (
                    isinstance(table, exp.Table)
                    and table.name.casefold()
                    != settings.postgres_attendance_table.rsplit(".", 1)[-1].casefold()
                ):
                    kept.append(item)
                    continue
                if (
                    not public_columns
                    or not isinstance(table, exp.Table)
                    or select.args.get("joins")
                ):
                    return None
                kept.extend(
                    exp.column(
                        name,
                        table=projection.table
                        if isinstance(projection, exp.Column)
                        else None,
                    )
                    for name in public_columns
                    if name.casefold() not in private_names
                )
                changed = True
                continue
            if _projects_private_source(item, relation_names=relation_names):
                changed = True
                continue
            kept.append(item)
        if len(kept) != len(select.expressions):
            if not any(
                not (
                    isinstance(
                        item.this if isinstance(item, exp.Alias) else item, exp.Window
                    )
                    and isinstance(
                        (item.this if isinstance(item, exp.Alias) else item).this,
                        exp.Count,
                    )
                )
                for item in kept
            ):
                return None
            select.set("expressions", kept)
    return statement.sql(dialect="postgres") if changed else sql


def _planner_control_alias(sql: str) -> str | None:
    """Recognize the planner's safe, single-value control protocol."""

    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    statement = statements[0]
    if any(statement.find_all(exp.Table)) or len(statement.expressions) != 1:
        return None
    expression = statement.expressions[0]
    if not isinstance(expression, exp.Alias):
        return None
    alias = expression.alias.casefold()
    if alias not in {"clarification_required", "unsupported_capability"}:
        return None
    value = expression.this
    while isinstance(value, (exp.Cast, exp.Paren)):
        value = value.this
    if not isinstance(value, exp.Literal) or not value.is_string:
        return None
    return alias


def _mentions_time_period(question: str) -> bool:
    folded = question.casefold()
    if re.search(
        r"\b(?:[1-9]|[12]\d|3[01])(?:st|nd|rd|th)\b"
        r"(?!\s+(?:shift|employee|person|rank|place|item|record)\b)",
        folded,
    ):
        return True
    if re.search(r"\b(?:19|20)\d{2}\b", folded):
        return True
    month_names = (
        *_MONTH_NUMBERS,
        *(calendar.month_abbr[number].casefold() for number in range(1, 13)),
    )
    if any(re.search(rf"\b{month}\b", folded) for month in month_names):
        return True
    if re.search(
        r"\b(?:today|yesterday|tomorrow)\b|"
        r"\b(?:this|last|previous|next)\s+"
        r"(?:day|week|month|year|quarter)\b",
        folded,
    ):
        return True
    return any(
        word in question
        for word in ("اليوم", "أمس", "امس", "أسبوع", "اسبوع", "شهر", "سنة")
    )


def _date_literal(node: exp.Expression) -> date | None:
    while isinstance(node, (exp.Paren, exp.Cast, exp.TryCast)):
        node = node.this
    if not isinstance(node, exp.Literal) or not node.is_string:
        return None
    try:
        return date.fromisoformat(str(node.this))
    except ValueError:
        return None


def _is_attendance_date(node: exp.Expression) -> bool:
    while isinstance(node, (exp.Paren, exp.Cast, exp.TryCast)):
        node = node.this
    return isinstance(node, exp.Column) and node.name.casefold() == "attendance_date"


def _date_scope_for_path(
    path: tuple[exp.Expression, ...],
) -> tuple[str, str] | None:
    lower: date | None = None
    upper: date | None = None
    for predicate in path:
        predicate = _unwrap_parentheses(predicate)
        if isinstance(predicate, exp.Between) and _is_attendance_date(predicate.this):
            start = _date_literal(predicate.args["low"])
            end = _date_literal(predicate.args["high"])
        elif isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
            column, literal = predicate.this, predicate.expression
            reversed_sides = not _is_attendance_date(column)
            if reversed_sides:
                column, literal = literal, column
            if not _is_attendance_date(column):
                continue
            value = _date_literal(literal)
            if value is None:
                continue
            start = end = None
            if isinstance(predicate, exp.EQ):
                start = end = value
            elif isinstance(predicate, (exp.GT, exp.GTE)):
                if reversed_sides:
                    end = value - timedelta(days=isinstance(predicate, exp.GT))
                else:
                    start = value + timedelta(days=isinstance(predicate, exp.GT))
            elif reversed_sides:
                start = value + timedelta(days=isinstance(predicate, exp.LT))
            else:
                end = value - timedelta(days=isinstance(predicate, exp.LT))
        else:
            continue
        if start is not None:
            lower = max(lower, start) if lower is not None else start
        if end is not None:
            upper = min(upper, end) if upper is not None else end
    if lower is None and upper is None:
        return None
    lower = lower or date.min
    upper = upper or date.max
    if lower > upper:
        return None
    return lower.isoformat(), upper.isoformat()


def _sql_date_scope(sql: str) -> tuple[str, str] | None:
    if sql.lstrip().upper().startswith("WHERE "):
        try:
            statement = parse("SELECT 1 " + sql, read="postgres")[0]
            where = statement.find(exp.Where)
            paths = boolean_paths(where.this) if where is not None else None
        except ParseError:
            return None
    else:
        paths = contributing_where_paths(sql)
    if not paths:
        return None
    scopes = {_date_scope_for_path(path) for path in paths}
    return next(iter(scopes)) if len(scopes) == 1 else None


def _previous_having(sql: str) -> str:
    match = re.search(
        r"\bHAVING\b\s+(.+?)(?=\bORDER\s+BY\b|\bLIMIT\b|;|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return "HAVING " + match.group(1).strip() if match else "none"


def _clarification(reason: str, locale: str) -> str:
    if locale == "ar":
        return {
            "missing_employee": "يرجى تحديد الموظف أو النطاق المطلوب.",
            "unknown_employee_id": "لم أجد الرقم الوظيفي المحدد. يرجى التحقق من الرقم.",
        }.get(reason, "يرجى توضيح الموظف أو طلب الحضور.")
    return {
        "missing_employee": "Please identify the employee or requested scope.",
        "unknown_employee_id": "I could not find that employee ID. Please check the code.",
    }.get(reason, "Please clarify the employee or attendance request.")


def _failed(locale: str) -> str:
    if locale == "ar":
        return "تعذر إكمال هذا الطلب بأمان. يرجى المحاولة مرة أخرى."
    return "I couldn't complete that request safely. Please try again."


def _unsupported_identifier(locale: str) -> str:
    if locale == "ar":
        return "تنسيق الرقم الوظيفي غير مدعوم. يرجى إدخال رقم وظيفي صالح."
    return (
        "That employee identifier format is not supported. Enter a valid employee ID."
    )


def _unsupported_value(locale: str, issue: str) -> str:
    if issue == "reversed_temporal_range":
        if locale == "ar":
            return (
                "تاريخ بداية النطاق يأتي بعد تاريخ نهايته. يرجى تصحيح ترتيب التاريخين."
            )
        return "The date range starts after it ends. Please correct the two dates."
    if locale == "ar":
        return "يحتوي الطلب على تاريخ أو قيمة رقمية غير صالحة. يرجى تصحيحها."
    return "The request contains an invalid date or numeric value. Please correct it."


def _explicit_date_scopes(question: str) -> set[tuple[str, str]]:
    """Collect explicit calendar intervals without collapsing separate periods."""

    months = {
        **_MONTH_NUMBERS,
        **{calendar.month_abbr[number].casefold(): number for number in range(1, 13)},
    }
    month_pattern = "|".join(sorted(months, key=len, reverse=True))
    month_day = rf"(?P<m>{month_pattern})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?"
    day_month = rf"(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{month_pattern})"
    found: list[tuple[tuple[int, int], tuple[str, str]]] = []

    def add(match: re.Match[str], start: date, end: date) -> None:
        found.append((match.span(), (start.isoformat(), end.isoformat())))

    # A range is a single scope. Its full span prevents its endpoints from
    # being counted again as independent day mentions.
    for match in re.finditer(
        r"\b(?P<first>\d{4}-\d{1,2}-\d{1,2})\s*"
        r"(?:to|through|[-–—])\s*"
        r"(?P<last>\d{4}-\d{1,2}-\d{1,2})\b",
        question,
        re.IGNORECASE,
    ):
        try:
            add(
                match,
                date.fromisoformat(match["first"]),
                date.fromisoformat(match["last"]),
            )
        except ValueError:
            return set()
    for match in re.finditer(
        r"\b(?:between|from)\s+(?P<first>\d{4}-\d{1,2}-\d{1,2})\s+"
        r"and\s+(?P<last>\d{4}-\d{1,2}-\d{1,2})\b",
        question,
        re.IGNORECASE,
    ):
        try:
            add(match, date.fromisoformat(match["first"]), date.fromisoformat(match["last"]))
        except ValueError:
            return set()
    for match in re.finditer(
        rf"\b(?P<first_m>{month_pattern})\s+(?P<first_d>\d{{1,2}})\s*"
        rf"(?:to|through|[-–—])\s*"
        rf"(?P<last_m>{month_pattern})\s+(?P<last_d>\d{{1,2}})\s*,?\s*"
        rf"(?P<y>\d{{4}})\b",
        question,
        re.IGNORECASE,
    ):
        try:
            year = int(match["y"])
            add(
                match,
                date(year, months[match["first_m"].casefold()], int(match["first_d"])),
                date(year, months[match["last_m"].casefold()], int(match["last_d"])),
            )
        except ValueError:
            return set()
    for match in re.finditer(
        rf"\b(?:between|from)\s+(?P<first_m>{month_pattern})\s+"
        rf"(?P<first_d>\d{{1,2}})\s+and\s+"
        rf"(?P<last_m>{month_pattern})\s+(?P<last_d>\d{{1,2}})\s*,?\s*"
        rf"(?P<y>\d{{4}})\b",
        question,
        re.IGNORECASE,
    ):
        try:
            year = int(match["y"])
            add(
                match,
                date(year, months[match["first_m"].casefold()], int(match["first_d"])),
                date(year, months[match["last_m"].casefold()], int(match["last_d"])),
            )
        except ValueError:
            return set()
    for match in re.finditer(
        rf"\b(?P<m>{month_pattern})\s+(?P<first>\d{{1,2}})\s*[-–—]\s*"
        rf"(?P<last>\d{{1,2}})\s*,?\s*(?P<y>\d{{4}})\b",
        question,
        re.IGNORECASE,
    ):
        if any(a <= match.start() and match.end() <= b for (a, b), _ in found):
            continue
        try:
            month = months[match["m"].casefold()]
            add(
                match,
                date(int(match["y"]), month, int(match["first"])),
                date(int(match["y"]), month, int(match["last"])),
            )
        except ValueError:
            return set()
    for match in re.finditer(
        rf"\b(?P<first>\d{{1,2}})\s*[-–—]\s*(?P<last>\d{{1,2}})\s+"
        rf"(?P<m>{month_pattern})\s+(?P<y>\d{{4}})\b",
        question,
        re.IGNORECASE,
    ):
        if any(a <= match.start() and match.end() <= b for (a, b), _ in found):
            continue
        try:
            month = months[match["m"].casefold()]
            add(
                match,
                date(int(match["y"]), month, int(match["first"])),
                date(int(match["y"]), month, int(match["last"])),
            )
        except ValueError:
            return set()
    # A comparison can give the year once after several month/day values.
    # Keep each requested day separate instead of enforcing the last one as
    # a global date bound on every independent query branch.
    shared_year_day = rf"(?:{month_pattern})\s+\d{{1,2}}(?:st|nd|rd|th)?"
    for match in re.finditer(
        rf"\b(?:{shared_year_day})(?:\s*(?:,|&|or|vs\.?|versus)\s*"
        rf"(?:{shared_year_day}))+\s*,?\s*(?P<y>\d{{4}})\b",
        question,
        re.IGNORECASE,
    ):
        if any(a <= match.start() and match.end() <= b for (a, b), _ in found):
            continue
        year = int(match["y"])
        for day_match in re.finditer(
            rf"\b(?P<m>{month_pattern})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b",
            match.group()[: match.start("y") - match.start()],
            re.IGNORECASE,
        ):
            try:
                day = date(year, months[day_match["m"].casefold()], int(day_match["d"]))
            except ValueError:
                return set()
            add(match, day, day)
    for match in re.finditer(
        r"\b(?P<operator>before|after|on\s+or\s+before|on\s+or\s+after)\s+"
        r"(?P<day>\d{4}-\d{1,2}-\d{1,2})\b",
        question,
        re.IGNORECASE,
    ):
        if any(a <= match.start() and match.end() <= b for (a, b), _ in found):
            continue
        try:
            day = date.fromisoformat(match["day"])
            operator = match["operator"].casefold()
            start = date.min if "before" in operator else day
            end = day if "before" in operator else date.max
            if operator == "before":
                end -= timedelta(days=1)
            elif operator == "after":
                start += timedelta(days=1)
            add(match, start, end)
        except (ValueError, OverflowError):
            return set()
    for match in re.finditer(r"\b\d{4}-\d{1,2}-\d{1,2}\b", question):
        if any(a <= match.start() and match.end() <= b for (a, b), _ in found):
            continue
        try:
            day = date.fromisoformat(match.group())
        except ValueError:
            return set()
        add(match, day, day)
    for pattern in (
        rf"\b{month_day}\s*,?\s*(?P<y>\d{{4}})\b",
        rf"\b{day_month}\s*,?\s*(?P<y>\d{{4}})\b",
    ):
        for match in re.finditer(pattern, question, re.IGNORECASE):
            if any(a <= match.start() and match.end() <= b for (a, b), _ in found):
                continue
            try:
                day = date(
                    int(match["y"]), months[match["m"].casefold()], int(match["d"])
                )
            except ValueError:
                return set()
            add(match, day, day)
    month_token = rf"(?:{month_pattern})"
    for match in re.finditer(
        rf"\b(?P<first>{month_token})\s*(?:to|through|[-–—])\s*"
        rf"(?P<last>{month_token})\s+(?P<y>\d{{4}})\b",
        question,
        re.IGNORECASE,
    ):
        year = int(match["y"])
        first = months[match["first"].casefold()]
        last = months[match["last"].casefold()]
        add(
            match,
            date(year, first, 1),
            date(year, last, calendar.monthrange(year, last)[1]),
        )
    separator = r"\s*(?:,|and|or|vs\.?|versus|/|&)\s*"
    for match in re.finditer(
        rf"\b(?P<months>{month_token}(?:{separator}{month_token})+)"
        rf"\s+(?P<y>\d{{4}})\b",
        question,
        re.IGNORECASE,
    ):
        if any(a <= match.start() and match.end() <= b for (a, b), _ in found):
            continue
        year = int(match["y"])
        for month_match in re.finditer(month_token, match["months"], re.IGNORECASE):
            month = months[month_match.group().casefold()]
            add(
                match,
                date(year, month, 1),
                date(year, month, calendar.monthrange(year, month)[1]),
            )
    for match in re.finditer(
        rf"\b(?P<m>{month_pattern})\s+(?P<y>\d{{4}})\b",
        question,
        re.IGNORECASE,
    ):
        if any(a <= match.start() and match.end() <= b for (a, b), _ in found):
            continue
        year, month = int(match["y"]), months[match["m"].casefold()]
        add(
            match,
            date(year, month, 1),
            date(year, month, calendar.monthrange(year, month)[1]),
        )
    return {scope for _, scope in found}


def _requested_date_scope(question: str) -> tuple[str, str] | None:
    """Resolve one explicit interval; leave multiple intervals to the planner."""

    scopes = _explicit_date_scopes(question)
    return next(iter(scopes)) if len(scopes) == 1 else None


def _verified_requested_date_scope(
    turn: VerifiedTurn | None,
) -> tuple[str, str] | None:
    if turn is None:
        return None
    if turn.requested_date_scope is not None:
        return turn.requested_date_scope
    # Older saved turns have only the executed SQL scope.
    explicit = _requested_date_scope(turn.original_question)
    return explicit if explicit == turn.date_scope else None


def _required_date_scope(
    question: str,
    *,
    request_relationship: str,
    subject_relationship: str | None,
    previous_scope: tuple[str, str] | None,
    union_has_criteria: bool = False,
    rewritten_request: str | None = None,
) -> tuple[str, str] | None:
    # A union can contain independent subjects with independent periods. One
    # global bound cannot validate that query without corrupting a branch.
    original_scopes = _explicit_date_scopes(question)
    if len(original_scopes) > 1:
        return None
    explicit = next(iter(original_scopes)) if original_scopes else None
    if subject_relationship == "union":
        return explicit if not union_has_criteria else None
    if explicit is not None:
        return explicit
    if _mentions_time_period(question) and rewritten_request:
        resolved = _requested_date_scope(rewritten_request)
        if resolved is not None:
            return resolved
    if request_relationship == "follow_up" and not _mentions_time_period(question):
        return previous_scope
    return None


def _request_value_issue(question: str) -> str | None:
    """Return a typed issue for literals PostgreSQL may accept misleadingly."""

    for match in re.finditer(r"\b\d{4}-\d{1,2}-\d{1,2}\b", question):
        try:
            date.fromisoformat(match.group(0))
        except ValueError:
            return "malformed_value"

    iso_range = re.search(
        r"\b(?:between|from)\s+(\d{4}-\d{2}-\d{2})\s+"
        r"(?:and|to)\s+(\d{4}-\d{2}-\d{2})\b",
        question,
        flags=re.IGNORECASE,
    )
    if iso_range is not None and date.fromisoformat(
        iso_range.group(1)
    ) > date.fromisoformat(iso_range.group(2)):
        return "reversed_temporal_range"

    month_pattern = "|".join(_MONTH_NUMBERS)
    for match in re.finditer(
        rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})\b",
        question,
        flags=re.IGNORECASE,
    ):
        month_name, day_text, year_text = match.groups()
        try:
            date(
                int(year_text),
                _MONTH_NUMBERS[month_name.casefold()],
                int(day_text),
            )
        except ValueError:
            return "malformed_value"

    natural_range = re.search(
        rf"\b(?:between|from)\s+({month_pattern})\s+(\d{{1,2}})"
        rf"(?:st|nd|rd|th)?\s+(?:and|to)\s+({month_pattern})\s+(\d{{1,2}})"
        rf"(?:st|nd|rd|th)?\b",
        question,
        flags=re.IGNORECASE,
    )
    if natural_range is not None:
        start_month, start_day, end_month, end_day = natural_range.groups()
        if start_month.casefold() == end_month.casefold() and int(start_day) > int(
            end_day
        ):
            return "reversed_temporal_range"

    if re.search(
        r"\b(?:between|from)\s+(?:not[- ]a[- ]date|invalid(?:\s+date)?|\?+)",
        question,
        re.IGNORECASE,
    ):
        return "unsupported_constraint"

    if re.search(
        r"\b(?:greater than|more than|less than|at least|at most|above|below|over|under)"
        r"\s*(?:\?+|_+|not[- ]a[- ]number)\s*\.?\s*hours?\b",
        question,
        re.IGNORECASE,
    ):
        return "malformed_value"

    if re.search(r"\b(?:nan|[+-]?infinity|[+-]?inf)\b", question, re.IGNORECASE):
        return "malformed_value"

    comparison_pattern = re.compile(
        r"\b(greater than|more than|less than|at least|at most|above|below|over|under)"
        r"\s+([^,.!?]*?)(?=\s+hours?\b|[,.!?]|$)",
        re.IGNORECASE,
    )
    for match in comparison_pattern.finditer(question):
        comparator = match.group(1).casefold()
        operand = " ".join(match.group(2).casefold().split())
        first_operand = operand.split(maxsplit=1)[0] if operand else ""
        if first_operand in _NUMBER_WORDS:
            continue
        has_hours = bool(
            re.match(r"\s+hours?\b", question[match.end() :], re.IGNORECASE)
        )
        if comparator in {"over", "under"} and not has_hours:
            continue
        try:
            numeric = float(first_operand.replace(",", ""))
        except ValueError:
            return "malformed_value"
        if not math.isfinite(numeric):
            return "malformed_value"
    return None


def _unsupported_schema_reason(result: SqlExecutionResult) -> str | None:
    if len(result.rows) != 1:
        return None
    row = result.rows[0]
    for key, value in row.items():
        if str(key).casefold() == "unsupported_capability" and value is not None:
            return str(value)
    return None


def _planner_clarification_reason(result: SqlExecutionResult) -> str | None:
    if len(result.rows) != 1:
        return None
    row = result.rows[0]
    for key, value in row.items():
        if str(key).casefold() == "clarification_required" and value is not None:
            return str(value)
    return None


def _confirmation_reply(pending: PendingEmployeeConfirmation, locale: str) -> str:
    if len(pending.options) > 1:
        choices = "\n".join(
            f"{index}. {option.employee_name} ({option.employee_id})"
            for index, option in enumerate(pending.options, start=1)
        )
        if locale == "ar":
            return f"وجدت عدة موظفين محتملين. اختر رقمًا، أو اكتب الاسم أو الرقم الوظيفي بدقة:\n{choices}"
        return f"I found several possible employees. Choose a number, or enter the exact name or employee ID:\n{choices}"
    option = pending.options[0]
    if locale == "ar":
        return f"هل تقصد {option.employee_name} ({option.employee_id})؟"
    return f"Did you mean {option.employee_name} ({option.employee_id})?"


def _pending_response(response: str, pending: PendingEmployeeConfirmation):
    normalized = " ".join(response.casefold().split())
    if normalized in {"no", "n", "cancel", "لا", "غير صحيح", "إلغاء"}:
        return "cancelled", None
    if len(pending.options) == 1 and normalized in {
        "yes",
        "y",
        "correct",
        "confirm",
        "نعم",
        "صحيح",
        "أجل",
    }:
        return "selected", pending.options[0]
    try:
        index = int(normalized) - 1
    except ValueError:
        index = None
    if index is not None:
        if 0 <= index < len(pending.options):
            return "selected", pending.options[index]
        return "invalid_selection", None
    matches = [
        option
        for option in pending.options
        if normalized
        in {
            option.employee_id.casefold(),
            " ".join(option.employee_name.casefold().split()),
        }
    ]
    if len(matches) == 1:
        return "selected", matches[0]
    if normalized in {
        "yes",
        "y",
        "correct",
        "confirm",
        "all",
        "both",
        "نعم",
        "صحيح",
        "أجل",
        "الكل",
        "كلاهما",
    }:
        return "invalid_selection", None
    return "new_request", None


def _authorized_directory(
    directory: tuple[Employee, ...],
    access: AccessContext | None,
) -> tuple[tuple[Employee, ...], tuple[str, ...] | None]:
    scope = authorize_access(access)
    if scope.kind == "all":
        return directory, None
    allowed = set(scope.employee_ids)
    return (
        tuple(item for item in directory if item.employee_id in allowed),
        scope.employee_ids,
    )


def _verified_options(
    options: tuple[EmployeeOption, ...],
    directory: tuple[Employee, ...],
) -> tuple[EmployeeOption, ...]:
    authoritative = {item.employee_id: item.name for item in directory}
    verified: list[EmployeeOption] = []
    seen: set[str] = set()
    for option in options:
        if (
            option.employee_id in seen
            or authoritative.get(option.employee_id) != option.employee_name
        ):
            continue
        seen.add(option.employee_id)
        verified.append(option)
        if len(verified) == MAX_EMPLOYEE_CANDIDATES:
            break
    return tuple(verified)


def _emit(
    observer: TurnObserver | None, stage: str, status: str, detail: str | None = None
) -> None:
    if observer is not None:
        observer(StageEvent(stage=stage, status=status, detail=detail))


def _result_evidence(result: SqlExecutionResult) -> tuple[Result, ...]:
    payload = result.model_dump(mode="json")
    return (
        Result(
            page_content=json.dumps(payload, ensure_ascii=False, indent=2),
            metadata={"kind": "sql_result", "row_count": len(result.rows)},
        ),
    )


def _multi_result_evidence(steps: tuple[ExecutedPlanStep, ...]) -> tuple[Result, ...]:
    return tuple(
        Result(
            page_content=json.dumps(
                {**step.result.model_dump(mode="json"),
                 "step_index": index, "subrequest": step.subrequest,
                 "executed_sql": step.sql},
                ensure_ascii=False, indent=2,
            ),
            metadata={"kind": "sql_result", "step_index": index,
                      "row_count": len(step.result.rows)},
        )
        for index, step in enumerate(steps)
    )


def _normalized_plan_indices(plan: MultiSqlPlan, clause_count: int) -> tuple[int, ...] | None:
    supplied = tuple(step.scope_clause_index for step in plan.steps)
    for offset in (0, 1):
        normalized = tuple(index - offset for index in supplied)
        if normalized == tuple(sorted(normalized)) and set(normalized) == set(range(clause_count)):
            return normalized
    return None


def _prior_step_metadata(turn: VerifiedTurn) -> tuple[dict[str, object], ...]:
    """Use prior SQL scope for follow-ups without trusting saved result values."""
    fields = ("scope_clause_index", "subrequest", "sql", "requested_date_scope",
              "requested_date_scopes", "date_scope")
    return tuple({key: step[key] for key in fields if key in step}
                 for step in turn.executed_steps)


class _MultiStepRetry(Exception):
    def __init__(self, index: int, step: SqlPlanStep, failure: dict[str, object]):
        self.index = index
        self.step = step
        self.failure = failure
        super().__init__(str(failure["database_error"]))


def _run_multi_plan(
    *, plan: MultiSqlPlan, deps: RuntimeDependencies,
    shared_context: SharedModelContext, bound: BoundReferences,
    previous: ConversationState, question: str,
    allowed_employee_ids: tuple[str, ...] | None, budget: CallBudget,
    observer: TurnObserver | None, count_reconciliation: bool,
) -> TurnOutcome | str:
    clauses = bound.scope_clauses
    if len(clauses) > 12:
        raise ProviderFailure("sql_planner", "incomplete_multi_plan", "The multi-step plan exceeds its execution bound")
    allowed_tables = tuple(
        f"{table.schema_name}.{table.table_name}"
        for table in shared_context.database_context.tables
    )
    plan_attempt = 1
    step_retries = 0
    while plan_attempt <= 2:
        log_layer_output("multi_plan", plan, attempt=plan_attempt)
        clause_indices = _normalized_plan_indices(plan, len(clauses))
        if clause_indices is None:
            if plan_attempt == 2:
                raise ProviderFailure("sql_planner", "incomplete_multi_plan", "Plan steps must cover every independent clause in order")
            correction = deps.planner(
                shared_context=shared_context, model=settings.llm_planner_model,
                budget=budget, timeout=settings.llm_planner_timeout_seconds,
                max_output_tokens=settings.llm_planner_max_output_tokens,
                attempt=plan_attempt + 1, observer=observer,
                sql_execution_failure={
                    "error_type": "plan_structure",
                    "database_error": (
                        "Plan steps must cover every independent scope clause in "
                        "order. Use either zero-based indices 0 through N-1 or "
                        "one-based indices 1 through N consistently. Multiple "
                        "steps may share one clause index when needed for a comparison."
                    ),
                    "failed_plan": plan.model_dump(mode="json"),
                },
            )
            if isinstance(correction, str):
                return correction
            if not isinstance(correction, MultiSqlPlan):
                raise ProviderFailure("sql_planner", "invalid_multi_replan", "The planner did not return a corrected multi-step plan")
            plan = correction
            plan_attempt += 1
            continue
        all_clause_dates = tuple(_explicit_date_scopes(item.request) for item in clauses)
        executed: list[ExecutedPlanStep] = []
        total_rows = 0
        total_bytes = 0
        total_row_limit = min(1000, min(settings.max_exact_results, 1000) * len(plan.steps))
        snapshot_context = (
            deps.snapshot_factory(
                dsn=settings.postgres_readonly_dsn,
                connect_timeout=settings.postgres_connect_timeout_seconds,
                statement_timeout_ms=settings.postgres_statement_timeout_ms,
                lock_timeout_ms=settings.postgres_lock_timeout_ms,
                idle_timeout_ms=settings.postgres_idle_transaction_timeout_ms,
            )
            if deps.snapshot_factory is not None else nullcontext(None)
        )
        try:
            with snapshot_context as snapshot_connection:
                for index, planned in enumerate(plan.steps):
                    clause_index = clause_indices[index]
                    clause = clauses[clause_index]
                    clause_dates = all_clause_dates[clause_index]
                    step_dates = _explicit_date_scopes(planned.subrequest)
                    if clause_dates and step_dates and not step_dates.issubset(clause_dates):
                        raise ProviderFailure("sql_planner", "multi_step_scope_mismatch",
                                              "A plan step introduced a date outside its clause")
                    if len(clause_dates) == 1:
                        clause_date_scope = next(iter(clause_dates))
                    elif len(clause_dates) > 1 and len(step_dates) == 1:
                        clause_date_scope = next(iter(step_dates))
                    elif clause.date_scope_relationship in {"independent", "unbounded"}:
                        clause_date_scope = None
                    elif shared_context.required_date_scope is not None:
                        clause_date_scope = shared_context.required_date_scope
                    else:
                        clause_date_scope = None
                    requested_dates = tuple(sorted(clause_dates or step_dates))
                    if not requested_dates and clause_date_scope is not None:
                        requested_dates = (clause_date_scope,)
                    sql = _strip_private_projections(
                        planned.sql, public_columns=tuple(
                            column.name for table in shared_context.database_context.tables
                            for column in table.columns
                        ),
                    )
                    if sql is None:
                        raise ProviderFailure("sql_planner", "private_source_payload", "A plan step projected private source data")
                    try:
                        validate_read_query(sql, allowed_tables=allowed_tables)
                        semantic_issue = _sql_semantic_issue(
                            clause.request, sql, required_date_scope=clause_date_scope,
                            independent_clauses=False,
                        )
                        if semantic_issue is not None:
                            raise ValueError(f"SQL semantic issue: {semantic_issue}")
                        result = deps.executor(
                            sql, dsn=settings.postgres_readonly_dsn,
                            connect_timeout=settings.postgres_connect_timeout_seconds,
                            statement_timeout_ms=settings.postgres_statement_timeout_ms,
                            lock_timeout_ms=settings.postgres_lock_timeout_ms,
                            idle_timeout_ms=settings.postgres_idle_transaction_timeout_ms,
                            result_limit=min(settings.max_exact_results, 1000),
                            max_response_bytes=settings.max_sql_result_bytes,
                            allowed_tables=allowed_tables,
                            scope_employee_ids=allowed_employee_ids,
                            **({"connection": snapshot_connection}
                               if snapshot_connection is not None else {}),
                        )
                    except (ValueError, psycopg.ProgrammingError, psycopg.DataError, RuntimeError) as exc:
                        failure = {
                            "retry_number": step_retries + 1, "failed_sql": sql,
                            "error_type": type(exc).__name__, "database_error": str(exc)[:4000],
                            "scope_clause_index": clause_index,
                            "subrequest": planned.subrequest,
                        }
                        log_layer_output("sql_execution_failure", failure, attempt=step_retries + 1)
                        raise _MultiStepRetry(index, planned, failure) from exc
                    total_rows += len(result.rows)
                    total_bytes += result.coverage.response_bytes
                    if total_rows > total_row_limit:
                        raise ProviderFailure("sql_execution", "multi_result_bound", "Combined plan results exceeded the row bound")
                    if total_bytes > settings.max_sql_result_bytes:
                        raise ProviderFailure("sql_execution", "multi_result_bound", "Combined plan results exceeded the response-size bound")
                    executed.append(ExecutedPlanStep(
                        scope_clause_index=clause_index, subrequest=planned.subrequest,
                        sql=sql, requested_date_scope=clause_date_scope,
                        requested_date_scopes=requested_dates,
                        date_scope=_sql_date_scope(sql), result=result,
                    ))
                    log_layer_output("sql_execution", {"step_index": index, "sql": sql,
                                                       "result": result.model_dump(mode="json")})
        except _MultiStepRetry as retry:
            if step_retries >= SQL_EXECUTION_ATTEMPT_LIMIT - 1:
                raise ProviderFailure(
                    "sql_planner", "multi_step_failed", str(retry)
                ) from retry
            step_retries += 1
            corrected_sql = retry_plan_step(
                shared_context=shared_context, step=retry.step,
                failure=retry.failure, model=settings.llm_planner_model,
                budget=budget, timeout=settings.llm_planner_timeout_seconds,
                max_output_tokens=settings.llm_planner_max_output_tokens,
                attempt=step_retries + 1, observer=observer,
            )
            corrected_steps = list(plan.steps)
            corrected_steps[retry.index] = retry.step.model_copy(
                update={"sql": corrected_sql}
            )
            plan = MultiSqlPlan(steps=tuple(corrected_steps))
            continue
        steps = tuple(executed)
        answer = answer_multi_result(
            shared_context=shared_context, steps=steps, employees=bound.employees,
            locale=bound.locale, model=settings.llm_planner_model, budget=budget,
            timeout=settings.llm_planner_timeout_seconds,
            max_output_tokens=settings.llm_planner_max_output_tokens,
            observer=observer,
        )
        if isinstance(answer, ReplanRequest):
            if plan_attempt == 2:
                raise ProviderFailure("sql_answer_review", "replan_limit_exceeded", answer.reason)
            replanned = deps.planner(
                shared_context=shared_context, model=settings.llm_planner_model,
                budget=budget, timeout=settings.llm_planner_timeout_seconds,
                max_output_tokens=settings.llm_planner_max_output_tokens,
                attempt=plan_attempt + 1, observer=observer,
                sql_execution_failure={"error_type": "answer_review_requery",
                                       "database_error": answer.reason,
                                       "executed_steps": [step.model_dump(mode="json") for step in steps]},
            )
            if isinstance(replanned, str):
                return replanned
            if not isinstance(replanned, MultiSqlPlan):
                raise ProviderFailure("sql_planner", "invalid_multi_replan", "The replanner omitted independent clauses")
            plan = replanned
            plan_attempt += 1
            continue
        published_employees = bound.employees
        verified = VerifiedTurn(
            turn_id=uuid4().hex, original_question=question,
            rewritten_request=bound.updated_request, answer=answer,
            locale=bound.locale, employees=published_employees,
            executed_sql=steps[0].sql, count_reconciliation=count_reconciliation,
            date_scope=(steps[0].date_scope if all(
                step.date_scope == steps[0].date_scope for step in steps) else None),
            requested_date_scope=(steps[0].requested_date_scope if all(
                step.requested_date_scope == steps[0].requested_date_scope
                for step in steps) else None),
            result={"steps": [step.result.model_dump(mode="json") for step in steps]},
            executed_steps=tuple(step.model_dump(mode="json") for step in steps),
            scope_clauses=clauses,
        )
        new_state = previous.model_copy(update={
            "verified_turns": (previous.verified_turns + (verified,))[-50:],
            "active_employee_ids": tuple(item.employee_id for item in published_employees),
            "active_employees": published_employees,
            "pending_employee_confirmation": None,
        })
        _emit(observer, "publication", "completed")
        log_layer_output("publication", new_state)
        return Answered(reply=answer, evidence=_multi_result_evidence(steps), state=new_state)
    raise AssertionError("unreachable multi-plan branch")


def run_turn(
    request: TurnRequest,
    *,
    observer: TurnObserver | None = None,
    dependencies: RuntimeDependencies | None = None,
) -> TurnOutcome:
    deps = dependencies or DEPENDENCIES
    previous = ConversationState.from_untrusted(request.state)
    conversation_history = model_history(request.history)
    locale = _locale(request.question)
    budget = CallBudget(limit=settings.llm_turn_provider_call_limit)
    question = request.question
    as_of_date = date.today().isoformat()
    count_reconciliation = False
    try:
        native_comparison = None
        native_running_total = None
        database_context = None
        loaded_directory = deps.directory_loader(
            dsn=settings.postgres_readonly_dsn,
            table=settings.postgres_attendance_table,
            connect_timeout=settings.postgres_connect_timeout_seconds,
        )
        directory, allowed_employee_ids = _authorized_directory(
            loaded_directory,
            request.access_context,
        )
        log_layer_output(
            "employee_directory",
            [item.model_dump(mode="json") for item in directory],
        )
        authoritative = {item.employee_id: item for item in directory}
        active = tuple(
            authoritative[item]
            for item in previous.active_employee_ids
            if item in authoritative
        )
        prior_reference_scope_clauses = (
            tuple(
                clause.model_dump(mode="json")
                for clause in previous.verified_turns[-1].scope_clauses
            )
            if previous.verified_turns
            else ()
        )
        pending = previous.pending_employee_confirmation
        pending_response = (
            _pending_response(request.question, pending)
            if pending is not None
            else None
        )
        if pending is not None and pending_response is not None:
            status, _selected_option = pending_response
            if status == "new_request":
                previous = previous.model_copy(
                    update={"pending_employee_confirmation": None}
                )
                log_layer_output(
                    "employee_confirmation",
                    {"status": "replaced_by_new_request"},
                )
                pending = None
        if pending is not None:
            assert pending_response is not None
            status, selected_option = pending_response
            if status == "invalid_selection":
                log_layer_output(
                    "employee_confirmation",
                    {
                        "status": "invalid_selection",
                        "pending": pending.model_dump(mode="json"),
                    },
                )
                return Clarification(
                    reply=_confirmation_reply(
                        pending, _locale(pending.original_question)
                    ),
                    state=previous,
                    reason="employee_confirmation",
                )
            if status == "cancelled":
                state = previous.model_copy(
                    update={"pending_employee_confirmation": None}
                )
                log_layer_output(
                    "employee_confirmation",
                    {"status": "cancelled", "state": state.model_dump(mode="json")},
                )
                return Clarification(
                    reply=_clarification("ambiguous_reference", locale),
                    state=state,
                    reason="ambiguous_reference",
                )
            assert selected_option is not None
            selected = authoritative.get(selected_option.employee_id)
            if selected is None or selected.name != selected_option.employee_name:
                raise AuthorizationError(
                    "confirmed employee is outside the authorized directory"
                )
            question = pending.original_question
            count_reconciliation = pending.count_reconciliation
            bound = complete_confirmation(pending, selected)
            log_layer_output(
                "employee_confirmation",
                {"status": "selected", "employee": selected.model_dump(mode="json")},
            )
        else:
            request_issue = _request_value_issue(question)
            if request_issue is not None:
                log_layer_output(
                    "unsupported",
                    {
                        "capability": request_issue,
                        "state": previous.model_dump(mode="json"),
                    },
                )
                return Unsupported(
                    reply=_unsupported_value(locale, request_issue),
                    state=previous,
                    capability=request_issue,
                )
            reference = deps.reference_writer(
                question,
                history=conversation_history,
                trusted_context=previous.trusted_context(),
                prior_reference_scope_clauses=prior_reference_scope_clauses,
                active_employees=active,
                as_of_date=as_of_date,
                model=settings.llm_reference_model,
                budget=budget,
                timeout=settings.llm_reference_timeout_seconds,
                max_output_tokens=settings.llm_reference_max_output_tokens,
                observer=observer,
            )
            decision = reference.decision
            needs_reconsideration = isinstance(
                decision, (AmbiguousReference, UnsupportedReference)
            )
            inconsistent_ready = False
            ungrounded_active_new = False
            if isinstance(decision, ReadyReference):
                has_employee_references = bool(
                    decision.employee_ids
                    or decision.employee_names
                    or decision.identity_claims
                )
                inconsistent_ready = (
                    decision.subject_relationship in {"criteria", "all_authorized"}
                    and has_employee_references
                ) or (
                    decision.subject_relationship
                    in {"employees", "union", "intersection"}
                    and not has_employee_references
                    and not (
                        decision.subject_relationship in {"union", "intersection"}
                        and decision.employee_criteria
                    )
                    and not (decision.request_relationship == "follow_up" and active)
                )
                ungrounded_active_new = (
                    decision.request_relationship == "new"
                    and decision.subject_relationship
                    in {"employees", "union", "intersection"}
                    and any(
                        employee.employee_id in decision.employee_ids
                        and not re.search(
                            rf"(?<!\w){re.escape(employee.employee_id)}(?!\w)",
                            question,
                            flags=re.IGNORECASE,
                        )
                        and not re.search(
                            rf"(?<!\w){re.escape(employee.name)}(?!\w)",
                            question,
                            flags=re.IGNORECASE,
                        )
                        for employee in active
                    )
                )
                needs_reconsideration = inconsistent_ready or ungrounded_active_new
            if needs_reconsideration and request.max_reference_calls > 1:
                feedback = (
                    "The prior decision has an inconsistent subject_relationship "
                    "and employee references. Re-evaluate the current user intent; "
                    "a person reference needs an employee side, while a criterion "
                    "or all-records request should not claim a named employee."
                    if inconsistent_ready
                    else (
                        "The prior decision included a verified employee from the "
                        "earlier turn but classified this as a new request, while "
                        "the current message did not explicitly name that employee. "
                        "Re-evaluate whether it is a shorthand follow-up referring "
                        "to that employee or a genuinely new broader request. "
                        "Do not invent an employee reference."
                        if ungrounded_active_new
                        else None
                    )
                )
                reference = deps.reference_writer(
                    question,
                    history=conversation_history,
                    trusted_context=previous.trusted_context(),
                    prior_reference_scope_clauses=prior_reference_scope_clauses,
                    active_employees=active,
                    as_of_date=as_of_date,
                    model=settings.llm_planner_model,
                    budget=budget,
                    timeout=settings.llm_planner_timeout_seconds,
                    max_output_tokens=settings.llm_reference_max_output_tokens,
                    observer=observer,
                    reconsideration_feedback=feedback,
                    prior_decision=reference,
                )
                log_layer_output("reference_reconsidered", reference)
            count_reconciliation = bool(reference.count_reconciliation)
            if isinstance(reference.decision, UnsupportedReference):
                bound = BoundReferences(
                    rewritten_request=question,
                    updated_request=attach_resolved_employees(question, ()),
                    locale=reference.decision.locale,
                    request_relationship="new",
                    subject_relationship=None,
                )
                log_layer_output("planner_owned_capability", bound.updated_request)
            else:
                bound = bind_references(
                    reference,
                    directory,
                    original_question=question,
                    active_employees=active,
                    has_verified_turns=bool(previous.verified_turns),
                )

        bound = bound.model_copy(update={"locale": _locale(question)})

        log_layer_output("employee_resolution", bound)

        if bound.confirmation is not None:
            confirmation = bound.confirmation.model_copy(
                update={"count_reconciliation": count_reconciliation}
            )
            state = previous.model_copy(
                update={"pending_employee_confirmation": confirmation}
            )
            log_layer_output("publication", state)
            return Clarification(
                reply=_confirmation_reply(confirmation, bound.locale),
                state=state,
                reason="employee_confirmation",
            )
        if bound.ambiguous and bound.unresolved_mention is not None:
            postgres_options: tuple[EmployeeOption, ...] = ()
            try:
                postgres_options = deps.employee_fuzzy_search(
                    bound.unresolved_mention,
                    dsn=settings.postgres_readonly_dsn,
                    table=settings.postgres_attendance_table,
                    allowed_employee_ids=allowed_employee_ids,
                    directory=directory,
                    connect_timeout=settings.postgres_connect_timeout_seconds,
                )
                log_layer_output("employee_fuzzy_search", postgres_options)
                _emit(
                    observer,
                    "employee_fuzzy_search",
                    "completed",
                    f"candidates={len(postgres_options)}",
                )
            except Exception as exc:
                _emit(
                    observer,
                    "employee_fuzzy_search",
                    "unavailable",
                    type(exc).__name__,
                )
            verified_postgres = _verified_options(postgres_options, directory)
            if verified_postgres and bound.pending_resolution is not None:
                pending = PendingEmployeeConfirmation(
                    original_question=question,
                    mention=bound.unresolved_mention,
                    options=verified_postgres,
                    resolution=bound.pending_resolution,
                    count_reconciliation=count_reconciliation,
                )
                state = previous.model_copy(
                    update={"pending_employee_confirmation": pending}
                )
                log_layer_output("publication", state)
                return Clarification(
                    reply=_confirmation_reply(pending, bound.locale),
                    state=state,
                    reason="employee_confirmation",
                )
            if bound.reason == "unknown_employee_id":
                return Clarification(
                    reply=_clarification("unknown_employee_id", bound.locale),
                    state=previous,
                    reason="unknown_employee_id",
                )
            return Clarification(
                reply=_clarification(bound.reason, bound.locale),
                state=previous,
                reason=bound.reason,
            )
        if bound.reason == "malformed_identifier":
            log_layer_output(
                "unsupported",
                {
                    "capability": "malformed_identifier",
                    "state": previous.model_dump(mode="json"),
                },
            )
            return Unsupported(
                reply=_unsupported_identifier(bound.locale),
                state=previous,
                capability="malformed_identifier",
            )
        if bound.ambiguous or bound.updated_request is None:
            log_layer_output(
                "clarification",
                {"reason": bound.reason, "state": previous.model_dump(mode="json")},
            )
            return Clarification(
                reply=_clarification(bound.reason, bound.locale),
                state=previous,
                reason=bound.reason,
            )

        native_scope_eligible = (
            bound.request_relationship == "follow_up"
            and bound.subject_relationship in {"employees", "all_authorized"}
            and bound.employee_ids == tuple(item.employee_id for item in active)
            and bool(previous.verified_turns)
            and not previous.verified_turns[-1].executed_steps
        )
        if native_scope_eligible and is_grouped_month_comparison_question(question):
            database_context = deps.context_loader(
                dsn=settings.postgres_readonly_dsn,
                attendance_objects=(settings.postgres_attendance_table,),
                connect_timeout=settings.postgres_connect_timeout_seconds,
                allowed_employee_ids=allowed_employee_ids,
            )
            native_comparison = build_grouped_month_comparison(
                question=question,
                previous_sql=previous.verified_turns[-1].executed_sql,
                previous_date_scope=previous.verified_turns[-1].date_scope,
                as_of_date=as_of_date,
                database_context=database_context,
            )
            if native_comparison is not None:
                log_layer_output("native_comparison_plan", native_comparison.sql)
        if native_scope_eligible and not active and is_running_total_question(question):
            if database_context is None:
                database_context = deps.context_loader(
                    dsn=settings.postgres_readonly_dsn,
                    attendance_objects=(settings.postgres_attendance_table,),
                    connect_timeout=settings.postgres_connect_timeout_seconds,
                    allowed_employee_ids=allowed_employee_ids,
                )
            native_running_total = build_running_total(
                question=question,
                previous_sql=previous.verified_turns[-1].executed_sql,
                previous_date_scope=previous.verified_turns[-1].date_scope,
                database_context=database_context,
            )
            if native_running_total is not None:
                log_layer_output("native_running_total_plan", native_running_total.sql)

        resolved_rewrite = bound.rewritten_request
        if bound.request_relationship == "new":
            # For an independent request, the current question is the authority
            # for its scope. A reference rewrite may accidentally carry filters
            # from an older turn, especially when the new question has a date.
            # Retain only identities resolved against the directory.
            bound = bound.model_copy(
                update={
                    "rewritten_request": question,
                    "updated_request": attach_resolved_employees(
                        question, bound.employees
                    ),
                }
            )

        enforced_date_scope = _required_date_scope(
            question,
            request_relationship=bound.request_relationship,
            subject_relationship=bound.subject_relationship,
            union_has_criteria=bool(bound.employee_criteria),
            rewritten_request=resolved_rewrite,
            previous_scope=(
                _verified_requested_date_scope(previous.verified_turns[-1])
                if previous.verified_turns
                else None
            ),
        )
        if database_context is None:
            database_context = deps.context_loader(
                dsn=settings.postgres_readonly_dsn,
                attendance_objects=(settings.postgres_attendance_table,),
                connect_timeout=settings.postgres_connect_timeout_seconds,
                allowed_employee_ids=allowed_employee_ids,
            )
        log_layer_output("database_context", database_context)
        downstream_history = conversation_history
        trusted_context = previous.trusted_context()
        previous_turn = previous.verified_turns[-1] if previous.verified_turns else None
        scope_provenance = {
            "current_original_question": question,
            "reference_interpretation": resolved_rewrite,
            "reference_scope_clauses": [
                clause.model_dump(mode="json") for clause in bound.scope_clauses
            ],
            "previous_original_question": (
                previous_turn.original_question if previous_turn is not None else None
            ),
            "carried_employee_ids_source": [
                {
                    "employee_id": employee.employee_id,
                    "mentioned_by_id_in_current_question": bool(
                        re.search(
                            rf"(?<!\w){re.escape(employee.employee_id)}(?!\w)",
                            question,
                            flags=re.IGNORECASE,
                        )
                    ),
                    "mentioned_by_exact_name_in_current_question": bool(
                        re.search(
                            rf"(?<!\w){re.escape(employee.name)}(?!\w)",
                            question,
                            flags=re.IGNORECASE,
                        )
                    ),
                    "present_in_previous_verified_subject": (
                        employee.employee_id in previous.active_employee_ids
                    ),
                }
                for employee in bound.employees
            ],
            "carried_filter_source": {
                "reference_rewrite": bound.rewritten_request or question,
                "previous_user_question": (
                    previous_turn.original_question
                    if previous_turn is not None
                    else None
                ),
                "previous_executed_sql": (
                    previous_turn.executed_sql
                    if previous_turn is not None and not previous_turn.executed_steps
                    else None
                ),
                "previous_executed_steps": (
                    _prior_step_metadata(previous_turn)
                    if previous_turn is not None and previous_turn.executed_steps
                    else None
                ),
            },
        }
        downstream_trusted_context = (
            {
                **trusted_context,
                "verified_turns": [
                    {
                        **turn,
                        **({"executed_steps": _prior_step_metadata(previous.verified_turns[-1])}
                           if previous.verified_turns[-1].executed_steps else
                           {"executed_sql": previous.verified_turns[-1].executed_sql}),
                    }
                    for turn in trusted_context["verified_turns"]
                ],
            }
            if bound.request_relationship == "follow_up"
            else {}
        )
        shared_context = SharedModelContext(
            current_question=question,
            latest_user_message=request.question,
            as_of_date=as_of_date,
            updated_request=bound.updated_request,
            previous_verified_turn=(
                {
                    "request": previous.verified_turns[-1].rewritten_request,
                    **({"executed_steps": _prior_step_metadata(previous.verified_turns[-1])}
                       if previous.verified_turns[-1].executed_steps else {
                           "sql": previous.verified_turns[-1].executed_sql,
                           "having": _previous_having(previous.verified_turns[-1].executed_sql),
                           "date_scope": previous.verified_turns[-1].date_scope,
                       }),
                }
                if bound.request_relationship == "follow_up" and previous.verified_turns
                else None
            ),
            request_relationship=bound.request_relationship,
            count_reconciliation=count_reconciliation,
            subject_relationship=bound.subject_relationship,
            resolved_employee_ids=bound.employee_ids,
            required_date_scope=enforced_date_scope,
            request_has_date_period=(
                _mentions_time_period(question) or enforced_date_scope is not None
            ),
            conversation_history=downstream_history,
            trusted_context=downstream_trusted_context,
            scope_provenance=scope_provenance,
            database_context=database_context,
        )
        initial_sql: str | None = None
        if len(bound.scope_clauses) > 1 and not count_reconciliation:
            planned = deps.planner(
                shared_context=shared_context, model=settings.llm_planner_model,
                budget=budget, timeout=settings.llm_planner_timeout_seconds,
                max_output_tokens=settings.llm_planner_max_output_tokens,
                attempt=1, observer=observer,
            )
            if isinstance(planned, MultiSqlPlan):
                multi_outcome = _run_multi_plan(
                    plan=planned, deps=deps, shared_context=shared_context,
                    bound=bound, previous=previous, question=question,
                    allowed_employee_ids=allowed_employee_ids,
                    budget=budget, observer=observer,
                    count_reconciliation=count_reconciliation,
                )
                if not isinstance(multi_outcome, str):
                    return multi_outcome
                initial_sql = multi_outcome
            else:
                initial_sql = planned
        sql_execution_failure: dict[str, object] | None = None
        for attempt in range(1, SQL_EXECUTION_ATTEMPT_LIMIT + 1):
            planner_args: dict[str, object] = {
                "shared_context": shared_context,
                "model": settings.llm_planner_model,
                "budget": budget,
                "timeout": settings.llm_planner_timeout_seconds,
                "max_output_tokens": settings.llm_planner_max_output_tokens,
                "attempt": attempt,
                "observer": observer,
            }
            if sql_execution_failure is not None:
                planner_args["sql_execution_failure"] = sql_execution_failure
            sql = (
                native_comparison.sql
                if native_comparison is not None and attempt == 1
                else native_running_total.sql
                if native_running_total is not None and attempt == 1
                else initial_sql
                if initial_sql is not None and attempt == 1
                else deps.planner(**planner_args)
            )
            if isinstance(sql, MultiSqlPlan):
                multi_outcome = _run_multi_plan(
                    plan=sql, deps=deps, shared_context=shared_context,
                    bound=bound, previous=previous, question=question,
                    allowed_employee_ids=allowed_employee_ids,
                    budget=budget, observer=observer,
                    count_reconciliation=count_reconciliation,
                )
                if not isinstance(multi_outcome, str):
                    return multi_outcome
                sql = multi_outcome
            sql = _strip_private_projections(
                sql,
                public_columns=tuple(
                    column.name
                    for table in database_context.tables
                    for column in table.columns
                ),
            )
            if sql is None:
                return Unsupported(
                    reply=(
                        "Private source payloads are not available through this attendance interface."
                        if locale == "en"
                        else "حمولات المصدر الخاصة غير متاحة من خلال واجهة الحضور هذه."
                    ),
                    state=previous,
                    capability="private_source_payload",
                )
            if not count_reconciliation and is_count_reconciliation_plan(shared_context, sql):
                count_reconciliation = True
                shared_context = shared_context.model_copy(
                    update={"count_reconciliation": True}
                )
            if count_reconciliation:
                reconciled_sql = build_count_reconciliation_sql(shared_context, sql)
                if reconciled_sql is None:
                    if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                        raise ProviderFailure(
                            "sql_planner", "count_reconciliation_unavailable",
                            "The two count populations could not be verified against one source scope.",
                        )
                    sql_execution_failure = {
                        "retry_number": attempt,
                        "failed_sql": sql,
                        "error_type": "count_reconciliation_scope",
                        "database_error": (
                            "Plan the all-record and positive-worked-hours distinct-person "
                            "counts by the same group, table, date, and filters. "
                            "The only source difference must be positive worked hours."
                        ),
                    }
                    log_layer_output(
                        "sql_execution_failure", sql_execution_failure, attempt=attempt
                    )
                    continue
                sql = reconciled_sql
            log_layer_output("sql_planner", sql, attempt=attempt)
            if (
                _planner_control_alias(sql) == "unsupported_capability"
                and attempt < SQL_EXECUTION_ATTEMPT_LIMIT
            ):
                sql_execution_failure = {
                    "retry_number": attempt,
                    "failed_sql": sql,
                    "error_type": "capability_reconsideration",
                    "database_error": (
                        "Recheck the complete supplied schema and business_meanings "
                        "before declaring the request unsupported. A broad request "
                        "can be answered with observable field indicators even if "
                        "the schema has no single judgment label. Match the requested "
                        "output shape, include the user's explicit filters, and bound "
                        "multi-row output with LIMIT and matched_count. If the "
                        "required concept truly has no representation, return the "
                        "same unsupported_capability protocol."
                    ),
                }
                log_layer_output(
                    "sql_execution_failure", sql_execution_failure, attempt=attempt
                )
                continue
            try:
                parse(sql, read="postgres")
            except ParseError as exc:
                if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                    raise ProviderFailure(
                        "sql_planner", "invalid_sql_syntax", str(exc)
                    ) from exc
                sql_execution_failure = {
                    "retry_number": attempt,
                    "failed_sql": sql,
                    "error_type": "invalid_sql_syntax",
                    "database_error": (
                        "The SQL could not be parsed as PostgreSQL. Fix syntax, "
                        "preserve the complete request and verified scope, and "
                        "return one simple bounded SELECT statement. "
                        f"Parser detail: {str(exc)[:1000]}"
                    ),
                }
                log_layer_output(
                    "sql_execution_failure", sql_execution_failure, attempt=attempt
                )
                continue
            try:
                validate_read_query(
                    sql,
                    allowed_tables=tuple(
                        f"{table.schema_name}.{table.table_name}"
                        for table in database_context.tables
                    ),
                )
            except ValueError as exc:
                if str(exc).startswith("SQL references an unavailable table:"):
                    reason = (
                        "The requested concept is not represented by the "
                        "attendance schema."
                    )
                    log_layer_output(
                        "unsupported", {"capability": "schema", "reason": reason}
                    )
                    return Unsupported(
                        reply=reason, state=previous, capability="schema"
                    )
            semantic_issue = _sql_semantic_issue(
                question,
                sql,
                required_date_scope=enforced_date_scope,
                independent_clauses=len(bound.scope_clauses) > 1,
            )
            if semantic_issue in {
                "invalid_grouped_matched_count",
                "invalid_running_total_expression",
                "self_membership_filter",
                "date_scope_mismatch",
            }:
                if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                    raise ProviderFailure(
                        "sql_semantics", semantic_issue, semantic_issue
                    )
                database_error = semantic_issue
                if semantic_issue == "invalid_grouped_matched_count":
                    database_error = (
                        "The matched_count alias is not a grouped window count. "
                        "Use another alias for a per-group measure."
                    )
                elif semantic_issue == "invalid_running_total_expression":
                    database_error = (
                        "The running_total expression is an ordinary aggregate, "
                        "not a cumulative value. First aggregate daily values, "
                        "then calculate SUM(daily_value) OVER (ORDER BY "
                        "the requested date column ROWS BETWEEN UNBOUNDED "
                        "PRECEDING AND "
                        "CURRENT ROW) AS running_total."
                    )
                elif semantic_issue == "self_membership_filter":
                    database_error = (
                        "The SQL added a column IN (SELECT the same column FROM "
                        "the same unfiltered table) predicate. It does not implement "
                        "a requested restriction. Remove that predicate."
                    )
                elif semantic_issue == "date_scope_mismatch":
                    database_error = (
                        "The SQL omitted or incorrectly scoped the required inclusive "
                        "attendance_date range. Apply both bounds to every OR branch: "
                        f"{enforced_date_scope[0]} through {enforced_date_scope[1]}."
                    )
                sql_execution_failure = {
                    "retry_number": attempt,
                    "failed_sql": sql,
                    "error_type": "sql_semantics",
                    "database_error": database_error,
                }
                log_layer_output(
                    "sql_execution_failure", sql_execution_failure, attempt=attempt
                )
                continue
            try:
                result = deps.executor(
                    sql,
                    dsn=settings.postgres_readonly_dsn,
                    connect_timeout=settings.postgres_connect_timeout_seconds,
                    statement_timeout_ms=settings.postgres_statement_timeout_ms,
                    lock_timeout_ms=settings.postgres_lock_timeout_ms,
                    idle_timeout_ms=settings.postgres_idle_transaction_timeout_ms,
                    result_limit=min(settings.max_exact_results, 1000),
                    max_response_bytes=settings.max_sql_result_bytes,
                    allowed_tables=tuple(
                        f"{table.schema_name}.{table.table_name}"
                        for table in database_context.tables
                    ),
                    scope_employee_ids=allowed_employee_ids,
                )
            except (psycopg.ProgrammingError, psycopg.DataError, ValueError) as exc:
                log_layer_failure("sql_execution", "query_rejected", exc)
                if isinstance(exc, ValueError) and str(exc).startswith(
                    "SQL references an unavailable table:"
                ):
                    reason = (
                        "The requested concept is not represented by the "
                        "attendance schema."
                    )
                    log_layer_output(
                        "unsupported", {"capability": "schema", "reason": reason}
                    )
                    return Unsupported(
                        reply=reason, state=previous, capability="schema"
                    )
                if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                    raise
                sql_execution_failure = {
                    "retry_number": attempt,
                    "failed_sql": sql,
                    "error_type": type(exc).__name__,
                    "database_error": str(exc)[:4000],
                }
                log_layer_output(
                    "sql_execution_failure",
                    sql_execution_failure,
                    attempt=attempt,
                )
                continue
            except RuntimeError as exc:
                if str(exc) not in {
                    "result row bound exceeded",
                    "result response-size bound exceeded",
                }:
                    raise
                if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                    raise
                sql_execution_failure = {
                    "retry_number": attempt,
                    "failed_sql": sql,
                    "error_type": "result_bound",
                    "database_error": (
                        "The result exceeded the configured row or response-size "
                        "bound. Return bounded rows with LIMIT and COUNT(*) OVER() "
                        "AS matched_count so the answer can state the full match "
                        "count and identify returned rows as a sample."
                    ),
                }
                log_layer_output(
                    "sql_execution_failure",
                    sql_execution_failure,
                    attempt=attempt,
                )
                continue
            log_layer_output("sql_execution", result)
            semantic_issue = _sql_semantic_issue(
                question,
                sql,
                required_date_scope=enforced_date_scope,
                independent_clauses=len(bound.scope_clauses) > 1,
            )
            if semantic_issue is not None:
                raise ProviderFailure("sql_semantics", semantic_issue, semantic_issue)
            clarification_reason = _planner_clarification_reason(result)
            unsupported_reason = _unsupported_schema_reason(result)
            control_result = (
                clarification_reason is not None or unsupported_reason is not None
            )
            native_comparison_used = (
                native_comparison is not None and sql == native_comparison.sql
            )
            native_running_total_used = (
                native_running_total is not None and sql == native_running_total.sql
            )
            date_scope = (
                None
                if native_comparison_used or native_running_total_used
                else _sql_date_scope(sql)
            )
            separate_sources = len(
                bound.scope_clauses
            ) > 1 and _has_multiple_source_scopes(sql)
            if (
                not control_result
                and enforced_date_scope is not None
                and not (
                    _has_required_date_scope(
                        sql,
                        enforced_date_scope,
                        independent_clauses=True,
                    )
                    if separate_sources
                    else date_scope == enforced_date_scope
                )
            ):
                raise ProviderFailure(
                    "sql_semantics", "date_scope_mismatch", "date_scope_mismatch"
                )
            answer = deps.answerer(
                shared_context=shared_context,
                sql=sql,
                result=result,
                employees=bound.employees,
                locale=bound.locale,
                model=settings.llm_planner_model,
                budget=budget,
                timeout=settings.llm_planner_timeout_seconds,
                max_output_tokens=settings.llm_planner_max_output_tokens,
                observer=observer,
                sql_execution_failure=sql_execution_failure,
            )
            if isinstance(answer, ReplanRequest):
                if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                    raise ProviderFailure(
                        "sql_answer_review",
                        "replan_limit_exceeded",
                        "review still requires a new query after the final SQL attempt",
                    )
                sql_execution_failure = {
                    "retry_number": attempt,
                    "failed_sql": sql,
                    "error_type": "answer_review_requery",
                    "database_error": answer.reason,
                }
                log_layer_output(
                    "sql_execution_failure", sql_execution_failure, attempt=attempt
                )
                continue
            log_layer_output("answer", answer)
            if clarification_reason is not None:
                log_layer_output(
                    "clarification",
                    {"reason": "planner_clarification", "reply": answer},
                )
                return Clarification(
                    reply=answer,
                    state=previous,
                    reason="planner_clarification",
                )
            if unsupported_reason is not None:
                log_layer_output(
                    "unsupported",
                    {"capability": "schema", "reason": answer},
                )
                return Unsupported(
                    reply=answer,
                    state=previous,
                    capability="schema",
                )
            break
        published_employees = bound.employees or _carried_verified_employees(
            sql, active, request_relationship=bound.request_relationship
        )
        verified = VerifiedTurn(
            turn_id=uuid4().hex,
            original_question=question,
            rewritten_request=bound.updated_request,
            answer=answer,
            locale=bound.locale,
            employees=published_employees,
            executed_sql=sql,
            count_reconciliation=count_reconciliation,
            date_scope=date_scope,
            requested_date_scope=(
                enforced_date_scope if len(bound.scope_clauses) <= 1 else None
            ),
            result=result.model_dump(mode="json"),
            scope_clauses=bound.scope_clauses,
        )
        new_state = previous.model_copy(
            update={
                "verified_turns": (previous.verified_turns + (verified,))[-50:],
                "active_employee_ids": tuple(
                    item.employee_id for item in published_employees
                ),
                "active_employees": published_employees,
                "pending_employee_confirmation": None,
            }
        )
        _emit(observer, "publication", "completed")
        log_layer_output("publication", new_state)
        return Answered(
            reply=answer,
            evidence=_result_evidence(result),
            state=new_state,
        )
    except AuthorizationError as exc:
        _emit(observer, "failure", "authorization", str(exc))
        log_layer_failure("pipeline", "authorization_failed", exc)
        return Failed(
            reply=_failed(locale), state=previous, code="authorization_failed"
        )
    except ProviderFailure as exc:
        _emit(observer, "failure", exc.code, str(exc))
        log_layer_failure(exc.stage, exc.code, exc)
        return Failed(reply=_failed(locale), state=previous, code=exc.code)
    except Exception as exc:
        _emit(observer, "failure", "internal_error", type(exc).__name__)
        log_layer_failure("pipeline", "internal_error", exc)
        return Failed(reply=_failed(locale), state=previous, code="internal_error")


__all__ = [
    "Answered",
    "Clarification",
    "DEPENDENCIES",
    "Failed",
    "RuntimeDependencies",
    "TurnOutcome",
    "TurnRequest",
    "Unsupported",
    "run_turn",
]
