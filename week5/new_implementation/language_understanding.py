"""Typed, source-preserving input-understanding contracts for attendance questions."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from week5.new_implementation.attendance_schema import (
    BUSINESS_PREDICATE_DEFINITIONS,
    CALCULATION_DEFINITIONS,
    FIELD_DEFINITIONS,
    FILTER_OPERATOR_DEFINITIONS,
    INTERPRETATION_PRESETS,
    MEASURE_DEFINITIONS,
    RESULT_INTENT_DEFINITIONS,
    PlannerProposal,
)
from week5.new_implementation.semantic_resolution import SemanticFact


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
                raise ValueError("candidate evidence span exceeds the original question")
            if self.original_text[start:end] != candidate.evidence_text:
                raise ValueError("candidate evidence must match its original source span")
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


class CatalogOption(_StrictFrozenModel):
    option_id: str = Field(min_length=1)
    display_value: str = Field(min_length=1)
    field: str = Field(min_length=1)
    value: str = Field(min_length=1)


class _ClarificationBase(_StrictFrozenModel):
    original_question: str = Field(min_length=1)
    reply_locale: ReplyLocale
    facts: tuple[SemanticFact, ...] = ()
    prepared_proposal: PlannerProposal | None = None


class EmployeeClarification(_ClarificationBase):
    kind: Literal["employee_selection"] = "employee_selection"
    options: tuple[EmployeeOption, ...] = Field(min_length=1)
    confirmation_required: bool
    allow_multiple: bool = False
    has_more_candidates: bool = False


class MeaningClarification(_ClarificationBase):
    kind: Literal["semantic_interpretation"] = "semantic_interpretation"
    options: tuple[MeaningOption, ...] = Field(min_length=1)


class CatalogClarification(_ClarificationBase):
    kind: Literal["catalog_value"] = "catalog_value"
    options: tuple[CatalogOption, ...] = Field(min_length=1)


class MissingIntentClarification(_ClarificationBase):
    kind: Literal["missing_intent"] = "missing_intent"
    employee: EmployeeOption | None = None


PendingClarification = Annotated[
    EmployeeClarification
    | MeaningClarification
    | CatalogClarification
    | MissingIntentClarification,
    Field(discriminator="kind"),
]


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
        target_name="absent_days",
        phrases=("أيام الغياب",),
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
