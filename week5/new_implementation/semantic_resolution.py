"""Pure, registry-driven semantic detection and value canonicalization."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Iterable, Mapping
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

try:
    from .attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        CALCULATION_DEFINITIONS,
        canonicalize_storage_value,
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
        CALCULATION_DEFINITIONS,
        canonicalize_storage_value,
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
    singular = [
        token[:-1] if len(token) > 3 and token.endswith("s") else token
        for token in tokens
    ]
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


def _semantic_token_forms(value: object) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(form.split()) for form in _token_forms(value))


def _evidence_spans(text: str, evidence: str) -> tuple[tuple[int, int], ...]:
    """Locate every occurrence in normalized token space."""
    haystacks = _semantic_token_forms(text)
    needles = _semantic_token_forms(evidence)
    spans = []
    for haystack in haystacks:
        for needle in needles:
            if not needle or len(needle) > len(haystack):
                continue
            spans.extend(
                (start, start + len(needle))
                for start in range(len(haystack) - len(needle) + 1)
                if haystack[start : start + len(needle)] == needle
            )
    return tuple(dict.fromkeys(spans))


def _evidence_span(text: str, evidence: str) -> tuple[int, int] | None:
    spans = _evidence_spans(text, evidence)
    return spans[0] if spans else None


def _unsupported_operator_between(
    question: str, field_evidence: str, value_evidence: str
) -> bool:
    tokens = normalize_semantic_text(question).split()
    field_span = _evidence_span(question, field_evidence)
    value_span = _evidence_span(question, value_evidence)
    if field_span is None or value_span is None:
        return False
    between = " ".join(
        tokens[min(field_span[1], value_span[1]) : max(field_span[0], value_span[0])]
    )
    return re.search(r"\b(?:match(?:es)?|regex|ends? with)\b", between) is not None


def _span_follows_unsupported_operator(question: str, span: tuple[int, int]) -> bool:
    tokens = normalize_semantic_text(question).split()
    return (
        re.search(
            r"\b(?:match(?:es)?|regex|ends? with)\s*$",
            " ".join(tokens[: span[0]]),
        )
        is not None
    )


def _unsupported_fact(concept_name: str, evidence_text: str) -> "SemanticFact":
    return SemanticFact(
        kind="unsupported",
        concept_name=concept_name,
        evidence_text=evidence_text,
        origin="question",
        strength="strong",
    )


def _numeric_operator(comparator: str | None) -> FilterOperator:
    normalized = normalize_semantic_text(comparator or "")
    return {
        "not more than": "lte",
        "no more than": "lte",
        "at most": "lte",
        "not less than": "gte",
        "no less than": "gte",
        "at least": "gte",
        "more than": "gt",
        "greater than": "gt",
        "above": "gt",
        "over": "gt",
        "less than": "lt",
        "below": "lt",
        "under": "lt",
        "equal to": "eq",
        "equals": "eq",
        "is": "eq",
        "exactly": "eq",
    }.get(normalized, "eq")


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
        rejected_facts: list[SemanticFact] = []
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
            number = r"[-+]?\d+(?:\.\d+)?"
            match = re.search(
                r"(?<![A-Za-z0-9_])(?P<comparator>not\s+more\s+than|no\s+more\s+than|at\s+most|"
                r"not\s+less\s+than|no\s+less\s+than|at\s+least|more\s+than|"
                r"greater\s+than|less\s+than|equal\s+to|above|below|over|under|"
                rf"equals?|is|exactly)\s+(?P<number>{number})(?![A-Za-z0-9_])",
                value_search_text,
                re.I,
            )
            if match is None:
                field_phrase = re.escape(field_facts[0].evidence_text).replace(
                    r"\ ", r"\s+"
                )
                match = re.search(
                    rf"\b{field_phrase}\b\s+(?:(?P<comparator>equals?|is)\s+)?"
                    rf"(?P<number>{number})(?![A-Za-z0-9_])",
                    question,
                    re.I,
                )
            if match:
                values.append((float(match.group("number")), match.group(0)))
            else:
                field_span = _evidence_span(question, field_facts[0].evidence_text)
                remaining = " ".join(
                    normalize_semantic_text(question).split()[
                        field_span[1] if field_span is not None else 0 :
                    ]
                )
                malformed = re.match(
                    r"(?:not\s+more\s+than|no\s+more\s+than|at\s+most|"
                    r"not\s+less\s+than|no\s+less\s+than|at\s+least|more\s+than|"
                    r"greater\s+than|less\s+than|equal\s+to|above|below|over|under|"
                    r"equals?|is|exactly)\s+(?P<value>\S+)",
                    remaining,
                    re.I,
                )
                if malformed:
                    rejected_facts.append(
                        _unsupported_fact("malformed_value", malformed.group(0))
                    )
        elif definition.resolution_kind == "temporal":
            pattern = (
                r"\b\d{4}-(?:0[1-9]|1[0-2])\b"
                if field == "Period"
                else r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b"
                if definition.storage_type == "datetime"
                else r"\b\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b"
                if definition.storage_type == "time"
                else r"\b\d{4}-\d{2}-\d{2}\b"
            )
            match = re.search(pattern, question)
            if match:
                values.append((match.group(0), match.group(0)))
        value_facts = []
        for value, evidence in dict.fromkeys(values):
            if _unsupported_operator_between(
                question, field_facts[0].evidence_text, evidence
            ):
                rejected_facts.append(
                    _unsupported_fact("unsupported_operator", evidence)
                )
                continue
            if (
                definition.resolution_kind == "temporal"
                and not _canonical_value_is_valid(field, value)
            ):
                rejected_facts.append(_unsupported_fact("malformed_value", evidence))
                continue
            value_facts.append(
                SemanticFact(
                    kind="filter",
                    field=field,
                    operator=(
                        _numeric_operator(match.group("comparator"))
                        if definition.resolution_kind == "numeric" and match
                        else "eq"
                    ),
                    values=(value,),
                    evidence_text=evidence,
                    origin="question",
                    strength="strong",
                )
            )
        return field_facts + tuple(value_facts) + tuple(rejected_facts)

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
        return (
            ResolutionOutcome("resolved", (value,))
            if value
            else ResolutionOutcome("unknown")
        )


class EntityResolver(FieldResolver):
    kinds = frozenset({"entity"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        key = normalize_semantic_text(raw_value)
        matches = tuple(
            item.name
            for item in context.employees
            if normalize_semantic_text(item.name) == key
        )
        if len(matches) == 1:
            return ResolutionOutcome("resolved", matches)
        if len(matches) > 1:
            return ResolutionOutcome("ambiguous", candidates=matches)
        return _catalog_outcome(field, raw_value, context)


class TemporalResolver(FieldResolver):
    kinds = frozenset({"temporal"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        try:
            value = canonicalize_storage_value(field, raw_value)
        except ValueError:
            return ResolutionOutcome("unknown")
        return ResolutionOutcome("resolved", (value,))


class NumericResolver(FieldResolver):
    kinds = frozenset({"numeric"})

    def canonicalize(self, field, raw_value, evidence_text, context):
        try:
            number = canonicalize_storage_value(field, raw_value)
        except ValueError:
            return ResolutionOutcome("unknown")
        return ResolutionOutcome("resolved", (number,))


def _canonical_value_is_valid(field: str, raw_value: object) -> bool:
    try:
        canonicalize_storage_value(field, raw_value)
    except ValueError:
        return False
    return True


def _closed_value_outcome(field: str, raw_value: object) -> ResolutionOutcome:
    definition = FIELD_DEFINITIONS[field]
    key = normalize_semantic_text(raw_value)
    exact = tuple(
        value
        for value in definition.closed_values
        if normalize_semantic_text(value) == key
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
    return (
        ResolutionOutcome("ambiguous", candidates=aliases)
        if aliases
        else ResolutionOutcome("unknown")
    )


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
        return (
            ResolutionOutcome("semantic_only", (value,))
            if value
            else ResolutionOutcome("unknown")
        )


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
        for phrase in sorted(
            definition.natural_names,
            key=lambda item: (len(normalize_semantic_text(item).split()), len(item)),
            reverse=True,
        ):
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
    return facts


def _fact_priority(fact: SemanticFact) -> int:
    if fact.kind == "field" and fact.field is not None:
        return FIELD_DEFINITIONS[fact.field].phrase_priority
    if fact.kind == "filter":
        if fact.concept_name in VALUE_CONCEPT_DEFINITIONS:
            return VALUE_CONCEPT_DEFINITIONS[fact.concept_name].phrase_priority
        return 110
    if fact.kind == "measure" and fact.concept_name in MEASURE_DEFINITIONS:
        return MEASURE_DEFINITIONS[fact.concept_name].phrase_priority
    if fact.kind == "predicate" and fact.concept_name in BUSINESS_PREDICATE_DEFINITIONS:
        return BUSINESS_PREDICATE_DEFINITIONS[fact.concept_name].phrase_priority
    if (
        fact.kind == "semantic_intent"
        and fact.concept_name in RETRIEVAL_INTENT_DEFINITIONS
    ):
        return RETRIEVAL_INTENT_DEFINITIONS[fact.concept_name].phrase_priority
    return 0


def _facts_have_same_meaning(left: SemanticFact, right: SemanticFact) -> bool:
    return (
        left.kind,
        left.field,
        left.operator,
        left.values,
        left.concept_name,
        left.origin,
    ) == (
        right.kind,
        right.field,
        right.operator,
        right.values,
        right.concept_name,
        right.origin,
    )


def _predicate_conflict(left: SemanticFact, right: SemanticFact) -> bool:
    if left.concept_name is None or right.concept_name is None:
        return False
    left_definition = BUSINESS_PREDICATE_DEFINITIONS[left.concept_name]
    right_definition = BUSINESS_PREDICATE_DEFINITIONS[right.concept_name]
    if (
        right.concept_name in left_definition.incompatible_with
        or left.concept_name in right_definition.incompatible_with
    ):
        return True
    return not (
        right.concept_name in left_definition.composes_with
        or left.concept_name in right_definition.composes_with
    )


def _fact_constraints(fact: SemanticFact):
    if fact.kind == "predicate" and fact.concept_name is not None:
        return BUSINESS_PREDICATE_DEFINITIONS[fact.concept_name].required_filters
    if fact.kind == "filter" and fact.field is not None and fact.operator is not None:
        value = fact.values if fact.operator == "in" else fact.values[0]
        return ((fact.field, fact.operator, value),)
    return ()


def _constraint_parts(constraint):
    if hasattr(constraint, "field"):
        return constraint.field, constraint.operator, constraint.value
    return constraint


def _finite_value_set(operator, value):
    if operator == "eq":
        return {value}
    if operator == "in":
        return set(value)
    return None


def _constraints_conflict(left: SemanticFact, right: SemanticFact) -> bool:
    for left_constraint in _fact_constraints(left):
        left_field, left_operator, left_value = _constraint_parts(left_constraint)
        left_values = _finite_value_set(left_operator, left_value)
        if left_values is None:
            continue
        for right_constraint in _fact_constraints(right):
            right_field, right_operator, right_value = _constraint_parts(
                right_constraint
            )
            if left_field != right_field:
                continue
            right_values = _finite_value_set(right_operator, right_value)
            if right_values is not None and left_values.isdisjoint(right_values):
                return True
    return False


def _overlapping_facts_conflict(left: SemanticFact, right: SemanticFact) -> bool:
    if _facts_have_same_meaning(left, right):
        return False
    if left.kind == right.kind == "predicate":
        return _predicate_conflict(left, right)
    if left.kind == right.kind and left.kind in {
        "field",
        "filter",
        "measure",
        "semantic_intent",
    }:
        return True
    if _constraints_conflict(left, right):
        return True
    if {left.kind, right.kind} == {"field", "predicate"}:
        return True
    if {left.kind, right.kind} == {"filter", "predicate"}:
        return True
    return False


def _select_longest_supported_facts(
    question: str, facts: Iterable[SemanticFact]
) -> tuple[SemanticFact, ...]:
    """Keep the longest supported meaning while preserving compatible facts."""
    ranked = []
    for index, fact in enumerate(facts):
        spans = _evidence_spans(question, fact.evidence_text)
        if not spans:
            spans = ((index, index + 1),)
        ranked.extend(
            (span[1] - span[0], _fact_priority(fact), -span[0], span, fact)
            for span in spans
            if fact.kind not in {"filter", "predicate"}
            or not _span_follows_unsupported_operator(question, span)
        )
    ranked.sort(key=lambda item: item[:3], reverse=True)

    selected: list[tuple[tuple[int, int], SemanticFact]] = []
    for length, priority, _, span, fact in ranked:
        if any(
            span == existing_span and _facts_have_same_meaning(fact, existing)
            for existing_span, existing in selected
        ):
            continue
        suppressed = False
        for existing_span, existing in selected:
            overlaps = span[0] < existing_span[1] and existing_span[0] < span[1]
            if not overlaps or not _overlapping_facts_conflict(fact, existing):
                continue
            existing_rank = (
                existing_span[1] - existing_span[0],
                _fact_priority(existing),
            )
            if existing_rank > (length, priority):
                suppressed = True
                break
        if not suppressed:
            selected.append((span, fact))
    deduplicated = []
    for _, fact in sorted(selected, key=lambda item: item[0]):
        if not any(_facts_have_same_meaning(fact, item) for item in deduplicated):
            deduplicated.append(fact)
    return tuple(deduplicated)


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


def _calculation_facts(
    question: str,
    selected_fields: tuple[str, ...],
    existing_facts: list[SemanticFact],
) -> list[SemanticFact]:
    facts = []
    for name, definition in CALCULATION_DEFINITIONS.items():
        matches = [
            match
            for pattern in definition.detection_patterns
            if (match := re.search(pattern, question, re.I)) is not None
        ]
        if not matches:
            continue
        evidence = min(matches, key=lambda match: match.start()).group(0)
        if name == "percentage":
            subjects = []
            for measure in MEASURE_DEFINITIONS.values():
                if any(
                    evidence_occurs(question, phrase)
                    for phrase in measure.natural_names
                ):
                    subjects.append(measure.aggregation_field)
            subjects = list(dict.fromkeys(subjects))
            if len(subjects) != 1:
                continue
            field = subjects[0]
        else:
            numeric_fields = [
                field
                for field in selected_fields
                if FIELD_DEFINITIONS[field].storage_type == "number"
                and FIELD_DEFINITIONS[field].aggregatable
                and not any(
                    fact.kind == "filter" and fact.field == field
                    for fact in existing_facts
                )
            ]
            if len(numeric_fields) != 1:
                continue
            field = numeric_fields[0]
        facts.append(
            SemanticFact(
                kind="calculation",
                field=field,
                concept_name=name,
                evidence_text=evidence,
                origin="question",
                strength="strong",
            )
        )
    return facts


def _grouping_facts(
    question: str, field_matches: list[tuple[str, str]]
) -> list[SemanticFact]:
    normalized_question = normalize_semantic_text(question)
    facts = []
    for field, phrase in field_matches:
        normalized_phrase = normalize_semantic_text(phrase)
        match = re.search(
            rf"\b(?:by|per|for each|in each|each)\s+(?:the\s+)?{re.escape(normalized_phrase)}\b",
            normalized_question,
        )
        if match is None:
            continue
        facts.append(
            SemanticFact(
                kind="group_by",
                field=field,
                evidence_text=match.group(0),
                origin="question",
                strength="strong",
            )
        )
    return facts


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
                    operator=concept.operator,
                    values=members,
                    concept_name=name,
                    evidence_text=phrase,
                    origin="question",
                    strength="strong",
                )
            )
    facts.extend(_grouping_facts(question, maximal_field_matches))
    facts.extend(_calculation_facts(question, selected_fields, facts))
    if re.search(r"\b(?:how many|count|number of|total number)\b", question, re.I):
        facts.extend(_earliest_measure_facts(question))
    predicate_facts = _facts_for_named_phrases(
        question, "predicate", BUSINESS_PREDICATE_DEFINITIONS
    )
    facts.extend(predicate_facts)
    facts.extend(
        _facts_for_named_phrases(
            question, "semantic_intent", RETRIEVAL_INTENT_DEFINITIONS
        )
    )
    return merge_semantic_facts(_select_longest_supported_facts(question, facts))


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
