"""Flat, bounded attendance query and include-only materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import AttendanceCatalog


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, str_strip_whitespace=True
    )


Scalar = str | int | float | bool
Value = Scalar | tuple[Scalar, ...]


class EvidenceSpan(_StrictModel):
    start: int = Field(ge=0, le=50000)
    end: int = Field(ge=1, le=50000)
    text: str = Field(min_length=1, max_length=50000)

    @model_validator(mode="after")
    def _is_forward(self):
        if self.end <= self.start:
            raise ValueError("evidence must be a forward half-open span")
        return self


class Condition(_StrictModel):
    kind: Literal["condition"] = "condition"
    field_id: str = Field(min_length=1, max_length=128)
    operator: Literal[
        "eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "starts_with"
    ]
    value: Value

    @model_validator(mode="after")
    def _value_matches_operator(self):
        if self.operator == "in":
            if not isinstance(self.value, tuple) or not self.value:
                raise ValueError("operator 'in' requires a non-empty tuple")
        elif isinstance(self.value, tuple):
            raise ValueError(f"operator {self.operator!r} requires one value")
        return self


class Predicate(_StrictModel):
    kind: Literal["predicate"] = "predicate"
    predicate_id: str = Field(min_length=1, max_length=128)


class All(_StrictModel):
    kind: Literal["all"] = "all"
    items: tuple[BooleanExpr, ...] = Field(min_length=1, max_length=64)


class Any(_StrictModel):
    kind: Literal["any"] = "any"
    items: tuple[BooleanExpr, ...] = Field(min_length=1, max_length=64)


class Not(_StrictModel):
    kind: Literal["not"] = "not"
    item: BooleanExpr


BooleanExpr = Annotated[Condition | Predicate | All | Any | Not, Field(discriminator="kind")]


class Ordering(_StrictModel):
    output_id: str = Field(min_length=1, max_length=128)
    direction: Literal["asc", "desc"]


class AggregateMeasure(_StrictModel):
    output_id: str = Field(min_length=1, max_length=128)
    function: Literal["count", "distinct_count", "sum", "average", "min", "max"]
    field_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _function_shape(self):
        if self.function == "count" and self.field_id is not None:
            raise ValueError("count does not accept a field")
        if self.function != "count" and self.field_id is None:
            raise ValueError(f"{self.function} requires a field")
        return self


class RowsOutput(_StrictModel):
    kind: Literal["rows"] = "rows"
    fields: tuple[str, ...] = Field(min_length=1, max_length=20)
    ordering: tuple[Ordering, ...] = Field(default=(), max_length=8)
    limit: int = Field(ge=1, le=1000)

    @field_validator("fields")
    @classmethod
    def _unique_fields(cls, value: tuple[str, ...]):
        if len(value) != len(set(value)):
            raise ValueError("row fields must be unique")
        return value


class AggregateOutput(_StrictModel):
    kind: Literal["aggregate"] = "aggregate"
    measures: tuple[AggregateMeasure, ...] = Field(min_length=1, max_length=16)
    group_by: tuple[str, ...] = Field(default=(), max_length=8)
    having: BooleanExpr | None = None
    ordering: tuple[Ordering, ...] = Field(default=(), max_length=8)
    limit: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def _unique_outputs(self):
        output_ids = [item.output_id for item in self.measures]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("aggregate output IDs must be unique")
        if set(output_ids) & set(self.group_by):
            raise ValueError("measure outputs cannot shadow grouping fields")
        if len(self.group_by) != len(set(self.group_by)):
            raise ValueError("grouping fields must be unique")
        return self


QueryOutput = Annotated[RowsOutput | AggregateOutput, Field(discriminator="kind")]


class AttendanceQuery(_StrictModel):
    filters: tuple[BooleanExpr, ...] = Field(default=(), max_length=16)
    output: QueryOutput


class QueryLimits(_StrictModel):
    max_filter_components: int = Field(default=16, ge=1, le=64)
    max_boolean_depth: int = Field(default=8, ge=1, le=64)
    max_boolean_nodes: int = Field(default=64, ge=1, le=512)
    max_projection_width: int = Field(default=20, ge=1, le=64)
    max_aggregate_width: int = Field(default=16, ge=1, le=32)
    max_grouping_width: int = Field(default=8, ge=1, le=16)
    max_ordering_width: int = Field(default=8, ge=1, le=16)
    max_result_rows: int = Field(default=1000, ge=1, le=1000)
    max_literal_length: int = Field(default=1000, ge=1, le=10000)
    max_list_cardinality: int = Field(default=100, ge=1, le=1000)


class FilterComponent(_StrictModel):
    kind: Literal["filter"] = "filter"
    component_key: str = Field(min_length=1, max_length=128)
    evidence: EvidenceSpan
    expression: BooleanExpr


class OutputComponent(_StrictModel):
    kind: Literal["output"] = "output"
    component_key: str = Field(min_length=1, max_length=128)
    evidence: EvidenceSpan
    output: QueryOutput


QueryComponent = Annotated[FilterComponent | OutputComponent, Field(discriminator="kind")]


class TrustedQueryComponent(_StrictModel):
    component_id: str = Field(min_length=1, max_length=128)
    owner_turn_id: str = Field(min_length=1, max_length=128)
    component: QueryComponent


class MaterializedQuery(_StrictModel):
    query: AttendanceQuery
    components: tuple[QueryComponent, ...]


@dataclass(frozen=True)
class QueryIssue:
    code: str
    path: str
    message: str


class QueryMaterializationError(ValueError):
    def __init__(self, issues: tuple[QueryIssue, ...] | list[QueryIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{item.code}:{item.path}" for item in self.issues))


def _boolean_metrics(expression: BooleanExpr, depth: int = 1) -> tuple[int, int]:
    if isinstance(expression, (Condition, Predicate)):
        return depth, 1
    if isinstance(expression, Not):
        child_depth, child_nodes = _boolean_metrics(expression.item, depth + 1)
        return child_depth, child_nodes + 1
    metrics = [_boolean_metrics(item, depth + 1) for item in expression.items]
    return max(item[0] for item in metrics), 1 + sum(item[1] for item in metrics)


def _conditions(expression: BooleanExpr):
    if isinstance(expression, Condition):
        yield expression
    elif isinstance(expression, Not):
        yield from _conditions(expression.item)
    elif isinstance(expression, (All, Any)):
        for item in expression.items:
            yield from _conditions(item)


def _predicates(expression: BooleanExpr):
    if isinstance(expression, Predicate):
        yield expression
    elif isinstance(expression, Not):
        yield from _predicates(expression.item)
    elif isinstance(expression, (All, Any)):
        for item in expression.items:
            yield from _predicates(item)


def validate_query(
    query: AttendanceQuery,
    catalog: AttendanceCatalog,
    limits: QueryLimits,
) -> tuple[QueryIssue, ...]:
    issues: list[QueryIssue] = []
    if len(query.filters) > limits.max_filter_components:
        issues.append(QueryIssue("filter_count", "filters", "too many filter components"))
    for index, expression in enumerate(query.filters):
        depth, nodes = _boolean_metrics(expression)
        if depth > limits.max_boolean_depth:
            issues.append(QueryIssue("boolean_depth", f"filters.{index}", "Boolean expression is too deep"))
        if nodes > limits.max_boolean_nodes:
            issues.append(QueryIssue("boolean_nodes", f"filters.{index}", "Boolean expression has too many nodes"))
        for condition in _conditions(expression):
            field = catalog.fields.get(condition.field_id)
            if field is None:
                issues.append(QueryIssue("unknown_field", f"filters.{index}", "unknown logical field"))
                continue
            if not field.filterable:
                issues.append(QueryIssue("field_role", f"filters.{index}", "field is not filterable"))
            if condition.operator not in field.operators:
                issues.append(QueryIssue("operator_type", f"filters.{index}", "operator is incompatible with field"))
            values = condition.value if isinstance(condition.value, tuple) else (condition.value,)
            if len(values) > limits.max_list_cardinality:
                issues.append(QueryIssue("list_cardinality", f"filters.{index}", "literal list is too large"))
            if any(isinstance(value, str) and len(value) > limits.max_literal_length for value in values):
                issues.append(QueryIssue("literal_length", f"filters.{index}", "literal is too long"))
            for value in values:
                try:
                    catalog.canonicalize_value(condition.field_id, value)
                except ValueError:
                    issues.append(QueryIssue("literal_type", f"filters.{index}", "literal is incompatible with field type"))
                    break
        for predicate in _predicates(expression):
            if predicate.predicate_id not in catalog.predicates:
                issues.append(QueryIssue("unknown_predicate", f"filters.{index}", "unknown business predicate"))

    output = query.output
    if len(output.ordering) > limits.max_ordering_width:
        issues.append(QueryIssue("ordering_width", "output.ordering", "ordering is too wide"))
    if isinstance(output, RowsOutput):
        if len(output.fields) > limits.max_projection_width:
            issues.append(QueryIssue("projection_width", "output.fields", "projection is too wide"))
        if not output.ordering:
            issues.append(QueryIssue("rows_ordering", "output.ordering", "row output requires deterministic ordering"))
        if output.limit > limits.max_result_rows:
            issues.append(QueryIssue("result_rows", "output.limit", "row limit is too large"))
        for field_id in output.fields:
            field = catalog.fields.get(field_id)
            if field is None:
                issues.append(QueryIssue("unknown_field", "output.fields", "unknown projection field"))
            elif not field.projectable:
                issues.append(QueryIssue("field_role", "output.fields", "field is not projectable"))
        valid_outputs = set(output.fields)
    else:
        if len(output.measures) > limits.max_aggregate_width:
            issues.append(QueryIssue("aggregate_width", "output.measures", "too many measures"))
        if len(output.group_by) > limits.max_grouping_width:
            issues.append(QueryIssue("grouping_width", "output.group_by", "grouping is too wide"))
        if output.group_by and (not output.ordering or output.limit is None):
            issues.append(QueryIssue("bounded_grouping", "output", "grouped output requires ordering and limit"))
        if output.limit is not None and output.limit > limits.max_result_rows:
            issues.append(QueryIssue("result_rows", "output.limit", "aggregate limit is too large"))
        valid_outputs = set(output.group_by)
        for field_id in output.group_by:
            field = catalog.fields.get(field_id)
            if field is None:
                issues.append(QueryIssue("unknown_field", "output.group_by", "unknown grouping field"))
            elif not field.groupable:
                issues.append(QueryIssue("field_role", "output.group_by", "field is not groupable"))
        for measure in output.measures:
            valid_outputs.add(measure.output_id)
            expected = catalog.aggregate_functions.get(measure.function)
            if expected is None:
                issues.append(QueryIssue("unsupported_function", "output.measures", "unsupported aggregate function"))
                continue
            if measure.field_id is None:
                continue
            field = catalog.fields.get(measure.field_id)
            if field is None:
                issues.append(QueryIssue("unknown_field", "output.measures", "unknown aggregate field"))
            elif expected == "number" and field.scalar_type != "number":
                issues.append(QueryIssue("function_type", "output.measures", "function requires a numeric field"))
            elif expected == "aggregatable" and not field.aggregatable:
                issues.append(QueryIssue("field_role", "output.measures", "field is not aggregatable"))
            elif expected == "ordered" and not field.orderable:
                issues.append(QueryIssue("field_role", "output.measures", "field is not orderable"))
        if output.having is not None:
            for condition in _conditions(output.having):
                if condition.field_id not in valid_outputs:
                    issues.append(QueryIssue("unknown_output", "output.having", "having references an unknown output"))
    for ordering in output.ordering:
        if ordering.output_id not in valid_outputs:
            issues.append(QueryIssue("unknown_output", "output.ordering", "ordering references an unknown output"))
    return tuple(issues)


def _valid_span(message: str, evidence: EvidenceSpan) -> bool:
    return evidence.end <= len(message) and message[evidence.start : evidence.end] == evidence.text


def materialize_query(
    *,
    message: str,
    relationship: Literal["new", "modify", "repeat"],
    base_turn_id: str | None,
    retained_component_ids: tuple[str, ...],
    current_filters: tuple[FilterComponent, ...],
    current_output: OutputComponent | None,
    trusted_components: tuple[TrustedQueryComponent, ...],
    catalog: AttendanceCatalog,
    limits: QueryLimits,
) -> MaterializedQuery:
    issues: list[QueryIssue] = []
    current_components: tuple[QueryComponent, ...] = current_filters + ((current_output,) if current_output is not None else ())
    if relationship == "new" and (base_turn_id is not None or retained_component_ids):
        issues.append(QueryIssue("relationship", "relationship", "new queries cannot inherit"))
    if relationship != "new" and base_turn_id is None:
        issues.append(QueryIssue("relationship", "base_turn_id", "follow-ups require one base turn"))
    if relationship == "repeat" and current_components:
        issues.append(QueryIssue("relationship", "components", "repeat cannot add current components"))
    if relationship == "repeat" and not retained_component_ids:
        issues.append(QueryIssue("relationship", "retained_component_ids", "repeat requires retained components"))
    if len(retained_component_ids) != len(set(retained_component_ids)):
        issues.append(QueryIssue("duplicate_component", "retained_component_ids", "retained component IDs must be unique"))
    keys = [component.component_key for component in current_components]
    if len(keys) != len(set(keys)):
        issues.append(QueryIssue("duplicate_component", "components", "current component keys must be unique"))
    for index, component in enumerate(current_components):
        if not _valid_span(message, component.evidence):
            issues.append(QueryIssue("current_span", f"components.{index}.evidence", "evidence must exactly match the current message"))

    trusted_by_id = {item.component_id: item for item in trusted_components}
    retained: list[QueryComponent] = []
    for index, component_id in enumerate(retained_component_ids):
        trusted = trusted_by_id.get(component_id)
        if trusted is None:
            issues.append(QueryIssue("unknown_component", f"retained_component_ids.{index}", "trusted component does not exist"))
        elif trusted.owner_turn_id != base_turn_id:
            issues.append(QueryIssue("component_ownership", f"retained_component_ids.{index}", "component is not owned by the selected base"))
        else:
            retained.append(trusted.component)
    if issues:
        raise QueryMaterializationError(issues)

    outputs = [component for component in retained if isinstance(component, OutputComponent)]
    if current_output is not None:
        outputs.append(current_output)
    if len(outputs) != 1:
        raise QueryMaterializationError(
            [QueryIssue("result_shape", "output", "materialized query requires exactly one output")]
        )
    filters = tuple(component for component in retained if isinstance(component, FilterComponent)) + current_filters
    query = AttendanceQuery(filters=tuple(item.expression for item in filters), output=outputs[0].output)
    validation = validate_query(query, catalog, limits)
    if validation:
        raise QueryMaterializationError(list(validation))
    return MaterializedQuery(query=query, components=filters + (outputs[0],))


for _model in (All, Any, Not, AggregateOutput):
    _model.model_rebuild()


__all__ = [
    "AggregateMeasure",
    "AggregateOutput",
    "All",
    "Any",
    "AttendanceQuery",
    "BooleanExpr",
    "Condition",
    "EvidenceSpan",
    "FilterComponent",
    "MaterializedQuery",
    "Not",
    "Ordering",
    "OutputComponent",
    "Predicate",
    "QueryIssue",
    "QueryLimits",
    "QueryMaterializationError",
    "RowsOutput",
    "TrustedQueryComponent",
    "materialize_query",
    "validate_query",
]
