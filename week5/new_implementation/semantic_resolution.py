"""Pure, registry-driven semantic detection and value canonicalization."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from collections.abc import Iterable, Mapping
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

try:
    from .attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        RETRIEVAL_INTENT_DEFINITIONS,
        VALUE_CONCEPT_DEFINITIONS,
        EvidenceOrigin,
        FilterOperator,
        ResolutionKind,
    )
except ImportError:  # Direct execution from week5/new_implementation.
    from attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        RETRIEVAL_INTENT_DEFINITIONS,
        VALUE_CONCEPT_DEFINITIONS,
        EvidenceOrigin,
        FilterOperator,
        ResolutionKind,
    )


def normalize_semantic_text(value: object) -> str:
    """Create a stable comparison key without changing canonical stored values."""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _token_forms(value: object) -> tuple[str, ...]:
    normalized = normalize_semantic_text(value)
    tokens = normalized.split()
    singular = [token[:-1] if len(token) > 3 and token.endswith("s") else token for token in tokens]
    return tuple(dict.fromkeys((normalized, " ".join(singular))))


def evidence_occurs(text: str, evidence: str) -> bool:
    text_forms = _token_forms(text)
    evidence_forms = _token_forms(evidence)
    return any(
        re.search(rf"(?:^|\s){re.escape(needle)}(?:$|\s)", haystack) is not None
        for haystack in text_forms
        for needle in evidence_forms
        if needle
    )


class EmployeeReference(BaseModel):
    model_config = ConfigDict(frozen=True)
    employee_id: str
    name: str


class SemanticFact(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: str
    field: str | None = None
    operator: FilterOperator | None = None
    values: tuple[str | float, ...] = ()
    concept_name: str | None = None
    evidence_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    strength: str


@dataclass(frozen=True)
class ResolutionContext:
    catalog: Mapping[str, tuple[str, ...]]
    employees: tuple[EmployeeReference, ...] = ()


@dataclass(frozen=True)
class ResolutionOutcome:
    status: str
    values: tuple[str | float, ...] = ()
    candidates: tuple[str, ...] = ()


class FieldResolver(ABC):
    @property
    @abstractmethod
    def kinds(self) -> frozenset[ResolutionKind]:
        raise NotImplementedError

    def detect(
        self, question: str, field: str, context: ResolutionContext
    ) -> tuple[SemanticFact, ...]:
        definition = FIELD_DEFINITIONS[field]
        field_facts = tuple(
            SemanticFact(
                kind="field",
                field=field,
                evidence_text=phrase,
                origin="question",
                strength="strong",
            )
            for phrase in sorted(definition.natural_names, key=len, reverse=True)
            if evidence_occurs(question, phrase)
        )[:1]
        if not field_facts:
            return ()
        value_search_text = normalize_semantic_text(question).replace(
            normalize_semantic_text(field_facts[0].evidence_text), " ", 1
        )
        values: list[tuple[object, str]] = []
        if definition.resolution_kind == "closed_value":
            values.extend(
                (value, value)
                for value in definition.closed_values
                if evidence_occurs(question, value)
            )
            values.extend(
                (alias.canonical_value, alias.natural_name)
                for alias in definition.value_aliases
                if evidence_occurs(question, alias.natural_name)
            )
        elif definition.resolution_kind == "catalog":
            values.extend(
                (value, value)
                for value in context.catalog.get(field, ())
                if evidence_occurs(question, value)
            )
        elif definition.resolution_kind == "identifier":
            match = re.search(r"\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b", question)
            if match:
                values.append((match.group(0), match.group(0)))
        elif definition.resolution_kind == "entity":
            values.extend(
                (employee.name, employee.name)
                for employee in context.employees
                if evidence_occurs(question, employee.name)
            )
        elif definition.resolution_kind == "numeric":
            match = re.search(
                r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?![A-Za-z])",
                value_search_text,
            )
            if match:
                values.append((float(match.group(0)), match.group(0)))
        elif definition.resolution_kind == "temporal":
            match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", question)
            if match:
                values.append((match.group(0), match.group(0)))
        value_facts = tuple(
            SemanticFact(
                kind="filter",
                field=field,
                operator="eq",
                values=(value,),
                evidence_text=evidence,
                origin="question",
                strength="strong",
            )
            for value, evidence in dict.fromkeys(values)
        )
        return field_facts + value_facts

    @abstractmethod
    def canonicalize(
        self,
        field: str,
        raw_value: object,
        evidence_text: str,
        context: ResolutionContext,
    ) -> ResolutionOutcome:
        raise NotImplementedError


class IdentifierResolver(FieldResolver):
    kinds = frozenset({"identifier"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        value = str(raw_value).strip()
        return ResolutionOutcome("resolved", (value,)) if value else ResolutionOutcome("unknown")


class EntityResolver(FieldResolver):
    kinds = frozenset({"entity"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        key = normalize_semantic_text(raw_value)
        matches = tuple(
            item.name for item in context.employees if normalize_semantic_text(item.name) == key
        )
        if len(matches) == 1:
            return ResolutionOutcome("resolved", matches)
        if len(matches) > 1:
            return ResolutionOutcome("ambiguous", candidates=matches)
        return _catalog_outcome(field, raw_value, context)


class TemporalResolver(FieldResolver):
    kinds = frozenset({"temporal"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        value = str(raw_value).strip()
        return ResolutionOutcome("resolved", (value,)) if value else ResolutionOutcome("unknown")


class NumericResolver(FieldResolver):
    kinds = frozenset({"numeric"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        try:
            number = float(Decimal(str(raw_value).strip()))
        except (InvalidOperation, ValueError):
            return ResolutionOutcome("unknown")
        return ResolutionOutcome("resolved", (number,))


def _closed_value_outcome(field: str, raw_value: object) -> ResolutionOutcome:
    definition = FIELD_DEFINITIONS[field]
    key = normalize_semantic_text(raw_value)
    exact = tuple(
        value for value in definition.closed_values if normalize_semantic_text(value) == key
    )
    if len(exact) == 1:
        return ResolutionOutcome("resolved", exact)
    aliases = tuple(
        alias.canonical_value
        for alias in definition.value_aliases
        if normalize_semantic_text(alias.natural_name) == key
    )
    aliases = tuple(dict.fromkeys(aliases))
    if len(aliases) == 1:
        return ResolutionOutcome("resolved", aliases)
    return ResolutionOutcome("ambiguous", candidates=aliases) if aliases else ResolutionOutcome("unknown")


class ClosedValueResolver(FieldResolver):
    kinds = frozenset({"closed_value"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        return _closed_value_outcome(field, raw_value)


def _catalog_outcome(
    field: str, raw_value: object, context: ResolutionContext
) -> ResolutionOutcome:
    values = context.catalog.get(field, ())
    key = normalize_semantic_text(raw_value)
    exact = tuple(value for value in values if normalize_semantic_text(value) == key)
    if len(exact) == 1:
        return ResolutionOutcome("resolved", exact)
    partial = tuple(
        value
        for value in values
        if key
        and any(
            any(token.startswith(key) for token in form.split())
            for form in _token_forms(value)
        )
    )
    if len(partial) == 1:
        return ResolutionOutcome("ambiguous", candidates=partial)
    if len(partial) > 1:
        return ResolutionOutcome("ambiguous", candidates=partial)
    return ResolutionOutcome("unknown")


class CatalogValueResolver(FieldResolver):
    kinds = frozenset({"catalog"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        definition = FIELD_DEFINITIONS[field]
        key = normalize_semantic_text(raw_value)
        aliases = tuple(
            alias.canonical_value
            for alias in definition.value_aliases
            if normalize_semantic_text(alias.natural_name) == key
            and alias.canonical_value in context.catalog.get(field, ())
        )
        if len(set(aliases)) == 1:
            return ResolutionOutcome("resolved", tuple(dict.fromkeys(aliases)))
        return _catalog_outcome(field, raw_value, context)


class FreeTextResolver(FieldResolver):
    kinds = frozenset({"free_text"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        value = str(raw_value).strip()
        return ResolutionOutcome("semantic_only", (value,)) if value else ResolutionOutcome("unknown")


@dataclass(frozen=True)
class ResolverRegistry:
    resolvers: tuple[FieldResolver, ...]

    @classmethod
    def default(cls):
        return cls(
            (
                IdentifierResolver(),
                EntityResolver(),
                TemporalResolver(),
                NumericResolver(),
                ClosedValueResolver(),
                CatalogValueResolver(),
                FreeTextResolver(),
            )
        )

    def owner_count(self, kind: ResolutionKind) -> int:
        return sum(kind in resolver.kinds for resolver in self.resolvers)

    def supports(self, kind: ResolutionKind) -> bool:
        return self.owner_count(kind) == 1

    def for_kind(self, kind: ResolutionKind) -> FieldResolver:
        owners = [resolver for resolver in self.resolvers if kind in resolver.kinds]
        if len(owners) != 1:
            raise ValueError(f"Resolution kind {kind!r} must have exactly one resolver")
        return owners[0]

    def canonicalize(self, field, raw_value, evidence_text, context):
        definition = FIELD_DEFINITIONS.get(field)
        if definition is None or not definition.filterable:
            return ResolutionOutcome("unknown")
        return self.for_kind(definition.resolution_kind).canonicalize(
            field, raw_value, evidence_text, context
        )


def _facts_for_named_phrases(question, kind, registry):
    facts = []
    for name, definition in registry.items():
        for phrase in definition.natural_names:
            if evidence_occurs(question, phrase):
                facts.append(
                    SemanticFact(
                        kind=kind,
                        concept_name=name,
                        evidence_text=phrase,
                        origin="question",
                        strength="strong",
                    )
                )
                break
    return facts


def _earliest_measure_facts(question: str) -> list[SemanticFact]:
    candidates = _facts_for_named_phrases(question, "measure", MEASURE_DEFINITIONS)
    if not candidates:
        return []
    normalized_question = normalize_semantic_text(question)
    positions = []
    for fact in candidates:
        position = len(normalized_question)
        for form in _token_forms(fact.evidence_text):
            found = normalized_question.find(form)
            if found >= 0:
                position = min(position, found)
        positions.append((position, fact))
    earliest = min(position for position, _ in positions)
    return [fact for position, fact in positions if position == earliest]


def detect_semantic_facts(
    question: str, context: ResolutionContext
) -> tuple[SemanticFact, ...]:
    facts: list[SemanticFact] = []
    for match in re.finditer(r"\b[A-Za-z]\d{4,}\b", question):
        value = match.group(0)
        facts.append(
            SemanticFact(
                kind="filter",
                field="Employee_ID",
                operator="eq",
                values=(value,),
                evidence_text=value,
                origin="question",
                strength="strong",
            )
        )
    registry = ResolverRegistry.default()
    field_matches = [
        (field, phrase)
        for field, definition in FIELD_DEFINITIONS.items()
        if definition.planner_visible
        for phrase in definition.natural_names
        if evidence_occurs(question, phrase)
    ]
    maximal_field_matches = [
        (field, phrase)
        for field, phrase in field_matches
        if not any(
            field != other_field
            and normalize_semantic_text(phrase) != normalize_semantic_text(other_phrase)
            and evidence_occurs(other_phrase, phrase)
            for other_field, other_phrase in field_matches
        )
    ]
    selected_fields = tuple(dict.fromkeys(field for field, _ in maximal_field_matches))
    for field in selected_fields:
        definition = FIELD_DEFINITIONS[field]
        facts.extend(
            registry.for_kind(definition.resolution_kind).detect(
                question, field, context
            )
        )
    for name, concept in VALUE_CONCEPT_DEFINITIONS.items():
        for phrase in sorted(concept.natural_names, key=len, reverse=True):
            if not evidence_occurs(question, phrase):
                continue
            members = concept.members
            if FIELD_DEFINITIONS[concept.field].resolution_kind == "catalog":
                catalog = context.catalog.get(concept.field, ())
                if not set(members) <= set(catalog):
                    continue
            facts.append(
                SemanticFact(
                    kind="filter",
                    field=concept.field,
                    operator="in",
                    values=members,
                    concept_name=name,
                    evidence_text=phrase,
                    origin="question",
                    strength="strong",
                )
            )
            break
    if re.search(r"\b(?:how many|count|number of|total number)\b", question, re.I):
        facts.extend(_earliest_measure_facts(question))
    predicate_facts = _facts_for_named_phrases(
        question, "predicate", BUSINESS_PREDICATE_DEFINITIONS
    )
    selected_phrases = [
        phrase for field, phrase in maximal_field_matches if field in selected_fields
    ]
    facts.extend(
        fact
        for fact in predicate_facts
        if not any(
            normalize_semantic_text(fact.evidence_text)
            != normalize_semantic_text(field_phrase)
            and evidence_occurs(field_phrase, fact.evidence_text)
            for field_phrase in selected_phrases
        )
    )
    facts.extend(_facts_for_named_phrases(question, "semantic_intent", RETRIEVAL_INTENT_DEFINITIONS))
    return merge_semantic_facts(facts)


def _fact_target(fact: SemanticFact):
    return (
        fact.kind,
        fact.field,
        fact.operator,
        fact.concept_name,
        normalize_semantic_text(fact.evidence_text),
        fact.origin,
    )


def merge_semantic_facts(
    *groups: Iterable[SemanticFact],
) -> tuple[SemanticFact, ...]:
    merged: list[SemanticFact] = []
    for fact in (item for group in groups for item in group):
        if fact in merged:
            continue
        target = _fact_target(fact)
        if fact.strength == "strong":
            merged = [
                existing
                for existing in merged
                if not (
                    existing.strength == "candidate"
                    and _fact_target(existing) == target
                )
            ]
        elif any(
            existing.strength == "strong" and _fact_target(existing) == target
            for existing in merged
        ):
            continue
        merged.append(fact)
    return tuple(merged)
