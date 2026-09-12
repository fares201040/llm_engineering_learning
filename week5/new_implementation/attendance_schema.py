"""Canonical APDC attendance fields and deterministic calculation semantics."""

from dataclasses import dataclass, replace
from datetime import date
import re
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, Field


class FilterCondition(BaseModel):
    field: str
    operator: Literal[
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
    value: str | float | list[str] | list[float]


MeasureName = Literal["distinct_dates", "attendance_records", "employees"]
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
    order_by: Literal["group", "value"] | None = None
    order_direction: Literal["asc", "desc"] = "desc"
    limit: int | None = None
    measure: MeasureName | None = None
    business_predicates: list[BusinessPredicateName] = Field(default_factory=list)
    interpretation_candidates: list[InterpretationName] = Field(default_factory=list)


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
    operators: tuple[str, ...] = ()
    sql_expression: str = ""
    searchable: bool = True
    metadata: bool = True
    context: bool = True
    catalog_resolution: bool = False


@dataclass(frozen=True)
class RequiredFilter:
    field: str
    operator: str
    value: str | float


@dataclass(frozen=True)
class MeasureDefinition:
    description: str
    aggregation: str
    aggregation_field: str | None


@dataclass(frozen=True)
class PredicateDefinition:
    description: str
    required_filters: tuple[RequiredFilter, ...]


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

_CATALOG_FIELDS = {
    "Department",
    "Work_Location",
    "Shift",
    "Status",
    "Exception",
    "Leave_Type",
}
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
        catalog_resolution=_field in _CATALOG_FIELDS,
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

QUESTION_CONTEXT_FIELDS = MappingProxyType(
    {
        field: definition.aliases
        for field, definition in FIELD_DEFINITIONS.items()
        if definition.aliases
    }
)

MEASURE_DEFINITIONS = MappingProxyType(
    {
        "distinct_dates": MeasureDefinition(
            "Count distinct attendance dates.", "distinct_count", "Date"
        ),
        "attendance_records": MeasureDefinition(
            "Count matching daily attendance rows.", "count", None
        ),
        "employees": MeasureDefinition(
            "Count distinct employee identifiers.",
            "distinct_count",
            "Employee_ID",
        ),
    }
)

BUSINESS_PREDICATE_DEFINITIONS = MappingProxyType(
    {
        "scheduled_working_day": PredicateDefinition(
            "Scheduled working dates; this is not proof that work occurred.",
            (RequiredFilter("Day_Type", "eq", "Working Day"),),
        ),
        "worked": PredicateDefinition(
            "Dates with positive actual worked hours.",
            (RequiredFilter("Total_Worked_Hrs", "gt", 0.0),),
        ),
        "not_worked": PredicateDefinition(
            "Dates without positive actual worked hours.",
            (RequiredFilter("Total_Worked_Hrs", "lte", 0.0),),
        ),
        "absent": PredicateDefinition(
            "Dates carrying the explicit Absent exception.",
            (RequiredFilter("Exception", "eq", "Absent"),),
        ),
        "authorized": PredicateDefinition(
            "Rows with workflow Status Authorized.",
            (RequiredFilter("Status", "eq", "Authorized"),),
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

SEMANTIC_INTENT_PATTERNS = (
    r"\b(?:abnormal|anomal(?:y|ies)|concerning|concerns?|odd|problematic|resembling|similar|suspicious|unusual|irregular)\b",
    r"\b(?:attendance|clocking) (?:behavior|behaviour|issues?|patterns?|summary|summaries)\b",
    r"\b(?:incomplete clocking|hr review|chronic lateness)\b",
)

INCOMPATIBLE_BUSINESS_PREDICATE_SETS = (
    frozenset(("worked", "not_worked")),
    frozenset(("worked", "absent")),
)


def _filter_explicitly_selects_absence(condition: FilterCondition) -> bool:
    if condition.field != "Exception":
        return False
    values = (
        condition.value
        if condition.operator == "in" and isinstance(condition.value, list)
        else [condition.value]
    )
    return condition.operator in {"eq", "in"} and any(
        str(value).casefold() == "absent" for value in values
    )


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
    if "worked" in predicate_set and any(
        _filter_explicitly_selects_absence(condition) for condition in compiled.filters
    ):
        raise ValueError(
            "Business predicate worked conflicts with an explicit Exception=Absent filter."
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
                if any(
                    condition.operator != required.operator
                    or condition.value != required.value
                    for condition in existing
                ):
                    raise ValueError(
                        f"Business predicate {name} conflicts with an explicit "
                        f"{required.field} filter."
                    )
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
    return {
        field: definition
        for field, definition in FIELD_DEFINITIONS.items()
        if any(
            re.search(pattern, question, flags=re.IGNORECASE)
            for pattern in definition.aliases
        )
    }
