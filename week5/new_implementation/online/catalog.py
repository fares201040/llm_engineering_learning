"""Authoritative logical attendance catalog for the online runtime."""

from __future__ import annotations

from dataclasses import dataclass
import json
from types import MappingProxyType

from ..attendance_schema import (
    BUSINESS_PREDICATE_DEFINITIONS,
    FIELD_DEFINITIONS,
    POSTGRES_FIELD_MAP,
    canonicalize_storage_value,
)


_LOGICAL_NAMES = {
    "Employee_ID": "employee_id",
    "Name": "employee_name",
    "Date": "date",
    "Total_Worked_Hrs": "worked_hours",
    "Lateness_Hrs": "lateness_hours",
    "Early_Out_Hrs": "early_out_hours",
    "Overbreak_Hrs": "overbreak_hours",
    "Post_OT_hrs": "post_overtime_hours",
    "pre_ot_hrs": "pre_overtime_hours",
    "Total_OT": "total_overtime_hours",
    "OT_Authorized": "authorized_overtime_hours",
    "OT_Not_Authorized": "unauthorized_overtime_hours",
    "Leave_Hrs": "leave_hours",
}


def logical_field_id(catalog_field: str) -> str:
    return _LOGICAL_NAMES.get(catalog_field, catalog_field.casefold())


@dataclass(frozen=True)
class FieldCapability:
    logical_id: str
    catalog_field: str
    scalar_type: str
    description: str
    operators: tuple[str, ...]
    filterable: bool
    projectable: bool
    groupable: bool
    orderable: bool
    aggregatable: bool
    identity: bool
    closed_values: tuple[str, ...]


@dataclass(frozen=True)
class PredicateCapability:
    predicate_id: str
    description: str
    filters: tuple[tuple[str, str, object], ...]


class AttendanceCatalog:
    """Single source of truth for logical validation and SQL compilation."""

    def __init__(self) -> None:
        fields: dict[str, FieldCapability] = {}
        for catalog_field, definition in FIELD_DEFINITIONS.items():
            if not definition.planner_visible or catalog_field not in POSTGRES_FIELD_MAP:
                continue
            logical_id = logical_field_id(catalog_field)
            fields[logical_id] = FieldCapability(
                logical_id=logical_id,
                catalog_field=catalog_field,
                scalar_type=definition.storage_type,
                description=definition.description,
                operators=tuple(definition.operators),
                filterable=(
                    definition.filterable
                    and catalog_field not in {"Employee_ID", "Name"}
                ),
                projectable=definition.context and catalog_field != "Period",
                groupable=definition.groupable,
                orderable=definition.orderable,
                aggregatable=definition.aggregatable,
                identity=catalog_field in {"Employee_ID", "Name"},
                closed_values=tuple(definition.closed_values),
            )
        predicates = {
            predicate_id: PredicateCapability(
                predicate_id=predicate_id,
                description=definition.description,
                filters=tuple(
                    (
                        logical_field_id(required.field),
                        required.operator,
                        required.value,
                    )
                    for required in definition.required_filters
                ),
            )
            for predicate_id, definition in BUSINESS_PREDICATE_DEFINITIONS.items()
        }
        self.fields = MappingProxyType(fields)
        self.predicates = MappingProxyType(predicates)
        self.aggregate_functions = MappingProxyType(
            {
                "count": "any",
                "distinct_count": "aggregatable",
                "sum": "number",
                "average": "number",
                "min": "ordered",
                "max": "ordered",
            }
        )

    def physical_expression(self, logical_id: str) -> str:
        field = self.fields.get(logical_id)
        if field is None:
            raise ValueError(f"unsupported logical field {logical_id!r}")
        return POSTGRES_FIELD_MAP[field.catalog_field]

    def canonicalize_value(self, logical_id: str, value: object) -> str | float:
        field = self.fields.get(logical_id)
        if field is None:
            raise ValueError(f"unsupported logical field {logical_id!r}")
        return canonicalize_storage_value(field.catalog_field, value)


ATTENDANCE_CATALOG = AttendanceCatalog()


def render_provider_catalog(catalog: AttendanceCatalog = ATTENDANCE_CATALOG) -> str:
    """Return logical capabilities without physical database identifiers."""

    payload = {
        "source": "attendance",
        "fields": [
            {
                "id": field.logical_id,
                "type": field.scalar_type,
                "description": field.description,
                "operators": list(field.operators) if field.filterable else [],
                "roles": [
                    role
                    for role, enabled in (
                        ("filter", field.filterable),
                        ("project", field.projectable),
                        ("group", field.groupable),
                        ("order", field.orderable),
                        ("aggregate", field.aggregatable),
                    )
                    if enabled
                ],
                "closed_values": list(field.closed_values),
            }
            for field in catalog.fields.values()
        ],
        "predicates": [
            {
                "id": predicate.predicate_id,
                "description": predicate.description,
                "filters": [
                    {"field_id": field_id, "operator": operator, "value": value}
                    for field_id, operator, value in predicate.filters
                ],
            }
            for predicate in catalog.predicates.values()
        ],
        "aggregate_functions": sorted(catalog.aggregate_functions),
        "unsupported": [
            "derive",
            "compare",
            "join",
            "set",
            "ranking",
            "window",
            "multiple_units",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = [
    "ATTENDANCE_CATALOG",
    "AttendanceCatalog",
    "FieldCapability",
    "PredicateCapability",
    "logical_field_id",
    "render_provider_catalog",
]
