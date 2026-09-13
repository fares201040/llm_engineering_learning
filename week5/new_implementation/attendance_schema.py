"""Canonical APDC attendance fields and deterministic calculation semantics."""

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json
import math
import re
from types import MappingProxyType
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ResolutionKind = Literal[
    "identifier",
    "entity",
    "temporal",
    "numeric",
    "closed_value",
    "catalog",
    "free_text",
]
EvidenceOrigin = Literal["question", "trusted_state", "deterministic_default"]
PlanningStatus = Literal["ready", "ambiguous", "unsupported"]
UnsupportedCapability = Literal[
    "nested_boolean_filters",
    "having_filter",
    "window_calculation",
    "cross_period_comparison",
    "multi_stage_aggregation",
    "unsupported_calculation",
    "narrative_explanation",
    "percentage_population",
    "unsupported_constraint",
]
FilterOperator = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "contains",
    "starts_with",
]
FILTER_OPERATORS: frozenset[FilterOperator] = frozenset(get_args(FilterOperator))
FilterScalar = str | float
FilterValue = FilterScalar | list[FilterScalar]
RegistryFilterValue = FilterScalar | tuple[FilterScalar, ...]
AnswerUnit = Literal["dates", "records", "employees", "hours", "percentage", "value"]


def _validate_filter_value_shape(operator: FilterOperator, value: FilterValue):
    if operator == "in":
        if not isinstance(value, list) or not value:
            raise ValueError("operator 'in' requires a non-empty list")
    elif isinstance(value, list):
        raise ValueError(f"operator {operator!r} requires a single value")
    return value


class FilterCondition(BaseModel):
    field: str
    operator: FilterOperator
    value: FilterValue

    @model_validator(mode="after")
    def _validate_value_shape(self):
        _validate_filter_value_shape(self.operator, self.value)
        return self


# Counts unique attendance dates after all filters are applied; duplicate rows for the same date are counted once (for example, three matching records on 2026-09-01 count as one date).
# Counts every matching daily attendance row after all filters are applied, including duplicate employee-date rows (for example, three matching rows count as three records).
# Counts unique non-null employee identifiers after all filters are applied; multiple rows for the same employee are counted once (for example, records for E123 on five dates count as one employee)..
MeasureName = Literal[
    "distinct_dates",
    "attendance_records",
    "employees",
]
BusinessPredicateName = Literal[
    "scheduled_working_day",
    "worked",
    "not_worked",
    "absent",
    "authorized",
]
InterpretationName = Literal[
    "worked_days",
    "scheduled_working_days",
    "scheduled_non_attended_days",
    "absent_days",
    "attendance_records",
    "authorized_records",
    "employees",
]


class QueryPlan(BaseModel):
    mode: Literal["exact", "semantic", "hybrid"] = Field(
        description=(
            "exact for structured lookups/calculations; semantic for fuzzy meaning; "
            "hybrid for structured filters plus semantic meaning"
        )
    )
    search_query: str
    filters: list[FilterCondition] = Field(default_factory=list)
    name_hint: str | None = None
    aggregation: Literal[
        "none",
        "count",
        "distinct_count",
        "sum",
        "average",
        "min",
        "max",
        "percentage",
    ] = "none"
    aggregation_field: str | None = None
    group_by: list[str] = Field(default_factory=list, max_length=2)
    projection: list[str] = Field(default_factory=list)
    percentage_condition: FilterCondition | None = None
    order_by: str | None = None
    order_direction: Literal["asc", "desc"] = "desc"
    limit: int | None = None
    measure: MeasureName | None = None
    business_predicates: list[BusinessPredicateName] = Field(default_factory=list)
    interpretation_candidates: list[InterpretationName] = Field(default_factory=list)

    @field_validator("group_by", "projection")
    @classmethod
    def _grouping_fields_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("group_by fields must be unique")
        return value


class _StrictPlannerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _EvidenceChoice(_StrictPlannerModel):
    evidence_text: str = Field(min_length=1)

    @field_validator("evidence_text")
    @classmethod
    def _evidence_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence_text must not be blank")
        return value


class ProposedFilter(_EvidenceChoice):
    field: str = Field(min_length=1)
    operator: FilterOperator
    value: FilterValue

    @model_validator(mode="after")
    def _validate_value_shape(self):
        _validate_filter_value_shape(self.operator, self.value)
        return self


class ProposedMeasureChoice(_EvidenceChoice):
    name: MeasureName


class ProposedPredicateChoice(_EvidenceChoice):
    name: BusinessPredicateName


class ProposedFieldChoice(_EvidenceChoice):
    field: str = Field(min_length=1)


class ProposedOrderChoice(ProposedFieldChoice):
    direction: Literal["asc", "desc"]


class ProposedNameHint(_EvidenceChoice):
    value: str = Field(min_length=1)


class ProposedLimit(_EvidenceChoice):
    value: int = Field(gt=0)


class ProposedCalculation(_EvidenceChoice):
    operation: Literal[
        "count", "distinct_count", "sum", "average", "min", "max", "percentage"
    ]
    field: str | None = None
    percentage_condition: ProposedFilter | None = None

    @model_validator(mode="after")
    def _validate_operation_shape(self):
        field_required = self.operation in {
            "distinct_count",
            "sum",
            "average",
            "min",
            "max",
        }
        if field_required and not self.field:
            raise ValueError(f"{self.operation} requires a field")
        if self.operation == "count" and self.field is not None:
            raise ValueError("count must not invent a field")
        if self.operation == "percentage" and self.percentage_condition is None:
            raise ValueError("percentage requires percentage_condition")
        if self.operation != "percentage" and self.percentage_condition is not None:
            raise ValueError("percentage_condition is only valid for percentage")
        return self


class AnswerContract(_StrictPlannerModel):
    shape: Literal["scalar", "grouped", "rows", "narrative"]
    unit: AnswerUnit
    subject_field: str | None = None
    grain: list[str] = Field(default_factory=list)


class PlannerProposal(_StrictPlannerModel):
    status: PlanningStatus
    filters: list[ProposedFilter] = Field(default_factory=list)
    name_hint: ProposedNameHint | None = None
    measure: ProposedMeasureChoice | None = None
    business_predicates: list[ProposedPredicateChoice] = Field(default_factory=list)
    calculation: ProposedCalculation | None = None
    group_by: list[ProposedFieldChoice] = Field(default_factory=list, max_length=2)
    projection: list[ProposedFieldChoice] = Field(default_factory=list)
    order_by: ProposedOrderChoice | None = None
    limit: ProposedLimit | None = None
    answer_contract: AnswerContract | None = None
    interpretation_candidates: list[InterpretationName] = Field(default_factory=list)
    unsupported_capabilities: list[UnsupportedCapability] = Field(default_factory=list)
    explanation: str | None = None

    @field_validator("group_by", "projection")
    @classmethod
    def _proposed_grouping_fields_must_be_unique(
        cls, value: list[ProposedFieldChoice]
    ) -> list[ProposedFieldChoice]:
        fields = [item.field for item in value]
        if len(fields) != len(set(fields)):
            raise ValueError("group_by fields must be unique")
        return value

    @model_validator(mode="after")
    def _validate_status_and_shape(self):
        if self.status == "ready":
            if self.answer_contract is None:
                raise ValueError("ready proposals require answer_contract")
            if self.interpretation_candidates:
                raise ValueError(
                    "ready proposals cannot contain interpretation candidates"
                )
        if self.status != "unsupported" and self.unsupported_capabilities:
            raise ValueError("capability identifiers require unsupported status")
        if self.status == "unsupported":
            if not self.unsupported_capabilities:
                raise ValueError(
                    "unsupported proposals require a capability identifier"
                )
            execution_choices = (
                self.filters
                or self.name_hint is not None
                or self.measure is not None
                or self.business_predicates
                or self.calculation is not None
                or self.group_by
                or self.projection
                or self.order_by is not None
                or self.limit is not None
                or self.answer_contract is not None
                or self.interpretation_candidates
            )
            if execution_choices:
                raise ValueError(
                    "unsupported proposals cannot contain execution choices"
                )
        if self.measure is not None and self.calculation is not None:
            raise ValueError("measure and calculation are mutually exclusive")
        if self.projection and (self.measure or self.calculation or self.group_by):
            raise ValueError("record projection cannot be combined with aggregation")
        if (
            self.projection
            and self.answer_contract
            and self.answer_contract.shape != "rows"
        ):
            raise ValueError("record projection requires rows answer shape")
        if self.answer_contract is not None:
            grouped = self.answer_contract.shape == "grouped"
            if grouped != bool(self.group_by):
                raise ValueError("grouped answer shape and group_by must agree")
        return self


class ExecutableQueryPlan(QueryPlan):
    answer_contract: AnswerContract


@dataclass(frozen=True)
class AccessContext:
    principal_id: str
    domain: str
    allowed_domains: frozenset[str]


class CoverageWindow(BaseModel):
    date_min: date
    date_max: date


LOCAL_DEMO_ACCESS = AccessContext(
    principal_id="local-demo",
    domain="attendance",
    allowed_domains=frozenset({"attendance"}),
)


class ScalarCalculationResult(BaseModel):
    operation: str
    value: float | int | None
    field: str | None = None


class GroupedCalculationRow(BaseModel):
    group: list[str | None]
    value: float | int | None


class GroupedCalculationResult(BaseModel):
    operation: str
    field: str | None = None
    group_by: list[str]
    rows: list[GroupedCalculationRow]
    total_groups: int
    truncated: bool = False


class PercentageCalculationResult(BaseModel):
    operation: Literal["percentage"] = "percentage"
    field: str | None
    numerator: int
    denominator: int
    value: float | None


@dataclass(frozen=True)
class FieldDefinition:
    storage_type: Literal["text", "date", "time", "datetime", "number"]
    description: str
    aliases: tuple[str, ...] = ()
    closed_values: tuple[str, ...] = ()
    operators: tuple[FilterOperator, ...] = ()
    sql_expression: str = ""
    searchable: bool = True
    metadata: bool = True
    context: bool = True
    natural_names: tuple[str, ...] = ()
    resolution_kind: ResolutionKind = "free_text"
    value_aliases: tuple["ValueAliasDefinition", ...] = ()
    filterable: bool = True
    groupable: bool = True
    orderable: bool = True
    aggregatable: bool = False
    planner_visible: bool = True
    phrase_priority: int = 100
    output_unit: AnswerUnit = "value"


@dataclass(frozen=True, kw_only=True)
class ValueAliasDefinition:
    natural_name: str
    canonical_value: str


@dataclass(frozen=True)
class RequiredFilter:
    field: str
    operator: FilterOperator
    value: RegistryFilterValue


@dataclass(frozen=True)
class MeasureDefinition:
    description: str
    aggregation: Literal["count", "distinct_count"]
    aggregation_field: str | None
    natural_names: tuple[str, ...] = ()
    answer_unit: Literal["dates", "records", "employees"] = "records"
    default_answer_shape: Literal["scalar", "grouped"] = "scalar"
    phrase_priority: int = 40


@dataclass(frozen=True)
class CalculationDefinition:
    description: str
    natural_names: tuple[str, ...]
    detection_patterns: tuple[str, ...]
    requires_numeric_field: bool = False


@dataclass(frozen=True)
class PredicateDefinition:
    description: str
    required_filters: tuple[RequiredFilter, ...]
    natural_names: tuple[str, ...] = ()
    incompatible_with: tuple[BusinessPredicateName, ...] = ()
    incompatible_filters: tuple[RequiredFilter, ...] = ()
    composes_with: tuple[BusinessPredicateName, ...] = ()
    phrase_priority: int = 80


@dataclass(frozen=True, kw_only=True)
class ValueConceptDefinition:
    field: str
    description: str
    natural_names: tuple[str, ...]
    members: tuple[str, ...]
    operator: FilterOperator = "in"
    phrase_priority: int = 60


@dataclass(frozen=True, kw_only=True)
class RetrievalIntentDefinition:
    description: str
    natural_names: tuple[str, ...]
    phrase_priority: int = 20


@dataclass(frozen=True)
class InterpretationDefinition:
    description: str
    measure: MeasureName
    business_predicates: tuple[BusinessPredicateName, ...] = ()


_FIELD_DEFINITIONS = {
    "Employee_ID": FieldDefinition(
        "text",
        "Stable unique employee identifier; authoritative when supplied.",
        (r"\bemployee ids?\b", r"\bids?\b"),
    ),
    "Name": FieldDefinition(
        "text",
        "Employee name used only for database-backed identity resolution.",
        (r"\bemployee names?\b", r"\bnames?\b"),
    ),
    "Organization_Unit": FieldDefinition(
        "text",
        "Organizational unit assigned to the employee.",
        (r"\borganization(?:al)? units?\b",),
    ),
    "Country": FieldDefinition(
        "text", "Employee work country.", (r"\bcountr(?:y|ies)\b",)
    ),
    "Work_Location": FieldDefinition(
        "text",
        "Employee work location.",
        (r"\bwork locations?\b", r"\blocations?\b"),
    ),
    "Department": FieldDefinition(
        "text", "Employee department.", (r"\bdepartments?\b",)
    ),
    "Position": FieldDefinition("text", "Employee position.", (r"\bpositions?\b",)),
    "Job": FieldDefinition("text", "Employee job title.", (r"\bjobs?\b",)),
    "Gradeset": FieldDefinition("text", "Employee grade set."),
    "Grade": FieldDefinition("text", "Employee grade.", (r"\bgrades?\b",)),
    "Date": FieldDefinition(
        "date", "Calendar date represented by one attendance record."
    ),
    "Day": FieldDefinition("text", "Day-of-week label for the attendance date."),
    "Day_Type": FieldDefinition(
        "text",
        "Calendar or schedule classification; it is not proof of attendance.",
        (r"\bday types?\b",),
        ("Working Day", "OFF Day", "OFF Day (ZAS)"),
    ),
    "Holiday_Type": FieldDefinition(
        "text", "Holiday classification for the date.", (r"\bholidays?\b",)
    ),
    "Shift": FieldDefinition("text", "Assigned work shift.", (r"\bshifts?\b",)),
    "Status": FieldDefinition(
        "text",
        "Workflow or approval state; it is not proof that work occurred.",
        (r"\bstatus\b",),
        ("Authorized", "Draft", "Pending For Authorization"),
    ),
    "Exception": FieldDefinition(
        "text",
        "Attendance outcome such as absence, lateness, early out, or OK.",
        (r"\bexceptions?\b",),
    ),
    "Total_Worked_Hrs": FieldDefinition(
        "number",
        "Actual hours worked; a worked day requires a value greater than zero.",
        (
            r"\bworked hours?\b",
            r"\bhours? worked\b",
            r"\bwork hours?\b",
            r"\btotal worked (?:hours?|hrs?)\b",
        ),
    ),
    "Lateness_Hrs": FieldDefinition(
        "number",
        "Hours of lateness.",
        (r"\blate(?:ness)?\b", r"\blate[ -]?in\b"),
    ),
    "Early_Out_Hrs": FieldDefinition(
        "number", "Hours of early departure.", (r"\bearly[ -]?out\b",)
    ),
    "Overbreak_Hrs": FieldDefinition(
        "number", "Hours beyond the permitted break.", (r"\bover[ -]?break\b",)
    ),
    "Regular_Units": FieldDefinition(
        "number", "Regular attendance units.", (r"\bregular units?\b",)
    ),
    "pre_ot_hrs": FieldDefinition("number", "Overtime hours before the shift."),
    "Post_OT_hrs": FieldDefinition("number", "Overtime hours after the shift."),
    "Total_OT": FieldDefinition(
        "number", "Total overtime hours.", (r"\bovertime\b", r"\btotal[ _-]?ot\b")
    ),
    "OT_Authorized": FieldDefinition(
        "number",
        "Authorized overtime hours.",
        (
            r"\bauthorized overtime\b",
            r"\bovertime authorized\b",
            r"\bauthorized ot\b",
            r"\bot authorized\b",
        ),
    ),
    "OT_Not_Authorized": FieldDefinition(
        "number",
        "Overtime hours that are not authorized.",
        (
            r"\bunauthorized overtime\b",
            r"\bovertime not authorized\b",
            r"\bunauthorized ot\b",
            r"\bot not authorized\b",
        ),
    ),
    "Leave_Type": FieldDefinition("text", "Recorded leave category.", (r"\bleave\b",)),
    "Leave_Hrs": FieldDefinition("number", "Recorded leave hours.", (r"\bleave\b",)),
    "chunk_type": FieldDefinition("text", "Internal retrieval chunk classification."),
    "Period": FieldDefinition("text", "Calendar month in YYYY-MM form."),
}

for _index in range(1, 6):
    _FIELD_DEFINITIONS[f"OT_Type_{_index}"] = FieldDefinition(
        "text", f"Overtime category in overtime slot {_index}."
    )
    _FIELD_DEFINITIONS[f"OT_Value_{_index}"] = FieldDefinition(
        "number", f"Overtime value in overtime slot {_index}."
    )

_MISSING_LIVE_FIELDS = {
    "Actual_From_Date": ("date", "Actual clock-in date."),
    "Actual_From_Time": ("time", "Actual clock-in time."),
    "Actual_To_Date": ("date", "Actual clock-out date."),
    "Actual_To_Time": ("time", "Actual clock-out time."),
    "Employee_Remarks": ("text", "Employee-entered attendance remarks."),
    "From_Date": ("date", "Workflow interval start date."),
    "From_Time": ("time", "Workflow interval start time."),
    "Pending_with": ("text", "Approver with whom the record is pending."),
    # The source export labels these as times although its values are dates.
    "Post_OT_End_Time": ("date", "Post-shift overtime end date value."),
    "Post_OT_Start_Time": ("date", "Post-shift overtime start date value."),
    "Pre_OT_End_Time": ("date", "Pre-shift overtime end date value."),
    "Pre_OT_Start_Time": ("date", "Pre-shift overtime start date value."),
    "Schedule_From_Date": ("date", "Scheduled shift start date."),
    "Schedule_From_Time": ("time", "Scheduled shift start time."),
    "Schedule_To_Date": ("date", "Scheduled shift end date."),
    "Schedule_To_Time": ("time", "Scheduled shift end time."),
    "To_Date": ("date", "Workflow interval end date."),
    "To_Time": ("time", "Workflow interval end time."),
    "last_Updated_date": ("datetime", "Source record update timestamp."),
}
for _field, (_storage_type, _description) in _MISSING_LIVE_FIELDS.items():
    _FIELD_DEFINITIONS[_field] = FieldDefinition(_storage_type, _description)

_TEXT_OPERATORS = ("eq", "ne", "in", "contains", "starts_with")
_ORDERED_OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "in")

_TYPED_POSTGRES_COLUMNS = {
    "Employee_ID": "employee_id",
    "Name": "name",
    "Organization_Unit": "organization_unit",
    "Country": "country",
    "Work_Location": "work_location",
    "Department": "department",
    "Position": "position",
    "Job": "job",
    "Gradeset": "gradeset",
    "Grade": "grade",
    "Date": "attendance_date",
    "Day": "day",
    "Day_Type": "day_type",
    "Holiday_Type": "holiday_type",
    "Shift": "shift",
    "Status": "status",
    "Exception": "exception",
    "Total_Worked_Hrs": "total_worked_hrs",
    "Lateness_Hrs": "lateness_hrs",
    "Early_Out_Hrs": "early_out_hrs",
    "Overbreak_Hrs": "overbreak_hrs",
    "Regular_Units": "regular_units",
    "pre_ot_hrs": "pre_ot_hrs",
    "Post_OT_hrs": "post_ot_hrs",
    "Total_OT": "total_ot",
    "OT_Authorized": "ot_authorized",
    "OT_Not_Authorized": "ot_not_authorized",
    "Leave_Type": "leave_type",
    "Leave_Hrs": "leave_hrs",
    "Period": "TO_CHAR(attendance_date, 'YYYY-MM')",
}

_FIELD_NATURAL_NAMES = {
    "Employee_ID": ("employee id", "employee ids", "id", "ids"),
    "Name": ("employee name", "employee names", "name", "names"),
    "Organization_Unit": (
        "organization unit",
        "organization units",
        "organizational unit",
        "organizational units",
    ),
    "Country": ("country", "countries"),
    "Work_Location": ("work location", "work locations", "location", "locations"),
    "Department": ("department", "departments"),
    "Position": ("position", "positions"),
    "Job": ("job", "jobs"),
    "Grade": ("grade", "grades"),
    "Day_Type": (
        "day type",
        "day types",
    ),
    "Holiday_Type": ("holiday", "holidays", "holiday type", "holiday types"),
    "Shift": ("shift", "shifts"),
    "Status": ("status",),
    "Exception": ("exception", "exceptions"),
    "Total_Worked_Hrs": (
        "worked hour",
        "worked hours",
        "hour worked",
        "hours worked",
        "work hour",
        "work hours",
        "total worked hr",
        "total worked hrs",
        "total worked hour",
        "total worked hours",
    ),
    "Lateness_Hrs": (
        "late",
        "lateness",
        "late in",
        "late-in",
        "late hour",
        "late hours",
        "lateness hour",
        "lateness hours",
    ),
    "Early_Out_Hrs": ("early out", "early-out"),
    "Overbreak_Hrs": ("over break", "over-break", "overbreak"),
    "Regular_Units": ("regular unit", "regular units"),
    "Total_OT": ("overtime", "total ot"),
    "OT_Authorized": (
        "authorized overtime",
        "overtime authorized",
        "authorized ot",
        "ot authorized",
    ),
    "OT_Not_Authorized": (
        "unauthorized overtime",
        "overtime not authorized",
        "unauthorized ot",
        "ot not authorized",
    ),
    "Leave_Type": ("leave", "leave type", "leave types"),
    "Leave_Hrs": ("leave", "leave hour", "leave hours"),
}

_FIELD_OUTPUT_UNITS: dict[str, AnswerUnit] = {
    "Employee_ID": "employees",
    "Date": "dates",
    "Total_Worked_Hrs": "hours",
    "Lateness_Hrs": "hours",
    "Early_Out_Hrs": "hours",
    "Overbreak_Hrs": "hours",
    "pre_ot_hrs": "hours",
    "Post_OT_hrs": "hours",
    "Total_OT": "hours",
    "OT_Authorized": "hours",
    "OT_Not_Authorized": "hours",
    "Leave_Hrs": "hours",
}


def _canonical_natural_name(field: str) -> str:
    return " ".join(part for part in re.split(r"[_\-\s]+", field) if part).casefold()


def _resolution_kind(field: str, definition: FieldDefinition) -> ResolutionKind:
    if field == "Employee_ID":
        return "identifier"
    if field == "Name":
        return "entity"
    if field == "Period" or definition.storage_type in {"date", "time", "datetime"}:
        return "temporal"
    if definition.storage_type == "number":
        return "numeric"
    if definition.closed_values or field == "chunk_type":
        return "closed_value"
    if field == "Employee_Remarks":
        return "free_text"
    return "catalog"


def canonicalize_storage_value(field: str, raw_value: object) -> str | float:
    """Return the canonical database value declared by the field registry."""
    definition = FIELD_DEFINITIONS.get(field)
    if definition is None:
        raise ValueError(f"Unknown attendance field {field!r}.")
    value = str(raw_value).strip()
    if field == "Period":
        if re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value) is None:
            raise ValueError(f"{field} requires an ISO year-month value.")
        return value
    if definition.storage_type == "number":
        try:
            number = float(Decimal(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field} requires a finite numeric value.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field} requires a finite numeric value.")
        return number
    try:
        if definition.storage_type == "date":
            return date.fromisoformat(value).isoformat()
        if definition.storage_type == "time":
            return time.fromisoformat(value).isoformat()
        if definition.storage_type == "datetime":
            return datetime.fromisoformat(value).isoformat()
    except ValueError as exc:
        label = (
            "ISO date"
            if definition.storage_type == "date"
            else f"ISO {definition.storage_type}"
        )
        raise ValueError(f"{field} requires a valid {label} value.") from exc
    return value


for _field, _definition in tuple(_FIELD_DEFINITIONS.items()):
    _sql_expression = _TYPED_POSTGRES_COLUMNS.get(_field)
    if _sql_expression is None:
        _json_value = f"record_json ->> '{_field}'"
        if _definition.storage_type == "number":
            _sql_expression = f"({_json_value})::double precision"
        elif _definition.storage_type == "date":
            _sql_expression = f"({_json_value})::date"
        elif _definition.storage_type == "time":
            _sql_expression = f"({_json_value})::time"
        elif _definition.storage_type == "datetime":
            _sql_expression = f"({_json_value})::timestamp"
        else:
            _sql_expression = _json_value
    _FIELD_DEFINITIONS[_field] = replace(
        _definition,
        operators=(
            _TEXT_OPERATORS
            if _definition.storage_type == "text"
            else _ORDERED_OPERATORS
        ),
        sql_expression=_sql_expression,
        natural_names=tuple(
            dict.fromkeys(
                (
                    _canonical_natural_name(_field),
                    *_FIELD_NATURAL_NAMES.get(_field, ()),
                )
            )
        ),
        resolution_kind=_resolution_kind(_field, _definition),
        aggregatable=(
            _definition.storage_type == "number" or _field in {"Date", "Employee_ID"}
        ),
        planner_visible=_field != "chunk_type",
        output_unit=_FIELD_OUTPUT_UNITS.get(_field, _definition.output_unit),
    )
FIELD_DEFINITIONS = MappingProxyType(_FIELD_DEFINITIONS)
FILTERABLE_FIELDS = frozenset(FIELD_DEFINITIONS)
NUMERIC_FILTER_FIELDS = frozenset(
    field
    for field, definition in FIELD_DEFINITIONS.items()
    if definition.storage_type == "number"
)
SEARCHABLE_FIELDS = tuple(
    field for field, definition in FIELD_DEFINITIONS.items() if definition.searchable
)
METADATA_FIELDS = tuple(
    field for field, definition in FIELD_DEFINITIONS.items() if definition.metadata
)
POSTGRES_FIELD_MAP = MappingProxyType(
    {
        field: definition.sql_expression
        for field, definition in FIELD_DEFINITIONS.items()
    }
)

MEASURE_DEFINITIONS = MappingProxyType(
    {
        "distinct_dates": MeasureDefinition(
            description="Count distinct attendance dates.",
            aggregation="distinct_count",
            aggregation_field="Date",
            natural_names=("day", "days", "date", "dates"),
            answer_unit="dates",
        ),
        "attendance_records": MeasureDefinition(
            description="Count matching daily attendance rows.",
            aggregation="count",
            aggregation_field=None,
            natural_names=(
                "attendance record",
                "attendance records",
                "record",
                "records",
                "row",
                "rows",
            ),
            answer_unit="records",
        ),
        "employees": MeasureDefinition(
            description="Count distinct employee identifiers.",
            aggregation="distinct_count",
            aggregation_field="Employee_ID",
            natural_names=("employee", "employees", "people"),
            answer_unit="employees",
        ),
    }
)

BUSINESS_PREDICATE_DEFINITIONS = MappingProxyType(
    {
        "scheduled_working_day": PredicateDefinition(
            description="Scheduled working dates; this is not proof that work occurred.",
            required_filters=(RequiredFilter("Day_Type", "eq", "Working Day"),),
            natural_names=(
                "scheduled day",
                "scheduled days",
                "scheduled working day",
                "scheduled working days",
                "exclude off day",
                "exclude off days",
                "excluding off day",
                "excluding off days",
                "without off day",
                "without off days",
            ),
            incompatible_filters=(
                RequiredFilter("Day_Type", "in", ("OFF Day", "OFF Day (ZAS)")),
            ),
            composes_with=("not_worked",),
            phrase_priority=90,
        ),
        "worked": PredicateDefinition(
            description="Dates with positive actual worked hours.",
            required_filters=(RequiredFilter("Total_Worked_Hrs", "gt", 0.0),),
            natural_names=(
                "work",
                "worked",
                "attend",
                "attended",
                "present",
                "positive work hour",
                "positive work hours",
                "positive worked hour",
                "positive worked hours",
            ),
            incompatible_with=("not_worked", "absent"),
            incompatible_filters=(RequiredFilter("Exception", "eq", "Absent"),),
        ),
        "not_worked": PredicateDefinition(
            description="Dates without positive actual worked hours.",
            required_filters=(RequiredFilter("Total_Worked_Hrs", "lte", 0.0),),
            natural_names=(
                "not work",
                "not worked",
                "did not work",
                "not attend",
                "not attended",
                "did not attend",
                "not present",
                "zero work hour",
                "zero work hours",
                "zero worked hour",
                "zero worked hours",
                "no positive worked hour",
                "no positive worked hours",
            ),
            incompatible_with=("worked",),
            composes_with=("scheduled_working_day",),
            phrase_priority=100,
        ),
        "absent": PredicateDefinition(
            description="Dates carrying the explicit Absent exception.",
            required_filters=(RequiredFilter("Exception", "eq", "Absent"),),
            natural_names=("absent", "absence", "absent day", "absent days"),
        ),
        "authorized": PredicateDefinition(
            description="Rows with workflow Status Authorized.",
            required_filters=(RequiredFilter("Status", "eq", "Authorized"),),
            natural_names=("authorized", "authorized record", "authorized records"),
        ),
    }
)

INTERPRETATION_PRESETS = MappingProxyType(
    {
        "worked_days": InterpretationDefinition(
            "Distinct dates on which actual work occurred.",
            "distinct_dates",
            ("worked",),
        ),
        "scheduled_working_days": InterpretationDefinition(
            "Distinct dates classified as scheduled working days.",
            "distinct_dates",
            ("scheduled_working_day",),
        ),
        "scheduled_non_attended_days": InterpretationDefinition(
            "Scheduled working dates without positive worked hours.",
            "distinct_dates",
            ("scheduled_working_day", "not_worked"),
        ),
        "absent_days": InterpretationDefinition(
            "Distinct dates carrying the explicit Absent exception.",
            "distinct_dates",
            ("absent",),
        ),
        "attendance_records": InterpretationDefinition(
            "Matching daily attendance rows.", "attendance_records"
        ),
        "authorized_records": InterpretationDefinition(
            "Matching attendance rows with workflow Status Authorized.",
            "attendance_records",
            ("authorized",),
        ),
        "employees": InterpretationDefinition(
            "Distinct employees in matching attendance rows.", "employees"
        ),
    }
)

CALCULATION_DEFINITIONS = MappingProxyType(
    {
        "sum": CalculationDefinition(
            description="Sum a numeric attendance field.",
            natural_names=("sum", "combined total", "total"),
            detection_patterns=(
                r"\bsum(?:\s+of)?\b",
                r"\bcombined\b",
                r"\btotal\b(?!\s+(?:number|count)\b)",
                r"\b(?:what\s+is|calculate|show\s+me)\s+(?:the\s+)?total\b",
            ),
            requires_numeric_field=True,
        ),
        "average": CalculationDefinition(
            description="Average a numeric attendance field.",
            natural_names=("average", "avg", "mean"),
            detection_patterns=(r"\b(?:average|avg|mean)\b",),
            requires_numeric_field=True,
        ),
        "min": CalculationDefinition(
            description="Find the minimum of a numeric attendance field.",
            natural_names=("minimum", "min"),
            detection_patterns=(r"\b(?:minimum|min)\b",),
            requires_numeric_field=True,
        ),
        "max": CalculationDefinition(
            description="Find the maximum of a numeric attendance field.",
            natural_names=("maximum", "max"),
            detection_patterns=(r"\b(?:maximum|max)\b",),
            requires_numeric_field=True,
        ),
        "percentage": CalculationDefinition(
            description=(
                "Calculate the percentage of a record, date, or employee population "
                "that matches one numerator condition."
            ),
            natural_names=("percentage", "percent", "rate"),
            detection_patterns=(r"\b(?:percentage|percent|rate)\b",),
        ),
    }
)


@dataclass(frozen=True)
class DerivedResultDefinition:
    description: str
    requires_grouping: bool = True


DERIVED_RESULT_DEFINITIONS = MappingProxyType(
    {
        "value": DerivedResultDefinition("The typed aggregate result for each group."),
    }
)

UNSUPPORTED_REQUEST_PATTERNS = MappingProxyType(
    {
        "nested_boolean_filters": (r"\bor\b", r"\bnot\s*\("),
        "unsupported_calculation": (
            r"\b(?:median|percentile|standard deviation|variance)\b",
        ),
        "narrative_explanation": (
            r"\b(?:explain why|why did|why were|recommend|predict|forecast)\b",
        ),
    }
)

VALUE_CONCEPT_DEFINITIONS = MappingProxyType(
    {
        "off_day": ValueConceptDefinition(
            field="Day_Type",
            description=(
                "Dates classified by the source system as either off-day category."
            ),
            natural_names=("off day", "off days"),
            members=("OFF Day", "OFF Day (ZAS)"),
        ),
        "draft_status": ValueConceptDefinition(
            field="Status",
            description="Rows with workflow Status Draft.",
            natural_names=("draft", "draft record", "draft records"),
            members=("Draft",),
            operator="eq",
        ),
    }
)

RETRIEVAL_INTENT_DEFINITIONS = MappingProxyType(
    {
        "attendance_anomaly": RetrievalIntentDefinition(
            description="Fuzzy requests about unusual attendance or clocking behavior.",
            natural_names=(
                "abnormal",
                "anomaly",
                "anomalies",
                "concerning",
                "concern",
                "concerns",
                "odd",
                "problematic",
                "resembling",
                "similar",
                "suspicious",
                "unusual",
                "irregular",
            ),
        ),
        "attendance_pattern": RetrievalIntentDefinition(
            description="Fuzzy requests about attendance or clocking patterns.",
            natural_names=(
                "attendance behavior",
                "attendance behaviour",
                "attendance issue",
                "attendance issues",
                "attendance pattern",
                "attendance patterns",
                "attendance summary",
                "attendance summaries",
                "clocking behavior",
                "clocking behaviour",
                "clocking issue",
                "clocking issues",
                "clocking pattern",
                "clocking patterns",
                "clocking summary",
                "clocking summaries",
            ),
        ),
        "attendance_review": RetrievalIntentDefinition(
            description="Fuzzy requests requiring attendance-record review.",
            natural_names=(
                "incomplete clocking",
                "hr review",
                "chronic lateness",
                "recurring lateness pattern",
                "recurring lateness patterns",
                "repeated lateness pattern",
                "repeated lateness patterns",
                "recurring absence pattern",
                "recurring absence patterns",
                "repeated absence pattern",
                "repeated absence patterns",
                "recurring attendance pattern",
                "recurring attendance patterns",
                "repeated attendance pattern",
                "repeated attendance patterns",
            ),
        ),
    }
)


def _named_registry_items(registry):
    return ((name, registry[name]) for name in sorted(registry))


def render_planner_schema() -> str:
    """Render deterministic, planner-safe metadata without SQL implementation details."""
    payload = {
        "fields": [
            {
                "name": name,
                "description": definition.description,
                "natural_names": list(definition.natural_names),
                "storage_type": definition.storage_type,
                "operators": list(definition.operators),
                "resolution_kind": definition.resolution_kind,
                "output_unit": definition.output_unit,
                "closed_values": list(definition.closed_values),
                "value_aliases": [
                    {
                        "natural_name": alias.natural_name,
                        "canonical_value": alias.canonical_value,
                    }
                    for alias in definition.value_aliases
                ],
                "roles": {
                    "filterable": definition.filterable,
                    "groupable": definition.groupable,
                    "orderable": definition.orderable,
                    "aggregatable": definition.aggregatable,
                },
            }
            for name, definition in _named_registry_items(FIELD_DEFINITIONS)
            if definition.planner_visible
        ],
        "measures": [
            {
                "name": name,
                "description": definition.description,
                "natural_names": list(definition.natural_names),
                "aggregation": definition.aggregation,
                "aggregation_field": definition.aggregation_field,
                "answer_unit": definition.answer_unit,
                "default_answer_shape": definition.default_answer_shape,
            }
            for name, definition in _named_registry_items(MEASURE_DEFINITIONS)
        ],
        "derived_results": [
            {
                "name": name,
                "description": definition.description,
                "role": "order_by",
                "requires_grouping": definition.requires_grouping,
            }
            for name, definition in DERIVED_RESULT_DEFINITIONS.items()
        ],
        "calculations": [
            {
                "name": name,
                "description": definition.description,
                "natural_names": list(definition.natural_names),
                "requires_numeric_field": definition.requires_numeric_field,
            }
            for name, definition in _named_registry_items(CALCULATION_DEFINITIONS)
        ],
        "predicates": [
            {
                "name": name,
                "description": definition.description,
                "natural_names": list(definition.natural_names),
                "required_filters": [
                    {
                        "field": item.field,
                        "operator": item.operator,
                        "value": item.value,
                    }
                    for item in definition.required_filters
                ],
                "incompatible_with": list(definition.incompatible_with),
            }
            for name, definition in _named_registry_items(
                BUSINESS_PREDICATE_DEFINITIONS
            )
        ],
        "interpretations": [
            {
                "name": name,
                "description": definition.description,
                "measure": definition.measure,
                "business_predicates": list(definition.business_predicates),
            }
            for name, definition in _named_registry_items(INTERPRETATION_PRESETS)
        ],
        "value_concepts": [
            {
                "name": name,
                "field": definition.field,
                "description": definition.description,
                "natural_names": list(definition.natural_names),
                "members": list(definition.members),
                "operator": definition.operator,
            }
            for name, definition in _named_registry_items(VALUE_CONCEPT_DEFINITIONS)
        ],
    }
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def relevant_field_definitions(question: str):
    normalized = re.sub(r"[^\w\s]", " ", question.casefold())
    normalized = " ".join(re.sub(r"[_-]+", " ", normalized).split())
    return {
        field: definition
        for field, definition in FIELD_DEFINITIONS.items()
        if any(
            re.search(rf"(?:^|\s){re.escape(phrase)}(?:$|\s)", normalized)
            for phrase in definition.natural_names
        )
    }
