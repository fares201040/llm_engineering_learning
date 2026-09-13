"""Pure compilation of verified plans into parameterized PostgreSQL artifacts."""

from dataclasses import dataclass
import hashlib
import re
from typing import Literal

try:
    from .attendance_schema import (
        FIELD_DEFINITIONS,
        POSTGRES_FIELD_MAP,
        ExecutableQueryPlan,
        FilterCondition,
    )
except ImportError:  # Direct execution from week5/new_implementation.
    from attendance_schema import (
        FIELD_DEFINITIONS,
        POSTGRES_FIELD_MAP,
        ExecutableQueryPlan,
        FilterCondition,
    )


@dataclass(frozen=True)
class SqlFragment:
    sql: str
    params: tuple[object, ...]


@dataclass(frozen=True)
class CompiledPostgresQuery:
    sql: str
    params: tuple[object, ...]
    purpose: Literal["sample", "count", "aggregation", "coverage"]
    fingerprint: str


def _fingerprint(sql: str) -> str:
    normalized = " ".join(sql.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _query(sql, params, purpose):
    return CompiledPostgresQuery(sql, tuple(params), purpose, _fingerprint(sql))


def _require_table_name(table_name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", table_name) is None:
        raise ValueError("Invalid configured PostgreSQL table name.")
    return table_name


def _require_executable(plan) -> ExecutableQueryPlan:
    if type(plan) is not ExecutableQueryPlan:
        raise TypeError("PostgreSQL compilation requires ExecutableQueryPlan.")
    return plan


def compile_where(filters) -> SqlFragment:
    clauses = []
    params = []
    operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    array_types = {
        "number": "double precision[]",
        "date": "date[]",
        "time": "time[]",
        "datetime": "timestamp[]",
        "text": "text[]",
    }
    for condition in filters:
        if not isinstance(condition, FilterCondition):
            raise TypeError("Filters must be executable FilterCondition objects.")
        definition = FIELD_DEFINITIONS.get(condition.field)
        expression = POSTGRES_FIELD_MAP.get(condition.field)
        if definition is None or expression is None:
            raise ValueError(f"Unknown PostgreSQL field {condition.field!r}.")
        if condition.operator not in definition.operators:
            raise ValueError(f"Invalid operator for {condition.field!r}.")
        if condition.field == "chunk_type":
            if condition.operator == "eq" and condition.value == "attendance_record":
                continue
            raise ValueError("The attendance table contains attendance_record rows only.")
        if condition.operator == "in":
            if not isinstance(condition.value, list) or not condition.value:
                raise ValueError("Operator 'in' requires a non-empty list.")
            clauses.append(f"{expression} = ANY(%s::{array_types[definition.storage_type]})")
            params.append(condition.value)
        elif isinstance(condition.value, list):
            raise ValueError(f"Operator {condition.operator!r} requires one value.")
        elif condition.operator in operators:
            cast = "::date" if definition.storage_type == "date" else ""
            clauses.append(f"{expression} {operators[condition.operator]} %s{cast}")
            params.append(condition.value)
        elif condition.operator in {"contains", "starts_with"}:
            text_expression = expression if definition.storage_type == "text" else f"CAST({expression} AS TEXT)"
            clauses.append(f"{text_expression} ILIKE %s")
            params.append(
                f"%{condition.value}%"
                if condition.operator == "contains"
                else f"{condition.value}%"
            )
        else:
            raise ValueError(f"Unsupported operator {condition.operator!r}.")
    return SqlFragment(" AND ".join(clauses) if clauses else "TRUE", tuple(params))


def compile_count_query(
    plan: ExecutableQueryPlan, table_name: str = "attendance_records"
) -> CompiledPostgresQuery:
    plan = _require_executable(plan)
    table = _require_table_name(table_name)
    where = compile_where(plan.filters)
    sql = f"SELECT COUNT(*) AS value FROM {table} WHERE {where.sql}"
    return _query(sql, where.params, "count")


def compile_sample_query(
    plan: ExecutableQueryPlan,
    table_name: str = "attendance_records",
    limit: int = 100,
) -> CompiledPostgresQuery:
    plan = _require_executable(plan)
    table = _require_table_name(table_name)
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("Sample limit must be a positive integer.")
    where = compile_where(plan.filters)
    order_field = plan.order_by if plan.order_by in POSTGRES_FIELD_MAP and plan.order_by != "chunk_type" else "Date"
    order_expression = POSTGRES_FIELD_MAP[order_field]
    direction = "DESC" if plan.order_direction == "desc" else "ASC"
    sql = f"""SELECT record_id, employee_id, name, attendance_date,
department, work_location, position, shift, status, exception,
total_worked_hrs, lateness_hrs, early_out_hrs, total_ot, leave_type,
leave_hrs, source_file, search_text, record_json
FROM {table} WHERE {where.sql}
ORDER BY {order_expression} {direction}, employee_id ASC, record_id ASC LIMIT %s"""
    return _query(sql, (*where.params, limit), "sample")


def _aggregation_expression(plan: ExecutableQueryPlan) -> tuple[str, str | None]:
    if plan.aggregation == "count":
        return "COUNT(*)", None
    if plan.aggregation == "distinct_count":
        expression = POSTGRES_FIELD_MAP.get(plan.aggregation_field or "")
        if expression is None or not FIELD_DEFINITIONS[plan.aggregation_field].aggregatable:
            raise ValueError("distinct_count requires an aggregatable field.")
        return f"COUNT(DISTINCT {expression})", plan.aggregation_field
    if plan.aggregation in {"sum", "average", "min", "max"}:
        field = plan.aggregation_field or ""
        definition = FIELD_DEFINITIONS.get(field)
        expression = POSTGRES_FIELD_MAP.get(field)
        if definition is None or expression is None or definition.storage_type != "number":
            raise ValueError(f"{plan.aggregation} requires a numeric field.")
        function = {"sum": "SUM", "average": "AVG", "min": "MIN", "max": "MAX"}[plan.aggregation]
        return f"{function}({expression})", field
    raise ValueError(f"Unsupported aggregation {plan.aggregation!r}.")


def compile_aggregation_queries(
    plan: ExecutableQueryPlan, table_name: str = "attendance_records"
) -> tuple[CompiledPostgresQuery, ...]:
    plan = _require_executable(plan)
    table = _require_table_name(table_name)
    if plan.aggregation == "none":
        return ()
    where = compile_where(plan.filters)
    if plan.aggregation == "percentage":
        if plan.percentage_condition is None:
            raise ValueError("Percentage requires a numerator condition.")
        if plan.aggregation_field is None:
            expression = "COUNT(*)"
        else:
            field_expression = POSTGRES_FIELD_MAP.get(plan.aggregation_field)
            if field_expression is None:
                raise ValueError("Percentage requires a supported identity field.")
            expression = f"COUNT(DISTINCT {field_expression})"
        numerator = compile_where([*plan.filters, plan.percentage_condition])
        return (
            _query(f"SELECT {expression} AS value FROM {table} WHERE {where.sql}", where.params, "aggregation"),
            _query(f"SELECT {expression} AS value FROM {table} WHERE {numerator.sql}", numerator.params, "aggregation"),
        )
    expression, _field = _aggregation_expression(plan)
    group_by = list(plan.group_by)
    if "Name" in group_by and "Employee_ID" not in group_by:
        group_by.insert(0, "Employee_ID")
    if group_by:
        if any(field not in POSTGRES_FIELD_MAP or not FIELD_DEFINITIONS[field].groupable for field in group_by):
            raise ValueError("Unsupported grouping field.")
        columns = [POSTGRES_FIELD_MAP[field] for field in group_by]
        groups = ", ".join(f"{column} AS group_{index}" for index, column in enumerate(columns))
        sql = f"SELECT {groups}, {expression} AS value FROM {table} WHERE {where.sql} GROUP BY {', '.join(columns)}"
    else:
        sql = f"SELECT {expression} AS value FROM {table} WHERE {where.sql}"
    return (_query(sql, where.params, "aggregation"),)


def compile_coverage_query(
    table_name: str = "attendance_records",
) -> CompiledPostgresQuery:
    table = _require_table_name(table_name)
    return _query(
        f"SELECT MIN(attendance_date) AS date_min, MAX(attendance_date) AS date_max FROM {table}",
        (),
        "coverage",
    )
