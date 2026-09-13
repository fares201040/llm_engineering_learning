"""Compile untrusted planner proposals into verified executable plans."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

try:
    from .attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        AnswerContract,
        CALCULATION_DEFINITIONS,
        DERIVED_RESULT_DEFINITIONS,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        EvidenceOrigin,
        ExecutableQueryPlan,
        effective_grouping_fields,
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
        AnswerContract,
        CALCULATION_DEFINITIONS,
        DERIVED_RESULT_DEFINITIONS,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        EvidenceOrigin,
        ExecutableQueryPlan,
        effective_grouping_fields,
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
    scope: Literal["population", "percentage_numerator"] = "population"


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
            "projection": "context",
        }
        for item in context.provenance:
            if item.field is None or item.target_kind not in role_by_kind:
                continue
            if (
                item.target_kind == "order_by"
                and item.field in DERIVED_RESULT_DEFINITIONS
            ):
                if (
                    context.candidate_plan.group_by
                    and context.candidate_plan.aggregation != "none"
                ):
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
            if (
                item.operator is not None
                and item.operator not in FIELD_DEFINITIONS[item.field].operators
            ):
                violations.append(
                    PlanViolation(
                        "invalid_schema",
                        item.field,
                        f"Operator {item.operator!r} is not valid for {item.field!r}.",
                    )
                )
        calculation = context.proposal.calculation if context.proposal else None
        if calculation is not None:
            definition = CALCULATION_DEFINITIONS.get(calculation.operation)
            field_definition = FIELD_DEFINITIONS.get(calculation.field or "")
            if (
                definition is not None
                and definition.requires_numeric_field
                and (
                    field_definition is None
                    or field_definition.storage_type != "number"
                )
            ):
                violations.append(
                    PlanViolation(
                        "invalid_schema",
                        calculation.field or "calculation",
                        f"{calculation.operation} requires a numeric field.",
                    )
                )
        return tuple(violations)


def _fact_matches_provenance(fact: SemanticFact, item: ConstraintProvenance) -> bool:
    if fact.origin != item.origin:
        return False
    if fact.scope != item.scope:
        return False
    if not evidence_occurs(
        fact.evidence_text, item.evidence_text
    ) and not evidence_occurs(item.evidence_text, fact.evidence_text):
        return False
    if item.target_kind == "filter":
        if item.name is not None and fact.kind == "predicate":
            return fact.concept_name == item.name
        if (
            fact.kind == "predicate"
            and fact.concept_name in BUSINESS_PREDICATE_DEFINITIONS
        ):
            return any(
                item.field == required.field
                and item.operator == required.operator
                and set(item.values)
                == set(
                    required.value
                    if isinstance(required.value, tuple)
                    else (required.value,)
                )
                for required in BUSINESS_PREDICATE_DEFINITIONS[
                    fact.concept_name
                ].required_filters
            )
        return (
            fact.kind == "filter"
            and fact.field == item.field
            and fact.operator == item.operator
            and set(fact.values) == set(item.values)
        )
    if item.target_kind in {"measure", "predicate"}:
        return fact.kind == item.target_kind and fact.concept_name == item.name
    if item.target_kind in {"group_by", "projection"}:
        return fact.field == item.field and fact.kind == item.target_kind
    if item.target_kind == "order_by":
        return (
            fact.field == item.field
            and fact.kind in {"order_by", "ranking"}
            and fact.direction == item.direction
        )
    if item.target_kind == "percentage_denominator":
        return (
            fact.kind == item.target_kind
            and fact.field == item.field
            and fact.concept_name == item.name
        )
    if item.target_kind == "calculation":
        return (
            fact.kind == "calculation"
            and fact.field == item.field
            and fact.concept_name == item.name
        )
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
            evidence_present = (
                evidence_occurs(context.compilation.question, item.evidence_text)
                or item.origin == "trusted_state"
            )
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
            "projection",
            "ranking",
            "percentage_denominator",
        }
        for index, fact in enumerate(context.compilation.facts):
            if fact.strength != "strong" or fact.kind not in relevant_kinds:
                continue
            if not any(
                _fact_matches_provenance(fact, item) for item in context.provenance
            ):
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
    required_values = (
        required.value if isinstance(required.value, tuple) else (required.value,)
    )
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
        contract = (
            proposal.answer_contract
            if proposal is not None
            else getattr(context.candidate_plan, "answer_contract", None)
        )
        if contract is None:
            return ()
        if contract.shape == "narrative" and (
            context.candidate_plan.mode == "exact"
            or context.candidate_plan.aggregation != "none"
        ):
            return (
                PlanViolation(
                    "unsupported_capability",
                    "narrative_explanation",
                    "Narrative explanations cannot be executed as structured calculations.",
                ),
            )
        expected = derive_expected_answer_contract(context.candidate_plan)
        if contract != expected:
            return (
                PlanViolation(
                    "answer_contract_mismatch",
                    "answer_contract",
                    "The answer contract does not match the complete compiled operation.",
                ),
            )
        return ()


def derive_expected_answer_contract(plan: QueryPlan) -> AnswerContract:
    """Derive the only answer shape allowed for a compiled operation."""
    if plan.aggregation != "none":
        effective_group_by = effective_grouping_fields(plan.group_by)
        shape = "grouped" if effective_group_by else "scalar"
        if plan.measure is not None:
            definition = MEASURE_DEFINITIONS[plan.measure]
            unit = definition.answer_unit
            subject_field = definition.aggregation_field
        elif plan.aggregation == "count":
            unit = "records"
            subject_field = None
        elif plan.aggregation == "percentage":
            unit = "percentage"
            subject_field = plan.aggregation_field
        else:
            subject_field = plan.aggregation_field
            definition = FIELD_DEFINITIONS.get(subject_field or "")
            unit = definition.output_unit if definition is not None else "value"
        grain = list(
            dict.fromkeys(
                [*effective_group_by, *([subject_field] if subject_field else [])]
            )
        )
        return AnswerContract(
            shape=shape,
            unit=unit,
            subject_field=subject_field,
            grain=grain,
        )
    if plan.projection:
        return AnswerContract(
            shape="rows",
            unit="value",
            subject_field=None,
            grain=list(plan.projection),
        )
    if plan.mode in {"semantic", "hybrid"}:
        return AnswerContract(
            shape="narrative", unit="value", subject_field=None, grain=[]
        )
    return AnswerContract(shape="rows", unit="value", subject_field=None, grain=[])


class CapabilityInvariant(PlanInvariant):
    def check(self, context):
        proposal = context.proposal
        proposed_capabilities = (
            proposal.unsupported_capabilities if proposal is not None else ()
        )
        detected_capabilities = tuple(
            fact.concept_name or "unsupported_constraint"
            for fact in context.compilation.facts
            if fact.kind == "unsupported" and fact.strength == "strong"
        )
        if (
            context.candidate_plan.aggregation == "percentage"
            and context.candidate_plan.group_by
        ):
            detected_capabilities += ("grouped_percentage",)
        return tuple(
            PlanViolation(
                "unsupported_capability",
                capability,
                "The request cannot be represented safely by the current plan language.",
            )
            for capability in (*proposed_capabilities, *detected_capabilities)
        )


class ExecutableChoiceInvariant(PlanInvariant):
    """Bind actual executable choices to the provenance checked by invariants."""

    def check(self, context):
        plan = context.candidate_plan
        required = []
        for condition in plan.filters:
            values = (
                condition.value
                if isinstance(condition.value, list)
                else [condition.value]
            )
            required.append(
                dict(
                    target_kind="filter",
                    field=condition.field,
                    operator=condition.operator,
                    values=tuple(values),
                    scope="population",
                )
            )
        for kind, fields in (
            ("group_by", plan.group_by),
            ("projection", plan.projection),
        ):
            required.extend(dict(target_kind=kind, field=field) for field in fields)
        required.extend(
            dict(target_kind="predicate", name=name)
            for name in plan.business_predicates
        )
        if plan.order_by:
            required.append(
                dict(
                    target_kind="order_by",
                    field=plan.order_by,
                    direction=plan.order_direction,
                )
            )
        if plan.limit is not None:
            required.append(dict(target_kind="limit", values=(float(plan.limit),)))
        if plan.measure:
            definition = MEASURE_DEFINITIONS[plan.measure]
            if (plan.aggregation, plan.aggregation_field) != (
                definition.aggregation,
                definition.aggregation_field,
            ):
                return (
                    PlanViolation(
                        "ungrounded_constraint",
                        "calculation",
                        "The executable measure was changed.",
                    ),
                )
            required.append(dict(target_kind="measure", name=plan.measure))
        elif plan.aggregation != "none":
            required.append(
                dict(
                    target_kind="calculation",
                    name=plan.aggregation,
                    field=plan.aggregation_field,
                )
            )
        if plan.percentage_condition:
            condition = plan.percentage_condition
            values = (
                condition.value
                if isinstance(condition.value, list)
                else [condition.value]
            )
            required.append(
                dict(
                    target_kind="filter",
                    field=condition.field,
                    operator=condition.operator,
                    values=tuple(values),
                    scope="percentage_numerator",
                )
            )
        if plan.aggregation == "percentage":
            denominator_name = next(
                (
                    name
                    for name, definition in MEASURE_DEFINITIONS.items()
                    if definition.aggregation_field == plan.aggregation_field
                ),
                None,
            )
            required.append(
                dict(
                    target_kind="percentage_denominator",
                    name=denominator_name,
                    field=plan.aggregation_field,
                )
            )
        violations = tuple(
            PlanViolation(
                "ungrounded_constraint",
                item["target_kind"],
                "An executable choice does not match its verified provenance.",
            )
            for item in required
            if not any(
                all(getattr(provenance, key) == value for key, value in item.items())
                for provenance in context.provenance
            )
        )
        executable_kinds = {
            "filter",
            "predicate",
            "measure",
            "calculation",
            "group_by",
            "projection",
            "order_by",
            "limit",
            "percentage_denominator",
        }
        violations += tuple(
            PlanViolation(
                "uncovered_fact",
                name,
                "A business predicate lost its required executable filter.",
            )
            for name in plan.business_predicates
            for condition in BUSINESS_PREDICATE_DEFINITIONS[name].required_filters
            if not any(
                _filter_matches_required(actual, condition) for actual in plan.filters
            )
        )
        return violations + tuple(
            PlanViolation(
                "uncovered_fact",
                provenance.target_kind,
                "A verified executable constraint was removed from the plan.",
            )
            for provenance in context.provenance
            if provenance.target_kind in executable_kinds
            and not any(
                all(getattr(provenance, key) == value for key, value in item.items())
                for item in required
            )
        )


PLAN_INVARIANTS: tuple[PlanInvariant, ...] = (
    SchemaInvariant(),
    GroundingInvariant(),
    CoverageInvariant(),
    ContradictionInvariant(),
    AnswerContractInvariant(),
    CapabilityInvariant(),
    ExecutableChoiceInvariant(),
)


def _canonicalize_filter(proposed, context, resolver_registry):
    raw_values = (
        proposed.value if isinstance(proposed.value, list) else [proposed.value]
    )
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
        elif outcome.status == "semantic_only" and proposed.operator in {
            "contains",
            "starts_with",
        }:
            canonical.extend(outcome.values)
        else:
            candidates.extend(outcome.candidates)
            return None, tuple(candidates), outcome.status
    value = canonical if proposed.operator == "in" else canonical[0]
    return (
        FilterCondition(field=proposed.field, operator=proposed.operator, value=value),
        (),
        "resolved",
    )


def _choice_origin(
    context,
    *,
    kind,
    evidence_text,
    field=None,
    operator=None,
    values=(),
    name=None,
    scope="population",
    direction=None,
):
    for fact in context.facts:
        if fact.strength != "strong" or fact.kind != kind:
            continue
        if fact.scope != scope or fact.direction != direction:
            continue
        if field is not None and (
            fact.field != field
            or fact.operator != operator
            or set(fact.values) != set(values)
        ):
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
        or proposal.projection
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
        values = (
            condition.value if isinstance(condition.value, list) else [condition.value]
        )
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
            condition, candidates, status = _canonicalize_filter(
                calculation.percentage_condition, context, resolver_registry
            )
            if condition is None:
                early_violations.append(
                    PlanViolation(
                        (
                            "ambiguous_value"
                            if status == "ambiguous"
                            else "invalid_schema"
                        ),
                        calculation.percentage_condition.field,
                        "The percentage condition could not be resolved uniquely.",
                        clarification_possible=status == "ambiguous",
                    )
                )
                if candidates:
                    clarification = PendingConstraintData(
                        field=calculation.percentage_condition.field,
                        reference=calculation.percentage_condition.evidence_text,
                        candidates=[
                            ConstraintCandidateData(
                                field=calculation.percentage_condition.field,
                                value=value,
                            )
                            for value in candidates
                        ],
                    )
            else:
                candidate.percentage_condition = condition
                values = (
                    condition.value
                    if isinstance(condition.value, list)
                    else [condition.value]
                )
                provenance.append(
                    ConstraintProvenance(
                        target_kind="filter",
                        field=condition.field,
                        operator=condition.operator,
                        values=tuple(values),
                        origin=_choice_origin(
                            context,
                            kind="filter",
                            evidence_text=calculation.percentage_condition.evidence_text,
                            field=condition.field,
                            operator=condition.operator,
                            values=tuple(values),
                            scope="percentage_numerator",
                        ),
                        evidence_text=calculation.percentage_condition.evidence_text,
                        scope="percentage_numerator",
                    )
                )
        if calculation.operation == "percentage":
            denominator_name = next(
                (
                    name
                    for name, definition in MEASURE_DEFINITIONS.items()
                    if definition.aggregation_field == calculation.field
                ),
                None,
            )
            provenance.append(
                ConstraintProvenance(
                    target_kind="percentage_denominator",
                    field=calculation.field,
                    name=denominator_name,
                    evidence_text=calculation.evidence_text,
                    origin=_choice_origin(
                        context,
                        kind="percentage_denominator",
                        evidence_text=calculation.evidence_text,
                        field=calculation.field,
                        name=denominator_name,
                    ),
                )
            )
        provenance.append(
            ConstraintProvenance(
                target_kind="calculation",
                field=calculation.field,
                name=calculation.operation,
                origin=_choice_origin(
                    context,
                    kind="calculation",
                    evidence_text=calculation.evidence_text,
                    field=calculation.field,
                    name=calculation.operation,
                ),
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
            value = (
                list(required.value)
                if isinstance(required.value, tuple)
                else required.value
            )
            candidate.filters.append(
                FilterCondition(
                    field=required.field, operator=required.operator, value=value
                )
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
                origin=_choice_origin(
                    context,
                    kind="group_by",
                    evidence_text=proposed.evidence_text,
                    field=proposed.field,
                ),
            )
        )
    for proposed in proposal.projection:
        candidate.projection.append(proposed.field)
        provenance.append(
            ConstraintProvenance(
                target_kind="projection",
                field=proposed.field,
                evidence_text=proposed.evidence_text,
                origin=_choice_origin(
                    context,
                    kind="projection",
                    evidence_text=proposed.evidence_text,
                    field=proposed.field,
                ),
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
                origin=_choice_origin(
                    context,
                    kind="order_by",
                    evidence_text=proposal.order_by.evidence_text,
                    field=proposal.order_by.field,
                    direction=proposal.order_by.direction,
                ),
            )
        )
    if proposal.limit is not None:
        candidate.limit = proposal.limit.value
        provenance.append(
            ConstraintProvenance(
                target_kind="limit",
                values=(float(proposal.limit.value),),
                evidence_text=proposal.limit.evidence_text,
                origin=_choice_origin(
                    context,
                    kind="limit",
                    evidence_text=proposal.limit.evidence_text,
                    values=(float(proposal.limit.value),),
                ),
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
        **candidate.model_dump(),
        answer_contract=derive_expected_answer_contract(candidate),
    )
    return PlanCompilationResult(executable, tuple(provenance), ())


def revalidate_executable_plan(
    plan: ExecutableQueryPlan,
    context: CompilationContext,
    provenance: tuple[ConstraintProvenance, ...],
) -> PlanCompilationResult:
    try:
        candidate = ExecutableQueryPlan.model_validate(plan.model_dump())
    except ValidationError:
        return PlanCompilationResult(
            None,
            provenance,
            (
                PlanViolation(
                    "invalid_schema",
                    "executable_plan",
                    "The executable plan failed structural validation.",
                ),
            ),
        )
    invariant_context = InvariantContext(context, None, candidate, provenance)
    violations = tuple(
        violation
        for invariant in PLAN_INVARIANTS
        for violation in invariant.check(invariant_context)
    )
    return PlanCompilationResult(
        candidate if not violations else None, provenance, violations
    )
