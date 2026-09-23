"""Semantic audit over immutable flat-query candidates."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .planner import PlannerResponse, ReadyPlan, request_plan
from .provider import CallBudget, ProviderFailure, TurnObserver, call_structured


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class AuditFinding(_Strict):
    code: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=1000)


class AuditPass(_Strict):
    status: Literal["pass"] = "pass"


class AuditReject(_Strict):
    status: Literal["reject"] = "reject"
    findings: tuple[AuditFinding, ...] = Field(min_length=1, max_length=32)


AuditDecision = Annotated[AuditPass | AuditReject, Field(discriminator="status")]


class AuditResponse(_Strict):
    decision: AuditDecision


_SYSTEM = """Audit one immutable flat attendance candidate against the current message,
trusted context, and logical catalog. Reject added, omitted, inverted, or inherited
meaning and unsupported capabilities. Do not rewrite the candidate, emit SQL, or use
physical identifiers. Return only pass or precise findings in the strict response."""


def request_audit(
    *,
    question: str,
    candidate: ReadyPlan,
    trusted_context: dict[str, object],
    catalog_text: str,
    model: str,
    budget: CallBudget,
    timeout: float,
    max_output_tokens: int,
    observer: TurnObserver | None = None,
    final: bool = False,
) -> AuditResponse:
    return call_structured(
        stage="final_audit" if final else "audit",
        model=model,
        system=_SYSTEM,
        payload={
            "current_message": question,
            "candidate": candidate.model_dump(mode="json"),
            "trusted_context": trusted_context,
            "logical_catalog": catalog_text,
            "final": final,
        },
        response_model=AuditResponse,
        budget=budget,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
        observer=observer,
    )


def audit_with_one_repair(
    *,
    initial: PlannerResponse,
    planner_args: dict[str, object],
    audit_args: dict[str, object],
) -> PlannerResponse:
    """Retain a valid initial candidate if audit is unavailable; repaired candidates require final pass."""

    if not isinstance(initial.decision, ReadyPlan):
        return initial
    try:
        verdict = request_audit(candidate=initial.decision, **audit_args)
    except ProviderFailure as exc:
        if exc.code == "provider_unavailable":
            return initial
        raise
    if isinstance(verdict.decision, AuditPass):
        return initial
    repaired_args = dict(planner_args)
    repaired_args["repair"] = {
        "candidate": initial.decision.model_dump(mode="json"),
        "findings": [item.model_dump(mode="json") for item in verdict.decision.findings],
    }
    repaired_args["attempt"] = 2
    repaired = request_plan(**repaired_args)
    if not isinstance(repaired.decision, ReadyPlan):
        raise ProviderFailure("planner", "repair_rejected", "repair did not produce an executable plan")
    final = request_audit(candidate=repaired.decision, final=True, **audit_args)
    if not isinstance(final.decision, AuditPass):
        raise ProviderFailure("final_audit", "semantic_rejection", "final audit rejected the repaired plan")
    return repaired


__all__ = [
    "AuditFinding",
    "AuditPass",
    "AuditReject",
    "AuditResponse",
    "audit_with_one_repair",
    "request_audit",
]
