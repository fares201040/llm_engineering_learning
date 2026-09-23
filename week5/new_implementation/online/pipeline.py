"""Single attendance-online/v1 turn pipeline and atomic state publication."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Callable, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from .answering import GroundedFact, Result, facts_from_execution, facts_from_narrative, generate_answer
from .audit import audit_with_one_repair
from .catalog import ATTENDANCE_CATALOG, render_provider_catalog
from .execution import (
    AccessContext,
    AuthorizationError,
    bind_query,
    execute_postgres,
    load_employee_directory,
    retrieve_narrative_postgres,
)
from .planner import AmbiguousPlan, ReadyPlan, UnsupportedPlan, request_valid_plan
from .provider import CallBudget, ProviderFailure, StageEvent, TurnObserver
from .query import MaterializedQuery, QueryLimits, materialize_query
from .reference import BoundReferences, Employee, ReferenceResponse, bind_references, request_references
from .state import ConversationState, VerifiedTurn


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, arbitrary_types_allowed=True)


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


TurnOutcome = Annotated[Answered | Clarification | Unsupported | Failed, Field(discriminator="kind")]


class RuntimeDependencies:
    """Small injectable seam used by deterministic tests and local acceptance."""

    def __init__(
        self,
        *,
        reference_writer: Callable[..., ReferenceResponse] = request_references,
        directory_loader: Callable[..., tuple[Employee, ...]] = load_employee_directory,
        planner: Callable[..., object] = request_valid_plan,
        executor: Callable[..., object] = execute_postgres,
        narrative_retriever: Callable[..., tuple[Result, ...]] = retrieve_narrative_postgres,
        answer_writer: Callable[..., str] = generate_answer,
    ):
        self.reference_writer = reference_writer
        self.directory_loader = directory_loader
        self.planner = planner
        self.executor = executor
        self.narrative_retriever = narrative_retriever
        self.answer_writer = answer_writer


DEPENDENCIES = RuntimeDependencies()
LIMITS = QueryLimits(max_result_rows=min(settings.max_exact_results, 1000))


def _locale(question: str) -> Literal["en", "ar"]:
    return "ar" if any("\u0600" <= char <= "\u06ff" for char in question) else "en"


def _clarification(reason: str, locale: str) -> str:
    if locale == "ar":
        return {
            "missing_employee": "يرجى تحديد الموظف بالاسم الكامل أو الرقم الوظيفي.",
            "ambiguous_reference": "يرجى تحديد الموظف بالاسم الكامل أو الرقم الوظيفي.",
            "missing_period": "يرجى تحديد الفترة المطلوبة.",
            "missing_result": "ما نتيجة الحضور التي تريدها؟",
        }.get(reason, "يرجى توضيح طلب الحضور.")
    return {
        "missing_employee": "Please identify the employee by exact name or employee ID.",
        "ambiguous_reference": "Please identify the employee by exact name or employee ID.",
        "missing_period": "Please specify the requested period.",
        "missing_result": "Which attendance result do you want?",
    }.get(reason, "Please clarify the attendance request.")


def _unsupported(capability: str, locale: str) -> str:
    if locale == "ar":
        return f"هذا الطلب غير مدعوم في استعلام حضور واحد ({capability})."
    return f"That capability is not supported in one attendance query ({capability})."


def _failed(locale: str) -> str:
    return "تعذر إكمال هذا الطلب بأمان. يرجى المحاولة مرة أخرى." if locale == "ar" else "I couldn't complete that request safely. Please try again."


def _confirmation_reply(bound: BoundReferences, locale: str) -> str:
    pending = bound.confirmation
    assert pending is not None
    if locale == "ar":
        return f"هل تقصد {pending.employee_name} ({pending.employee_id})؟"
    return f"Did you mean {pending.employee_name} ({pending.employee_id})?"


def _evidence_from_facts(facts: tuple[GroundedFact, ...]) -> tuple[Result, ...]:
    return tuple(
        Result(
            page_content="\n".join(f"{atom.role}: {atom.value}" for atom in fact.atoms),
            metadata={"fact_id": fact.fact_id, "kind": fact.kind},
        )
        for fact in facts
    )


def _today() -> date:
    from datetime import datetime

    return datetime.now(ZoneInfo(settings.app_timezone)).date()


def _emit(observer: TurnObserver | None, stage: str, status: str, detail: str | None = None) -> None:
    if observer is not None:
        observer(StageEvent(stage=stage, status=status, detail=detail))


def run_turn(
    request: TurnRequest,
    *,
    observer: TurnObserver | None = None,
    dependencies: RuntimeDependencies | None = None,
) -> TurnOutcome:
    deps = dependencies or DEPENDENCIES
    previous = ConversationState.from_untrusted(request.state)
    locale = _locale(request.question)
    budget = CallBudget()
    question = request.question
    forced_employee_ids: tuple[str, ...] = ()
    pending = previous.pending_employee_confirmation
    try:
        if pending is not None:
            response = " ".join(request.question.casefold().split())
            if response in {"yes", "y", "correct", "confirm", "نعم", "صحيح", "أجل"}:
                question = pending.original_question
                forced_employee_ids = (pending.employee_id,)
                working = previous.model_copy(update={"pending_employee_confirmation": None})
            elif response in {"no", "n", "لا", "غير صحيح"}:
                state = previous.model_copy(update={"pending_employee_confirmation": None})
                return Clarification(reply=_clarification("ambiguous_reference", locale), state=state, reason="ambiguous_reference")
            else:
                return Clarification(reply=_confirmation_reply(BoundReferences(employee_ids=(), confirmation=pending), locale), state=previous, reason="employee_confirmation")
        else:
            working = previous

        directory = deps.directory_loader(
            dsn=settings.postgres_readonly_dsn,
            table=settings.postgres_attendance_table,
            connect_timeout=settings.postgres_connect_timeout_seconds,
        )
        if forced_employee_ids:
            bound_references = BoundReferences(employee_ids=forced_employee_ids)
        else:
            reference = deps.reference_writer(
                question,
                active_employee_ids=working.active_employee_ids,
                model=settings.llm_reference_model,
                budget=budget,
                timeout=settings.llm_reference_timeout_seconds,
                max_output_tokens=settings.llm_reference_max_output_tokens,
                observer=observer,
            )
            bound_references = bind_references(question, reference, directory)
        if bound_references.confirmation is not None:
            state = working.model_copy(update={"pending_employee_confirmation": bound_references.confirmation})
            return Clarification(reply=_confirmation_reply(bound_references, locale), state=state, reason="employee_confirmation")
        if bound_references.ambiguous:
            return Clarification(reply=_clarification("ambiguous_reference", locale), state=working, reason="ambiguous_reference")

        planner_args: dict[str, object] = {
            "question": question,
            "employee_ids": bound_references.employee_ids,
            "trusted_context": working.trusted_context(),
            "catalog": ATTENDANCE_CATALOG,
            "limits": LIMITS,
            "model": settings.llm_planner_model,
            "budget": budget,
            "timezone": settings.app_timezone,
            "current_date": _today(),
            "timeout": settings.llm_planner_timeout_seconds,
            "max_output_tokens": settings.llm_planner_max_output_tokens,
            "observer": observer,
        }
        planned = deps.planner(**planner_args)
        decision = planned.decision
        if isinstance(decision, AmbiguousPlan):
            return Clarification(reply=_clarification(decision.reason, decision.locale), state=working, reason=decision.reason)
        if isinstance(decision, UnsupportedPlan):
            return Unsupported(reply=_unsupported(decision.capability, decision.locale), state=previous, capability=decision.capability)
        assert isinstance(decision, ReadyPlan)
        materialized: MaterializedQuery | None = None
        if decision.narrative_search is None:
            materialized = materialize_query(
                message=question,
                relationship=decision.relationship,
                base_turn_id=decision.base_turn_id,
                retained_component_ids=decision.retained_component_ids,
                current_filters=decision.filters,
                current_output=decision.output,
                trusted_components=working.trusted_components(),
                catalog=ATTENDANCE_CATALOG,
                limits=LIMITS,
            )
        audit_args = {
            "question": question,
            "trusted_context": working.trusted_context(),
            "catalog_text": render_provider_catalog(),
            "model": settings.llm_plan_audit_model,
            "budget": budget,
            "timeout": settings.llm_plan_audit_timeout_seconds,
            "max_output_tokens": settings.llm_plan_audit_max_output_tokens,
            "observer": observer,
        }
        planned = audit_with_one_repair(initial=planned, planner_args=planner_args, audit_args=audit_args)
        decision = planned.decision
        assert isinstance(decision, ReadyPlan)
        if decision.narrative_search is not None:
            access = request.access_context
            if access is None:
                raise AuthorizationError("attendance access is required")
            evidence = deps.narrative_retriever(
                decision.narrative_search,
                employee_ids=bound_references.employee_ids,
                access=access,
                dsn=settings.postgres_readonly_dsn,
                chunks_table=settings.postgres_chunks_table,
                embedding_model=settings.embedding_model,
                limit=settings.final_k,
            )
            facts = facts_from_narrative(evidence)
            components = ()
            result_summary = {"kind": "narrative", "record_ids": [item.metadata.get("record_id") for item in evidence]}
        else:
            materialized = materialize_query(
                message=question,
                relationship=decision.relationship,
                base_turn_id=decision.base_turn_id,
                retained_component_ids=decision.retained_component_ids,
                current_filters=decision.filters,
                current_output=decision.output,
                trusted_components=working.trusted_components(),
                catalog=ATTENDANCE_CATALOG,
                limits=LIMITS,
            )
            bound = bind_query(materialized.query, bound_references.employee_ids, request.access_context, limits=LIMITS)
            result = deps.executor(
                bound,
                dsn=settings.postgres_readonly_dsn,
                table=settings.postgres_attendance_table,
                connect_timeout=settings.postgres_connect_timeout_seconds,
                result_limit=LIMITS.max_result_rows,
            )
            facts = facts_from_execution(bound, result)
            evidence = _evidence_from_facts(facts)
            components = materialized.components
            result_summary = result.model_dump(mode="json")
        answer = deps.answer_writer(
            question=question,
            facts=facts,
            history=request.history,
            locale=decision.locale,
            writer_model=settings.llm_answer_model,
            verifier_model=settings.llm_answer_verifier_model,
            budget=budget,
            timeout=settings.llm_answer_timeout_seconds,
            max_output_tokens=settings.llm_answer_max_output_tokens,
            observer=observer,
        )
        verified = VerifiedTurn(
            turn_id=uuid4().hex,
            question=question,
            answer=answer,
            locale=decision.locale,
            employee_ids=bound_references.employee_ids,
            components=components,
            result=result_summary,
        )
        new_state = working.model_copy(
            update={
                "verified_turns": (working.verified_turns + (verified,))[-50:],
                "active_employee_ids": bound_references.employee_ids or working.active_employee_ids,
                "pending_employee_confirmation": None,
            }
        )
        _emit(observer, "publication", "completed")
        return Answered(reply=answer, evidence=evidence, state=new_state)
    except AuthorizationError as exc:
        _emit(observer, "failure", "authorization", str(exc))
        return Failed(reply=_failed(locale), state=previous, code="authorization_failed")
    except ProviderFailure as exc:
        _emit(observer, "failure", exc.code, str(exc))
        return Failed(reply=_failed(locale), state=previous, code=exc.code)
    except Exception as exc:
        _emit(observer, "failure", "internal_error", type(exc).__name__)
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
