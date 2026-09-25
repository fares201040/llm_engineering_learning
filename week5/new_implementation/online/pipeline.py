"""Direct-SQL attendance turn pipeline with atomic state publication."""

from __future__ import annotations

from datetime import date, timedelta
import json
import math
import re
from typing import Annotated, Callable, Literal
from uuid import uuid4

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from .answering import Result, generate_answer
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
    Employee,
    EmployeeOption,
    PendingEmployeeConfirmation,
    ReferenceResponse,
    UnsupportedReference,
    bind_references,
    complete_confirmation,
    has_malformed_identifier,
    request_references,
    search_employee_candidates,
)
from .state import ConversationState, VerifiedTurn


SQL_EXECUTION_ATTEMPT_LIMIT = 2
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
    if re.search(r"\bnot\s+absent\b", question, flags=re.IGNORECASE):
        return "not_absent"
    if re.search(r"\babsen(?:t|ce)\b", question, flags=re.IGNORECASE):
        return "explicit_absence"
    return None


def _sql_semantic_issue(question: str, sql: str) -> str | None:
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


def _request_value_issue(question: str) -> str | None:
    """Return a typed issue for literals PostgreSQL may accept misleadingly."""

    for match in re.finditer(r"\b\d{4}-\d{1,2}-\d{1,2}\b", question):
        try:
            date.fromisoformat(match.group(0))
        except ValueError:
            return "malformed_value"

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

    if re.search(r"\b(?:nan|[+-]?infinity|[+-]?inf)\b", question, re.IGNORECASE):
        return "malformed_value"

    comparison_pattern = re.compile(
        r"\b(?:greater than|more than|less than|at least|at most|above|below|over|under)"
        r"\s+([^,.!?]*?)(?=\s+hours?\b|[,.!?]|$)",
        re.IGNORECASE,
    )
    for match in comparison_pattern.finditer(question):
        operand = " ".join(match.group(1).casefold().split())
        first_operand = operand.split(maxsplit=1)[0] if operand else ""
        if first_operand in _NUMBER_WORDS:
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
            active = tuple(
                authoritative[item]
                for item in previous.active_employee_ids
                if item in authoritative
            )
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

        required_date_scope = (
            previous.verified_turns[-1].date_scope
            if bound.request_relationship == "follow_up"
            and previous.verified_turns
            and not _mentions_time_period(question)
            else None
        )
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
            sql = deps.planner(**planner_args)
            log_layer_output("sql_planner", sql, attempt=attempt)
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
        semantic_issue = _sql_semantic_issue(question, sql)
        if semantic_issue is not None:
            raise ProviderFailure("sql_semantics", semantic_issue, semantic_issue)
        date_scope = _sql_date_scope(sql)
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
