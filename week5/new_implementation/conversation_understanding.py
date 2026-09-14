"""Strict contracts for bounded, source-grounded conversation decisions."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

try:
    from .attendance_schema import MultiEmployeeDateView
except ImportError:  # Direct execution from week5/new_implementation.
    from attendance_schema import MultiEmployeeDateView


ConversationRoute = Literal["attendance", "social", "unrelated"]
ConversationRelation = Literal[
    "new",
    "repeat",
    "modify_scope",
    "replace_result",
    "add_constraints",
    "change_view",
    "explain_previous",
]
ConversationAmbiguityReason = Literal[
    "ambiguous_segmentation",
    "ambiguous_reference",
    "ambiguous_relation",
    "missing_context",
    "insufficient_grounding",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _validate_source_span(value: tuple[int, int]) -> tuple[int, int]:
    start, end = value
    if start < 0 or end <= start:
        raise ValueError("source_span must be a non-empty forward source span")
    return value


def _nonblank_identifier(value: str) -> str:
    if not value.strip():
        raise ValueError("request-local identifiers must not be blank")
    return value


class ResolveEmployeeMention(_StrictFrozenModel):
    """Ask the deterministic directory resolver to resolve this exact source span."""

    kind: Literal["resolve"] = "resolve"
    source_span: tuple[int, int]

    _source_span_is_forward = field_validator("source_span")(_validate_source_span)


class ReferenceChoiceEmployeeMention(_StrictFrozenModel):
    """Select one server-supplied employee/referent choice by opaque ID."""

    kind: Literal["reference_choice"] = "reference_choice"
    source_span: tuple[int, int]
    choice_id: str = Field(min_length=1)

    _source_span_is_forward = field_validator("source_span")(_validate_source_span)
    _choice_id_is_not_blank = field_validator("choice_id")(_nonblank_identifier)


class PreviousUnitEmployeeMention(_StrictFrozenModel):
    """Reuse employee bindings from an earlier unit in this same decision."""

    kind: Literal["previous_unit"] = "previous_unit"
    source_span: tuple[int, int]
    unit_index: int = Field(ge=0)

    _source_span_is_forward = field_validator("source_span")(_validate_source_span)


ConversationEmployeeMention = Annotated[
    ResolveEmployeeMention
    | ReferenceChoiceEmployeeMention
    | PreviousUnitEmployeeMention,
    Field(discriminator="kind"),
]


class _ConversationUnitDecision(_StrictFrozenModel):
    source_span: tuple[int, int]

    _source_span_is_forward = field_validator("source_span")(_validate_source_span)


class AttendanceUnitDecision(_ConversationUnitDecision):
    route: Literal["attendance"] = "attendance"
    relation: ConversationRelation
    base_unit_choice_id: str | None = None
    fact_ids: tuple[str, ...] = ()
    employee_mentions: tuple[ConversationEmployeeMention, ...] = ()
    view: MultiEmployeeDateView | None = None

    @field_validator("base_unit_choice_id")
    @classmethod
    def _base_identifier_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None:
            _nonblank_identifier(value)
        return value

    @field_validator("fact_ids")
    @classmethod
    def _fact_identifiers_are_finite_and_unique(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("fact identifiers must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("fact identifiers must be unique within a unit")
        return value

    @model_validator(mode="after")
    def _relation_has_a_safe_shape(self):
        if self.relation == "new":
            if self.base_unit_choice_id is not None:
                raise ValueError("new attendance units cannot select a base unit")
        elif self.base_unit_choice_id is None:
            raise ValueError(f"{self.relation} attendance units require a base unit")

        if self.relation == "change_view":
            if self.view is None:
                raise ValueError("change_view attendance units require a view")
            if self.fact_ids or self.employee_mentions:
                raise ValueError("change_view cannot also change facts or employees")
        elif self.view is not None and self.relation != "new":
            raise ValueError("a view is valid only for new or change_view units")

        if self.relation in {"repeat", "explain_previous"} and (
            self.fact_ids or self.employee_mentions or self.view is not None
        ):
            raise ValueError(
                f"{self.relation} attendance units cannot contain modifications"
            )
        return self


class SocialUnitDecision(_ConversationUnitDecision):
    route: Literal["social"] = "social"


class UnrelatedUnitDecision(_ConversationUnitDecision):
    route: Literal["unrelated"] = "unrelated"


ConversationUnitDecision = Annotated[
    AttendanceUnitDecision | SocialUnitDecision | UnrelatedUnitDecision,
    Field(discriminator="route"),
]


class ResolvedConversationDecision(_StrictFrozenModel):
    status: Literal["resolved"] = "resolved"
    units: tuple[ConversationUnitDecision, ...] = Field(min_length=1)


class AmbiguousConversationDecision(_StrictFrozenModel):
    status: Literal["ambiguous"] = "ambiguous"
    reason: ConversationAmbiguityReason


ConversationDecisionPayload = Annotated[
    ResolvedConversationDecision | AmbiguousConversationDecision,
    Field(discriminator="status"),
]


class ConversationDecision(RootModel[ConversationDecisionPayload]):
    """Concrete structured-response model around the strict decision union."""

    model_config = ConfigDict(frozen=True)

    @property
    def status(self) -> Literal["resolved", "ambiguous"]:
        return self.root.status

    @property
    def units(self) -> tuple[ConversationUnitDecision, ...]:
        if isinstance(self.root, ResolvedConversationDecision):
            return self.root.units
        return ()

    @property
    def reason(self) -> ConversationAmbiguityReason | None:
        if isinstance(self.root, AmbiguousConversationDecision):
            return self.root.reason
        return None


class ConversationDecisionContext(_StrictFrozenModel):
    """Trusted request-local identifiers and bounds for one provider decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    message: str = Field(min_length=1)
    employee_choice_ids: tuple[str, ...] = ()
    prior_unit_choice_ids: tuple[str, ...] = ()
    fact_choice_ids: tuple[str, ...] = ()
    strong_fact_ids: tuple[str, ...] = ()
    max_units: int = Field(gt=0)

    @field_validator("message")
    @classmethod
    def _message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("conversation message must not be blank")
        return value

    @field_validator(
        "employee_choice_ids",
        "prior_unit_choice_ids",
        "fact_choice_ids",
        "strong_fact_ids",
    )
    @classmethod
    def _choice_identifiers_are_finite_and_unique(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("request-local identifiers must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("request-local identifiers must be unique")
        return value

    @model_validator(mode="after")
    def _strong_facts_are_known_choices(self):
        if self.fact_choice_ids and not set(self.strong_fact_ids) <= set(
            self.fact_choice_ids
        ):
            raise ValueError("strong fact identifiers must be known fact choices")
        return self


class ConversationDecisionValidationError(ValueError):
    """A provider decision failed trusted request-local validation."""


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _validate_non_overlapping_spans(
    spans: tuple[tuple[int, int], ...], *, label: str
) -> None:
    for index, left in enumerate(spans):
        for right in spans[index + 1 :]:
            if left != right and _spans_overlap(left, right):
                raise ConversationDecisionValidationError(
                    f"{label} source spans partially overlap"
                )


def validate_conversation_decision(
    decision: ConversationDecision,
    context: ConversationDecisionContext,
) -> ConversationDecision:
    """Validate an already parsed provider decision against trusted local context."""
    if decision.status == "ambiguous":
        return decision
    if len(decision.units) > context.max_units:
        raise ConversationDecisionValidationError("conversation unit budget exceeded")

    unit_spans = tuple(unit.source_span for unit in decision.units)
    if any(end > len(context.message) for _, end in unit_spans):
        raise ConversationDecisionValidationError(
            "conversation unit source span exceeds message bounds"
        )
    if any(
        current[0] < previous[0]
        for previous, current in zip(unit_spans, unit_spans[1:])
    ):
        raise ConversationDecisionValidationError(
            "conversation units must be in source order"
        )
    _validate_non_overlapping_spans(unit_spans, label="conversation unit")

    known_employees = set(context.employee_choice_ids)
    known_bases = set(context.prior_unit_choice_ids)
    known_facts = set(context.fact_choice_ids) | set(context.strong_fact_ids)
    covered_facts: set[str] = set()

    for unit_index, unit in enumerate(decision.units):
        if unit.route != "attendance":
            continue
        if (
            unit.base_unit_choice_id is not None
            and unit.base_unit_choice_id not in known_bases
        ):
            raise ConversationDecisionValidationError("unknown base-unit choice ID")
        unknown_facts = set(unit.fact_ids) - known_facts
        if unknown_facts:
            raise ConversationDecisionValidationError("unknown fact choice ID")
        covered_facts.update(unit.fact_ids)

        mention_spans = tuple(mention.source_span for mention in unit.employee_mentions)
        _validate_non_overlapping_spans(mention_spans, label="employee mention")
        for mention in unit.employee_mentions:
            start, end = mention.source_span
            unit_start, unit_end = unit.source_span
            if not (unit_start <= start < end <= unit_end):
                raise ConversationDecisionValidationError(
                    "employee mention source span must be inside its unit"
                )
            if isinstance(mention, ReferenceChoiceEmployeeMention):
                if mention.choice_id not in known_employees:
                    raise ConversationDecisionValidationError(
                        "unknown employee choice ID"
                    )
            elif isinstance(mention, PreviousUnitEmployeeMention):
                if mention.unit_index >= unit_index:
                    raise ConversationDecisionValidationError(
                        "previous-unit references must target an earlier unit"
                    )

    uncovered = set(context.strong_fact_ids) - covered_facts
    if uncovered:
        raise ConversationDecisionValidationError("strong facts are not covered")
    return decision


# Stable concise aliases for later pipeline stages and direct-script consumers.
EmployeeMention = ConversationEmployeeMention
AttendanceUnitDraft = AttendanceUnitDecision
SocialUnitDraft = SocialUnitDecision
UnrelatedUnitDraft = UnrelatedUnitDecision
ConversationUnitDraft = ConversationUnitDecision
