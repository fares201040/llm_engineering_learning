"""Single logical/physical field and predicate authority for online queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json
import math
from types import MappingProxyType


@dataclass(frozen=True)
class FieldCapability:
    logical_id: str
    physical_expression: str
    scalar_type: str
    description: str
    operators: tuple[str, ...]
    filterable: bool = True
    projectable: bool = True
    groupable: bool = True
    orderable: bool = True
    aggregatable: bool = False
    identity: bool = False
    closed_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class PredicateCapability:
    predicate_id: str
    description: str
    filters: tuple[tuple[str, str, object], ...]


_TEXT = ("eq", "ne", "in", "contains", "starts_with")
_ORDERED = ("eq", "ne", "gt", "gte", "lt", "lte", "in")


def _field(logical_id, physical, scalar_type, description, **options):
    return FieldCapability(
        logical_id=logical_id,
        physical_expression=physical,
        scalar_type=scalar_type,
        description=description,
        operators=_TEXT if scalar_type == "text" else _ORDERED,
        aggregatable=scalar_type == "number" or logical_id in {"date", "employee_id"},
        **options,
    )


_FIELDS = (
    _field("employee_id", "employee_id", "text", "Stable employee identifier.", filterable=False, identity=True),
    _field("employee_name", "name", "text", "Authoritative employee name.", filterable=False, identity=True),
    _field("organization_unit", "organization_unit", "text", "Employee organization unit."),
    _field("country", "country", "text", "Employee work country."),
    _field("work_location", "work_location", "text", "Employee work location."),
    _field("department", "department", "text", "Employee department."),
    _field("position", "position", "text", "Employee position."),
    _field("job", "job", "text", "Employee job title."),
    _field("gradeset", "gradeset", "text", "Employee grade set."),
    _field("grade", "grade", "text", "Employee grade."),
    _field("date", "attendance_date", "date", "Calendar date represented by the record."),
    _field("day", "day", "text", "Day-of-week label."),
    _field("day_type", "day_type", "text", "Schedule classification, not proof of attendance.", closed_values=("Working Day", "OFF Day", "OFF Day (ZAS)")),
    _field("holiday_type", "holiday_type", "text", "Holiday classification."),
    _field("shift", "shift", "text", "Assigned work shift."),
    _field("status", "status", "text", "Workflow state, not proof of work.", closed_values=("Authorized", "Draft", "Pending For Authorization")),
    _field("exception", "exception", "text", "Attendance outcome such as Absent or OK."),
    _field("worked_hours", "total_worked_hrs", "number", "Actual hours worked."),
    _field("lateness_hours", "lateness_hrs", "number", "Hours of lateness."),
    _field("early_out_hours", "early_out_hrs", "number", "Hours of early departure."),
    _field("overbreak_hours", "overbreak_hrs", "number", "Hours beyond the permitted break."),
    _field("regular_units", "regular_units", "number", "Regular attendance units."),
    _field("pre_overtime_hours", "pre_ot_hrs", "number", "Overtime before shift."),
    _field("post_overtime_hours", "post_ot_hrs", "number", "Overtime after shift."),
    _field("total_overtime_hours", "total_ot", "number", "Total overtime hours."),
    _field("authorized_overtime_hours", "ot_authorized", "number", "Authorized overtime hours."),
    _field("unauthorized_overtime_hours", "ot_not_authorized", "number", "Unauthorized overtime hours."),
    _field("leave_type", "leave_type", "text", "Recorded leave category."),
    _field("leave_hours", "leave_hrs", "number", "Recorded leave hours."),
)


class AttendanceCatalog:
    def __init__(self):
        self.fields = MappingProxyType({item.logical_id: item for item in _FIELDS})
        self.predicates = MappingProxyType(
            {
                "scheduled_working_day": PredicateCapability("scheduled_working_day", "Scheduled working dates; not proof that work occurred.", (("day_type", "eq", "Working Day"),)),
                "worked": PredicateCapability("worked", "Dates with positive actual worked hours.", (("worked_hours", "gt", 0.0),)),
                "not_worked": PredicateCapability("not_worked", "Dates without positive actual worked hours.", (("worked_hours", "lte", 0.0),)),
                "absent": PredicateCapability("absent", "Dates carrying the explicit Absent exception.", (("exception", "eq", "Absent"),)),
                "authorized": PredicateCapability("authorized", "Rows with workflow Status Authorized.", (("status", "eq", "Authorized"),)),
                "off_day": PredicateCapability("off_day", "Either source-system off-day category.", (("day_type", "in", ("OFF Day", "OFF Day (ZAS)")),)),
            }
        )
        self.aggregate_functions = MappingProxyType(
            {"count": "any", "distinct_count": "aggregatable", "sum": "number", "average": "number", "min": "ordered", "max": "ordered"}
        )

    def physical_expression(self, logical_id: str) -> str:
        try:
            return self.fields[logical_id].physical_expression
        except KeyError as exc:
            raise ValueError(f"unsupported logical field {logical_id!r}") from exc

    def canonicalize_value(self, logical_id: str, value: object) -> str | float:
        try:
            field = self.fields[logical_id]
        except KeyError as exc:
            raise ValueError(f"unsupported logical field {logical_id!r}") from exc
        text = str(value).strip()
        if field.scalar_type == "number":
            try:
                number = float(Decimal(text))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"{logical_id} requires a number") from exc
            if not math.isfinite(number):
                raise ValueError(f"{logical_id} requires a finite number")
            return number
        try:
            if field.scalar_type == "date":
                return date.fromisoformat(text).isoformat()
            if field.scalar_type == "time":
                return time.fromisoformat(text).isoformat()
            if field.scalar_type == "datetime":
                return datetime.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise ValueError(f"{logical_id} requires a valid ISO {field.scalar_type}") from exc
        return text


ATTENDANCE_CATALOG = AttendanceCatalog()


def render_provider_catalog(catalog: AttendanceCatalog = ATTENDANCE_CATALOG) -> str:
    payload = {
        "source": "attendance",
        "fields": [
            {
                "id": item.logical_id,
                "type": item.scalar_type,
                "description": item.description,
                "operators": list(item.operators) if item.filterable else [],
                "roles": [role for role, enabled in (("filter", item.filterable), ("project", item.projectable), ("group", item.groupable), ("order", item.orderable), ("aggregate", item.aggregatable)) if enabled],
                "closed_values": list(item.closed_values),
            }
            for item in catalog.fields.values()
        ],
        "predicates": [{"id": item.predicate_id, "description": item.description} for item in catalog.predicates.values()],
        "aggregate_functions": sorted(catalog.aggregate_functions),
        "unsupported": ["multiple_units", "derive", "compare", "join", "set", "ranking", "window"],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = ["ATTENDANCE_CATALOG", "AttendanceCatalog", "FieldCapability", "PredicateCapability", "render_provider_catalog"]
