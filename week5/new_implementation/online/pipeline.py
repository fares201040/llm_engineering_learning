"""Direct-SQL attendance turn pipeline with atomic state publication."""

from __future__ import annotations

import calendar
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
    load_employee_directory,
    search_employee_directory_postgres,
    validate_read_query,
)
from .history import model_history
from .limits import MAX_EMPLOYEE_CANDIDATES
from .planner import ReplanRequest, answer_result, request_sql
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
    ReferenceResponse,
    UnsupportedReference,
    attach_resolved_employees,
    bind_references,
    complete_confirmation,
    has_malformed_identifier,
    has_unknown_identifier,
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
        planner: Callable[..., str] = request_sql,
        executor: Callable[..., SqlExecutionResult] = execute_sql,
        answerer: Callable[..., str | ReplanRequest] = answer_result,
    ):
        self.reference_writer = reference_writer
        self.directory_loader = directory_loader
        self.employee_fallback_search = employee_fallback_search
        self.employee_fuzzy_search = employee_fuzzy_search
        self.context_loader = context_loader
        self.planner = planner
        self.executor = executor
        self.answerer = answerer


DEPENDENCIES = RuntimeDependencies()


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
    sql: str, required_date_scope: tuple[str, str] | None
) -> bool:
    if required_date_scope is None:
        return True
    paths = contributing_where_paths(sql)
    return bool(paths) and all(
        _date_scope_for_path(path) == required_date_scope for path in paths
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
    question: str,
    sql: str,
    *,
    rewritten_request: str = "",
    allow_rewritten_contracts: bool = True,
    database_context: DatabaseContext | None = None,
    required_date_scope: tuple[str, str] | None = None,
) -> str | None:
    if _planner_control_alias(sql) is not None:
        return None
    if _has_self_membership_filter(sql):
        return "self_membership_filter"
    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        statements = ()
    running_total_found = False
    for statement in statements:
        if statement is None:
            continue
        if any(True for _ in statement.find_all(exp.ArrayAgg)):
            return "nested_result_shape"
        for alias in statement.find_all(exp.Alias):
            if alias.alias.casefold() != "running_total":
                continue
            running_total_found = True
            if not (
                isinstance(alias.this, exp.Window)
                and isinstance(alias.this.this, exp.Sum)
                and alias.this.args.get("order") is not None
            ):
                return "invalid_running_total_expression"
            if not any(True for _ in statement.find_all(exp.Group)):
                return "running_total_requires_daily_grouping"
        for select_node in statement.find_all(exp.Select):
            if not (select_node.args.get("group") and select_node.args.get("limit")):
                continue
            matched = [
                item
                for item in select_node.expressions
                if item.alias_or_name.casefold() == "matched_count"
            ]
            if not matched or not all(
                isinstance(item, exp.Alias)
                and isinstance(item.this, exp.Window)
                and isinstance(item.this.this, exp.Count)
                for item in matched
            ):
                return "invalid_grouped_matched_count"
            if not any(
                aggregate.find_ancestor(exp.Window) is None
                for item in select_node.expressions
                for aggregate in item.find_all(exp.AggFunc)
            ):
                return "missing_group_measure"
            order = select_node.args.get("order")
            if order is not None and any(
                isinstance(column, exp.Column)
                and column.name.casefold() == "matched_count"
                for column in order.find_all(exp.Column)
            ):
                return "group_order_uses_total_count"
    if is_running_total_question(question) and not running_total_found:
        return "missing_running_total_expression"
    folded = question.casefold()
    detail_noun = re.search(
        r"\b(?:attendance|details?|entries|records?|rows?)\b", folded
    )
    detail_verb = re.search(r"\b(?:display|give|list|show)\b", folded)
    aggregate_intent = re.search(
        r"\b(?:average|avg|count|how many|maximum|max|minimum|min|sum|total)\b",
        folded,
    )
    select = re.search(r"\bSELECT\b(.*?)\bFROM\b", sql, re.IGNORECASE | re.DOTALL)
    select_list = select.group(1) if select else ""
    scalar_aggregate = bool(
        re.search(r"\b(?:AVG|COUNT|MAX|MIN|SUM)\s*\(", select_list, re.IGNORECASE)
        and not re.search(r"\bOVER\s*\(", select_list, re.IGNORECASE)
    )
    if (
        detail_noun
        and detail_verb
        and not aggregate_intent
        and scalar_aggregate
        and not re.search(r"\brecord_id\b", select_list, re.IGNORECASE)
    ):
        return "detail_request_requires_rows"
    if not _has_required_date_scope(sql, required_date_scope):
        return "date_scope_mismatch"
    return None


def _repair_group_order(sql: str) -> str:
    """Order bounded groups by their sole measure when total-count sort is inert."""

    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return sql
    if len(statements) != 1 or statements[0] is None:
        return sql
    statement = statements[0]
    changed = False
    for select_node in statement.find_all(exp.Select):
        if not (select_node.args.get("group") and select_node.args.get("limit")):
            continue
        measures = [
            item.alias
            for item in select_node.expressions
            if isinstance(item, exp.Alias)
            and item.alias.casefold() != "matched_count"
            and any(
                aggregate.find_ancestor(exp.Window) is None
                for aggregate in item.find_all(exp.AggFunc)
            )
        ]
        if len(measures) != 1:
            continue
        order = select_node.args.get("order")
        if order is None:
            continue
        for ordered in order.expressions:
            if (
                isinstance(ordered.this, exp.Column)
                and ordered.this.name.casefold() == "matched_count"
            ):
                ordered.set("this", exp.column(measures[0]))
                changed = True
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
    if re.search(r"\b(?:19|20)\d{2}\b", folded):
        return True
    if any(re.search(rf"\b{month}\b", folded) for month in _MONTH_NUMBERS):
        return True
    if re.search(
        r"\b(?:today|yesterday|tomorrow|this|last|previous|next)\s+"
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
    if lower is None or upper is None or lower > upper:
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


def _unsupported_value(locale: str) -> str:
    if locale == "ar":
        return "يحتوي الطلب على تاريخ أو قيمة رقمية غير صالحة. يرجى تصحيحها."
    return "The request contains an invalid date or numeric value. Please correct it."


def _requested_month_scope(question: str) -> tuple[str, str] | None:
    month_pattern = "|".join(_MONTH_NUMBERS)
    match = re.search(
        rf"\b({month_pattern})\s+(\d{{4}})\b", question, flags=re.IGNORECASE
    )
    if match is None:
        return None
    month_name, year_text = match.groups()
    year = int(year_text)
    month = _MONTH_NUMBERS[month_name.casefold()]
    return (
        date(year, month, 1).isoformat(),
        date(year, month, calendar.monthrange(year, month)[1]).isoformat(),
    )


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
            bound = complete_confirmation(pending, selected)
            log_layer_output(
                "employee_confirmation",
                {"status": "selected", "employee": selected.model_dump(mode="json")},
            )
        else:
            if has_malformed_identifier(question, directory):
                log_layer_output(
                    "unsupported",
                    {
                        "capability": "malformed_identifier",
                        "state": previous.model_dump(mode="json"),
                    },
                )
                return Unsupported(
                    reply=_unsupported_identifier(locale),
                    state=previous,
                    capability="malformed_identifier",
                )
            if has_unknown_identifier(question, directory):
                return Clarification(
                    reply=_clarification("unknown_employee_id", locale),
                    state=previous,
                    reason="unknown_employee_id",
                )
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
                    reply=_unsupported_value(locale),
                    state=previous,
                    capability=request_issue,
                )
            active = tuple(
                authoritative[item]
                for item in previous.active_employee_ids
                if item in authoritative
            )
            if previous.verified_turns and is_grouped_month_comparison_question(
                question
            ):
                database_context = deps.context_loader(
                    dsn=settings.postgres_readonly_dsn,
                    attendance_objects=(settings.postgres_attendance_table,),
                    connect_timeout=settings.postgres_connect_timeout_seconds,
                )
                native_comparison = build_grouped_month_comparison(
                    question=question,
                    previous_sql=previous.verified_turns[-1].executed_sql,
                    previous_date_scope=previous.verified_turns[-1].date_scope,
                    as_of_date=as_of_date,
                    database_context=database_context,
                )
            if (
                previous.verified_turns
                and not active
                and is_running_total_question(question)
            ):
                if database_context is None:
                    database_context = deps.context_loader(
                        dsn=settings.postgres_readonly_dsn,
                        attendance_objects=(settings.postgres_attendance_table,),
                        connect_timeout=settings.postgres_connect_timeout_seconds,
                    )
                native_running_total = build_running_total(
                    question=question,
                    previous_sql=previous.verified_turns[-1].executed_sql,
                    previous_date_scope=previous.verified_turns[-1].date_scope,
                    database_context=database_context,
                )
            if native_comparison is not None or native_running_total is not None:
                bound = BoundReferences(
                    rewritten_request=question,
                    updated_request=attach_resolved_employees(question, active),
                    locale=locale,
                    request_relationship="follow_up",
                    subject_relationship="employees" if active else "all_authorized",
                    employees=active,
                )
                if native_comparison is not None:
                    log_layer_output("native_comparison_plan", native_comparison.sql)
                else:
                    log_layer_output(
                        "native_running_total_plan", native_running_total.sql
                    )
            else:
                reference = deps.reference_writer(
                    question,
                    history=conversation_history,
                    trusted_context=previous.trusted_context(),
                    active_employees=active,
                    as_of_date=as_of_date,
                    model=settings.llm_reference_model,
                    budget=budget,
                    timeout=settings.llm_reference_timeout_seconds,
                    max_output_tokens=settings.llm_reference_max_output_tokens,
                    observer=observer,
                )
                if isinstance(reference.decision, UnsupportedReference):
                    deterministic = bind_references(
                        ReferenceResponse(
                            decision=AmbiguousReference(
                                rewritten_request=(
                                    reference.decision.rewritten_request or question
                                ),
                                locale=reference.decision.locale,
                                reason="missing_employee",
                            )
                        ),
                        directory,
                        original_question=question,
                        active_employees=active,
                        has_verified_turns=bool(previous.verified_turns),
                    )
                    if (
                        deterministic.employees
                        or deterministic.confirmation is not None
                        or deterministic.unresolved_mention is not None
                    ):
                        bound = deterministic
                    else:
                        bound = BoundReferences(
                            rewritten_request=(
                                reference.decision.rewritten_request or question
                            ),
                            updated_request=attach_resolved_employees(
                                reference.decision.rewritten_request or question, ()
                            ),
                            locale=reference.decision.locale,
                            request_relationship="new",
                            subject_relationship="all_authorized",
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

        if (
            bound.ambiguous
            and bound.unresolved_mention is None
            and bound.subject_relationship is None
            and not (previous.active_employee_ids and not active)
        ):
            rewritten_request = bound.rewritten_request or question
            bound = BoundReferences(
                rewritten_request=rewritten_request,
                updated_request=attach_resolved_employees(
                    rewritten_request, bound.employees
                ),
                locale=bound.locale,
                request_relationship=bound.request_relationship,
                subject_relationship=(
                    bound.subject_relationship
                    or ("employees" if bound.employees else "all_authorized")
                ),
                employee_criteria=bound.employee_criteria,
                employees=bound.employees,
            )
            log_layer_output("planner_owned_ambiguity", rewritten_request)

        log_layer_output("employee_resolution", bound)

        if bound.confirmation is not None:
            state = previous.model_copy(
                update={"pending_employee_confirmation": bound.confirmation}
            )
            log_layer_output("publication", state)
            return Clarification(
                reply=_confirmation_reply(bound.confirmation, bound.locale),
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
            rewritten_request = bound.rewritten_request or question
            bound = BoundReferences(
                rewritten_request=rewritten_request,
                updated_request=attach_resolved_employees(rewritten_request, ()),
                locale=bound.locale,
                request_relationship=bound.request_relationship,
                subject_relationship="all_authorized",
            )
            log_layer_output("planner_owned_ambiguity", rewritten_request)
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

        explicit_date_scope = _requested_month_scope(question)
        required_date_scope = (
            explicit_date_scope
            if explicit_date_scope is not None
            else previous.verified_turns[-1].date_scope
            if bound.request_relationship == "follow_up"
            and previous.verified_turns
            and not _mentions_time_period(question)
            else None
        )
        if database_context is None:
            database_context = deps.context_loader(
                dsn=settings.postgres_readonly_dsn,
                attendance_objects=(settings.postgres_attendance_table,),
                connect_timeout=settings.postgres_connect_timeout_seconds,
            )
        log_layer_output("database_context", database_context)
        downstream_history = conversation_history
        trusted_context = previous.trusted_context()
        previous_turn = previous.verified_turns[-1] if previous.verified_turns else None
        scope_provenance = {
            "current_original_question": question,
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
                    previous_turn.executed_sql if previous_turn is not None else None
                ),
            },
        }
        downstream_trusted_context = (
            {
                **trusted_context,
                "verified_turns": [
                    {
                        **turn,
                        "executed_sql": previous.verified_turns[-1].executed_sql,
                    }
                    for turn in trusted_context["verified_turns"]
                ],
            }
            if bound.request_relationship == "follow_up"
            else {}
        )
        shared_context = SharedModelContext(
            current_question=question,
            as_of_date=as_of_date,
            updated_request=bound.updated_request,
            previous_verified_turn=(
                {
                    "request": previous.verified_turns[-1].rewritten_request,
                    "sql": previous.verified_turns[-1].executed_sql,
                    "having": _previous_having(
                        previous.verified_turns[-1].executed_sql
                    ),
                    "date_scope": previous.verified_turns[-1].date_scope,
                }
                if bound.request_relationship == "follow_up" and previous.verified_turns
                else None
            ),
            request_relationship=bound.request_relationship,
            subject_relationship=bound.subject_relationship,
            resolved_employee_ids=bound.employee_ids,
            required_date_scope=required_date_scope,
            request_has_date_period=(
                _mentions_time_period(question) or required_date_scope is not None
            ),
            conversation_history=downstream_history,
            trusted_context=downstream_trusted_context,
            scope_provenance=scope_provenance,
            database_context=database_context,
        )
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
                else deps.planner(**planner_args)
            )
            sql = _repair_group_order(sql)
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
                rewritten_request=bound.updated_request,
                allow_rewritten_contracts=(bound.request_relationship == "follow_up"),
                database_context=database_context,
                required_date_scope=required_date_scope,
            )
            if semantic_issue in {
                "nested_result_shape",
                "detail_request_requires_rows",
                "invalid_grouped_matched_count",
                "missing_group_measure",
                "group_order_uses_total_count",
                "invalid_running_total_expression",
                "missing_running_total_expression",
                "running_total_requires_daily_grouping",
                "self_membership_filter",
                "date_scope_mismatch",
            }:
                if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                    raise ProviderFailure(
                        "sql_semantics", semantic_issue, semantic_issue
                    )
                database_error = semantic_issue
                if semantic_issue == "nested_result_shape":
                    database_error = (
                        "Return flat relational columns and bounded rows rather than "
                        "an array aggregate. Include the requested date or detail "
                        "column on each row and COUNT(*) OVER() AS matched_count."
                    )
                elif semantic_issue == "detail_request_requires_rows":
                    database_error = (
                        "The user requested attendance detail rows, but this SQL "
                        "returns only an aggregate. Return bounded matching rows with "
                        "record_id and COUNT(*) OVER() AS matched_count."
                    )
                elif semantic_issue == "invalid_grouped_matched_count":
                    database_error = (
                        "A bounded grouped result must include COUNT(*) OVER() "
                        "AS matched_count for the number of returned groups. "
                        "Do not use COUNT(*) AS matched_count for each group's "
                        "indicator rows; give that measure a distinct alias."
                    )
                elif semantic_issue == "missing_group_measure":
                    database_error = (
                        "The grouped query selects group keys and a total group "
                        "count but no measure for each group. Add the requested "
                        "per-group aggregate, such as COUNT(*) AS indicator_count "
                        "over qualifying rows. Keep COUNT(*) OVER() AS "
                        "matched_count for the number of groups."
                    )
                elif semantic_issue == "group_order_uses_total_count":
                    database_error = (
                        "The grouped query orders by matched_count, which is the "
                        "same total number of groups on every row. Order by the "
                        "requested per-group measure or a meaningful group key."
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
                elif semantic_issue == "missing_running_total_expression":
                    database_error = (
                        "The request asks for a cumulative running total, but the "
                        "SQL returns only daily values. Aggregate the requested "
                        "measure by date, then select SUM(daily_value) OVER "
                        "(ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND "
                        "CURRENT ROW) AS running_total."
                    )
                elif semantic_issue == "running_total_requires_daily_grouping":
                    database_error = (
                        "The cumulative window runs over raw attendance rows, "
                        "so a date can appear more than once. Aggregate the "
                        "requested measure by date first, then apply the ordered "
                        "running SUM to those daily values."
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
                        f"{required_date_scope[0]} through {required_date_scope[1]}."
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
                rewritten_request=bound.updated_request,
                allow_rewritten_contracts=(bound.request_relationship == "follow_up"),
                database_context=database_context,
                required_date_scope=required_date_scope,
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
            if (
                not control_result
                and required_date_scope is not None
                and date_scope != required_date_scope
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
        verified = VerifiedTurn(
            turn_id=uuid4().hex,
            original_question=question,
            rewritten_request=bound.updated_request,
            answer=answer,
            locale=bound.locale,
            employees=bound.employees,
            executed_sql=sql,
            date_scope=date_scope,
            result=result.model_dump(mode="json"),
        )
        new_state = previous.model_copy(
            update={
                "verified_turns": (previous.verified_turns + (verified,))[-50:],
                "active_employee_ids": bound.employee_ids,
                "active_employees": bound.employees,
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
