"""Pure, registry-driven semantic detection and value canonicalization."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from collections.abc import Iterable, Mapping
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

try:
    from .attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        CALCULATION_DEFINITIONS,
        COLLECTIVE_MODIFIER_PATTERN,
        CONSTRAINT_CLAUSE_GRAMMAR,
        canonicalize_storage_value,
        FIELD_DEFINITIONS,
        FILTER_OPERATOR_DEFINITIONS,
        MEASURE_DEFINITIONS,
        ORDERING_ROLE_PATTERNS,
        RETRIEVAL_INTENT_DEFINITIONS,
        VALUE_CONCEPT_DEFINITIONS,
        UNSUPPORTED_FILTER_OPERATOR_PATTERN,
        UNSUPPORTED_REQUEST_PATTERNS,
        EvidenceOrigin,
        FilterOperator,
        ResolutionKind,
    )
except ImportError:  # Direct execution from week5/new_implementation.
    from attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        CALCULATION_DEFINITIONS,
        COLLECTIVE_MODIFIER_PATTERN,
        CONSTRAINT_CLAUSE_GRAMMAR,
        canonicalize_storage_value,
        FIELD_DEFINITIONS,
        FILTER_OPERATOR_DEFINITIONS,
        MEASURE_DEFINITIONS,
        ORDERING_ROLE_PATTERNS,
        RETRIEVAL_INTENT_DEFINITIONS,
        VALUE_CONCEPT_DEFINITIONS,
        UNSUPPORTED_FILTER_OPERATOR_PATTERN,
        UNSUPPORTED_REQUEST_PATTERNS,
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
    return re.search(UNSUPPORTED_FILTER_OPERATOR_PATTERN, between) is not None


def _unsupported_operator_before_span(
    question: str, span: tuple[int, int]
) -> tuple[str, tuple[int, int]] | None:
    tokens = normalize_semantic_text(question).split()
    for token_count in (2, 1):
        start = span[0] - token_count
        if start < 0:
            continue
        evidence = " ".join(tokens[start : span[0]])
        if re.fullmatch(UNSUPPORTED_FILTER_OPERATOR_PATTERN, evidence):
            return evidence, (start, span[0])
    return None


def _unsupported_fact(concept_name: str, evidence_text: str) -> "SemanticFact":
    return SemanticFact(
        kind="unsupported",
        concept_name=concept_name,
        evidence_text=evidence_text,
        origin="question",
        strength="strong",
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
    evidence_span: tuple[int, int] | None = None
    direction: Literal["asc", "desc"] | None = None
    scope: Literal["population", "percentage_numerator"] = "population"


@dataclass(frozen=True)
class ResolutionContext:
    catalog: Mapping[str, tuple[str, ...]]
    employees: tuple[EmployeeReference, ...] = ()
    reference_date: date | None = None


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
        self,
        question: str,
        field: str,
        context: ResolutionContext,
        *,
        categorical_clauses=None,
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
        if not field_facts and definition.resolution_kind not in {
            "catalog",
            "closed_value",
        }:
            return ()
        field_evidence = field_facts[0].evidence_text if field_facts else ""
        if definition.resolution_kind in {"catalog", "closed_value"}:
            return field_facts + _categorical_field_facts(
                question, field, field_evidence, context, categorical_clauses
            )
        values: list[tuple[object, str]] = []
        rejected_facts: list[SemanticFact] = []
        if definition.resolution_kind == "identifier":
            match = re.search(r"\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b", question)
            if match:
                values.append((match.group(0), match.group(0)))
        elif definition.resolution_kind == "entity":
            values.extend(
                (employee.name, employee.name)
                for employee in context.employees
                if evidence_occurs(question, employee.name)
            )
        elif definition.resolution_kind == "temporal":
            pattern = (
                r"\b\d{4}-(?:0[1-9]|1[0-2])\b"
                if field == "Period"
                else (
                    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b"
                    if definition.storage_type == "datetime"
                    else (
                        r"\b\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b"
                        if definition.storage_type == "time"
                        else r"\b\d{4}-\d{2}-\d{2}\b"
                    )
                )
            )
            match = re.search(pattern, question)
            if match:
                values.append((match.group(0), match.group(0)))
        value_facts = []
        for value, evidence in dict.fromkeys(values):
            if _unsupported_operator_between(question, field_evidence, evidence):
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
                    operator="eq",
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


def _raw_phrase_spans(text: str, phrase: str) -> tuple[tuple[int, int], ...]:
    """Locate a registry phrase without losing the source character offsets."""
    tokens = normalize_semantic_text(phrase).split()
    if not tokens:
        return ()
    pattern = r"[\W_]+".join(re.escape(token) for token in tokens)
    return tuple(
        match.span() for match in re.finditer(rf"(?<!\w){pattern}(?!\w)", text, re.I)
    )


@dataclass(frozen=True)
class CategoricalBinding:
    value_span: tuple[int, int]
    operator: FilterOperator | None
    constraint_span: tuple[int, int]


def _registered_filter_operator(text):
    """Retain an operator role even when its semantics have no native operator."""
    grammar = " ".join(text.casefold().split())
    for operator, definition in FILTER_OPERATOR_DEFINITIONS.items():
        if re.fullmatch(definition.detection_pattern, grammar):
            return True, operator
    negated = re.fullmatch(r"(?:(?:does|do|is) )?not (.+)", grammar)
    if negated:
        for definition in FILTER_OPERATOR_DEFINITIONS.values():
            if re.fullmatch(definition.detection_pattern, negated.group(1)):
                return True, definition.negated_operator
    return bool(re.fullmatch(UNSUPPORTED_FILTER_OPERATOR_PATTERN, grammar)), None


def _field_has_collective_modifier(question, start, end):
    return bool(
        re.search(r"\b(?:by|per|each|every|all|any)\s*$", question[:start], re.I)
        and re.match(rf"\s+{COLLECTIVE_MODIFIER_PATTERN}\b", question[end:], re.I)
    )


def _adjacent_categorical_bindings(question, field_evidence, value_evidence):
    """Bind value-as-field-modifier phrases outside explicit consumed clauses."""
    value_spans = _raw_phrase_spans(question, value_evidence)
    field_spans = {
        span
        for form in _token_forms(field_evidence)
        for span in _raw_phrase_spans(question, form)
    }
    candidates = []
    for field_start, field_end in field_spans:
        if _field_has_collective_modifier(question, field_start, field_end):
            continue
        field_is_subject = re.search(
            r"\b(?:which|by|per|each|every|all|any)\s*$", question[:field_start], re.I
        )
        for value_start, value_end in value_spans:
            if not (field_end <= value_start or value_end <= field_start):
                continue
            gap = question[min(field_end, value_end) : max(field_start, value_start)]
            adjacent = not gap.strip() and not (
                field_end <= value_start and field_is_subject
            )
            if adjacent:
                candidates.append(
                    (
                        len(normalize_semantic_text(gap)),
                        CategoricalBinding(
                            (value_start, value_end),
                            "eq",
                            (min(field_start, value_start), max(field_end, value_end)),
                        ),
                    )
                )
    if not candidates:
        return ()
    distance = min(item[0] for item in candidates)
    nearest = {binding for gap, binding in candidates if gap == distance}
    return tuple(sorted(nearest, key=lambda item: item.value_span))


@dataclass(frozen=True)
class ConstraintClause:
    field: str
    operator: FilterOperator | None
    field_span: tuple[int, int]
    operator_span: tuple[int, int]
    operand_span: tuple[int, int]
    consumed_span: tuple[int, int]
    values: tuple[str | float, ...] = ()
    member_spans: tuple[tuple[int, int], ...] = ()
    violation: str | None = None


def _registry_field_occurrences(question):
    candidates = {
        (field, start, end)
        for field, definition in FIELD_DEFINITIONS.items()
        if definition.planner_visible
        for phrase in definition.natural_names
        for form in _token_forms(phrase)
        for start, end in _raw_phrase_spans(question, form)
    }
    return tuple(
        sorted(
            (
                item
                for item in candidates
                if not any(
                    other[1] <= item[1]
                    and item[2] <= other[2]
                    and other[2] - other[1] > item[2] - item[1]
                    for other in candidates
                )
            ),
            key=lambda item: (item[1], -item[2], item[0]),
        )
    )


def _operator_prefix(question, start, end, field=None):
    matches = []
    for token in re.finditer(r"\w+|[^\w\s]+", question[start:end]):
        stop = start + token.end()
        recognized, operator = _registered_filter_operator(question[start:stop])
        if (
            not recognized
            and field is not None
            and FIELD_DEFINITIONS[field].resolution_kind == "temporal"
        ):
            phrase = " ".join(question[start:stop].casefold().split())
            operator = TemporalResolver._operators.get(phrase)
            recognized = operator is not None
        if recognized:
            matches.append((stop, operator))
    if not matches:
        return False, None, start
    stop, operator = max(matches, key=lambda item: item[0])
    return True, operator, stop


def _trim_operand_span(question, start, end):
    while start < end and question[start].isspace():
        start += 1
    while end > start and (question[end - 1].isspace() or question[end - 1] in "?.!"):
        end -= 1
    return start, end


def _resolve_complete_operand(question, field, operator, span, context):
    """Resolve all source characters and every list member, or return no values."""
    registry = ResolverRegistry.default()
    start, end = _trim_operand_span(question, *span)
    if start == end or operator not in FIELD_DEFINITIONS[field].operators:
        return (), (), "unsupported_constraint"

    def atom(lo, hi):
        lo, hi = _trim_operand_span(question, lo, hi)
        if lo == hi:
            return None
        raw = question[lo:hi]
        if FIELD_DEFINITIONS[field].resolution_kind == "identifier" and not any(
            fact.kind == "filter"
            and fact.field == field
            and fact.strength == "strong"
            and fact.evidence_text == raw
            for fact in registry.for_kind("identifier").detect(raw, field, context)
        ):
            return None
        result = registry.canonicalize(field, raw, raw, context, operator=operator)
        if result.status == "resolved" and len(result.values) == 1:
            return result.values[0], (lo, hi)
        return None

    if operator != "in":
        resolved = atom(start, end)
        return (
            ((resolved[0],), (resolved[1],), None)
            if resolved
            else ((), (), "unsupported_constraint")
        )
    separators = tuple(
        re.finditer(
            CONSTRAINT_CLAUSE_GRAMMAR["list_separator"], question[start:end], re.I
        )
    )
    memo = {}

    def members(cursor):
        if cursor in memo:
            return memo[cursor]
        solutions = []
        endpoints = [
            (start + match.start(), start + match.end())
            for match in separators
            if start + match.start() >= cursor
        ]
        for stop, following in [*endpoints, (end, None)]:
            resolved = atom(cursor, stop)
            if resolved is None:
                continue
            tails = ((),) if following is None else members(following)
            for tail in tails:
                solution = (resolved, *tail)
                if solution not in solutions:
                    solutions.append(solution)
                if len(solutions) > 1:
                    memo[cursor] = tuple(solutions)
                    return memo[cursor]
        memo[cursor] = tuple(solutions)
        return memo[cursor]

    solutions = members(start)
    if len(solutions) != 1:
        return (
            (),
            (),
            "ambiguous_value_binding" if solutions else "unsupported_constraint",
        )
    return (
        tuple(item[0] for item in solutions[0]),
        tuple(item[1] for item in solutions[0]),
        None,
    )


def _preposed_numeric_clause(question, field, start, end, fields, context):
    """Prove an operator + complete operand immediately before its numeric field."""
    if FIELD_DEFINITIONS[field].resolution_kind != "numeric":
        return None
    lower = max((hi for _, _, hi in fields if hi <= start), default=0)
    scope_starts = tuple(
        lower + match.end()
        for match in re.finditer(
            CONSTRAINT_CLAUSE_GRAMMAR["clause_separator"], question[lower:start], re.I
        )
    )
    lower = max(scope_starts, default=lower)
    for token in re.finditer(r"\w+|[^\w\s]+", question[lower:start]):
        operator_start = lower + token.start()
        recognized, operator, operator_end = _operator_prefix(
            question, operator_start, start, field
        )
        if not recognized or operator_end == start:
            continue
        span = _trim_operand_span(question, operator_end, start)
        values, members, violation = _resolve_complete_operand(
            question, field, operator, span, context
        )
        if violation is None or operator != "eq" or scope_starts:
            return ConstraintClause(
                field,
                operator,
                (start, end),
                (operator_start, operator_end),
                span,
                (operator_start, end),
                values,
                members,
                violation,
            )
    return None


def _clause_operand_ends(question, start, fields, context, field, operator, cache=None):
    """Keep grounded atoms whole and split only before complete native clauses."""
    cache = {} if cache is None else cache
    key = (start, field, operator)
    if key in cache:
        return cache[key]
    definition = FIELD_DEFINITIONS[field]
    literal_values = (
        *definition.closed_values,
        *context.catalog.get(field, ()),
        *(alias.natural_name for alias in definition.value_aliases),
    )
    if (
        operator in FILTER_OPERATOR_DEFINITIONS
        and FILTER_OPERATOR_DEFINITIONS[operator].uses_text_pattern
    ):
        literal_values = _categorical_literal_fragments(field, context)
    atomic_spans = {
        (lo, hi)
        for value in literal_values
        for lo, hi in _raw_phrase_spans(question, value)
        if start <= lo
    }
    hard_end = len(question)
    for next_field, field_start, field_end in fields:
        if field_start <= start:
            continue
        preposed = _preposed_numeric_clause(
            question, next_field, field_start, field_end, fields, context
        )
        if not preposed and not question[field_end:].strip(" \t\r\n?.!"):
            continue
        clause_start = preposed.consumed_span[0] if preposed else field_start
        separator = re.search(
            CONSTRAINT_CLAUSE_GRAMMAR["coordinator"], question[start:clause_start], re.I
        )
        if not separator:
            continue
        boundary = start + separator.start()
        if any(lo < boundary and field_end <= hi for lo, hi in atomic_spans):
            continue
        if preposed and preposed.violation is None:
            hard_end = min(hard_end, boundary)
            continue
        recognized, next_operator, operand_start = _operator_prefix(
            question, field_end, len(question), next_field
        )
        if not recognized:
            next_operator, operand_start = "eq", field_end
        if next_operator not in FIELD_DEFINITIONS[next_field].operators:
            continue
        suffix_ends = _clause_operand_ends(
            question, operand_start, fields, context, next_field, next_operator, cache
        )
        if any(
            _resolve_complete_operand(
                question, next_field, next_operator, (operand_start, end), context
            )[2]
            is None
            for end in suffix_ends
        ):
            hard_end = min(hard_end, boundary)
    ends = {hard_end}
    ends.update(
        start + match.start()
        for match in re.finditer(
            CONSTRAINT_CLAUSE_GRAMMAR["identity_scope"], question[start:hard_end], re.I
        )
    )
    field_matches = [(field, question[lo:hi]) for field, lo, hi in fields]
    for fact in _grouping_facts(question, field_matches):
        ends.update(
            lo
            for lo, _ in _raw_phrase_spans(question, fact.evidence_text)
            if start <= lo < hard_end
        )
    for reference_start, _ in TemporalResolver.reference_spans(question):
        if start <= reference_start < hard_end:
            _, _, operator_start = TemporalResolver._operator(
                question[start:reference_start]
            )
            if operator_start is not None:
                ends.add(start + operator_start)
    cache[key] = tuple(sorted(ends, reverse=True))
    return cache[key]


def _parse_constraint_clauses(
    question, context, *, excluded_spans=(), include_typed=False
):
    fields = tuple(
        item
        for item in _registry_field_occurrences(question)
        if not any(lo <= item[1] and item[2] <= hi for lo, hi in excluded_spans)
    )
    value_spans = {
        span
        for target, definition in FIELD_DEFINITIONS.items()
        if definition.resolution_kind in {"catalog", "closed_value"}
        for value in (
            *definition.closed_values,
            *context.catalog.get(target, ()),
            *(alias.natural_name for alias in definition.value_aliases),
        )
        for span in _raw_phrase_spans(question, value)
    }
    value_spans.update(
        span
        for registry in (
            BUSINESS_PREDICATE_DEFINITIONS,
            MEASURE_DEFINITIONS,
            VALUE_CONCEPT_DEFINITIONS,
        )
        for definition in registry.values()
        for phrase in definition.natural_names
        for span in _raw_phrase_spans(question, phrase)
    )
    projection = re.search(CONSTRAINT_CLAUSE_GRAMMAR["projection"], question, re.I)
    projection_fields = []
    if projection:
        remainder = question[projection.start(1) : projection.end(1)]
        projection_fields = [
            item
            for item in fields
            if projection.start(1) <= item[1] and item[2] <= projection.end(1)
        ]
        for _, lo, hi in reversed(projection_fields):
            lo, hi = lo - projection.start(1), hi - projection.start(1)
            remainder = remainder[:lo] + " " * (hi - lo) + remainder[hi:]
        if re.sub(r"\b(?:and|the|only)\b|[\s,?.!]", "", remainder, flags=re.I):
            projection_fields = []
    clauses = []
    for field, start, end in fields:
        definition = FIELD_DEFINITIONS[field]
        categorical = definition.resolution_kind in {"catalog", "closed_value"}
        if not categorical and not (
            include_typed
            and definition.resolution_kind in {"numeric", "temporal", "identifier"}
        ):
            continue
        if any(
            lo <= start < hi for clause in clauses for lo, hi in (clause.consumed_span,)
        ) or _field_has_collective_modifier(question, start, end):
            continue
        recognized, operator, operator_end = _operator_prefix(
            question, end, len(question), field
        )
        preposed = (
            _preposed_numeric_clause(question, field, start, end, fields, context)
            if include_typed
            else None
        )
        if not recognized and (
            any(
                lo <= start and end <= hi and (lo, hi) != (start, end)
                for lo, hi in value_spans
            )
            or (field, start, end) in projection_fields
            or any(
                not question[rank.end() : start].strip()
                for rank in re.finditer(
                    ORDERING_ROLE_PATTERNS["limit"], question[:start], re.I
                )
            )
        ):
            continue
        if not recognized and not preposed and re.match(r"\s*[,;?!]", question[end:]):
            continue
        operator = operator if recognized else "eq"
        operand_start = operator_end if recognized else end
        candidates = _clause_operand_ends(
            question, operand_start, fields, context, field, operator
        )
        if definition.resolution_kind == "numeric" and any(
            end <= lo
            and not question[end:lo].strip()
            and any(
                _trim_operand_span(question, end, stop)[1] == hi for stop in candidates
            )
            for lo, hi in TemporalResolver.scope_spans(question)
        ):
            continue
        resolved = []
        for candidate_end in candidates:
            span = _trim_operand_span(question, operand_start, candidate_end)
            values, member_spans, violation = _resolve_complete_operand(
                question, field, operator, span, context
            )
            if violation is None:
                resolved.append((span, values, member_spans))
        if preposed:
            tail_empty = not question[end:].strip(" \t\r\n?.!")
            independent_tail = any(
                not question[end:candidate].strip() for candidate in candidates
            )
            if not recognized and not resolved and (tail_empty or independent_tail):
                clauses.append(preposed)
            else:
                clauses.append(
                    ConstraintClause(
                        field,
                        None,
                        (start, end),
                        preposed.operator_span,
                        (preposed.operand_span[0], candidates[0]),
                        (preposed.consumed_span[0], candidates[0]),
                        violation="ambiguous_value_binding",
                    )
                )
            continue
        non_constraint_role = re.search(
            CONSTRAINT_CLAUSE_GRAMMAR["non_constraint_prefix"], question[:start], re.I
        )
        if (
            not recognized
            and not resolved
            and (
                non_constraint_role
                or not question[end:].strip(" \t\r\n?.!")
                or (
                    any(not question[end:candidate].strip() for candidate in candidates)
                    and re.match(
                        CONSTRAINT_CLAUSE_GRAMMAR["scope_boundary"],
                        question[end:],
                        re.I,
                    )
                )
            )
        ):
            continue
        span, values, member_spans = (
            resolved[0]
            if resolved
            else (_trim_operand_span(question, operand_start, candidates[0]), (), ())
        )
        violation = (
            None
            if resolved
            else "unsupported_constraint"
            if operator in definition.operators
            else "unsupported_operator"
        )
        if len(resolved) > 1:
            violation = "ambiguous_value_binding"
        if not recognized and resolved:
            preposed = any(
                value_end <= start and not question[value_end:start].strip()
                for value in (
                    *definition.closed_values,
                    *context.catalog.get(field, ()),
                )
                for _, value_end in _raw_phrase_spans(question, value)
            )
            if preposed:
                violation = "ambiguous_value_binding"
        # Numeric and categorical facts are owned by this clause parser. Other
        # typed fields retain their authoritative detector's rejection path.
        if (
            definition.resolution_kind not in {"catalog", "closed_value", "numeric"}
            and violation is not None
        ):
            continue
        clauses.append(
            ConstraintClause(
                field,
                operator,
                (start, end),
                (end, operator_end),
                span,
                (start, max(end, span[1])),
                values if violation is None else (),
                member_spans if violation is None else (),
                violation,
            )
        )
    return tuple(clauses)


def _categorical_field_facts(question, field, field_evidence, context, clauses=None):
    clauses = (
        _parse_constraint_clauses(question, context) if clauses is None else clauses
    )
    facts = []
    for clause in clauses:
        if clause.field != field:
            continue
        if clause.violation:
            facts.append(
                _unsupported_fact(
                    clause.violation, question[slice(*clause.consumed_span)].strip()
                )
            )
        else:
            facts.append(
                SemanticFact(
                    kind="filter",
                    field=field,
                    operator=clause.operator,
                    values=clause.values,
                    evidence_text=question[slice(*clause.operand_span)],
                    evidence_span=clause.operand_span,
                    origin="question",
                    strength="strong",
                )
            )
    if any(
        clause.field == field and clause.violation == "ambiguous_value_binding"
        for clause in clauses
    ):
        return tuple(facts)
    definition = FIELD_DEFINITIONS[field]
    values = [
        (value, value)
        for value in (*definition.closed_values, *context.catalog.get(field, ()))
    ]
    values.extend(
        (alias.canonical_value, alias.natural_name)
        for alias in definition.value_aliases
    )
    for value, evidence in dict.fromkeys(values):
        bindings = _adjacent_categorical_bindings(question, field_evidence, evidence)
        if not bindings:
            spans = _implicit_categorical_value_spans(question, evidence)
            if spans and _categorical_value_owners(value, context) != {field}:
                facts.append(_unsupported_fact("ambiguous_value_binding", evidence))
                continue
            bindings = tuple(CategoricalBinding(span, "eq", span) for span in spans)
        bindings = tuple(
            binding
            for binding in bindings
            if not any(
                binding.value_span[0] < clause.consumed_span[1]
                and clause.consumed_span[0] < binding.value_span[1]
                for clause in clauses
            )
        )
        if len(bindings) > 1:
            facts.append(_unsupported_fact("ambiguous_value_binding", evidence))
        elif bindings:
            facts.append(
                SemanticFact(
                    kind="filter",
                    field=field,
                    operator="eq",
                    values=(value,),
                    evidence_text=evidence,
                    evidence_span=bindings[0].value_span,
                    origin="question",
                    strength="strong",
                )
            )
    return tuple(facts)


def _categorical_literal_fragments(field, context):
    definition = FIELD_DEFINITIONS[field]
    return tuple(
        sorted(
            {
                " ".join(tokens[start:end])
                for value in (
                    *context.catalog.get(field, ()),
                    *definition.closed_values,
                )
                for tokens in (str(value).split(),)
                for start in range(len(tokens))
                for end in range(start + 1, len(tokens) + 1)
            }
        )
    )


def _categorical_value_owners(value, context):
    return {
        field
        for field, definition in FIELD_DEFINITIONS.items()
        if definition.planner_visible
        and definition.resolution_kind in {"catalog", "closed_value"}
        if any(
            normalize_semantic_text(value) == normalize_semantic_text(candidate)
            for candidate in (
                *definition.closed_values,
                *context.catalog.get(field, ()),
                *(alias.canonical_value for alias in definition.value_aliases),
            )
        )
    }


def _implicit_categorical_value_spans(question, evidence):
    """Prove membership or a nominal modifier without borrowing an operation role."""
    subjects = {
        form
        for definition in MEASURE_DEFINITIONS.values()
        for phrase in definition.natural_names
        for form in _token_forms(phrase)
    }
    operation_word = any(
        re.fullmatch(pattern, evidence, re.I)
        for pattern in (
            *ORDERING_ROLE_PATTERNS.values(),
            *(
                pattern
                for definition in CALCULATION_DEFINITIONS.values()
                for pattern in definition.detection_patterns
            ),
        )
    ) or any(
        normalize_semantic_text(evidence) == normalize_semantic_text(phrase)
        for definition in FIELD_DEFINITIONS.values()
        if definition.storage_type == "number"
        for phrase in definition.natural_names
    )
    spans = []
    for start, end in _raw_phrase_spans(question, evidence):
        prefix = normalize_semantic_text(question[:start])
        suffix = normalize_semantic_text(question[end:])
        membership = any(
            re.search(rf"\b{re.escape(subject)}s?\s+(?:in|from)(?:\s+the)?$", prefix)
            for subject in subjects
        )
        modifier = not operation_word and any(
            re.match(rf"{re.escape(subject)}s?\b", suffix) for subject in subjects
        )
        if membership or modifier:
            spans.append((start, end))
    return tuple(spans)


class IdentifierResolver(FieldResolver):
    kinds = frozenset({"identifier"})

    def detect(self, question, field, context):
        facts = []
        for match in re.finditer(
            r"\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b", question
        ):
            value = match.group(0)
            if any(
                normalize_semantic_text(value) == normalize_semantic_text(phrase)
                for definition in FIELD_DEFINITIONS.values()
                for phrase in definition.natural_names
            ):
                continue
            if re.fullmatch(r"[A-Za-z]\d{5}", value) is None:
                facts.append(_unsupported_fact("malformed_identifier", value))
                continue
            facts.extend(
                (
                    SemanticFact(
                        kind="entity",
                        field=field,
                        values=(value,),
                        evidence_text=value,
                        origin="question",
                        strength="candidate",
                    ),
                    SemanticFact(
                        kind="filter",
                        field=field,
                        operator="eq",
                        values=(value,),
                        evidence_text=value,
                        origin="question",
                        strength="strong",
                    ),
                )
            )
        return tuple(facts)

    def canonicalize(self, field, raw_value, evidence_text, context):
        value = str(raw_value).strip()
        return (
            ResolutionOutcome("resolved", (value,))
            if value
            else ResolutionOutcome("unknown")
        )


class EntityResolver(FieldResolver):
    kinds = frozenset({"entity"})

    def detect(self, question, field, context):
        temporal_spans = TemporalResolver.reference_spans(question)
        temporal_fields = TemporalResolver.field_spans(question)
        protected_spans = [
            span
            for definition in (
                *VALUE_CONCEPT_DEFINITIONS.values(),
                *MEASURE_DEFINITIONS.values(),
                *BUSINESS_PREDICATE_DEFINITIONS.values(),
            )
            for phrase in definition.natural_names
            for span in _raw_phrase_spans(question, phrase)
        ]
        non_entity_field_spans = []
        categorical_clauses = _parse_constraint_clauses(question, context)
        categorical_operator_spans = [
            clause.operator_span for clause in categorical_clauses
        ]
        protected_spans.extend(clause.consumed_span for clause in categorical_clauses)
        for target, definition in FIELD_DEFINITIONS.items():
            if definition.resolution_kind in {"entity", "identifier"}:
                continue
            field_spans = [
                span
                for phrase in definition.natural_names
                for span in _raw_phrase_spans(question, phrase)
            ]
            non_entity_field_spans.extend(field_spans)
            if field_spans:
                values = (*context.catalog.get(target, ()), *definition.closed_values)
                protected_spans.extend(
                    span
                    for value in values
                    for span in _raw_phrase_spans(question, value)
                )
        syntax_spans = []
        for match in re.finditer(
            r"\b(?P<name>[^\W\d_][\w'-]*(?:\s+[^\W\d_][\w'-]*)*)['’]s\b", question
        ):
            name = match.group("name")
            command = re.match(
                r"(?:(?:what|how)\s+(?:is|are|was|were)|count|show|list|find|summarize)\s+",
                name,
                re.I,
            )
            syntax_spans.append(
                (
                    match.start("name") + (command.end() if command else 0),
                    match.end("name"),
                )
            )
        for match in re.finditer(
            r"\b(?:for|did|does|do|named|belong(?:s)?\s+to)\s+(?P<name>[^\W\d_][\w'-]*(?:\s+[^\W\d_][\w'-]*)*)",
            question,
            re.I,
        ):
            start, end = match.span("name")
            boundary = re.search(
                r"\s+(?:have|has|had|work|worked|attend|attended|on|in|and|with|who|whose|that|which)\b",
                question[start:end],
                re.I,
            )
            name_end = start + boundary.start() if boundary else end
            for reference_start, _ in temporal_spans:
                if reference_start < start:
                    continue
                preceding_fields = [
                    (begin - start, stop - start)
                    for begin, stop, _ in temporal_fields
                    if start <= begin and stop <= reference_start
                ]
                local_field = max(preceding_fields, default=None)
                _, _, operator_start = TemporalResolver._operator(
                    question[start:reference_start], local_field
                )
                if operator_start is not None:
                    name_end = min(name_end, start + operator_start)
                if local_field is not None:
                    name_end = min(name_end, start + local_field[0])
            while name_end > start and question[name_end - 1].isspace():
                name_end -= 1
            syntax_spans.append((start, name_end))
        known_spans = [
            span
            for employee in context.employees
            for span in _raw_phrase_spans(question, employee.name)
        ]
        facts = list(super().detect(question, field, context))
        candidates = []
        for start, end in sorted(
            set(syntax_spans + known_spans),
            key=lambda span: (span[0], -(span[1] - span[0])),
        ):
            reference = question[start:end]
            if not reference or re.search(r"\d", reference):
                continue
            if any(begin <= start < stop for begin, stop in categorical_operator_spans):
                continue
            quantified = re.fullmatch(
                rf"(?:each|every|all|any)\s+(?:the\s+)?(.+?)(?:\s+{COLLECTIVE_MODIFIER_PATTERN})?",
                normalize_semantic_text(reference),
            )
            if quantified and any(
                set(_token_forms(quantified.group(1))) & set(_token_forms(phrase))
                for definition in (
                    *FIELD_DEFINITIONS.values(),
                    *MEASURE_DEFINITIONS.values(),
                )
                for phrase in definition.natural_names
            ):
                continue
            if any(start < stop and begin < end for begin, stop in temporal_spans):
                continue
            if any(begin <= start and end <= stop for begin, stop in protected_spans):
                continue
            if any(
                begin == start and stop <= end for begin, stop in non_entity_field_spans
            ):
                continue
            # A fully registered concept is a constraint, not a new person's name.
            definitions = (
                *VALUE_CONCEPT_DEFINITIONS.values(),
                *MEASURE_DEFINITIONS.values(),
                *BUSINESS_PREDICATE_DEFINITIONS.values(),
            )
            if any(
                normalize_semantic_text(reference) == normalize_semantic_text(phrase)
                for definition in definitions
                for phrase in definition.natural_names
            ):
                continue
            if any(begin <= start and end <= stop for begin, stop in candidates):
                continue
            candidates.append((start, end))
            matches = tuple(
                employee.employee_id
                for employee in context.employees
                if normalize_semantic_text(employee.name)
                == normalize_semantic_text(reference)
            )
            facts.append(
                SemanticFact(
                    kind="entity",
                    field=field,
                    values=matches or (reference,),
                    evidence_text=reference,
                    origin="question",
                    strength="candidate",
                    evidence_span=(start, end),
                )
            )
        return tuple(facts)

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
    _month = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    # Candidate shapes deliberately include malformed suffixes and short years.
    _literal_pattern = re.compile(
        rf"(?<!\w)(?:"
        rf"\d{{4}}-\d{{1,2}}(?:-\d{{1,2}})?(?:[T ]\d{{1,2}}:\d{{2}}(?::\d{{2}}(?:\.\d+)?)?(?:Z|[+-]\d{{2}}:\d{{2}})?)?[\w/+-]*"
        rf"|\d{{1,2}}/\d{{1,2}}/\d{{1,4}}[\w/+-]*"
        rf"|\d{{1,2}}:\d{{2}}(?::\d{{2}}(?:\.\d+)?)?[\w/+-]*"
        rf"|{_month}\s+\d{{1,2}}(?!\d)(?:,?\s+\d{{2,4}})?[\w/-]*"
        rf"|\d{{1,2}}\s+{_month}(?:\s+\d{{2,4}})?[\w/-]*"
        rf")",
        re.I,
    )
    _operators = {
        "on or before": "lte",
        "on or after": "gte",
        "not before": "gte",
        "not after": "lte",
        "no earlier than": "gte",
        "no later than": "lte",
        "earlier than": "lt",
        "later than": "gt",
        "before": "lt",
        "after": "gt",
        "since": "gte",
        "until": "lte",
        "through": "lte",
        "from": "gte",
        "between": "gte",
        "to": "lte",
        "on": "eq",
        "at": "eq",
        "is": "eq",
        "equals": "eq",
        "equal to": "eq",
        ">=": "gte",
        "<=": "lte",
        ">": "gt",
        "<": "lt",
        "=": "eq",
        "==": "eq",
    }

    @classmethod
    def literal_matches(cls, question):
        return tuple(cls._literal_pattern.finditer(question))

    @staticmethod
    def field_spans(question):
        return tuple(
            (start, end, field)
            for field, definition in FIELD_DEFINITIONS.items()
            if definition.resolution_kind == "temporal" and definition.planner_visible
            for phrase in definition.natural_names
            for start, end in _raw_phrase_spans(question, phrase)
        )

    @classmethod
    def reference_spans(cls, question):
        """Protect calendar references as well as literals from entity detection."""
        calendar = rf"\b(?:{cls._month}\s+\d{{4}}|(?:this|last|next|current|previous)\s+(?:day|week|month|quarter|year)|today|yesterday|tomorrow)\b"
        return tuple(
            match.span()
            for match in (
                *cls.literal_matches(question),
                *re.finditer(calendar, question, re.I),
            )
        )

    @classmethod
    def scope_spans(cls, question):
        """Recognize a scope preposition bound to a complete calendar reference."""
        return tuple(
            (match.start(), end)
            for start, end in cls.reference_spans(question)
            if (
                match := re.search(
                    CONSTRAINT_CLAUSE_GRAMMAR["temporal_scope_prefix"],
                    question[:start],
                    re.I,
                )
            )
        )

    def detect(self, question, field, context):
        # All temporal filters come from the per-literal path, including times.
        return tuple(
            fact
            for fact in super().detect(question, field, context)
            if fact.kind == "field"
        )

    @classmethod
    def _operator(cls, prefix, field_span=None):
        after_field = prefix[field_span[1] :] if field_span else ""
        # Mask the field without changing offsets: entity boundaries and temporal
        # evidence must refer to the same complete source operator occurrence.
        operator_text = (
            prefix[: field_span[0]]
            + " " * (field_span[1] - field_span[0])
            + after_field
            if field_span
            else prefix
        )
        expression = "|".join(
            re.escape(phrase).replace(r"\ ", r"\s+")
            for phrase in sorted(cls._operators, key=len, reverse=True)
        )
        match = re.search(
            rf"(?<!\w)(?P<operator>{expression})\s*$", operator_text, re.I
        )
        if match:
            preceding = operator_text[: match.start()]
            modifier = re.search(
                r"(?:\b(?:not|never|approximately|roughly|around)|[<>=!])\s*$",
                preceding,
                re.I,
            )
            if modifier:
                return None, "", modifier.start()
            phrase = " ".join(match.group("operator").casefold().split())
            return cls._operators[phrase], phrase, match.start()
        if field_span and after_field.strip():
            return None, "", field_span[1]
        modifier = re.search(
            r"(?:\b(?:not|never|approximately|roughly|around)|[<>=!])\s*$",
            operator_text,
            re.I,
        )
        if modifier:
            return None, "", modifier.start()
        return "eq", "", None

    @staticmethod
    def _canonical_literal(field, literal, year):
        definition = FIELD_DEFINITIONS[field]
        if "/" in literal:
            raise ValueError("Numeric slash dates need an explicit unambiguous format.")
        if definition.storage_type == "date":
            canonical = re.sub(r"\bsept\b", "Sep", literal, flags=re.I).replace(",", "")
            if re.search(r"\b\d{4}\b", canonical) is None:
                canonical += f" {year}"
            for fmt in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
                try:
                    return datetime.strptime(canonical, fmt).date().isoformat()
                except ValueError:
                    continue
            raise ValueError("Invalid date literal.")
        if definition.storage_type == "datetime" and not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?",
            literal,
        ):
            raise ValueError("Datetime fields require a complete timestamp.")
        return canonicalize_storage_value(field, literal)

    def detect_literals(self, question, selected_fields, context):
        del selected_fields  # Binding is local to each literal, not a global field set.
        facts = []
        matches = self.literal_matches(question)
        field_spans = self.field_spans(question)
        previous = None
        range_start = None
        for match in matches:
            literal = match.group(0)
            preceding_fields = [
                item for item in field_spans if item[1] <= match.start()
            ]
            target_span = (
                max(preceding_fields, key=lambda item: (item[1], item[1] - item[0]))
                if preceding_fields
                else None
            )
            target = target_span[2] if target_span else "Date"
            segment_start = previous.end() if previous else 0
            prefix = question[segment_start : match.start()]
            local_field = (
                (target_span[0] - segment_start, target_span[1] - segment_start)
                if target_span and target_span[0] >= segment_start
                else None
            )
            operator, phrase, operator_start = self._operator(prefix, local_field)
            evidence_start = min(
                match.start(),
                (
                    segment_start + operator_start
                    if operator_start is not None
                    else match.start()
                ),
                target_span[0] if local_field else match.start(),
            )
            paired = bool(
                range_start and re.fullmatch(r"\s*(?:to|and)\s*", prefix, re.I)
            )
            if paired:
                target = range_start.field
                operator = "lte"
            elif range_start is not None:
                range_start = None
            definition = FIELD_DEFINITIONS.get(target)
            if (
                definition is None
                or not definition.filterable
                or not definition.planner_visible
            ):
                facts.append(
                    _unsupported_fact("unrepresentable_temporal_target", literal)
                )
                previous = match
                continue
            if operator is None:
                facts.append(
                    _unsupported_fact(
                        "unsupported_operator", question[evidence_start : match.end()]
                    )
                )
                previous = match
                continue
            # An unbound clock literal cannot borrow an unrelated date target.
            if re.match(r"^\d{1,2}:", literal) and definition.storage_type != "time":
                facts.append(
                    _unsupported_fact("unrepresentable_temporal_target", literal)
                )
                previous = match
                continue
            year = (
                int(str(range_start.values[0])[:4])
                if paired
                else (context.reference_date or date.today()).year
            )
            try:
                value = self._canonical_literal(target, literal, year)
            except ValueError:
                facts.append(
                    _unsupported_fact(
                        "ambiguous_date" if "/" in literal else "malformed_value",
                        literal,
                    )
                )
                previous = match
                continue
            evidence = question[evidence_start : match.end()].strip()
            fact = SemanticFact(
                kind="filter",
                field=target,
                operator=operator,
                values=(value,),
                evidence_text=evidence,
                origin="question",
                strength="strong",
                evidence_span=(evidence_start, match.end()),
            )
            facts.append(fact)
            range_start = fact if phrase in {"from", "between"} else None
            abbreviated_end = (
                re.match(
                    r"\s+and\s+(\d{1,2})(?:,?\s+(\d{4}))?(?![\d/-])\b",
                    question[match.end() :],
                )
                if phrase == "between"
                else None
            )
            if abbreviated_end and definition.storage_type == "date":
                try:
                    end = date.fromisoformat(value).replace(
                        day=int(abbreviated_end.group(1)),
                        year=int(abbreviated_end.group(2) or value[:4]),
                    )
                    facts.append(
                        SemanticFact(
                            kind="filter",
                            field=target,
                            operator="lte",
                            values=(end.isoformat(),),
                            evidence_text=abbreviated_end.group(0).strip(),
                            origin="question",
                            strength="strong",
                        )
                    )
                except ValueError:
                    facts.append(
                        _unsupported_fact(
                            "malformed_value", abbreviated_end.group(0).strip()
                        )
                    )
            previous = match
        lower_bounds = [
            fact
            for fact in facts
            if fact.kind == "filter" and fact.operator in {"gte", "gt"}
        ]
        upper_bounds = [
            fact
            for fact in facts
            if fact.kind == "filter" and fact.operator in {"lte", "lt"}
        ]
        if any(
            lo.field == hi.field
            and (
                lo.values[0] > hi.values[0]
                or (
                    lo.values == hi.values
                    and (lo.operator == "gt" or hi.operator == "lt")
                )
            )
            for lo in lower_bounds
            for hi in upper_bounds
        ):
            facts.append(_unsupported_fact("reversed_temporal_range", question))
        return tuple(facts)

    def canonicalize(self, field, raw_value, evidence_text, context):
        try:
            value = canonicalize_storage_value(field, raw_value)
        except ValueError:
            return ResolutionOutcome("unknown")
        return ResolutionOutcome("resolved", (value,))


class NumericResolver(FieldResolver):
    kinds = frozenset({"numeric"})

    def detect(self, question, field, context, *, constraint_clauses=None):
        fields = super().detect(question, field, context)
        clauses = (
            _parse_constraint_clauses(question, context, include_typed=True)
            if constraint_clauses is None
            else constraint_clauses
        )
        facts = []
        for clause in clauses:
            if clause.field != field:
                continue
            evidence = question[slice(*clause.consumed_span)].strip()
            if clause.violation:
                facts.append(_unsupported_fact(clause.violation, evidence))
            else:
                facts.append(
                    SemanticFact(
                        kind="filter",
                        field=field,
                        operator=clause.operator,
                        values=clause.values,
                        evidence_text=evidence,
                        evidence_span=clause.consumed_span,
                        origin="question",
                        strength="strong",
                    )
                )
        return fields + tuple(facts)

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

    def canonicalize(self, field, raw_value, evidence_text, context, *, operator=None):
        definition = FIELD_DEFINITIONS.get(field)
        if definition is None or not definition.filterable:
            return ResolutionOutcome("unknown")
        operator_definition = FILTER_OPERATOR_DEFINITIONS.get(operator)
        pattern_literals = (
            tuple(
                value
                for value in _categorical_literal_fragments(field, context)
                if normalize_semantic_text(value) == normalize_semantic_text(raw_value)
            )
            if definition.resolution_kind in {"catalog", "closed_value"}
            else ()
        )
        if (
            operator in definition.operators
            and operator_definition is not None
            and operator_definition.uses_text_pattern
            and definition.resolution_kind in {"catalog", "closed_value"}
            and isinstance(raw_value, str)
            and len(pattern_literals) == 1
        ):
            # Pattern operands are literals, not incomplete names to expand to a
            # guessed member. Independent filter facts still ground the compiler.
            return ResolutionOutcome("resolved", pattern_literals)
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
        left.direction,
        left.scope,
    ) == (
        right.kind,
        right.field,
        right.operator,
        right.values,
        right.concept_name,
        right.origin,
        right.direction,
        right.scope,
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


def _fact_token_spans(question, fact):
    if fact.evidence_span is not None:
        start, end = fact.evidence_span
        return (
            (
                len(normalize_semantic_text(question[:start]).split()),
                len(normalize_semantic_text(question[:end]).split()),
            ),
        )
    return _evidence_spans(question, fact.evidence_text)


def _select_longest_supported_facts(
    question: str, facts: Iterable[SemanticFact]
) -> tuple[SemanticFact, ...]:
    """Keep the longest supported meaning while preserving compatible facts."""
    ranked = []
    for index, fact in enumerate(facts):
        spans = _fact_token_spans(question, fact)
        if not spans:
            spans = ((index, index + 1),)
        for span in spans:
            operator = (
                _unsupported_operator_before_span(question, span)
                if fact.kind in {"filter", "predicate"}
                else None
            )
            if operator is not None:
                evidence, operator_span = operator
                rejected = _unsupported_fact("unsupported_operator", evidence)
                ranked.append(
                    (
                        operator_span[1] - operator_span[0],
                        _fact_priority(rejected),
                        -operator_span[0],
                        operator_span,
                        rejected,
                    )
                )
                continue
            ranked.append(
                (span[1] - span[0], _fact_priority(fact), -span[0], span, fact)
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


def _earliest_measure_facts(question: str, existing_facts=()) -> list[SemanticFact]:
    candidates = _facts_for_named_phrases(question, "measure", MEASURE_DEFINITIONS)
    if not candidates:
        return []
    positions = []
    # A bound literal owns its internal words. Registry concepts are compositional:
    # their complete evidence phrase can independently supply the counted subject.
    filter_spans = [
        span
        for fact in existing_facts
        if fact.kind == "filter" and fact.evidence_span is not None
        for span in _fact_token_spans(question, fact)
    ]
    for fact in candidates:
        unbound = [
            start
            for start, end in _evidence_spans(question, fact.evidence_text)
            if not any(lo <= start and end <= hi for lo, hi in filter_spans)
        ]
        if unbound:
            positions.append((min(unbound), fact))
    if not positions:
        return []
    earliest = min(position for position, _ in positions)
    return [fact for position, fact in positions if position == earliest]


def _calculation_matches(question, definition, facts):
    matches = [
        match
        for pattern in definition.detection_patterns
        for match in re.finditer(pattern, question, re.I)
    ]
    # A correction verb following a subject pronoun is not an aggregate noun.
    grammatical = [
        match
        for match in matches
        if not (
            normalize_semantic_text(match.group(0)) == "mean"
            and re.search(
                r"\b(?:i|we|you|they)\s+(?:really\s+)?$",
                question[: match.start()],
                re.I,
            )
        )
    ]
    # Operation-looking tokens inside an independently bound filter phrase
    # describe that field, not a separate aggregate request.
    filter_spans = [
        span
        for fact in facts
        if fact.kind == "filter"
        for span in _fact_token_spans(question, fact)
    ]
    return [
        match
        for match in grammatical
        if not any(
            start <= len(normalize_semantic_text(question[: match.start()]).split())
            and len(normalize_semantic_text(question[: match.end()]).split()) <= end
            for start, end in filter_spans
        )
    ]


def _calculation_facts(
    question: str,
    selected_fields: tuple[str, ...],
    existing_facts: list[SemanticFact],
) -> list[SemanticFact]:
    facts = []
    for name, definition in CALCULATION_DEFINITIONS.items():
        matches = _calculation_matches(question, definition, existing_facts)
        if not matches:
            continue
        match = min(matches, key=lambda match: match.start())
        evidence = match.group(0)
        if name == "percentage":
            population_text = question[
                min(matches, key=lambda match: match.start()).end() :
            ]
            population_text = re.split(
                r"\b(?:have|has|with|where|that|are|were|is)\b",
                population_text,
                maxsplit=1,
                flags=re.I,
            )[0]
            subjects = []
            for measure in MEASURE_DEFINITIONS.values():
                if any(
                    evidence_occurs(population_text, phrase)
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
                evidence_span=match.span(),
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
    subject_matches = [
        (definition.aggregation_field, phrase)
        for definition in MEASURE_DEFINITIONS.values()
        if definition.aggregation_field is not None
        and FIELD_DEFINITIONS[definition.aggregation_field].groupable
        for phrase in definition.natural_names
        if not any(
            set(_token_forms(phrase)) & set(_token_forms(field_phrase))
            for _, field_phrase in field_matches
        )
    ]
    for field, phrase in [*field_matches, *subject_matches]:
        subject_forms = {
            variant
            for form in _token_forms(phrase)
            for variant in (form, form if form.endswith("s") else form + "s")
        }
        subject = (
            "(?:" + "|".join(re.escape(form) for form in sorted(subject_forms)) + ")"
        )
        match = re.search(
            rf"\b(?:(?:by|per)\s+(?:(?:each|every|all|any)\s+)?(?:the\s+)?{subject}\b"
            rf"|(?:each|every)\s+(?:the\s+)?{subject}\b"
            rf"|(?:does|do)\s+(?:each|every|all|any)\s+(?:the\s+)?{subject}\b(?=\s+have\b))",
            normalized_question,
        )
        if match is None:
            continue
        if re.search(
            r"\b(?:order(?:ed)?|sort(?:ed)?)\s*$", normalized_question[: match.start()]
        ):
            continue
        # Each/every explicitly distribute the operation; all/any are collective
        # only when a total modifier scopes them and no by/per clause overrides it.
        if (
            re.search(r"\b(?:all|any)\b", match.group(0))
            and not re.match(r"(?:by|per)\b", match.group(0))
            and (
                re.match(r"in total\b", normalized_question)
                or re.match(
                    rf"\s+(?:have\s+)?{COLLECTIVE_MODIFIER_PATTERN}\b",
                    normalized_question[match.end() :],
                )
            )
        ):
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
    registry = ResolverRegistry.default()
    for field, definition in FIELD_DEFINITIONS.items():
        if (
            definition.resolution_kind in {"identifier", "entity"}
            and definition.planner_visible
        ):
            facts.extend(
                registry.for_kind(definition.resolution_kind).detect(
                    question, field, context
                )
            )
    semantic_question = question
    for fact in facts:
        if (
            fact.kind == "entity"
            and fact.field == "Name"
            and fact.evidence_span is not None
        ):
            start, end = fact.evidence_span
            semantic_question = (
                semantic_question[:start]
                + " " * (end - start)
                + semantic_question[end:]
            )
    constraint_clauses = _parse_constraint_clauses(
        question,
        context,
        include_typed=True,
        excluded_spans=tuple(
            fact.evidence_span
            for fact in facts
            if fact.kind == "entity" and fact.evidence_span is not None
        ),
    )
    field_matches = [
        (field, phrase)
        for field, definition in FIELD_DEFINITIONS.items()
        if definition.planner_visible
        for phrase in definition.natural_names
        if evidence_occurs(semantic_question, phrase)
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
        if definition.resolution_kind in {"identifier", "entity"}:
            phrase = next(
                phrase
                for matched_field, phrase in maximal_field_matches
                if matched_field == field
            )
            facts.append(
                SemanticFact(
                    kind="field",
                    field=field,
                    evidence_text=phrase,
                    origin="question",
                    strength="strong",
                )
            )
            continue
        facts.extend(
            registry.for_kind(definition.resolution_kind).detect(
                semantic_question,
                field,
                context,
                **(
                    {"categorical_clauses": constraint_clauses}
                    if definition.resolution_kind in {"catalog", "closed_value"}
                    else {"constraint_clauses": constraint_clauses}
                    if definition.resolution_kind == "numeric"
                    else {}
                ),
            )
        )
    for field, definition in FIELD_DEFINITIONS.items():
        if (
            field not in selected_fields
            and definition.planner_visible
            and definition.resolution_kind in {"catalog", "closed_value"}
        ):
            facts.extend(
                registry.for_kind(definition.resolution_kind).detect(
                    semantic_question,
                    field,
                    context,
                    categorical_clauses=constraint_clauses,
                )
            )
    facts.extend(
        registry.for_kind("temporal").detect_literals(
            semantic_question, selected_fields, context
        )
    )
    role_question = semantic_question
    constraint_question = question
    for clause in constraint_clauses:
        if clause.violation is None and any(
            fact.kind == "filter"
            and fact.strength == "strong"
            and (fact.field, fact.operator, fact.values)
            == (clause.field, clause.operator, clause.values)
            for fact in facts
        ):
            start, end = clause.consumed_span
            role_question = (
                role_question[:start] + " " * (end - start) + role_question[end:]
            )
            constraint_question = (
                constraint_question[:start]
                + " " * (end - start)
                + constraint_question[end:]
            )
    role_field_matches = [
        (field, phrase)
        for field, phrase in maximal_field_matches
        if evidence_occurs(role_question, phrase)
    ]
    for name, concept in VALUE_CONCEPT_DEFINITIONS.items():
        for phrase in sorted(concept.natural_names, key=len, reverse=True):
            if not evidence_occurs(role_question, phrase):
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
    facts.extend(_grouping_facts(role_question, role_field_matches))
    facts.extend(
        _calculation_facts(
            role_question,
            tuple(dict.fromkeys(field for field, _ in role_field_matches)),
            facts,
        )
    )
    if re.search(r"\b(?:how many|count|number of|total number)\b", role_question, re.I):
        facts.extend(_earliest_measure_facts(role_question, facts))
    predicate_facts = _facts_for_named_phrases(
        role_question, "predicate", BUSINESS_PREDICATE_DEFINITIONS
    )
    facts.extend(predicate_facts)
    facts.extend(
        _facts_for_named_phrases(
            role_question, "semantic_intent", RETRIEVAL_INTENT_DEFINITIONS
        )
    )
    selected = list(_select_longest_supported_facts(question, facts))
    selected = _executable_choice_facts(
        role_question,
        role_field_matches,
        selected,
        # Preserve identity operand syntax: erasing only its value must not turn
        # a Name constraint into a bare projection of the Name field.
        original_question=constraint_question,
    )
    return merge_semantic_facts(selected)


def _executable_choice_facts(question, field_matches, facts, *, original_question):
    """Compose executable roles from recognized fields and operation clauses."""
    role_question = question
    for fact in facts:
        if fact.strength == "strong" and fact.kind == "filter":
            value_spans = (
                (fact.evidence_span,)
                if fact.evidence_span is not None
                else _raw_phrase_spans(question, fact.evidence_text)
            )
            # Never guess that every identical word has the resolved value role.
            # Source offsets survive entity masking and normalization happens last.
            if len(value_spans) == 1:
                start, end = value_spans[0]
                role_question = (
                    role_question[:start] + " " * (end - start) + role_question[end:]
                )
    normalized = normalize_semantic_text(role_question)

    def add(kind, evidence, **values):
        facts.append(
            SemanticFact(
                kind=kind,
                evidence_text=evidence,
                origin="question",
                strength="strong",
                **values,
            )
        )

    for capability, patterns in UNSUPPORTED_REQUEST_PATTERNS.items():
        for pattern in patterns:
            if match := re.search(pattern, question, re.I):
                add("unsupported", match.group(0), concept_name=capability)
                break

    for name, definition in CALCULATION_DEFINITIONS.items():
        matches = _calculation_matches(question, definition, facts)
        represented = any(
            f.kind == "calculation" and f.concept_name == name for f in facts
        )
        named_count = name == "sum" and all(
            (
                normalize_semantic_text(match.group(0)) in {"combined", "total"}
                and any(f.kind == "measure" for f in facts)
                and not any(
                    f.kind == "field"
                    and f.field in FIELD_DEFINITIONS
                    and FIELD_DEFINITIONS[f.field].storage_type == "number"
                    for f in facts
                )
            )
            or any(
                re.match(
                    rf"^(?:of\s+)?(?:the\s+)?{re.escape(form)}\b",
                    normalize_semantic_text(question[match.end() :]),
                )
                for fact in facts
                if fact.kind == "measure"
                for phrase in MEASURE_DEFINITIONS[fact.concept_name].natural_names
                for form in _token_forms(phrase)
            )
            for match in matches
        )
        if matches and not represented and not named_count:
            add(
                "unsupported",
                matches[0].group(0),
                concept_name="unsupported_calculation",
            )

    temporal_rank = re.search(ORDERING_ROLE_PATTERNS["temporal"], normalized)
    if temporal_rank:
        row_subject = any(
            evidence_occurs(normalized[temporal_rank.end() :], phrase)
            for definition in MEASURE_DEFINITIONS.values()
            if definition.aggregation == "count"
            and definition.aggregation_field is None
            for phrase in definition.natural_names
        )
        if row_subject and not any(
            f.kind in {"calculation", "measure", "group_by"} for f in facts
        ):
            add(
                "order_by",
                temporal_rank.group(0),
                field="Date",
                direction="desc" if temporal_rank.group(1) == "latest" else "asc",
            )
            if temporal_rank.group(2):
                add(
                    "limit",
                    temporal_rank.group(0),
                    values=(float(temporal_rank.group(2)),),
                )
        else:
            add(
                "unsupported",
                temporal_rank.group(0),
                concept_name="unsupported_constraint",
            )

    superlative = re.search(ORDERING_ROLE_PATTERNS["aggregate"], normalized)
    if superlative:
        grouping = [f for f in facts if f.kind == "group_by"]
        if not grouping:
            subject_aliases = [
                (field, phrase)
                for field, definition in FIELD_DEFINITIONS.items()
                if definition.planner_visible and definition.groupable
                for phrase in definition.natural_names
            ] + [
                (definition.aggregation_field, phrase)
                for definition in MEASURE_DEFINITIONS.values()
                if definition.aggregation_field is not None
                and FIELD_DEFINITIONS[definition.aggregation_field].groupable
                for phrase in definition.natural_names
            ]
            subjects = {}
            for field, phrase in subject_aliases:
                subject = re.search(
                    rf"\bwhich\s+({re.escape(normalize_semantic_text(phrase))})\b",
                    normalized[: superlative.start()],
                )
                if subject:
                    subjects[field] = subject.group(0)
            if len(subjects) == 1:
                field, evidence = next(iter(subjects.items()))
                add("group_by", evidence, field=field)
                grouping = [f for f in facts if f.kind == "group_by"]
        operations = [f for f in facts if f.kind in {"calculation", "measure"}]
        modifies_operation = any(
            re.match(
                rf"\s+(?:(?:the|number of|count of)\s+)?{re.escape(form)}\b",
                normalized[superlative.end() :],
            )
            for fact in operations
            for form in _token_forms(fact.evidence_text)
        )
        if grouping and len(operations) == 1 and modifies_operation:
            direction = "desc" if superlative.group(1) == "highest" else "asc"
            add("order_by", superlative.group(0), field="value", direction=direction)
            add("ranking", superlative.group(0), field="value", direction=direction)
            add("limit", superlative.group(0), values=(1.0,))
        else:
            add(
                "unsupported",
                superlative.group(0),
                concept_name="unsupported_constraint",
            )

    limit = re.search(ORDERING_ROLE_PATTERNS["limit"], normalized)
    if limit:
        add("limit", limit.group(0), values=(float(limit.group(2)),))
        if limit.group(1) in {"first", "last"}:
            add("unsupported", limit.group(0), concept_name="unsupported_constraint")
    rank = limit if limit and limit.group(1) in {"top", "bottom"} else None
    if rank:
        direction = "desc" if rank.group(1) == "top" else "asc"
        if any(f.kind in {"measure", "calculation"} for f in facts):
            add("order_by", rank.group(0), field="value", direction=direction)
            add("ranking", rank.group(0), field="value", direction=direction)
            tail = normalized[rank.end() :]
            for field, phrase in field_matches:
                if re.match(
                    rf"\s+{re.escape(normalize_semantic_text(phrase))}s?\b", tail
                ):
                    add("group_by", phrase, field=field)

    for field, phrase in field_matches:
        token = re.escape(normalize_semantic_text(phrase))
        order = re.search(
            rf"\b(?:order(?:ed)?|sort(?:ed)?)\s+by\s+({token})\s+(ascending|descending|asc|desc)\b",
            normalized,
        )
        if order:
            direction = "asc" if order.group(2) in {"ascending", "asc"} else "desc"
            add("order_by", order.group(0), field=field, direction=direction)
            remaining = normalized[order.end() :].strip()
            if remaining and not re.match(
                r"^(?:where|with|for|limit|before|after|between|on|in)\b", remaining
            ):
                add("unsupported", remaining, concept_name="unsupported_constraint")

    order_clause = re.search(r"\b(?:order(?:ed)?|sort(?:ed)?)\s+by\b", normalized)
    if order_clause and not any(f.kind == "order_by" for f in facts):
        add("unsupported", order_clause.group(0), concept_name="unsupported_constraint")
    rank_clause = re.search(r"\b(?:rank|ranking|leaderboard)\b", normalized)
    if rank_clause and not any(f.kind == "ranking" for f in facts):
        add("unsupported", rank_clause.group(0), concept_name="unsupported_constraint")

    projection_text = normalize_semantic_text(original_question)
    projection_pattern = CONSTRAINT_CLAUSE_GRAMMAR["projection"]
    projection = re.search(projection_pattern, projection_text)
    raw_projection = re.search(projection_pattern, original_question, re.I)
    if projection and not any(
        f.kind in {"measure", "calculation", "group_by"} for f in facts
    ):
        projection_fields = [
            (field, phrase)
            for field, phrase in field_matches
            if evidence_occurs(projection.group(1), phrase)
        ]
        remainder = projection.group(1)
        for _, phrase in sorted(
            projection_fields, key=lambda item: len(item[1]), reverse=True
        ):
            remainder = re.sub(
                rf"\b{re.escape(normalize_semantic_text(phrase))}s?\b", " ", remainder
            )
        if not re.sub(r"\b(?:and|the|only)\b|\s+", "", remainder):
            for field, phrase in projection_fields:
                add("projection", phrase, field=field)
        elif projection_fields and (
            re.search(r"\bfrom\b", projection_text[projection.end() - 5 :])
            or re.search(r"\band\b", projection.group(1))
            or (raw_projection and "," in raw_projection.group(1))
        ):
            add(
                "unsupported",
                projection.group(1),
                concept_name="unsupported_constraint",
            )

    percentage = next(
        (
            f
            for f in facts
            if f.kind == "calculation" and f.concept_name == "percentage"
        ),
        None,
    )
    if percentage:
        add(
            "percentage_denominator",
            percentage.evidence_text,
            field=percentage.field,
            concept_name=next(
                name
                for name, definition in MEASURE_DEFINITIONS.items()
                if definition.aggregation_field == percentage.field
            ),
        )
        conditions = [f for f in facts if f.kind == "filter"]
        predicates = [f for f in facts if f.kind == "predicate"]
        # The language supports one numerator condition. Compound populations
        # require an explicit future representation, never provider inference.
        if len(conditions) == 1 and not predicates:
            condition = conditions[0]
            facts[facts.index(condition)] = condition.model_copy(
                update={"scope": "percentage_numerator"}
            )
        elif len(predicates) == 1 and not conditions:
            predicate = predicates[0]
            required = BUSINESS_PREDICATE_DEFINITIONS[
                predicate.concept_name
            ].required_filters
            if len(required) == 1:
                item = required[0]
                facts.remove(predicate)
                add(
                    "filter",
                    predicate.evidence_text,
                    field=item.field,
                    operator=item.operator,
                    values=(
                        item.value if isinstance(item.value, tuple) else (item.value,)
                    ),
                    scope="percentage_numerator",
                )
            else:
                add(
                    "unsupported",
                    percentage.evidence_text,
                    concept_name="percentage_population",
                )
        else:
            add(
                "unsupported",
                percentage.evidence_text,
                concept_name="percentage_population",
            )
    if not any(f.kind in {"measure", "calculation", "semantic_intent"} for f in facts):
        row_request = re.search(r"\b(?:show|list|display|select)\b", normalized)
        if row_request:
            add("result_shape", row_request.group(0), concept_name="rows")
    return facts


def _fact_target(fact: SemanticFact):
    return (
        fact.kind,
        fact.field,
        fact.operator,
        fact.concept_name,
        normalize_semantic_text(fact.evidence_text),
        fact.origin,
        fact.direction,
        fact.scope,
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
