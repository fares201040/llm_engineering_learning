"""Typed, source-preserving input-understanding contracts for attendance questions."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Annotated, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    from .attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        CALCULATION_DEFINITIONS,
        FIELD_DEFINITIONS,
        FILTER_OPERATOR_DEFINITIONS,
        INTERPRETATION_PRESETS,
        InterpretationName,
        MEASURE_DEFINITIONS,
        RESULT_INTENT_DEFINITIONS,
        AnswerContract,
        PlannerProposal,
    )
    from .semantic_resolution import SemanticFact
except ImportError:  # Imported through answer.py's supported direct-script mode.
    from attendance_schema import (
        BUSINESS_PREDICATE_DEFINITIONS,
        CALCULATION_DEFINITIONS,
        FIELD_DEFINITIONS,
        FILTER_OPERATOR_DEFINITIONS,
        INTERPRETATION_PRESETS,
        InterpretationName,
        MEASURE_DEFINITIONS,
        RESULT_INTENT_DEFINITIONS,
        AnswerContract,
        PlannerProposal,
    )
    from semantic_resolution import SemanticFact


ReplyLocale = Literal["en", "ar"]
MatchMethod = Literal[
    "exact",
    "localized_alias",
    "transliteration",
    "fuzzy",
    "user_clarification",
]
TargetKind = Literal[
    "field",
    "measure",
    "predicate",
    "calculation",
    "operator",
    "interpretation",
    "result_intent",
]
UnderstandingStatus = Literal[
    "ready",
    "employee_clarification",
    "meaning_clarification",
    "missing_intent",
    "unsupported",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SurfaceCandidate(_StrictFrozenModel):
    candidate_id: str = Field(min_length=1)
    target_kind: TargetKind
    target_name: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    evidence_span: tuple[int, int]
    method: MatchMethod
    score: float = Field(ge=0.0, le=1.0)

    @field_validator("candidate_id", "target_name", "evidence_text")
    @classmethod
    def _text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate text must not be blank")
        return value

    @field_validator("evidence_span")
    @classmethod
    def _span_must_be_forward(cls, value: tuple[int, int]) -> tuple[int, int]:
        start, end = value
        if start < 0 or end <= start:
            raise ValueError("evidence_span must be a non-empty forward source span")
        return value


class QuestionSurface(_StrictFrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    original_text: str = Field(min_length=1)
    reply_locale: ReplyLocale
    candidates: tuple[SurfaceCandidate, ...] = ()

    @model_validator(mode="after")
    def _validate_candidates(self):
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("surface candidate identifiers must be unique")
        for candidate in self.candidates:
            start, end = candidate.evidence_span
            if end > len(self.original_text):
                raise ValueError(
                    "candidate evidence span exceeds the original question"
                )
            if self.original_text[start:end] != candidate.evidence_text:
                raise ValueError(
                    "candidate evidence must match its original source span"
                )
        return self


class EmployeeOption(_StrictFrozenModel):
    employee_id: str = Field(min_length=1)
    name: str = Field(min_length=1)

    @field_validator("employee_id", "name")
    @classmethod
    def _employee_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("employee option values must not be blank")
        return value


class MeaningOption(_StrictFrozenModel):
    option_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    target_kind: TargetKind
    target_name: str = Field(min_length=1)
    evidence_text: str | None = None
    evidence_span: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _source_evidence_is_complete(self):
        if (self.evidence_text is None) != (self.evidence_span is None):
            raise ValueError("meaning option evidence text and span must be paired")
        if self.evidence_text is not None:
            start, end = self.evidence_span
            if not self.evidence_text.strip() or start < 0 or end <= start:
                raise ValueError("meaning option source evidence must be non-empty")
        return self


class CatalogOption(_StrictFrozenModel):
    option_id: str = Field(min_length=1)
    display_value: str = Field(min_length=1)
    field: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ContextChoiceOption(_StrictFrozenModel):
    option_id: str = Field(min_length=1)
    label: str = Field(min_length=1)

    @field_validator("option_id", "label")
    @classmethod
    def _context_choice_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("context choice values must not be blank")
        return value


class EmployeeReferent(_StrictFrozenModel):
    """A confirmed employee identity that is safe to retain for this session."""

    employee_id: str = Field(min_length=1)
    name: str = Field(min_length=1)

    @field_validator("employee_id", "name")
    @classmethod
    def _referent_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("employee referent values must not be blank")
        return value


class CoverageSnapshot(_StrictFrozenModel):
    available_start: str = Field(min_length=1)
    available_end: str = Field(min_length=1)
    requested_start: str = Field(min_length=1)
    requested_end: str = Field(min_length=1)
    complete: bool


class ResultSnapshot(_StrictFrozenModel):
    """Bounded result metadata retained for contextual follow-up only."""

    answer_contract: AnswerContract | None = None
    matched_count: int | None = Field(default=None, ge=0)
    coverage: CoverageSnapshot | None = None
    operation: str | None = Field(default=None, min_length=1)
    scalar_value: str | int | float | None = None


class AttendanceUnitFrame(_StrictFrozenModel):
    unit_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    facts: tuple[SemanticFact, ...] = ()
    employees: tuple[EmployeeReferent, ...] = ()
    result: ResultSnapshot

    @field_validator("unit_id", "source_text")
    @classmethod
    def _unit_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("attendance unit values must not be blank")
        return value


class ConversationTurnFrame(_StrictFrozenModel):
    original_question: str = Field(min_length=1)
    reply_locale: ReplyLocale
    units: tuple[AttendanceUnitFrame, ...] = Field(min_length=1)

    @field_validator("original_question")
    @classmethod
    def _turn_question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("conversation turn question must not be blank")
        return value


class _ClarificationBase(_StrictFrozenModel):
    original_question: str = Field(min_length=1)
    reply_locale: ReplyLocale
    facts: tuple[SemanticFact, ...] = ()
    prepared_proposal: PlannerProposal | None = None


class EmployeeClarification(_ClarificationBase):
    kind: Literal["employee_selection"] = "employee_selection"
    options: tuple[EmployeeOption, ...] = Field(min_length=1)
    resolved_options: tuple[EmployeeOption, ...] = ()
    reference_text: str | None = None
    reference_span: tuple[int, int] | None = None
    confirmation_required: bool
    allow_multiple: bool = False
    has_more_candidates: bool = False

    @model_validator(mode="after")
    def _reference_matches_saved_question(self):
        if (self.reference_text is None) != (self.reference_span is None):
            raise ValueError("employee reference text and span must be paired")
        if self.reference_text is None:
            return self
        start, end = self.reference_span
        if (
            not self.reference_text.strip()
            or start < 0
            or end <= start
            or end > len(self.original_question)
            or self.original_question[start:end] != self.reference_text
        ):
            raise ValueError(
                "employee reference must match the saved original question"
            )
        return self


class MeaningClarification(_ClarificationBase):
    kind: Literal["semantic_interpretation"] = "semantic_interpretation"
    options: tuple[MeaningOption, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _option_evidence_matches_saved_question(self):
        for option in self.options:
            if option.evidence_text is None:
                continue
            start, end = option.evidence_span
            if (
                end > len(self.original_question)
                or self.original_question[start:end] != option.evidence_text
            ):
                raise ValueError(
                    "meaning option evidence must match the saved original question"
                )
        return self


class CatalogClarification(_ClarificationBase):
    kind: Literal["catalog_value"] = "catalog_value"
    options: tuple[CatalogOption, ...] = Field(min_length=1)


class MissingIntentClarification(_ClarificationBase):
    kind: Literal["missing_intent"] = "missing_intent"
    employee: EmployeeOption | None = None


class ContextChoiceClarification(_ClarificationBase):
    kind: Literal["context_choice"] = "context_choice"
    options: tuple[ContextChoiceOption, ...] = Field(min_length=1)

    @field_validator("options")
    @classmethod
    def _context_choice_identifiers_must_be_unique(
        cls, value: tuple[ContextChoiceOption, ...]
    ) -> tuple[ContextChoiceOption, ...]:
        option_ids = [option.option_id for option in value]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("context choice identifiers must be unique")
        return value


PendingClarification = Annotated[
    EmployeeClarification
    | MeaningClarification
    | CatalogClarification
    | MissingIntentClarification
    | ContextChoiceClarification,
    Field(discriminator="kind"),
]


class ResolvedPendingMention(_StrictFrozenModel):
    """A confirmed identity bound to one saved pending-request mention."""

    source_text: str = Field(min_length=1)
    referent: EmployeeReferent
    source_span: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _source_span_is_complete_when_present(self):
        if self.source_span is None:
            return self
        start, end = self.source_span
        if start < 0 or end <= start:
            raise ValueError("resolved pending mention source span must be forward")
        return self


class PendingConstraintCandidate(_StrictFrozenModel):
    field: str = Field(min_length=1)
    value: str = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)


class PendingConstraintSnapshot(_StrictFrozenModel):
    field: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    candidates: tuple[PendingConstraintCandidate, ...] = ()


class PendingRequestFrame(_StrictFrozenModel):
    """The sole resumable request record; display state stays in PendingClarification."""

    original_question: str = Field(min_length=1)
    reply_locale: ReplyLocale
    facts: tuple[SemanticFact, ...] = ()
    prepared_proposal: PlannerProposal | None = None
    clarification: PendingClarification | None = None
    resolved_mentions: tuple[ResolvedPendingMention, ...] = ()
    pending_candidates: tuple[EmployeeOption, ...] = ()
    pending_constraint: PendingConstraintSnapshot | None = None
    pending_interpretations: tuple[InterpretationName, ...] = ()
    unrelated_refusal_given: bool = False

    @field_validator("original_question")
    @classmethod
    def _pending_question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("pending request question must not be blank")
        return value

    @model_validator(mode="after")
    def _duplicated_choices_match_displayed_clarification(self):
        clarification = self.clarification
        if self.pending_candidates and clarification is not None:
            if not isinstance(clarification, EmployeeClarification) or (
                self.pending_candidates != clarification.options
            ):
                raise ValueError(
                    "pending employee choices must match the displayed clarification"
                )

        if self.pending_constraint is not None and clarification is not None:
            if not isinstance(clarification, CatalogClarification):
                raise ValueError(
                    "pending catalog choices must match the displayed clarification"
                )
            displayed_catalog = tuple(
                (option.field, option.value, option.display_value)
                for option in clarification.options
            )
            resumable_catalog = tuple(
                (candidate.field, candidate.value, candidate.label or candidate.value)
                for candidate in self.pending_constraint.candidates
            )
            if displayed_catalog != resumable_catalog:
                raise ValueError(
                    "pending catalog choices must match the displayed clarification"
                )

        if self.pending_interpretations and clarification is not None:
            if not isinstance(clarification, MeaningClarification):
                raise ValueError(
                    "pending interpretation choices must match the displayed clarification"
                )
            displayed_interpretations = tuple(
                option.target_name
                for option in clarification.options
                if option.target_kind == "interpretation"
            )
            if (
                len(displayed_interpretations) != len(clarification.options)
                or displayed_interpretations != self.pending_interpretations
            ):
                raise ValueError(
                    "pending interpretation choices must match the displayed clarification"
                )
        return self


class InputUnderstanding(_StrictFrozenModel):
    status: UnderstandingStatus
    original_question: str = Field(min_length=1)
    reply_locale: ReplyLocale
    facts: tuple[SemanticFact, ...] = ()
    accepted_corrections: tuple[SurfaceCandidate, ...] = ()
    clarification: PendingClarification | None = None

    @model_validator(mode="after")
    def _status_must_match_clarification(self):
        expected_kind = {
            "employee_clarification": "employee_selection",
            "meaning_clarification": "semantic_interpretation",
            "missing_intent": "missing_intent",
        }
        if self.status in {"ready", "unsupported"}:
            if self.clarification is not None:
                raise ValueError(f"{self.status} understanding cannot clarify")
            return self
        if self.clarification is None:
            raise ValueError(f"{self.status} understanding requires clarification")
        required_kind = expected_kind[self.status]
        if self.clarification.kind != required_kind:
            raise ValueError(
                f"{self.status} understanding requires {required_kind} clarification"
            )
        return self


_TARGET_REGISTRIES = {
    "field": FIELD_DEFINITIONS,
    "measure": MEASURE_DEFINITIONS,
    "predicate": BUSINESS_PREDICATE_DEFINITIONS,
    "calculation": CALCULATION_DEFINITIONS,
    "operator": FILTER_OPERATOR_DEFINITIONS,
    "interpretation": INTERPRETATION_PRESETS,
    "result_intent": RESULT_INTENT_DEFINITIONS,
}


@dataclass(frozen=True, kw_only=True)
class LocalizedAliasDefinition:
    locale: ReplyLocale
    target_kind: TargetKind
    target_name: str
    phrases: tuple[str, ...]

    def __post_init__(self):
        registry = _TARGET_REGISTRIES[self.target_kind]
        if self.target_name not in registry:
            raise ValueError(
                f"unknown {self.target_kind} alias target {self.target_name!r}"
            )
        if not self.phrases or any(not phrase.strip() for phrase in self.phrases):
            raise ValueError("localized aliases require non-blank phrases")
        normalized = tuple(phrase.strip().casefold() for phrase in self.phrases)
        if len(normalized) != len(set(normalized)):
            raise ValueError("localized alias phrases must be unique")


LOCALIZED_ALIAS_DEFINITIONS: tuple[LocalizedAliasDefinition, ...] = (
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="predicate",
        target_name="worked",
        phrases=("حضر", "عمل"),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="predicate",
        target_name="absent",
        phrases=("غائب", "غياب"),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="predicate",
        target_name="authorized",
        phrases=("معتمد", "معتمدة"),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="interpretation",
        target_name="worked_days",
        phrases=("أيام الحضور", "أيام العمل"),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="interpretation",
        target_name="scheduled_working_days",
        phrases=("أيام العمل المجدولة",),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="interpretation",
        target_name="absent_days",
        phrases=("أيام الغياب",),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="interpretation",
        target_name="attendance_records",
        phrases=("سجلات الحضور", "سجلات الدوام", "السجلات"),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="calculation",
        target_name="average",
        phrases=("متوسط", "المتوسط"),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="calculation",
        target_name="sum",
        phrases=("مجموع", "إجمالي"),
    ),
    LocalizedAliasDefinition(
        locale="ar",
        target_kind="result_intent",
        target_name="employee_profile",
        phrases=("من هو", "من هي", "ملف الموظف"),
    ),
)


def validate_localized_alias_registry(
    definitions: tuple[LocalizedAliasDefinition, ...],
) -> None:
    """Revalidate alias references and reject duplicate locale phrases."""
    seen: set[tuple[str, str]] = set()
    for definition in definitions:
        if definition.target_name not in _TARGET_REGISTRIES[definition.target_kind]:
            raise ValueError(
                f"unknown {definition.target_kind} target {definition.target_name!r}"
            )
        for phrase in definition.phrases:
            key = (definition.locale, phrase.strip().casefold())
            if key in seen:
                raise ValueError(f"duplicate localized alias {phrase!r}")
            seen.add(key)


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC_LETTERS = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
    }
)
_ARABIC_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06edـ]")
_ARABIC_ALPHA = re.compile(r"[\u0600-\u06ff]")
_LATIN_ALPHA = re.compile(r"[A-Za-z]")
_TOKEN_PATTERN = re.compile(r"\b\w+\b", re.UNICODE)
_NEGATION_TOKENS = frozenset(
    {"not", "no", "without", "never", "لم", "لن", "لا", "ليس", "ما"}
)


@dataclass(frozen=True)
class _NormalizedText:
    text: str
    source_indexes: tuple[int, ...]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        return self.source_indexes[start], self.source_indexes[end - 1] + 1


def _normalize_character(character: str) -> str:
    normalized = unicodedata.normalize("NFKC", character).casefold()
    normalized = normalized.translate(_ARABIC_DIGITS).translate(_ARABIC_LETTERS)
    normalized = _ARABIC_MARKS.sub("", normalized)
    return "".join(
        item if item.isalnum() or item in {"_", "<", ">", "=", "!"} else " "
        for item in normalized
    )


def _normalize_with_source_map(text: str) -> _NormalizedText:
    characters: list[str] = []
    source_indexes: list[int] = []
    for source_index, character in enumerate(text):
        for normalized in _normalize_character(character):
            if normalized.isspace():
                if not characters or characters[-1] == " ":
                    continue
                characters.append(" ")
                source_indexes.append(source_index)
            else:
                characters.append(normalized)
                source_indexes.append(source_index)
    if characters and characters[-1] == " ":
        characters.pop()
        source_indexes.pop()
    return _NormalizedText("".join(characters), tuple(source_indexes))


def _normalized_phrase(text: str) -> str:
    return _normalize_with_source_map(text).text


def normalize_for_matching(text: str) -> str:
    """Normalize harmless surface variants without changing semantic values."""
    return _normalized_phrase(text)


_PRIOR_CONVERSATION_REFERENCES = frozenset(
    normalize_for_matching(value)
    for value in (
        "again",
        "same",
        "same one",
        "same employee",
        "same person",
        "the same employee",
        "the same person",
        "that employee",
        "those days",
        "that period",
        "previous result",
        "the previous result",
        "first result",
        "second result",
        "third result",
        "the first",
        "the second",
        "the third",
        "this",
        "that",
        "it",
        "he",
        "she",
        "him",
        "her",
        "his",
        "hers",
        "them",
        "they",
        "their",
        "theirs",
        "former",
        "the former",
        "latter",
        "the latter",
        "مرة أخرى",
        "مجددا",
        "مجدداً",
        "نفسه",
        "نفسها",
        "نفس الموظف",
        "نفس الموظفة",
        "هذا",
        "هذه",
        "هو",
        "هي",
        "هم",
        "له",
        "لها",
        "لهم",
        "السابق",
        "السابقة",
        "الأول",
        "الأولى",
        "الثاني",
        "الثانية",
        "الأخير",
        "الأخيرة",
    )
)
_CONVERSATION_CONTROL_REFERENCES = _PRIOR_CONVERSATION_REFERENCES | frozenset(
    normalize_for_matching(value)
    for value in (
        "both",
        "all",
        "also",
        "then",
        "separately",
        "together",
        "كلاهما",
        "كليهما",
        "الجميع",
        "معا",
        "معاً",
        "بشكل منفصل",
        "ايضا",
        "ثم",
    )
)


def _bounded_phrase_pattern(values: frozenset[str]) -> re.Pattern[str]:
    return re.compile(
        r"(?<!\w)(?:"
        + "|".join(
            sorted((re.escape(value) for value in values), key=len, reverse=True)
        )
        + r")(?!\w)"
    )


_CONVERSATION_CONTROL_PATTERN = _bounded_phrase_pattern(
    _CONVERSATION_CONTROL_REFERENCES
)
_PRIOR_CONVERSATION_PATTERN = _bounded_phrase_pattern(_PRIOR_CONVERSATION_REFERENCES)
_CONVERSATION_RESIDUE_COORDINATORS = frozenset(
    {"and", "then", "also", "و", "ثم", "ايضا"}
)


def is_conversation_control_reference(text: str) -> bool:
    """Return whether bare text is a control/reference, never a person's name."""
    return normalize_for_matching(text) in _CONVERSATION_CONTROL_REFERENCES


def contains_conversation_control(text: str) -> bool:
    """Return whether text contains a bounded registered conversation control."""
    return bool(_CONVERSATION_CONTROL_PATTERN.search(normalize_for_matching(text)))


def conversation_control_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return source spans occupied by registered conversation controls."""
    normalized = _normalize_with_source_map(text)
    return tuple(
        normalized.source_span(*match.span())
        for match in _CONVERSATION_CONTROL_PATTERN.finditer(normalized.text)
    )


def contains_prior_conversation_reference(text: str) -> bool:
    """Return whether text contains a bounded reference requiring saved context."""
    return bool(_PRIOR_CONVERSATION_PATTERN.search(normalize_for_matching(text)))


def is_conversation_control_residue(text: str) -> bool:
    """Accept residue made only of registered controls and coordinators."""
    normalized = normalize_for_matching(text)
    remainder = _CONVERSATION_CONTROL_PATTERN.sub(" ", normalized)
    return all(
        token in _CONVERSATION_RESIDUE_COORDINATORS
        for token in re.findall(r"\w+", remainder)
    )


def _reply_locale(question: str) -> ReplyLocale:
    arabic_tokens = sum(
        1 for token in _TOKEN_PATTERN.findall(question) if _ARABIC_ALPHA.search(token)
    )
    latin_tokens = sum(
        1 for token in _TOKEN_PATTERN.findall(question) if _LATIN_ALPHA.search(token)
    )
    return "ar" if arabic_tokens >= latin_tokens and arabic_tokens else "en"


def _registry_aliases():
    for name, definition in FIELD_DEFINITIONS.items():
        for phrase in definition.natural_names:
            yield "en", "field", name, phrase
    for name, definition in MEASURE_DEFINITIONS.items():
        for phrase in definition.natural_names:
            yield "en", "measure", name, phrase
    for name, definition in BUSINESS_PREDICATE_DEFINITIONS.items():
        for phrase in definition.natural_names:
            yield "en", "predicate", name, phrase
    for name, definition in CALCULATION_DEFINITIONS.items():
        for phrase in definition.natural_names:
            yield "en", "calculation", name, phrase
    for name in INTERPRETATION_PRESETS:
        yield "en", "interpretation", name, name.replace("_", " ")
    for name, definition in RESULT_INTENT_DEFINITIONS.items():
        for phrase in definition.natural_names:
            yield "en", "result_intent", name, phrase
    for definition in LOCALIZED_ALIAS_DEFINITIONS:
        for phrase in definition.phrases:
            yield (
                definition.locale,
                definition.target_kind,
                definition.target_name,
                phrase,
            )


def _surface_candidate(
    *,
    original: str,
    normalized: _NormalizedText,
    normalized_span: tuple[int, int],
    target_kind: TargetKind,
    target_name: str,
    method: MatchMethod,
    score: float,
) -> SurfaceCandidate:
    start, end = normalized.source_span(*normalized_span)
    return SurfaceCandidate(
        candidate_id=f"{target_kind}:{target_name}:{start}:{end}:{method}",
        target_kind=target_kind,
        target_name=target_name,
        evidence_text=original[start:end],
        evidence_span=(start, end),
        method=method,
        score=round(score, 6),
    )


def _has_adjacent_negation(tokens: list[re.Match[str]], start_index: int) -> bool:
    if start_index <= 0:
        return False
    return tokens[start_index - 1].group(0) in _NEGATION_TOKENS


def analyze_question_surface(question: str) -> QuestionSurface:
    """Find registry-grounded wording while retaining the untouched source text."""
    if not question or not question.strip():
        raise ValueError("question must not be blank")
    normalized = _normalize_with_source_map(question)
    candidates: list[SurfaceCandidate] = []
    seen: set[tuple[str, str, int, int, str]] = set()
    aliases = []

    for locale, target_kind, target_name, phrase in _registry_aliases():
        normalized_alias = _normalized_phrase(phrase)
        if not normalized_alias:
            continue
        aliases.append((locale, target_kind, target_name, normalized_alias))
        pattern = re.compile(rf"(?<!\w){re.escape(normalized_alias)}(?!\w)")
        for match in pattern.finditer(normalized.text):
            method: MatchMethod = "exact" if locale == "en" else "localized_alias"
            key = (target_kind, target_name, match.start(), match.end(), method)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                _surface_candidate(
                    original=question,
                    normalized=normalized,
                    normalized_span=(match.start(), match.end()),
                    target_kind=target_kind,
                    target_name=target_name,
                    method=method,
                    score=1.0,
                )
            )

    tokens = list(_TOKEN_PATTERN.finditer(normalized.text))
    for _locale, target_kind, target_name, alias in aliases:
        if len(alias) < 4 or target_kind == "operator":
            continue
        alias_words = alias.split()
        width = len(alias_words)
        for token_index in range(0, len(tokens) - width + 1):
            window = tokens[token_index : token_index + width]
            start, end = window[0].start(), window[-1].end()
            text = normalized.text[start:end]
            if (
                text == alias
                or any(character.isdigit() for character in text)
                or any(token.group(0) in {"and", "or", "not"} for token in window)
            ):
                continue
            if _has_adjacent_negation(tokens, token_index):
                continue
            score = SequenceMatcher(None, text, alias).ratio()
            if score < 0.72:
                continue
            key = (target_kind, target_name, start, end, "fuzzy")
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                _surface_candidate(
                    original=question,
                    normalized=normalized,
                    normalized_span=(start, end),
                    target_kind=target_kind,
                    target_name=target_name,
                    method="fuzzy",
                    score=score,
                )
            )

    candidates.sort(
        key=lambda item: (
            item.evidence_span,
            -item.score,
            item.target_kind,
            item.target_name,
            item.method,
        )
    )
    return QuestionSurface(
        original_text=question,
        reply_locale=_reply_locale(question),
        candidates=tuple(candidates),
    )


def automatically_accepted_candidates(
    surface: QuestionSurface,
) -> tuple[SurfaceCandidate, ...]:
    """Return exact aliases and only uniquely dominant high-confidence fuzzies."""
    exact_candidates = [
        candidate
        for candidate in surface.candidates
        if candidate.method in {"exact", "localized_alias"}
    ]
    accepted = [
        candidate
        for candidate in exact_candidates
        if not any(
            other.evidence_span[0] <= candidate.evidence_span[0]
            and other.evidence_span[1] >= candidate.evidence_span[1]
            and other.evidence_span != candidate.evidence_span
            for other in exact_candidates
        )
        and not _has_unregistered_arabic_residual(surface, candidate)
    ]
    fuzzy = [
        candidate for candidate in surface.candidates if candidate.method == "fuzzy"
    ]
    for candidate in fuzzy:
        competitors = [
            other
            for other in fuzzy
            if other.candidate_id != candidate.candidate_id
            and other.evidence_span == candidate.evidence_span
        ]
        next_score = max((other.score for other in competitors), default=0.0)
        if (
            candidate.score >= 0.90
            and candidate.score - next_score >= 0.08
            and not _has_unregistered_arabic_residual(surface, candidate)
        ):
            accepted.append(candidate)
    return tuple(accepted)


_ARABIC_CONTINUATION_TOKENS = frozenset(
    {
        "في",
        "من",
        "الي",
        "خلال",
        "بين",
        "قبل",
        "بعد",
        "عن",
        "مع",
        "حيث",
        "حتي",
        "للموظف",
        "للموظفة",
    }
)

_ARABIC_MEANING_REVERSAL_PREFIXES = frozenset(
    {
        "بدون",
        "دون",
        "لا",
        "استبعد",
        "استبعاد",
        "باستثناء",
        "عدا",
    }
)

_MEANING_REVERSAL_PREFIXES = _ARABIC_MEANING_REVERSAL_PREFIXES | frozenset(
    {"without", "exclude", "excluding", "omit", "remove", "not", "no"}
)

_MEANING_CLAUSE_COORDINATORS = frozenset(
    {
        "but",
        "however",
        "then",
        "yet",
        "instead",
        "and",
        "or",
        "لكن",
        "ثم",
        "بل",
        "و",
    }
)
_MEANING_CLAUSE_PUNCTUATION = re.compile(r"[،,.!?؟:;]+")


def _has_meaning_reversal_prefix(
    surface: QuestionSurface, candidate: SurfaceCandidate
) -> bool:
    prefix = surface.original_text[: candidate.evidence_span[0]]
    current_clause = _MEANING_CLAUSE_PUNCTUATION.split(prefix)[-1]
    clause_tokens = normalize_for_matching(current_clause).split()
    last_coordinator = max(
        (
            index
            for index, token in enumerate(clause_tokens)
            if token in _MEANING_CLAUSE_COORDINATORS
        ),
        default=-1,
    )
    return bool(set(clause_tokens[last_coordinator + 1 :]) & _MEANING_REVERSAL_PREFIXES)


def _has_unregistered_arabic_residual(
    surface: QuestionSurface, candidate: SurfaceCandidate
) -> bool:
    if (
        candidate.method not in {"localized_alias", "fuzzy"}
        or candidate.target_kind not in {"predicate", "interpretation"}
        or not _ARABIC_ALPHA.search(candidate.evidence_text)
    ):
        return False
    if _has_meaning_reversal_prefix(surface, candidate):
        return True
    suffix = surface.original_text[candidate.evidence_span[1] :].lstrip()
    if not suffix or suffix[0] in "،,.!?؟:;":
        return False
    next_word = re.match(r"[\u0600-\u06ff]+", suffix)
    if next_word is None:
        return False
    return normalize_for_matching(next_word.group(0)) not in _ARABIC_CONTINUATION_TOKENS


def has_unregistered_arabic_meaning_modifier(question: str) -> bool:
    """Detect a bounded prefix that reverses a registered attendance meaning."""
    surface = analyze_question_surface(question)
    for candidate in surface.candidates:
        if candidate.method not in {
            "exact",
            "localized_alias",
            "fuzzy",
        } or candidate.target_kind not in {"predicate", "interpretation"}:
            continue
        if _has_meaning_reversal_prefix(surface, candidate):
            if any(
                other.candidate_id != candidate.candidate_id
                and other.method in {"exact", "localized_alias"}
                and other.target_kind in {"predicate", "interpretation"}
                and other.evidence_span[0] < candidate.evidence_span[0]
                and candidate.evidence_span[1] <= other.evidence_span[1]
                for other in surface.candidates
            ):
                continue
            if any(
                field.target_kind == "field"
                and field.method in {"exact", "localized_alias"}
                and field.evidence_span[1] <= candidate.evidence_span[0]
                and re.fullmatch(
                    r"\s*(?:(?:is|are|was|were|does|do)\s+)?"
                    r"(?:not|no)(?:\s+(?:in|contain|contains|start with|end with))?\s*",
                    surface.original_text[
                        field.evidence_span[1] : candidate.evidence_span[0]
                    ],
                    re.I,
                )
                for field in surface.candidates
            ):
                continue
            return True
    return False
