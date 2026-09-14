"""Deterministic plan assembly and bounded provider decision contracts."""

from dataclasses import dataclass
from typing import get_args

try:
    from .attendance_schema import (
        AnswerContract,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        PlannerDecision,
        PlannerProposal,
        ProposedCalculation,
        ProposedFieldChoice,
        ProposedFilter,
        ProposedLimit,
        ProposedMeasureChoice,
        ProposedNameHint,
        ProposedOrderChoice,
        ProposedPredicateChoice,
        UnsupportedCapability,
    )
    from .semantic_resolution import SemanticFact
except ImportError:
    from attendance_schema import (
        AnswerContract,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        PlannerDecision,
        PlannerProposal,
        ProposedCalculation,
        ProposedFieldChoice,
        ProposedFilter,
        ProposedLimit,
        ProposedMeasureChoice,
        ProposedNameHint,
        ProposedOrderChoice,
        ProposedPredicateChoice,
        UnsupportedCapability,
    )
    from semantic_resolution import SemanticFact


@dataclass(frozen=True)
class PlanningCandidate:
    candidate_id: str
    fact_index: int


@dataclass(frozen=True)
class PlanningNeed:
    need_id: str
    kind: str
    candidates: tuple[PlanningCandidate, ...]


@dataclass(frozen=True)
class PlanningDraft:
    question: str
    facts: tuple[SemanticFact, ...]
    needs: tuple[PlanningNeed, ...] = ()


class PlanningDecisionError(ValueError):
    """A provider decision does not match its request-local planning draft."""


def validate_planner_decision(
    draft: PlanningDraft, decision: PlannerDecision
) -> PlannerDecision:
    needs = {need.need_id: need for need in draft.needs}
    if decision.status == "resolved":
        selections = {selection.need_id: selection for selection in decision.selections}
        if set(selections) != set(needs):
            raise PlanningDecisionError(
                "resolved decisions must select every request-local need exactly once"
            )
        for need_id, selection in selections.items():
            allowed = {
                candidate.candidate_id for candidate in needs[need_id].candidates
            }
            if selection.candidate_id not in allowed:
                raise PlanningDecisionError(
                    "provider selected an unknown request-local candidate"
                )
    elif decision.status == "ambiguous":
        if not set(decision.clarification_need_ids) <= set(needs):
            raise PlanningDecisionError(
                "provider requested clarification for an unknown planning need"
            )
    return decision


def build_planning_draft(
    question: str, facts: tuple[SemanticFact, ...]
) -> PlanningDraft:
    """Keep grounded facts immutable and enumerate only genuine finite choices."""
    # Employee and catalog ambiguity is resolved by the authoritative resolver
    # and clarification boundary. The current supported semantic grammar emits
    # no provider-owned finite choices, so complete facts need no provider call.
    return PlanningDraft(question=question, facts=tuple(facts))


def _unique_facts(facts, kind):
    result = []
    seen = set()
    for fact in facts:
        if fact.kind != kind or fact.strength != "strong":
            continue
        key = (
            fact.field,
            fact.operator,
            fact.values,
            fact.concept_name,
            fact.direction,
            fact.scope,
        )
        if key not in seen:
            seen.add(key)
            result.append(fact)
    return result


def _unsupported_proposal(capabilities) -> PlannerProposal:
    allowed = set(get_args(UnsupportedCapability))
    normalized = tuple(
        dict.fromkeys(
            capability if capability in allowed else "unsupported_constraint"
            for capability in capabilities
        )
    )
    return PlannerProposal(
        status="unsupported",
        unsupported_capabilities=list(normalized or ("unsupported_constraint",)),
    )


def _answer_contract(
    *, measure, calculation, groups, projection, semantic
) -> AnswerContract:
    if projection:
        return AnswerContract(
            shape="rows",
            unit="value",
            grain=[choice.field for choice in projection],
        )
    if measure is not None:
        definition = MEASURE_DEFINITIONS[measure.name]
        grain = [choice.field for choice in groups]
        if definition.aggregation_field:
            grain.append(definition.aggregation_field)
        return AnswerContract(
            shape="grouped" if groups else definition.default_answer_shape,
            unit=definition.answer_unit,
            subject_field=definition.aggregation_field,
            grain=list(dict.fromkeys(grain)),
        )
    if calculation is not None:
        field = calculation.field
        unit = (
            "percentage"
            if calculation.operation == "percentage"
            else FIELD_DEFINITIONS[field].output_unit
            if field
            else "records"
            if calculation.operation == "count"
            else "value"
        )
        grain = [choice.field for choice in groups]
        if field:
            grain.append(field)
        return AnswerContract(
            shape="grouped" if groups else "scalar",
            unit=unit,
            subject_field=field,
            grain=list(dict.fromkeys(grain)),
        )
    if semantic:
        return AnswerContract(shape="narrative", unit="value", grain=[])
    return AnswerContract(
        shape="rows",
        unit="value",
        grain=[choice.field for choice in projection],
    )


def assemble_grounded_proposal(
    draft: PlanningDraft, decision: PlannerDecision | None = None
) -> PlannerProposal:
    """Create the compiler input solely from verified facts and decisions."""
    if draft.needs and decision is None:
        return _unsupported_proposal(("unsupported_constraint",))
    if decision is not None and decision.status == "unsupported":
        return _unsupported_proposal(decision.unsupported_capabilities)
    if decision is not None and decision.status == "ambiguous":
        return PlannerProposal(status="ambiguous")

    facts = tuple(fact for fact in draft.facts if fact.strength == "strong")
    unsupported = _unique_facts(facts, "unsupported")
    if unsupported:
        return _unsupported_proposal(fact.concept_name for fact in unsupported)

    measures = _unique_facts(facts, "measure")
    calculations = _unique_facts(facts, "calculation")
    if len(measures) > 1 or len(calculations) > 1 or (measures and calculations):
        return _unsupported_proposal(("multi_stage_aggregation",))

    filters = []
    for fact in _unique_facts(facts, "filter"):
        if fact.scope == "percentage_numerator":
            continue
        filters.append(
            ProposedFilter(
                field=fact.field,
                operator=fact.operator,
                value=list(fact.values) if fact.operator == "in" else fact.values[0],
                evidence_text=fact.evidence_text,
            )
        )

    measure = (
        ProposedMeasureChoice(
            name=measures[0].concept_name,
            evidence_text=measures[0].evidence_text,
        )
        if measures
        else None
    )
    calculation = None
    if calculations:
        fact = calculations[0]
        operation = fact.concept_name
        field = fact.field
        percentage_condition = None
        if operation == "percentage":
            denominators = _unique_facts(facts, "percentage_denominator")
            if len(denominators) != 1:
                return _unsupported_proposal(("percentage_population",))
            denominator = MEASURE_DEFINITIONS.get(denominators[0].concept_name)
            if denominator is None:
                return _unsupported_proposal(("percentage_population",))
            field = denominator.aggregation_field
            numerators = [
                item
                for item in _unique_facts(facts, "filter")
                if item.scope == "percentage_numerator"
            ]
            if len(numerators) != 1:
                return _unsupported_proposal(("percentage_population",))
            numerator = numerators[0]
            percentage_condition = ProposedFilter(
                field=numerator.field,
                operator=numerator.operator,
                value=(
                    list(numerator.values)
                    if numerator.operator == "in"
                    else numerator.values[0]
                ),
                evidence_text=numerator.evidence_text,
            )
        calculation = ProposedCalculation(
            operation=operation,
            field=field,
            percentage_condition=percentage_condition,
            evidence_text=fact.evidence_text,
        )

    predicates = [
        ProposedPredicateChoice(
            name=fact.concept_name,
            evidence_text=fact.evidence_text,
        )
        for fact in _unique_facts(facts, "predicate")
        if fact.scope == "population"
    ]
    groups = [
        ProposedFieldChoice(field=fact.field, evidence_text=fact.evidence_text)
        for fact in _unique_facts(facts, "group_by")
    ]
    projection = [
        ProposedFieldChoice(field=fact.field, evidence_text=fact.evidence_text)
        for fact in _unique_facts(facts, "projection")
    ]
    if calculation is not None and calculation.operation == "percentage" and groups:
        return _unsupported_proposal(("grouped_percentage",))
    if projection and (measure is not None or calculation is not None or groups):
        return _unsupported_proposal(("multi_stage_aggregation",))

    orders = _unique_facts(facts, "order_by")
    limits = _unique_facts(facts, "limit")
    if len(orders) > 1 or len(limits) > 1:
        return _unsupported_proposal(("unsupported_constraint",))
    order = (
        ProposedOrderChoice(
            field=orders[0].field,
            direction=orders[0].direction,
            evidence_text=orders[0].evidence_text,
        )
        if orders
        else None
    )
    limit = (
        ProposedLimit(
            value=int(limits[0].values[0]), evidence_text=limits[0].evidence_text
        )
        if limits
        else None
    )
    employee_filter = any(item.field == "Employee_ID" for item in filters)
    entities = [
        fact
        for fact in draft.facts
        if fact.kind == "entity" and fact.field == "Name" and not employee_filter
    ]
    name_hint = (
        ProposedNameHint(
            value=str(entities[0].values[0]), evidence_text=entities[0].evidence_text
        )
        if entities
        else None
    )
    semantic = bool(_unique_facts(facts, "semantic_intent"))
    contract = _answer_contract(
        measure=measure,
        calculation=calculation,
        groups=groups,
        projection=projection,
        semantic=semantic,
    )
    return PlannerProposal(
        status="ready",
        filters=filters,
        name_hint=name_hint,
        measure=measure,
        business_predicates=predicates,
        calculation=calculation,
        group_by=groups,
        projection=projection,
        order_by=order,
        limit=limit,
        answer_contract=contract,
    )
