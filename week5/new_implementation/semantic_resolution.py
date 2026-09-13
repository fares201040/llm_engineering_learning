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
        canonicalize_storage_value,
        FIELD_DEFINITIONS,
        MEASURE_DEFINITIONS,
        RETRIEVAL_INTENT_DEFINITIONS,
        VALUE_CONCEPT_DEFINITIONS,
        UNSUPPORTED_REQUEST_PATTERNS,
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
    return re.search(r"\b(?:match(?:es)?|regex|ends? with)\b", between) is not None


def _unsupported_operator_before_span(
    question: str, span: tuple[int, int]
) -> tuple[str, tuple[int, int]] | None:
    tokens = normalize_semantic_text(question).split()
    for token_count in (2, 1):
        start = span[0] - token_count
        if start < 0:
            continue
        evidence = " ".join(tokens[start : span[0]])
        if re.fullmatch(r"(?:match(?:es)?|regex|ends? with)", evidence):
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
                if malformed and not re.match(
                    r"(?:over|under)\s+(?:the|last|next|this|current)\b",
                    remaining,
                    re.I,
                ):
                    rejected_facts.append(
                        _unsupported_fact("malformed_value", malformed.group(0))
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


def _raw_phrase_spans(text: str, phrase: str) -> tuple[tuple[int, int], ...]:
    """Locate a registry phrase without losing the source character offsets."""
    tokens = normalize_semantic_text(phrase).split()
    if not tokens:
        return ()
    pattern = r"[\W_]+".join(re.escape(token) for token in tokens)
    return tuple(
        match.span() for match in re.finditer(rf"(?<!\w){pattern}(?!\w)", text, re.I)
    )


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
            command = re.match(r"(?:count|show|list|find|summarize)\s+", name, re.I)
            syntax_spans.append(
                (
                    match.start("name") + (command.end() if command else 0),
                    match.end("name"),
                )
            )
        for match in re.finditer(
            r"\b(?:for|did|named)\s+(?P<name>[^\W\d_][\w'-]*(?:\s+[^\W\d_][\w'-]*)*)",
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


def _select_longest_supported_facts(
    question: str, facts: Iterable[SemanticFact]
) -> tuple[SemanticFact, ...]:
    """Keep the longest supported meaning while preserving compatible facts."""
    ranked = []
    for index, fact in enumerate(facts):
        spans = (
            (
                (
                    len(
                        normalize_semantic_text(
                            question[: fact.evidence_span[0]]
                        ).split()
                    ),
                    len(
                        normalize_semantic_text(
                            question[: fact.evidence_span[1]]
                        ).split()
                    ),
                ),
            )
            if fact.evidence_span is not None
            else _evidence_spans(question, fact.evidence_text)
        )
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
        for span in _evidence_spans(question, fact.evidence_text)
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
        if re.search(
            r"\b(?:order(?:ed)?|sort(?:ed)?)\s*$", normalized_question[: match.start()]
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
                semantic_question, field, context
            )
        )
    facts.extend(
        registry.for_kind("temporal").detect_literals(
            semantic_question, selected_fields, context
        )
    )
    for name, concept in VALUE_CONCEPT_DEFINITIONS.items():
        for phrase in sorted(concept.natural_names, key=len, reverse=True):
            if not evidence_occurs(semantic_question, phrase):
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
    facts.extend(_calculation_facts(semantic_question, selected_fields, facts))
    if re.search(r"\b(?:how many|count|number of|total number)\b", question, re.I):
        facts.extend(_earliest_measure_facts(semantic_question))
    predicate_facts = _facts_for_named_phrases(
        semantic_question, "predicate", BUSINESS_PREDICATE_DEFINITIONS
    )
    facts.extend(predicate_facts)
    facts.extend(
        _facts_for_named_phrases(
            question, "semantic_intent", RETRIEVAL_INTENT_DEFINITIONS
        )
    )
    selected = list(_select_longest_supported_facts(question, facts))
    selected = _executable_choice_facts(
        semantic_question, maximal_field_matches, selected, original_question=question
    )
    return merge_semantic_facts(selected)


def _executable_choice_facts(question, field_matches, facts, *, original_question):
    """Compose executable roles from recognized fields and operation clauses."""
    normalized = normalize_semantic_text(question)

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
            any(
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

    limit = re.search(r"\b(top|bottom|first|last|limit(?: to)?)\s+(\d+)\b", normalized)
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
    projection_pattern = r"\b(?:show|list|display|select)\s+(.+?)(?:\s+(?:from|for|where|with|ordered|sorted)\b|$)"
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
