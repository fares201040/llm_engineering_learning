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
from .answering import Result, generate_answer
from .comparison import (
    build_grouped_month_comparison,
    is_grouped_month_comparison_question,
    render_grouped_month_comparison,
)
from .context import DatabaseContext, SharedModelContext, load_database_context
from .execution import (
    AccessContext,
    AuthorizationError,
    SqlExecutionResult,
    authorize_access,
    execute_sql,
    load_employee_directory,
    search_employee_directory_postgres,
)
from .planner import request_sql
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
    request_references,
    search_employee_candidates,
)
from .running_total import (
    build_running_total,
    is_running_total_question,
    render_running_total,
)
from .state import ConversationState, VerifiedTurn


SQL_EXECUTION_ATTEMPT_LIMIT = 3
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
        answer_writer: Callable[..., str] = generate_answer,
    ):
        self.reference_writer = reference_writer
        self.directory_loader = directory_loader
        self.employee_fallback_search = employee_fallback_search
        self.employee_fuzzy_search = employee_fuzzy_search
        self.context_loader = context_loader
        self.planner = planner
        self.executor = executor
        self.answer_writer = answer_writer


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


def _attendance_meaning(
    question: str,
) -> Literal["explicit_absence", "not_absent"] | None:
    if re.search(
        r"\bexception\s+equal\s+to\s+[\"“][^\"”]+[\"”]",
        question,
        flags=re.IGNORECASE,
    ):
        # The quoted text is an exact schema value, not a request to reinterpret
        # every value containing the word "absence" as the canonical Absent state.
        return None
    if re.search(r"\bnot\s+absent\b", question, flags=re.IGNORECASE):
        return "not_absent"
    if re.search(r"\babsen(?:t|ce)\b", question, flags=re.IGNORECASE):
        return "explicit_absence"
    return None


_MANUAL_SWIPE_PAIRS = frozenset(
    {
        frozenset({"From_Date", "Actual_From_Date"}),
        frozenset({"From_Time", "Actual_From_Time"}),
        frozenset({"To_Date", "Actual_To_Date"}),
        frozenset({"To_Time", "Actual_To_Time"}),
    }
)


def _manual_swipe_intent(*requests: str) -> bool:
    text = "\n".join(requests)
    folded = text.casefold()
    english = re.search(
        r"\b(?:manual(?:ly)?|modified|adjusted|edited|clerk[ -]entered|"
        r"clerk[ -]adjusted)\b",
        folded,
    ) and re.search(r"\b(?:swipe|clock|punch|check[ -]?(?:in|out))s?\b", folded)
    arabic = re.search(
        r"(?:يدوي(?:ا|اً)?|معدل(?:ة|ه)?|تعديل|تعديلات|تغيير)", text
    ) and re.search(r"(?:بصم|دخول|خروج|دوام)", text)
    return bool(english or arabic)


def _unwrap_parentheses(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _flatten_or(node: exp.Expression) -> tuple[exp.Expression, ...]:
    node = _unwrap_parentheses(node)
    if isinstance(node, exp.Or):
        return _flatten_or(node.this) + _flatten_or(node.expression)
    return (node,)


def _manual_swipe_pair(node: exp.Expression) -> frozenset[str] | None:
    node = _unwrap_parentheses(node)
    if not isinstance(node, exp.NullSafeNEQ):
        return None
    names = frozenset(
        re.findall(
            r"\b(?:Actual_)?(?:From|To)_(?:Date|Time)\b",
            node.sql(dialect="postgres"),
        )
    )
    return names if names in _MANUAL_SWIPE_PAIRS else None


def _has_complete_manual_swipe_filter(sql: str) -> bool:
    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return False
    for statement in statements:
        for where in statement.find_all(exp.Where):
            for candidate in where.this.walk():
                if not isinstance(candidate, exp.Or):
                    continue
                leaves = _flatten_or(candidate)
                pairs = tuple(_manual_swipe_pair(leaf) for leaf in leaves)
                if (
                    len(pairs) == 4
                    and None not in pairs
                    and frozenset(pairs) == _MANUAL_SWIPE_PAIRS
                ):
                    return True
    return False


def _sql_semantic_issue(
    question: str, sql: str, *, rewritten_request: str = ""
) -> str | None:
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
    if _manual_swipe_intent(question, rewritten_request) and not (
        _has_complete_manual_swipe_filter(sql)
    ):
        return "incomplete_manual_swipe_comparison"
    meaning = _attendance_meaning(question)
    if meaning == "not_absent" and not re.search(
        r"\bexception\s+IS\s+DISTINCT\s+FROM\s+'Absent'", sql, flags=re.IGNORECASE
    ):
        return "wrong_absence_polarity"
    if meaning == "explicit_absence":
        if not re.search(r"\bexception\s*=\s*'Absent'", sql, re.IGNORECASE):
            return "wrong_absence_semantics"
        where = re.search(
            r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        filters = where.group(1) if where else ""
        if not re.search(r"\b(?:hours?|work(?:ed|ing)?)\b", question, re.IGNORECASE):
            if re.search(r"\btotal_worked_hrs\b", filters, re.IGNORECASE):
                return "wrong_absence_semantics"
        if not re.search(
            r"\b(?:schedul(?:e|ed)|working\s+day)\b", question, re.IGNORECASE
        ):
            if re.search(r"\bday_type\b", filters, re.IGNORECASE):
                return "wrong_absence_semantics"
    return None


def _unrequested_date_filter(sql: str) -> bool:
    """Find attendance-date predicates, including nested or computed bounds."""

    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        return False
    for statement in statements:
        for predicate in statement.find_all(exp.Predicate):
            if any(
                column.name.casefold() == "attendance_date"
                for column in predicate.find_all(exp.Column)
            ):
                return True
    return False


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


def _sql_date_scope(sql: str) -> tuple[str, str] | None:
    where = re.search(
        r"\bWHERE\b(.*?)(?=\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if where is None:
        return None
    filters = where.group(1)
    field = r'\battendance_date"?'
    literal = r"(?:DATE\s*)?'(\d{4}-\d{2}-\d{2})'"
    between = re.search(
        rf"{field}\s+BETWEEN\s+{literal}\s+AND\s+{literal}",
        filters,
        flags=re.IGNORECASE,
    )
    if between:
        start_text, end_text = between.groups()
    else:
        lower = re.search(
            rf"{field}\s*(>=|>)\s*{literal}", filters, flags=re.IGNORECASE
        )
        upper = re.search(
            rf"{field}\s*(<=|<)\s*{literal}", filters, flags=re.IGNORECASE
        )
        if lower is None or upper is None:
            return None
        start_text = lower.group(2)
        end_text = upper.group(2)
        try:
            start = date.fromisoformat(start_text) + timedelta(
                days=1 if lower.group(1) == ">" else 0
            )
            end = date.fromisoformat(end_text) - timedelta(
                days=1 if upper.group(1) == "<" else 0
            )
        except ValueError:
            return None
        start_text, end_text = start.isoformat(), end.isoformat()
    try:
        if date.fromisoformat(start_text) > date.fromisoformat(end_text):
            return None
    except ValueError:
        return None
    return start_text, end_text


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


def _unsupported_domain(locale: str) -> str:
    if locale == "ar":
        return "يدعم هذا المساعد أسئلة الحضور المصرح بها فقط."
    return "This assistant supports authorized attendance questions only."


def _missing_join_target(question: str, database_context: DatabaseContext) -> bool:
    """Recognize an explicit join to a table absent from the allowed schema."""

    match = re.fullmatch(
        r"join\s+(.+?)\s+(?:to|with)\s+(.+?)\s*[.?!]*",
        question.strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        return False

    def known_table(phrase: str) -> bool:
        words = set(re.findall(r"[a-z0-9]+", phrase.casefold()))
        return bool(words) and any(
            words <= set(re.findall(r"[a-z0-9]+", table.table_name.casefold()))
            or words
            == set(
                re.findall(
                    r"[a-z0-9]+",
                    f"{table.schema_name} {table.table_name}".casefold(),
                )
            )
            for table in database_context.tables
        )

    left_known, right_known = (known_table(phrase) for phrase in match.groups())
    return left_known != right_known


def _schema_grounded_analytic_question(
    question: str,
    database_context: DatabaseContext,
    employees: tuple[Employee, ...] = (),
) -> bool:
    """Confirm an independent aggregate request uses only known schema concepts."""

    def normalized_words(value: str) -> set[str]:
        words = re.findall(r"[a-z]+", value.casefold())
        return {
            "hour"
            if word in {"hours", "hrs"}
            else word[:-1]
            if len(word) > 1 and word.endswith("s")
            else word
            for word in words
        }

    words = normalized_words(question)
    if words.intersection({"that", "those", "previous", "last", "instead", "same"}):
        return False
    analytic_words = {
        "rank",
        "ranking",
        "group",
        "grouped",
        "sum",
        "average",
        "avg",
        "count",
        "total",
    }
    if not words.intersection(analytic_words):
        return False
    grammar_words = {
        "a",
        "an",
        "and",
        "are",
        "by",
        "did",
        "do",
        "does",
        "each",
        "for",
        "had",
        "has",
        "have",
        "is",
        "of",
        "per",
        "please",
        "s",
        "show",
        "the",
        "was",
        "were",
        "what",
        "with",
    }
    employee_words = {
        word
        for employee in employees
        for value in (employee.employee_id, employee.name)
        for word in normalized_words(value)
    }
    concepts = words - analytic_words - grammar_words - employee_words
    if len(concepts) < 2:
        return False
    schema_words: set[str] = set()
    for table in database_context.tables:
        schema_words.update(normalized_words(table.table_name.replace("_", " ")))
        for column in table.columns:
            schema_words.update(normalized_words(column.name.replace("_", " ")))
    return concepts <= schema_words


def _attendance_semantic_question(question: str) -> bool:
    """Recognize high-level attendance analysis without treating it as a person."""

    folded = " ".join(re.findall(r"[a-z0-9]+", question.casefold()))
    if re.search(
        r"\b(?:incomplete clocking|repeated lateness|chronic lateness|"
        r"early departures?|absence issues?|overtime behavior|"
        r"unscheduled deviation|hr review)\b",
        folded,
    ):
        return True
    qualifier = re.search(
        r"\b(?:unusual|problematic|abnormal|suspicious|concerning|irregular|"
        r"anomal(?:y|ies|ous)|issues?|incomplete|repeated|chronic|unscheduled|"
        r"deviation|concerns?)\b",
        folded,
    )
    attendance_concept = re.search(
        r"\b(?:attendance|timekeeping|clocking|lateness|late|early departures?|"
        r"overtime|absence|absent|records?|employees?|behavior|patterns?|"
        r"summaries)\b",
        folded,
    )
    return qualifier is not None and attendance_concept is not None


def _grouped_schema_calculation_syntax(question: str) -> bool:
    return (
        re.fullmatch(
            r"\s*(?:what is\s+(?:the\s+)?)?(?:average|avg|sum)\s+(?:of\s+)?"
            r"[A-Za-z_]+\s+by\s+[A-Za-z_]+[?.]?\s*",
            question,
            flags=re.IGNORECASE,
        )
        is not None
    )


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


def _build_native_employee_day_count_sql(
    question: str,
    database_context: DatabaseContext,
    employees: tuple[Employee, ...],
) -> str | None:
    """Build stable scalar counts for common employee/date attendance measures."""

    if not employees or not re.search(r"\bhow many\b", question, re.IGNORECASE):
        return None
    month_scope = _requested_month_scope(question)
    if month_scope is None:
        return None
    table = next(
        (
            item
            for item in database_context.tables
            if item.table_name.casefold().endswith("attendance_records")
        ),
        database_context.tables[0],
    )
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", table.schema_name
    ) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table.table_name):
        return None
    columns = {column.name.casefold(): column.name for column in table.columns}
    required = {"employee_id", "attendance_date", "total_worked_hrs"}
    if not required <= set(columns):
        return None
    relation = f'"{table.schema_name}"."{table.table_name}"'
    ids = ", ".join(
        "'" + employee.employee_id.replace("'", "''") + "'" for employee in employees
    )
    start, end = month_scope
    filters = [
        f"employee_id IN ({ids})",
        f"attendance_date >= '{start}'",
        f"attendance_date <= '{end}'",
    ]
    folded = " ".join(question.casefold().split())
    aggregate = "COUNT(DISTINCT attendance_date)"
    alias = "matched_count"
    if "attendance records" in folded:
        aggregate = "COUNT(*)"
    elif re.search(r"\boff days?\b", folded):
        if "day_type" not in columns:
            return None
        filters.append("day_type IN ('OFF Day', 'OFF Day (ZAS)')")
    elif re.search(r"\babsent\b", folded):
        if "exception" not in columns:
            return None
        filters.append("exception = 'Absent'")
    elif "zero worked hours" in folded:
        filters.append("COALESCE(total_worked_hrs, 0) <= 0")
    elif "did not attend" in folded or re.search(r"\bnot work\b", folded):
        if "day_type" not in columns:
            return None
        filters.extend(
            (
                "day_type = 'Working Day'",
                "COALESCE(total_worked_hrs, 0) <= 0",
            )
        )
    elif re.search(r"\b(?:attend|worked?|work)\b", folded):
        filters.append("COALESCE(total_worked_hrs, 0) > 0")
    else:
        return None
    return f"SELECT {aggregate} AS {alias} FROM {relation} WHERE " + " AND ".join(
        filters
    )


def _build_native_schema_aggregate_sql(
    question: str,
    database_context: DatabaseContext,
    employees: tuple[Employee, ...],
) -> str | None:
    """Build deterministic SQL for explicit schema counts, percentages, and groups."""

    table = next(
        (
            item
            for item in database_context.tables
            if item.table_name.casefold().endswith("attendance_records")
        ),
        database_context.tables[0],
    )
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", table.schema_name
    ) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table.table_name):
        return None
    relation = f'"{table.schema_name}"."{table.table_name}"'
    columns = {
        re.sub(r"[^a-z0-9]", "", column.name.casefold()): column.name
        for column in table.columns
    }

    def column_for(label: str) -> str | None:
        return columns.get(re.sub(r"[^a-z0-9]", "", label.casefold()))

    scope = ""
    if employees:
        ids = ", ".join(
            "'" + employee.employee_id.replace("'", "''") + "'"
            for employee in employees
        )
        scope = f"employee_id IN ({ids})"

    exact = re.fullmatch(
        r"\s*(how many|what percentage of all) attendance records have "
        r"([A-Za-z_ ]+?) equal to [\"“](.+?)[\"”]\?\s*",
        question,
        flags=re.IGNORECASE,
    )
    if exact is not None:
        operation, field_label, value = exact.groups()
        field = column_for(field_label)
        if field is None:
            return None
        literal = value.replace("'", "''")
        predicate = f"{field} = '{literal}'"
        if operation.casefold() == "how many":
            filters = " AND ".join(item for item in (scope, predicate) if item)
            return f"SELECT COUNT(*) AS matched_count FROM {relation} WHERE {filters}"
        where = f" WHERE {scope}" if scope else ""
        return (
            "SELECT ROUND((100.0 * COUNT(*) FILTER (WHERE "
            f"{predicate}) / NULLIF(COUNT(*), 0))::numeric, 2)::double precision "
            f"AS percentage FROM {relation}{where}"
        )

    employee_status_count = re.fullmatch(
        r"\s*how many (authorized|draft) attendance records([^?]*)\?\s*",
        question,
        flags=re.IGNORECASE,
    )
    if employee_status_count is not None:
        status_field = column_for("Status")
        trailing_scope = employee_status_count.group(2).strip()
        if status_field is None or (trailing_scope and not scope):
            return None
        status = employee_status_count.group(1).title()
        filters = " AND ".join(
            item for item in (scope, f"{status_field} = '{status}'") if item
        )
        return f"SELECT COUNT(*) AS matched_count FROM {relation} WHERE {filters}"

    distinct_status_employees = re.fullmatch(
        r"\s*count distinct employees with (authorized|draft) attendance records\.?\s*",
        question,
        flags=re.IGNORECASE,
    )
    if distinct_status_employees is not None:
        employee_field = column_for("Employee_ID")
        status_field = column_for("Status")
        if employee_field is None or status_field is None:
            return None
        status = distinct_status_employees.group(1).title()
        filters = " AND ".join(
            item for item in (scope, f"{status_field} = '{status}'") if item
        )
        return (
            f"SELECT COUNT(DISTINCT {employee_field}) AS matched_count "
            f"FROM {relation} WHERE {filters}"
        )

    threshold = re.fullmatch(
        r"\s*how many attendance records have ([A-Za-z_ ]+?) "
        r"(greater than|more than|less than|at least|at most|above|below) "
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\?\s*",
        question,
        flags=re.IGNORECASE,
    )
    if threshold is not None:
        field_label, comparison, value = threshold.groups()
        field = column_for(field_label)
        if field is None:
            return None
        operator = {
            "greater than": ">",
            "more than": ">",
            "above": ">",
            "less than": "<",
            "below": "<",
            "at least": ">=",
            "at most": "<=",
        }[comparison.casefold()]
        predicate = f"{field} {operator} {value}"
        filters = " AND ".join(item for item in (scope, predicate) if item)
        return f"SELECT COUNT(*) AS matched_count FROM {relation} WHERE {filters}"

    grouped = re.fullmatch(
        r"\s*(?:what is\s+(?:the\s+)?)?(average|avg|sum)\s+"
        r"(?:of\s+)?([A-Za-z_]+)\s+by\s+"
        r"([A-Za-z_]+)[?.]?\s*",
        question,
        flags=re.IGNORECASE,
    )
    if grouped is None:
        return None
    operation, metric_label, group_label = grouped.groups()
    metric = column_for(metric_label)
    group = column_for(group_label)
    if metric is None or group is None:
        return None
    function = "AVG" if operation.casefold() in {"average", "avg"} else "SUM"
    alias_prefix = "average" if function == "AVG" else "total"
    where = f" WHERE {scope}" if scope else ""
    return (
        f"SELECT {group}, {function}({metric}) AS {alias_prefix}_{metric}, "
        f"COUNT(*) OVER() AS matched_count FROM {relation}{where} "
        f"GROUP BY {group} ORDER BY {group} LIMIT 100"
    )


def _build_native_attendance_observation_sql(
    question: str,
    database_context: DatabaseContext,
    employees: tuple[Employee, ...],
) -> str | None:
    """Build stable SQL for qualitative attendance observations and simple statuses."""

    folded = " ".join(question.casefold().split())
    semantic = _attendance_semantic_question(question)
    status_match = re.search(
        r"\b(authorized|draft)(?:\s+attendance)?\s+records?\b", folded
    )
    month_scope = _requested_month_scope(question)
    general_date_detail = bool(
        month_scope
        and re.search(r"\b(?:show|list|give|display)\b", folded)
        and re.search(r"\battendance\b", folded)
    )
    if not semantic and status_match is None and not general_date_detail:
        return None
    table = database_context.tables[0]
    for candidate in database_context.tables:
        if candidate.table_name.casefold().endswith("attendance_records"):
            table = candidate
            break
    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", table.schema_name
    ) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table.table_name):
        return None
    relation = f'"{table.schema_name}"."{table.table_name}"'

    filters: list[str] = []
    if employees:
        literals = ", ".join(
            "'" + employee.employee_id.replace("'", "''") + "'"
            for employee in employees
        )
        filters.append(f"employee_id IN ({literals})")
    if month_scope is not None:
        filters.extend(
            (
                f"attendance_date >= '{month_scope[0]}'",
                f"attendance_date <= '{month_scope[1]}'",
            )
        )
    iso_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", question)
    if iso_date is not None:
        filters.append(f"attendance_date = '{iso_date.group(1)}'")
    if status_match is not None:
        status = "Authorized" if status_match.group(1) == "authorized" else "Draft"
        filters.append(f"status = '{status}'")
    if "working day" in folded:
        filters.append("day_type = 'Working Day'")

    threshold = re.search(
        r"\b(?:more than|greater than|above)\s+(\d+(?:\.\d+)?)\s+"
        r"lateness\s+hours?\b",
        folded,
    )
    if threshold is not None:
        filters.append(f"lateness_hrs > {threshold.group(1)}")
    elif "repeated lateness" in folded or "chronic lateness" in folded:
        filters.append("COALESCE(lateness_hrs, 0) > 0")
    elif "incomplete clocking" in folded:
        filters.append(
            "(exception ILIKE '%Missing In%' OR exception ILIKE '%Missing Out%')"
        )
    elif "early departure" in folded or "early departures" in folded:
        filters.append("COALESCE(early_out_hrs, 0) > 0")
    elif "absence" in folded or "absent" in folded:
        filters.append("exception = 'Absent'")
    elif "overtime" in folded:
        filters.append("COALESCE(ot_not_authorized, 0) > 0")
    elif semantic:
        filters.append(
            "(NULLIF(BTRIM(exception), '') IS NOT NULL "
            "OR COALESCE(lateness_hrs, 0) > 0 "
            "OR COALESCE(early_out_hrs, 0) > 0 "
            "OR COALESCE(overbreak_hrs, 0) > 0 "
            "OR COALESCE(ot_not_authorized, 0) > 0)"
        )
    where = " WHERE " + " AND ".join(filters) if filters else ""

    detail_request = bool(
        re.search(r"\b(?:records?|rows?|details?|entries)\b", folded)
        or (iso_date is not None and re.search(r"\b(?:show|find|which)\b", folded))
        or status_match is not None
        or general_date_detail
    )
    if detail_request:
        return (
            "SELECT record_id, employee_id, name, attendance_date, day_type, status, "
            "exception, total_worked_hrs, lateness_hrs, early_out_hrs, overbreak_hrs, "
            "total_ot, ot_authorized, ot_not_authorized, leave_type, leave_hrs, "
            f"COUNT(*) OVER() AS matched_count FROM {relation}{where} "
            "ORDER BY attendance_date, employee_id, record_id LIMIT 100"
        )

    employee_summary = bool(
        re.search(r"\bemployees?\b", folded)
        or "repeated lateness" in folded
        or "chronic lateness" in folded
        or "early departure" in folded
        or employees
    )
    if employee_summary:
        having = (
            " HAVING COUNT(*) > 1"
            if "repeated lateness" in folded or "chronic lateness" in folded
            else ""
        )
        return (
            "SELECT employee_id, name, COUNT(*) AS indicator_records, "
            "SUM(COALESCE(lateness_hrs, 0)) AS lateness_hours, "
            "SUM(COALESCE(early_out_hrs, 0)) AS early_out_hours, "
            "SUM(COALESCE(overbreak_hrs, 0)) AS overbreak_hours, "
            "SUM(COALESCE(ot_not_authorized, 0)) AS unauthorized_overtime_hours, "
            f"COUNT(*) OVER() AS matched_count FROM {relation}{where} "
            f"GROUP BY employee_id, name{having} "
            "ORDER BY indicator_records DESC, employee_id LIMIT 100"
        )

    indicator = (
        "CASE WHEN NULLIF(BTRIM(exception), '') IS NOT NULL "
        "AND UPPER(BTRIM(exception)) <> 'OK' THEN exception "
        "ELSE 'Metric indicator' END"
    )
    return (
        f"SELECT {indicator} AS attendance_indicator, "
        f"COUNT(*) AS occurrence_count, COUNT(*) OVER() AS matched_count "
        f"FROM {relation}{where} GROUP BY {indicator} "
        "ORDER BY occurrence_count DESC, attendance_indicator LIMIT 100"
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
        index = -1
    if 0 <= index < len(pending.options):
        return "selected", pending.options[index]
    matches = [
        option
        for option in pending.options
        if normalized
        in {
            option.employee_id.casefold(),
            " ".join(option.employee_name.casefold().split()),
        }
    ]
    return ("selected", matches[0]) if len(matches) == 1 else ("invalid", None)


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
        if len(verified) == 5:
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
    locale = _locale(request.question)
    budget = CallBudget(limit=settings.llm_turn_provider_call_limit)
    question = request.question
    as_of_date = date.today().isoformat()
    try:
        native_comparison = None
        native_running_total = None
        native_attendance_observation = None
        native_employee_day_count = None
        native_schema_aggregate = None
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
        if pending is not None:
            locale = pending.resolution.locale
            status, selected_option = _pending_response(request.question, pending)
            if status == "invalid":
                log_layer_output(
                    "employee_confirmation",
                    {"status": "invalid", "pending": pending.model_dump(mode="json")},
                )
                return Clarification(
                    reply=_confirmation_reply(pending, locale),
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
            if _grouped_schema_calculation_syntax(question):
                database_context = deps.context_loader(
                    dsn=settings.postgres_readonly_dsn,
                    attendance_objects=(settings.postgres_attendance_table,),
                    connect_timeout=settings.postgres_connect_timeout_seconds,
                )
                if (
                    _build_native_schema_aggregate_sql(question, database_context, ())
                    is None
                ):
                    log_layer_output(
                        "unsupported",
                        {
                            "capability": "unsupported_calculation",
                            "reason": "unknown_schema_metric_or_group",
                        },
                    )
                    return Unsupported(
                        reply=_unsupported_value(locale),
                        state=previous,
                        capability="unsupported_calculation",
                    )
            if re.match(r"\s*join\b", question, flags=re.IGNORECASE):
                database_context = deps.context_loader(
                    dsn=settings.postgres_readonly_dsn,
                    attendance_objects=(settings.postgres_attendance_table,),
                    connect_timeout=settings.postgres_connect_timeout_seconds,
                )
                if _missing_join_target(question, database_context):
                    reply = (
                        "جدول الربط المطلوب غير موجود في مخطط الحضور المصرح به."
                        if locale == "ar"
                        else "The requested join target is not in the authorized attendance schema."
                    )
                    log_layer_output(
                        "unsupported",
                        {"capability": "schema", "reason": "missing_join_target"},
                    )
                    return Unsupported(reply=reply, state=previous, capability="schema")
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
                    history=request.history[-2:],
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
                    database_context = deps.context_loader(
                        dsn=settings.postgres_readonly_dsn,
                        attendance_objects=(settings.postgres_attendance_table,),
                        connect_timeout=settings.postgres_connect_timeout_seconds,
                    )
                    explicit_schema_aggregate = (
                        _build_native_schema_aggregate_sql(
                            question, database_context, ()
                        )
                        is not None
                    )
                    deterministic = bind_references(
                        ReferenceResponse(
                            decision=AmbiguousReference(
                                rewritten_request=question,
                                locale=reference.decision.locale,
                                reason="missing_employee",
                            )
                        ),
                        directory,
                        original_question=question,
                        active_employees=active,
                        has_verified_turns=bool(previous.verified_turns),
                    )
                    if deterministic.employees and (
                        _build_native_schema_aggregate_sql(
                            question, database_context, deterministic.employees
                        )
                        is not None
                        or _schema_grounded_analytic_question(
                            question, database_context, deterministic.employees
                        )
                        or _attendance_semantic_question(question)
                    ):
                        bound = deterministic
                        log_layer_output("schema_grounded_reference", question)
                    elif (
                        explicit_schema_aggregate
                        or _schema_grounded_analytic_question(
                            question, database_context
                        )
                        or _attendance_semantic_question(question)
                    ):
                        bound = BoundReferences(
                            rewritten_request=question,
                            updated_request=attach_resolved_employees(question, ()),
                            locale=locale,
                            request_relationship="new",
                            subject_relationship="all_authorized",
                        )
                        log_layer_output("schema_grounded_reference", question)
                    else:
                        log_layer_output(
                            "unsupported",
                            {
                                "capability": reference.decision.capability,
                                "rewritten_request": reference.decision.rewritten_request,
                            },
                        )
                        return Unsupported(
                            reply=_unsupported_domain(reference.decision.locale),
                            state=previous,
                            capability=reference.decision.capability,
                        )
                else:
                    bound = bind_references(
                        reference,
                        directory,
                        original_question=question,
                        active_employees=active,
                        has_verified_turns=bool(previous.verified_turns),
                    )

        bound = bound.model_copy(update={"locale": _locale(question)})

        if bound.ambiguous:
            if database_context is None:
                database_context = deps.context_loader(
                    dsn=settings.postgres_readonly_dsn,
                    attendance_objects=(settings.postgres_attendance_table,),
                    connect_timeout=settings.postgres_connect_timeout_seconds,
                )
            native_general_observation = _build_native_attendance_observation_sql(
                question, database_context, ()
            )
            if _build_native_schema_aggregate_sql(
                question, database_context, ()
            ) is not None or (
                bound.reason == "missing_employee"
                and bound.unresolved_mention is None
                and native_general_observation is not None
            ):
                bound = BoundReferences(
                    rewritten_request=question,
                    updated_request=attach_resolved_employees(question, ()),
                    locale=bound.locale,
                    request_relationship="new",
                    subject_relationship="all_authorized",
                )
                log_layer_output("schema_grounded_reference", question)

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
            semantic_options: tuple[EmployeeOption, ...] = ()
            try:
                semantic_options = deps.employee_fallback_search(
                    bound.unresolved_mention,
                    directory,
                    embedding_model=settings.embedding_model,
                    embedding_provider=settings.embedding_provider,
                    collection_name=settings.chroma_collection_name,
                    allowed_employee_ids=allowed_employee_ids,
                )
                log_layer_output("employee_chroma_fallback", semantic_options)
                _emit(
                    observer,
                    "employee_fallback",
                    "completed",
                    f"candidates={len(semantic_options)}",
                )
            except Exception as exc:
                _emit(observer, "employee_fallback", "unavailable", type(exc).__name__)
            combined = semantic_options[:5]
            if bound.fallback_options:
                combined = semantic_options[:4] + bound.fallback_options
            options = _verified_options(combined, directory)
            if options and bound.pending_resolution is not None:
                pending = PendingEmployeeConfirmation(
                    original_question=question,
                    mention=bound.unresolved_mention,
                    options=options,
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

        if bound.request_relationship == "new" and not _mentions_time_period(question):
            # The reference model may resolve a nonexistent relative date against
            # today. For an independent request with no time expression, preserve
            # the user's scope verbatim while retaining only authoritative identity
            # resolution.
            bound = bound.model_copy(
                update={
                    "rewritten_request": question,
                    "updated_request": attach_resolved_employees(
                        question, bound.employees
                    ),
                }
            )

        required_date_scope = (
            previous.verified_turns[-1].date_scope
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
        downstream_history = (
            tuple(item for item in request.history[-2:] if item.get("role") == "user")
            if bound.request_relationship == "follow_up"
            else ()
        )
        downstream_trusted_context = (
            previous.trusted_context()
            if bound.request_relationship == "follow_up"
            else {}
        )
        shared_context = SharedModelContext(
            current_question=question,
            as_of_date=as_of_date,
            updated_request=(
                "Immediately previous verified request:\n"
                f"{previous.verified_turns[-1].rewritten_request}\n"
                "Its verified SQL defines the grouping and eligibility:\n"
                f"{previous.verified_turns[-1].executed_sql}\n"
                "Previous HAVING clause, if still applicable:\n"
                f"{_previous_having(previous.verified_turns[-1].executed_sql)}\n"
                "Current follow-up change:\n"
                f"{bound.updated_request}"
                if bound.request_relationship == "follow_up" and previous.verified_turns
                else bound.updated_request
            ),
            request_relationship=bound.request_relationship,
            subject_relationship=bound.subject_relationship,
            resolved_employee_ids=bound.employee_ids,
            required_date_scope=required_date_scope,
            request_has_date_period=(
                _mentions_time_period(question) or required_date_scope is not None
            ),
            attendance_meaning=_attendance_meaning(question),
            conversation_history=downstream_history,
            trusted_context=downstream_trusted_context,
            database_context=database_context,
        )
        native_attendance_observation = _build_native_attendance_observation_sql(
            question,
            database_context,
            bound.employees,
        )
        native_employee_day_count = _build_native_employee_day_count_sql(
            question,
            database_context,
            bound.employees,
        )
        native_schema_aggregate = _build_native_schema_aggregate_sql(
            question,
            database_context,
            bound.employees,
        )
        if native_schema_aggregate is not None:
            log_layer_output("native_schema_aggregate_plan", native_schema_aggregate)
        if native_employee_day_count is not None:
            log_layer_output(
                "native_employee_day_count_plan", native_employee_day_count
            )
        if native_attendance_observation is not None:
            log_layer_output(
                "native_attendance_observation_plan",
                native_attendance_observation,
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
                else native_schema_aggregate
                if native_schema_aggregate is not None and attempt == 1
                else native_employee_day_count
                if native_employee_day_count is not None and attempt == 1
                else native_attendance_observation
                if native_attendance_observation is not None and attempt == 1
                else deps.planner(**planner_args)
            )
            log_layer_output("sql_planner", sql, attempt=attempt)
            if not shared_context.request_has_date_period and _unrequested_date_filter(
                sql
            ):
                issue = "unrequested_date_filter"
                if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                    raise ProviderFailure("sql_semantics", issue, issue)
                sql_execution_failure = {
                    "retry_number": attempt,
                    "failed_sql": sql,
                    "error_type": "sql_semantics",
                    "database_error": (
                        "The user did not request a date interval, but this SQL filters "
                        "attendance_date. Remove the invented date restriction and "
                        "answer over all authorized rows."
                    ),
                }
                log_layer_output(
                    "sql_execution_failure", sql_execution_failure, attempt=attempt
                )
                continue
            semantic_issue = _sql_semantic_issue(
                question, sql, rewritten_request=bound.updated_request
            )
            if semantic_issue in {
                "detail_request_requires_rows",
                "incomplete_manual_swipe_comparison",
            }:
                if attempt == SQL_EXECUTION_ATTEMPT_LIMIT:
                    raise ProviderFailure(
                        "sql_semantics", semantic_issue, semantic_issue
                    )
                database_error = semantic_issue
                if semantic_issue == "detail_request_requires_rows":
                    database_error = (
                        "The user requested attendance detail rows, but this SQL "
                        "returns only an aggregate. Return bounded matching rows with "
                        "record_id and COUNT(*) OVER() AS matched_count."
                    )
                elif semantic_issue == "incomplete_manual_swipe_comparison":
                    database_error = (
                        "Manual swipe detection must compare all four payroll-effective "
                        "From_Date, From_Time, To_Date, and To_Time values with their "
                        "corresponding Actual_* device values using IS DISTINCT FROM, "
                        "joining all four comparisons with OR."
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
                )
                break
            except (psycopg.ProgrammingError, psycopg.DataError) as exc:
                log_layer_failure("sql_execution", "query_rejected", exc)
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
        log_layer_output("sql_execution", result)
        semantic_issue = _sql_semantic_issue(
            question, sql, rewritten_request=bound.updated_request
        )
        if semantic_issue is not None:
            raise ProviderFailure("sql_semantics", semantic_issue, semantic_issue)
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
        if required_date_scope is not None and date_scope != required_date_scope:
            raise ProviderFailure(
                "sql_semantics", "date_scope_mismatch", "date_scope_mismatch"
            )
        unsupported_reason = _unsupported_schema_reason(result)
        if unsupported_reason is not None:
            log_layer_output(
                "unsupported",
                {"capability": "schema", "reason": unsupported_reason},
            )
            return Unsupported(
                reply=unsupported_reason,
                state=previous,
                capability="schema",
            )
        if native_comparison_used:
            answer = render_grouped_month_comparison(
                native_comparison, result, locale=bound.locale
            )
        elif native_running_total_used:
            answer = render_running_total(
                native_running_total, result, locale=bound.locale
            )
        else:
            answer = deps.answer_writer(
                shared_context=shared_context,
                sql=sql,
                result=result,
                employees=bound.employees,
                locale=bound.locale,
                model=settings.llm_answer_model,
                budget=budget,
                timeout=settings.llm_answer_timeout_seconds,
                max_output_tokens=settings.llm_answer_max_output_tokens,
                observer=observer,
            )
        log_layer_output("answer", answer)
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
