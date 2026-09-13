"""Compile untrusted planner proposals into verified executable plans."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

try:
    from .attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        AnswerContract,
        EvidenceOrigin,
        ExecutableQueryPlan,
        FilterCondition,
        FilterOperator,
        PlannerProposal,
        QueryPlan,
    )
    from .semantic_resolution import (
        ResolutionContext,
        ResolverRegistry,
        SemanticFact,
        evidence_occurs,
        normalize_semantic_text,
    )
except ImportError:  # Direct execution from week5/new_implementation.
    from attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        AnswerContract,
        EvidenceOrigin,
        ExecutableQueryPlan,
        FilterCondition,
        FilterOperator,
        PlannerProposal,
        QueryPlan,
    )
    from semantic_resolution import (
        ResolutionContext,
        ResolverRegistry,
        SemanticFact,
        evidence_occurs,
        normalize_semantic_text,
    )


ViolationCode = Literal[
    "invalid_schema",
    "ungrounded_constraint",
    "uncovered_fact",
    "contradiction",
    "answer_contract_mismatch",
    "unsupported_capability",
    "ambiguous_value",
]


@dataclass(frozen=True)
class CompilationContext:
    question: str
    facts: tuple[SemanticFact, ...]
    resolution_context: ResolutionContext


@dataclass(frozen=True)
class PlanViolation:
    code: ViolationCode
    target: str
    message: str
    clarification_possible: bool = False


@dataclass(frozen=True)
class ConstraintProvenance:
    target_kind: str
    field: str | None = None
    operator: FilterOperator | None = None
    values: tuple[str | float, ...] = ()
    name: str | None = None
    direction: Literal["asc", "desc"] | None = None
    origin: EvidenceOrigin = "question"
    evidence_text: str = ""


@dataclass(frozen=True)
class InvariantContext:
    compilation: CompilationContext
    proposal: PlannerProposal | None
    candidate_plan: QueryPlan
    provenance: tuple[ConstraintProvenance, ...]


class ConstraintCandidateData(BaseModel):
    field: str
    value: str
    label: str | None = None


class PendingConstraintData(BaseModel):
    field: str
    reference: str
    candidates: list[ConstraintCandidateData] = Field(default_factory=list)


@dataclass(frozen=True)
class PlanCompilationResult:
    executable_plan: ExecutableQueryPlan | None
    provenance: tuple[ConstraintProvenance, ...]
    violations: tuple[PlanViolation, ...]
    clarification: PendingConstraintData | None = None

    @property
    def ready(self) -> bool:
        return self.executable_plan is not None and not self.violations


class PlanInvariant(ABC):
    @abstractmethod
    def check(self, context: InvariantContext) -> tuple[PlanViolation, ...]:
        raise NotImplementedError


def _field_role_allowed(field: str, role: str, origin: EvidenceOrigin) -> bool:
    definition = FIELD_DEFINITIONS.get(field)
    if definition is None:
        return False
    if not definition.planner_visible and origin != "deterministic_default":
        return False
    return bool(getattr(definition, role, False))


class SchemaInvariant(PlanInvariant):
    def check(self, context):
        violations = []
        role_by_kind = {
            "filter": "filterable",
            "group_by": "groupable",
            "order_by": "orderable",
            "calculation": "aggregatable",
        }
        for item in context.provenance:
            if item.field is None or item.target_kind not in role_by_kind:
                continue
            if not _field_role_allowed(
                item.field, role_by_kind[item.target_kind], item.origin
            ):
                violations.append(
                    PlanViolation(
                        "invalid_schema",
                        item.field,
                        f"Field {item.field!r} is not valid for {item.target_kind}.",
                    )
                )
                continue
            if item.operator is not None and item.operator not in FIELD_DEFINITIONS[item.field].operators:
                violations.append(
                    PlanViolation(
                        "invalid_schema",
                        item.field,
                        f"Operator {item.operator!r} is not valid for {item.field!r}.",
                    )
                )
        return tuple(violations)


def _fact_matches_provenance(fact: SemanticFact, item: ConstraintProvenance) -> bool:
    if fact.origin != item.origin:
        return False
    if not evidence_occurs(fact.evidence_text, item.evidence_text) and not evidence_occurs(
        item.evidence_text, fact.evidence_text
    ):
        return False
    if item.target_kind == "filter":
        if item.name is not None and fact.kind == "predicate":
            return fact.concept_name == item.name
        return (
            fact.kind == "filter"
            and fact.field == item.field
            and fact.operator == item.operator
            and set(fact.values) == set(item.values)
        )
    if item.target_kind in {"measure", "predicate"}:
        return fact.kind == item.target_kind and fact.concept_name == item.name
    if item.target_kind in {"group_by", "order_by", "calculation"}:
        return fact.field == item.field and fact.kind in {item.target_kind, "field"}
    if item.target_kind == "entity":
        return fact.kind == "entity" and set(fact.values) == set(item.values)
    if item.target_kind == "limit":
        return fact.kind == "limit" and fact.values == item.values
    return False


class GroundingInvariant(PlanInvariant):
    def check(self, context):
        violations = []
        for index, item in enumerate(context.provenance):
            if item.origin == "deterministic_default":
                continue
            evidence_present = evidence_occurs(
                context.compilation.question, item.evidence_text
            ) or item.origin == "trusted_state"
            grounded = any(
                fact.strength == "strong" and _fact_matches_provenance(fact, item)
                for fact in context.compilation.facts
            )
            if item.target_kind == "entity" and evidence_present:
                grounded = True
            if item.origin == "trusted_state":
                grounded = True
            if not evidence_present or not grounded:
                violations.append(
                    PlanViolation(
                        "ungrounded_constraint",
                        f"{item.target_kind}:{index}",
                        "The proposed constraint is not supported by independent facts.",
                    )
                )
        return tuple(violations)


class CoverageInvariant(PlanInvariant):
    def check(self, context):
        violations = []
        relevant_kinds = {
            "filter",
            "measure",
            "predicate",
            "calculation",
            "group_by",
            "order_by",
            "limit",
            "entity",
        }
        for index, fact in enumerate(context.compilation.facts):
            if fact.strength != "strong" or fact.kind not in relevant_kinds:
                continue
            if not any(_fact_matches_provenance(fact, item) for item in context.provenance):
                violations.append(
                    PlanViolation(
                        "uncovered_fact",
                        f"{fact.kind}:{index}",
                        "A strong fact from the request is missing from the plan.",
                        clarification_possible=True,
                    )
                )
        return tuple(violations)


def _filter_matches_required(condition: FilterCondition, required) -> bool:
    values = condition.value if isinstance(condition.value, list) else [condition.value]
    required_values = required.value if isinstance(required.value, tuple) else (required.value,)
    return (
        condition.field == required.field
        and condition.operator == required.operator
        and {normalize_semantic_text(value) for value in values}
        == {normalize_semantic_text(value) for value in required_values}
    )


class ContradictionInvariant(PlanInvariant):
    def check(self, context):
        violations = []
        predicates = set(context.candidate_plan.business_predicates)
        pairs = {
            frozenset((name, other))
            for name, definition in BUSINESS_PREDICATE_DEFINITIONS.items()
            for other in definition.incompatible_with
        }
        for pair in sorted(pairs, key=lambda item: tuple(sorted(item))):
            if pair <= predicates:
                violations.append(
                    PlanViolation(
                        "contradiction",
                        ":".join(sorted(pair)),
                        "The selected business predicates are incompatible.",
                    )
                )
        for name in sorted(predicates):
            definition = BUSINESS_PREDICATE_DEFINITIONS[name]
            for incompatible in definition.incompatible_filters:
                if any(
                    _filter_matches_required(condition, incompatible)
                    for condition in context.candidate_plan.filters
                ):
                    violations.append(
                        PlanViolation(
                            "contradiction",
                            f"{name}:{incompatible.field}",
                            "A business predicate conflicts with an explicit filter.",
                        )
                    )
        return tuple(violations)


class AnswerContractInvariant(PlanInvariant):
    def check(self, context):
        proposal = context.proposal
        if proposal is None or proposal.answer_contract is None:
            return ()
        expected_unit = None
        expected_subject = None
        if proposal.measure is not None:
            definition = MEASURE_DEFINITIONS[proposal.measure.name]
            expected_unit = definition.answer_unit
            expected_subject = definition.aggregation_field
        elif proposal.calculation is not None:
            expected_unit = (
                "percentage"
                if proposal.calculation.operation == "percentage"
                else "hours"
                if proposal.calculation.field
                and FIELD_DEFINITIONS.get(proposal.calculation.field)
                and FIELD_DEFINITIONS[proposal.calculation.field].storage_type == "number"
                else "value"
            )
            expected_subject = proposal.calculation.field
        contract = proposal.answer_contract
        if expected_unit and (
            contract.unit != expected_unit or contract.subject_field != expected_subject
        ):
            return (
                PlanViolation(
                    "answer_contract_mismatch",
                    "answer_contract",
                    "The answer contract does not match the selected calculation.",
                ),
            )
        return ()


class CapabilityInvariant(PlanInvariant):
    def check(self, context):
        proposal = context.proposal
        if proposal is None or not proposal.unsupported_capabilities:
            return ()
        return tuple(
            PlanViolation(
                "unsupported_capability",
                capability,
                "The request cannot be represented safely by the current plan language.",
            )
            for capability in proposal.unsupported_capabilities
        )


PLAN_INVARIANTS: tuple[PlanInvariant, ...] = (
    SchemaInvariant(),
    GroundingInvariant(),
    CoverageInvariant(),
    ContradictionInvariant(),
    AnswerContractInvariant(),
    CapabilityInvariant(),
)


def _canonicalize_filter(proposed, context, resolver_registry):
    raw_values = proposed.value if isinstance(proposed.value, list) else [proposed.value]
    canonical = []
    candidates = []
    for raw_value in raw_values:
        outcome = resolver_registry.canonicalize(
            proposed.field,
            raw_value,
            proposed.evidence_text,
            context.resolution_context,
        )
        if outcome.status == "resolved":
            canonical.extend(outcome.values)
        elif outcome.status == "semantic_only" and proposed.operator in {"contains", "starts_with"}:
            canonical.extend(outcome.values)
        else:
            candidates.extend(outcome.candidates)
            return None, tuple(candidates), outcome.status
    value = canonical if proposed.operator == "in" else canonical[0]
    return FilterCondition(field=proposed.field, operator=proposed.operator, value=value), (), "resolved"


def _choice_origin(context, *, kind, evidence_text, field=None, operator=None, values=(), name=None):
    for fact in context.facts:
        if fact.strength != "strong" or fact.kind != kind:
            continue
        if field is not None and (fact.field != field or fact.operator != operator or set(fact.values) != set(values)):
            continue
        if name is not None and fact.concept_name != name:
            continue
        if evidence_occurs(fact.evidence_text, evidence_text) or evidence_occurs(
            evidence_text, fact.evidence_text
        ):
            return fact.origin
    return "question"


def _candidate_plan(proposal, context):
    structured = bool(
        proposal.filters
        or proposal.name_hint
        or proposal.measure
        or proposal.business_predicates
        or proposal.calculation
        or proposal.group_by
        or proposal.order_by
        or proposal.limit
    )
    semantic = any(fact.kind == "semantic_intent" for fact in context.facts)
    mode = "hybrid" if semantic and structured else "semantic" if semantic else "exact"
    return QueryPlan(mode=mode, search_query=context.question.strip())


def compile_proposal(
    proposal: PlannerProposal, context: CompilationContext
) -> PlanCompilationResult:
    candidate = _candidate_plan(proposal, context)
    provenance = []
    early_violations = []
    clarification = None
    resolver_registry = ResolverRegistry.default()

    if proposal.status == "unsupported":
        invariant_context = InvariantContext(context, proposal, candidate, ())
        violations = tuple(
            violation
            for invariant in PLAN_INVARIANTS
            for violation in invariant.check(invariant_context)
        )
        return PlanCompilationResult(None, (), violations)
    if proposal.status == "ambiguous":
        return PlanCompilationResult(
            None,
            (),
            (
                PlanViolation(
                    "ambiguous_value",
                    "proposal",
                    "The request requires clarification before execution.",
                    clarification_possible=True,
                ),
            ),
        )

    for proposed in proposal.filters:
        condition, candidates, status = _canonicalize_filter(
            proposed, context, resolver_registry
        )
        if condition is None:
            early_violations.append(
                PlanViolation(
                    "ambiguous_value" if status == "ambiguous" else "invalid_schema",
                    proposed.field,
                    "The proposed value could not be resolved uniquely.",
                    clarification_possible=status == "ambiguous",
                )
            )
            if candidates:
                clarification = PendingConstraintData(
                    field=proposed.field,
                    reference=proposed.evidence_text,
                    candidates=[
                        ConstraintCandidateData(field=proposed.field, value=value)
                        for value in candidates
                    ],
                )
            continue
        candidate.filters.append(condition)
        values = condition.value if isinstance(condition.value, list) else [condition.value]
        provenance.append(
            ConstraintProvenance(
                target_kind="filter",
                field=condition.field,
                operator=condition.operator,
                values=tuple(values),
                origin=_choice_origin(
                    context,
                    kind="filter",
                    evidence_text=proposed.evidence_text,
                    field=condition.field,
                    operator=condition.operator,
                    values=tuple(values),
                ),
                evidence_text=proposed.evidence_text,
            )
        )

    if proposal.name_hint is not None:
        candidate.name_hint = proposal.name_hint.value
        provenance.append(
            ConstraintProvenance(
                target_kind="entity",
                field="Name",
                values=(proposal.name_hint.value,),
                evidence_text=proposal.name_hint.evidence_text,
            )
        )

    if proposal.measure is not None:
        definition = MEASURE_DEFINITIONS[proposal.measure.name]
        candidate.measure = proposal.measure.name
        candidate.aggregation = definition.aggregation
        candidate.aggregation_field = definition.aggregation_field
        provenance.append(
            ConstraintProvenance(
                target_kind="measure",
                field=definition.aggregation_field,
                name=proposal.measure.name,
                origin=_choice_origin(
                    context,
                    kind="measure",
                    evidence_text=proposal.measure.evidence_text,
                    name=proposal.measure.name,
                ),
                evidence_text=proposal.measure.evidence_text,
            )
        )
    elif proposal.calculation is not None:
        calculation = proposal.calculation
        candidate.aggregation = calculation.operation
        candidate.aggregation_field = calculation.field
        if calculation.percentage_condition is not None:
            condition, _, _ = _canonicalize_filter(
                calculation.percentage_condition, context, resolver_registry
            )
            candidate.percentage_condition = condition
        provenance.append(
            ConstraintProvenance(
                target_kind="calculation",
                field=calculation.field,
                name=calculation.operation,
                evidence_text=calculation.evidence_text,
            )
        )

    for proposed in proposal.business_predicates:
        candidate.business_predicates.append(proposed.name)
        provenance.append(
            ConstraintProvenance(
                target_kind="predicate",
                name=proposed.name,
                origin=_choice_origin(
                    context,
                    kind="predicate",
                    evidence_text=proposed.evidence_text,
                    name=proposed.name,
                ),
                evidence_text=proposed.evidence_text,
            )
        )
        for required in BUSINESS_PREDICATE_DEFINITIONS[proposed.name].required_filters:
            value = list(required.value) if isinstance(required.value, tuple) else required.value
            candidate.filters.append(
                FilterCondition(field=required.field, operator=required.operator, value=value)
            )
            values = value if isinstance(value, list) else [value]
            provenance.append(
                ConstraintProvenance(
                    target_kind="filter",
                    field=required.field,
                    operator=required.operator,
                    values=tuple(values),
                    name=proposed.name,
                    evidence_text=proposed.evidence_text,
                )
            )

    for proposed in proposal.group_by:
        candidate.group_by.append(proposed.field)
        provenance.append(
            ConstraintProvenance(
                target_kind="group_by",
                field=proposed.field,
                evidence_text=proposed.evidence_text,
            )
        )
    if proposal.order_by is not None:
        candidate.order_by = proposal.order_by.field
        candidate.order_direction = proposal.order_by.direction
        provenance.append(
            ConstraintProvenance(
                target_kind="order_by",
                field=proposal.order_by.field,
                direction=proposal.order_by.direction,
                evidence_text=proposal.order_by.evidence_text,
            )
        )
    if proposal.limit is not None:
        candidate.limit = proposal.limit.value
        provenance.append(
            ConstraintProvenance(
                target_kind="limit",
                values=(float(proposal.limit.value),),
                evidence_text=proposal.limit.evidence_text,
            )
        )

    invariant_context = InvariantContext(
        context, proposal, candidate, tuple(provenance)
    )
    violations = tuple(early_violations) + tuple(
        violation
        for invariant in PLAN_INVARIANTS
        for violation in invariant.check(invariant_context)
    )
    if violations:
        return PlanCompilationResult(
            None, tuple(provenance), violations, clarification=clarification
        )
    executable = ExecutableQueryPlan(
        **candidate.model_dump(), answer_contract=proposal.answer_contract
    )
    return PlanCompilationResult(executable, tuple(provenance), ())


def revalidate_executable_plan(
    plan: ExecutableQueryPlan,
    context: CompilationContext,
    provenance: tuple[ConstraintProvenance, ...],
) -> PlanCompilationResult:
    candidate = QueryPlan.model_validate(plan.model_dump(exclude={"answer_contract"}))
    invariant_context = InvariantContext(context, None, candidate, provenance)
    violations = tuple(
        violation
        for invariant in PLAN_INVARIANTS
        for violation in invariant.check(invariant_context)
    )
    return PlanCompilationResult(plan if not violations else None, provenance, violations)
