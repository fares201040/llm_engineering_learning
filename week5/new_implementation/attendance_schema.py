"""Canonical APDC attendance fields and deterministic calculation semantics."""

from dataclasses import dataclass, replace
from datetime import date
import json
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


class FilterCondition(BaseModel):
    field: str
    operator: FilterOperator
    value: FilterValue


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
    group_by: list[str] = Field(default_factory=list)
    percentage_condition: FilterCondition | None = None
    order_by: str | None = None
    order_direction: Literal["asc", "desc"] = "desc"
    limit: int | None = None
    measure: MeasureName | None = None
    business_predicates: list[BusinessPredicateName] = Field(default_factory=list)
    interpretation_candidates: list[InterpretationName] = Field(default_factory=list)


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
            "percentage",
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
    unit: Literal["dates", "records", "employees", "hours", "percentage", "value"]
    subject_field: str | None = None
    grain: list[str] = Field(default_factory=list)


class PlannerProposal(_StrictPlannerModel):
    status: PlanningStatus
    filters: list[ProposedFilter] = Field(default_factory=list)
    name_hint: ProposedNameHint | None = None
    measure: ProposedMeasureChoice | None = None
    business_predicates: list[ProposedPredicateChoice] = Field(default_factory=list)
    calculation: ProposedCalculation | None = None
    group_by: list[ProposedFieldChoice] = Field(default_factory=list)
    order_by: ProposedOrderChoice | None = None
    limit: ProposedLimit | None = None
    answer_contract: AnswerContract | None = None
    interpretation_candidates: list[InterpretationName] = Field(default_factory=list)
    unsupported_capabilities: list[UnsupportedCapability] = Field(default_factory=list)
    explanation: str | None = None

    @model_validator(mode="after")
    def _validate_status_and_shape(self):
        if self.status == "ready":
            if self.answer_contract is None:
                raise ValueError("ready proposals require answer_contract")
            if self.interpretation_candidates:
                raise ValueError("ready proposals cannot contain interpretation candidates")
        if self.status != "unsupported" and self.unsupported_capabilities:
            raise ValueError("capability identifiers require unsupported status")
        if self.status == "unsupported":
            if not self.unsupported_capabilities:
                raise ValueError("unsupported proposals require a capability identifier")
            execution_choices = (
                self.filters
                or self.name_hint is not None
                or self.measure is not None
                or self.business_predicates
                or self.calculation is not None
                or self.group_by
                or self.order_by is not None
                or self.limit is not None
                or self.answer_contract is not None
                or self.interpretation_candidates
            )
            if execution_choices:
                raise ValueError("unsupported proposals cannot contain execution choices")
        if self.measure is not None and self.calculation is not None:
            raise ValueError("measure and calculation are mutually exclusive")
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
    field: str
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


@dataclass(frozen=True)
class PredicateDefinition:
    description: str
    required_filters: tuple[RequiredFilter, ...]
    natural_names: tuple[str, ...] = ()
    incompatible_with: tuple[BusinessPredicateName, ...] = ()
    incompatible_filters: tuple[RequiredFilter, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ValueConceptDefinition:
    field: str
    description: str
    natural_names: tuple[str, ...]
    members: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RetrievalIntentDefinition:
    description: str
    natural_names: tuple[str, ...]


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
        (r"\bday types?\b", r"\bscheduled working days?\b"),
        ("Working Day", "OFF Day", "OFF Day (ZAS)"),
    ),
    "Holiday_Type": FieldDefinition(
        "text", "Holiday classification for the date.", (r"\bholidays?\b",)
    ),
    "Shift": FieldDefinition("text", "Assigned work shift.", (r"\bshifts?\b",)),
    "Status": FieldDefinition(
        "text",
        "Workflow or approval state; it is not proof that work occurred.",
        (r"\bstatus\b", r"\bauthorized records?\b", r"\bdraft records?\b"),
        ("Authorized", "Draft", "Pending For Authorization"),
    ),
    "Exception": FieldDefinition(
        "text",
        "Attendance outcome such as absence, lateness, early out, or OK.",
        (r"\bexceptions?\b", r"\babs(?:ent|ence)\b"),
    ),
    "Total_Worked_Hrs": FieldDefinition(
        "number",
        "Actual hours worked; a worked day requires a value greater than zero.",
        (r"\bworked hours?\b", r"\bhours? worked\b", r"\bwork hours?\b"),
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
    "Day_Type": ("day type", "day types", "scheduled working day", "scheduled working days"),
    "Holiday_Type": ("holiday", "holidays", "holiday type", "holiday types"),
    "Shift": ("shift", "shifts"),
    "Status": ("status", "authorized record", "authorized records", "draft record", "draft records"),
    "Exception": ("exception", "exceptions", "absent", "absence"),
    "Total_Worked_Hrs": ("worked hour", "worked hours", "hours worked", "work hours"),
    "Lateness_Hrs": ("late", "lateness", "late in", "late-in"),
    "Early_Out_Hrs": ("early out", "early-out"),
    "Overbreak_Hrs": ("over break", "over-break", "overbreak"),
    "Regular_Units": ("regular unit", "regular units"),
    "Total_OT": ("overtime", "total ot"),
    "OT_Authorized": ("authorized overtime", "overtime authorized", "authorized ot", "ot authorized"),
    "OT_Not_Authorized": (
        "unauthorized overtime",
        "overtime not authorized",
        "unauthorized ot",
        "ot not authorized",
    ),
    "Leave_Type": ("leave", "leave type", "leave types"),
    "Leave_Hrs": ("leave", "leave hour", "leave hours"),
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
            _definition.storage_type == "number"
            or _field in {"Date", "Employee_ID"}
        ),
        planner_visible=_field != "chunk_type",
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
            natural_names=("attendance record", "attendance records", "row", "rows"),
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
            natural_names=("scheduled working day", "scheduled working days"),
        ),
        "worked": PredicateDefinition(
            description="Dates with positive actual worked hours.",
            required_filters=(RequiredFilter("Total_Worked_Hrs", "gt", 0.0),),
            natural_names=("worked", "attended", "present"),
            incompatible_with=("not_worked", "absent"),
            incompatible_filters=(RequiredFilter("Exception", "eq", "Absent"),),
        ),
        "not_worked": PredicateDefinition(
            description="Dates without positive actual worked hours.",
            required_filters=(RequiredFilter("Total_Worked_Hrs", "lte", 0.0),),
            natural_names=("not worked", "did not work", "not attended", "not present"),
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
            }
            for name, definition in _named_registry_items(VALUE_CONCEPT_DEFINITIONS)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

INCOMPATIBLE_BUSINESS_PREDICATE_SETS = tuple(
    sorted(
        {
            frozenset((name, incompatible))
            for name, definition in BUSINESS_PREDICATE_DEFINITIONS.items()
            for incompatible in definition.incompatible_with
        },
        key=lambda item: tuple(sorted(item)),
    )
)


def _matches_registry_filter(condition: FilterCondition, required: RequiredFilter) -> bool:
    values = condition.value if isinstance(condition.value, list) else [condition.value]
    required_values = required.value if isinstance(required.value, tuple) else (required.value,)
    return condition.field == required.field and condition.operator == required.operator and {
        str(value).casefold() for value in values
    } == {str(value).casefold() for value in required_values}


def compile_business_intent(plan: QueryPlan) -> QueryPlan:
    """Compile orthogonal measure and predicate choices into executable fields."""
    compiled = plan.model_copy(deep=True)
    predicate_names = list(dict.fromkeys(compiled.business_predicates))
    predicate_set = set(predicate_names)
    for incompatible in INCOMPATIBLE_BUSINESS_PREDICATE_SETS:
        if incompatible <= predicate_set:
            first, second = sorted(incompatible, reverse=True)
            raise ValueError(
                f"Business predicates {first} and {second} are incompatible."
            )
    for name in predicate_names:
        for incompatible in BUSINESS_PREDICATE_DEFINITIONS[name].incompatible_filters:
            if any(
                _matches_registry_filter(condition, incompatible)
                for condition in compiled.filters
            ):
                raise ValueError(
                    f"Business predicate {name} conflicts with an explicit "
                    f"{incompatible.field}={incompatible.value} filter."
                )

    if compiled.measure is not None:
        definition = MEASURE_DEFINITIONS[compiled.measure]
        compiled.mode = "exact"
        compiled.aggregation = definition.aggregation
        compiled.aggregation_field = definition.aggregation_field
        if definition.aggregation_field is not None:
            compiled.group_by = [
                field
                for field in compiled.group_by
                if field != definition.aggregation_field
            ]

    for name in predicate_names:
        compiled.mode = "exact"
        for required in BUSINESS_PREDICATE_DEFINITIONS[name].required_filters:
            existing = [
                condition
                for condition in compiled.filters
                if condition.field == required.field
            ]
            if existing:

                def matches_required(condition):
                    same_value = (
                        str(condition.value).casefold()
                        == str(required.value).casefold()
                        if isinstance(condition.value, str)
                        and isinstance(required.value, str)
                        else condition.value == required.value
                    )
                    return condition.operator == required.operator and same_value

                if any(not matches_required(condition) for condition in existing):
                    raise ValueError(
                        f"Business predicate {name} conflicts with an explicit "
                        f"{required.field} filter."
                    )
                for condition in existing:
                    if isinstance(condition.value, str) and isinstance(
                        required.value, str
                    ):
                        condition.value = required.value
                continue
            compiled.filters.append(
                FilterCondition(
                    field=required.field,
                    operator=required.operator,
                    value=required.value,
                )
            )
    compiled.business_predicates = predicate_names
    return compiled


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
