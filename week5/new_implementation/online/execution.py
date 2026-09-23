"""Authorized PostgreSQL execution for one flat attendance source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .catalog import ATTENDANCE_CATALOG, AttendanceCatalog
from .query import (
    AggregateOutput,
    All,
    Any,
    AttendanceQuery,
    BooleanExpr,
    Condition,
    Not,
    Predicate,
    QueryLimits,
    RowsOutput,
    validate_query,
)


@dataclass(frozen=True)
class AttendanceRowScope:
    kind: Literal["all", "employee_ids"]
    employee_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind == "all" and self.employee_ids:
            raise ValueError("all scope cannot list employee IDs")
        if self.kind == "employee_ids" and not self.employee_ids:
            raise ValueError("employee scope requires employee IDs")
        if len(self.employee_ids) != len(set(self.employee_ids)):
            raise ValueError("scope employee IDs must be unique")


@dataclass(frozen=True)
class AccessContext:
    principal_id: str
    domain: str
    allowed_domains: frozenset[str]
    attendance_scope: AttendanceRowScope | None = None


LOCAL_DEMO_ACCESS = AccessContext(
    principal_id="local-demo",
    domain="attendance",
    allowed_domains=frozenset({"attendance"}),
    attendance_scope=AttendanceRowScope("all"),
)


class AuthorizationError(PermissionError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BoundAttendanceQuery(_Strict):
    query: AttendanceQuery
    employee_ids: tuple[str, ...]
    principal_id: str


class CompiledQuery(_Strict):
    sql: str
    params: tuple[object, ...]
    columns: tuple[str, ...]
    purpose: Literal["main", "coverage", "witness"]


class Coverage(_Strict):
    requested_start: date | None = None
    requested_end: date | None = None
    available_start: date | None = None
    available_end: date | None = None
    complete: bool


class ExecutionResult(_Strict):
    rows: tuple[dict[str, object], ...]
    coverage: Coverage | None = None
    witnesses: tuple[dict[str, object], ...] = ()


def bind_query(
    query: AttendanceQuery,
    employee_ids: tuple[str, ...],
    access: AccessContext | None,
    *,
    catalog: AttendanceCatalog = ATTENDANCE_CATALOG,
    limits: QueryLimits = QueryLimits(),
) -> BoundAttendanceQuery:
    if access is None or access.domain != "attendance" or "attendance" not in access.allowed_domains:
        raise AuthorizationError("attendance access is required")
    scope = access.attendance_scope
    if scope is None:
        raise AuthorizationError("attendance row scope is required")
    issues = validate_query(query, catalog, limits)
    if issues:
        raise ValueError("; ".join(f"{item.code}:{item.path}" for item in issues))
    requested = tuple(dict.fromkeys(employee_ids))
    if scope.kind == "employee_ids":
        allowed = set(scope.employee_ids)
        if requested and not set(requested) <= allowed:
            raise AuthorizationError("requested employee is outside the authorized row scope")
        requested = requested or scope.employee_ids
    return BoundAttendanceQuery(query=query, employee_ids=requested, principal_id=access.principal_id)


def _identifier(value: str) -> str:
    part = r"[A-Za-z_][A-Za-z0-9_]*"
    if not re.fullmatch(rf"{part}(?:\.{part})?", value):
        raise ValueError("unsafe attendance table identifier")
    return ".".join(f'"{item}"' for item in value.split("."))


def _compile_condition(condition: Condition, catalog: AttendanceCatalog, params: list[object], *, outputs: dict[str, str] | None = None) -> str:
    if outputs is not None and condition.field_id in outputs:
        expression = outputs[condition.field_id]
        scalar_type = "number"
    else:
        expression = catalog.physical_expression(condition.field_id)
        scalar_type = catalog.fields[condition.field_id].scalar_type
    operator = condition.operator
    if operator == "in":
        values = tuple(catalog.canonicalize_value(condition.field_id, item) if outputs is None else item for item in condition.value)  # type: ignore[union-attr]
        params.append(list(values))
        return f"{expression} = ANY(%s)"
    value = condition.value
    canonical = catalog.canonicalize_value(condition.field_id, value) if outputs is None else value
    params.append(canonical)
    sql_operator = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}.get(operator)
    if sql_operator:
        return f"{expression} {sql_operator} %s"
    if scalar_type != "text":
        raise ValueError(f"{operator} requires a string field")
    params[-1] = f"%{canonical}%" if operator == "contains" else f"{canonical}%"
    return f"{expression} ILIKE %s ESCAPE '\\'"


def _compile_expr(expression: BooleanExpr, catalog: AttendanceCatalog, params: list[object], *, outputs: dict[str, str] | None = None) -> str:
    if isinstance(expression, Condition):
        return _compile_condition(expression, catalog, params, outputs=outputs)
    if isinstance(expression, Predicate):
        predicate = catalog.predicates[expression.predicate_id]
        parts = [
            _compile_condition(Condition(field_id=field, operator=operator, value=value), catalog, params)
            for field, operator, value in predicate.filters
        ]
        return "(" + " AND ".join(parts) + ")"
    if isinstance(expression, Not):
        return f"(NOT {_compile_expr(expression.item, catalog, params, outputs=outputs)})"
    joiner = " AND " if isinstance(expression, All) else " OR "
    return "(" + joiner.join(_compile_expr(item, catalog, params, outputs=outputs) for item in expression.items) + ")"


def _where(bound: BoundAttendanceQuery, catalog: AttendanceCatalog, params: list[object]) -> str:
    parts: list[str] = []
    if bound.employee_ids:
        params.append(list(bound.employee_ids))
        parts.append(f"{catalog.physical_expression('employee_id')} = ANY(%s)")
    parts.extend(_compile_expr(item, catalog, params) for item in bound.query.filters)
    return " WHERE " + " AND ".join(parts) if parts else ""


def _measure_sql(function: str, field_id: str | None, catalog: AttendanceCatalog) -> str:
    if function == "count":
        return "COUNT(*)"
    expression = catalog.physical_expression(field_id or "")
    if function == "distinct_count":
        return f"COUNT(DISTINCT {expression})"
    sql_function = {"sum": "SUM", "average": "AVG", "min": "MIN", "max": "MAX"}[function]
    return f"{sql_function}({expression})"


def compile_main(
    bound: BoundAttendanceQuery,
    *,
    table: str,
    catalog: AttendanceCatalog = ATTENDANCE_CATALOG,
) -> CompiledQuery:
    source = _identifier(table)
    params: list[object] = []
    where = _where(bound, catalog, params)
    output = bound.query.output
    if isinstance(output, RowsOutput):
        selected = [f'{catalog.physical_expression(item)} AS "{item}"' for item in output.fields]
        ordering = ", ".join(
            f'"{item.output_id}" {item.direction.upper()}' for item in output.ordering
        )
        params.append(output.limit)
        sql = f"SELECT {', '.join(selected)} FROM {source}{where} ORDER BY {ordering} LIMIT %s"
        return CompiledQuery(sql=sql, params=tuple(params), columns=output.fields, purpose="main")
    group_select = [f'{catalog.physical_expression(item)} AS "{item}"' for item in output.group_by]
    measure_select = [f'{_measure_sql(item.function, item.field_id, catalog)} AS "{item.output_id}"' for item in output.measures]
    group_sql = ", ".join(catalog.physical_expression(item) for item in output.group_by)
    inner = f"SELECT {', '.join(group_select + measure_select)} FROM {source}{where}"
    if group_sql:
        inner += f" GROUP BY {group_sql}"
    outer_conditions: list[str] = []
    if output.having is not None:
        aliases = {item: f'"{item}"' for item in output.group_by}
        aliases.update({item.output_id: f'"{item.output_id}"' for item in output.measures})
        outer_conditions.append(_compile_expr(output.having, catalog, params, outputs=aliases))
    sql = f"SELECT * FROM ({inner}) AS grouped"
    if outer_conditions:
        sql += " WHERE " + " AND ".join(outer_conditions)
    if output.ordering:
        sql += " ORDER BY " + ", ".join(f'"{item.output_id}" {item.direction.upper()}' for item in output.ordering)
    if output.limit is not None:
        params.append(output.limit)
        sql += " LIMIT %s"
    columns = output.group_by + tuple(item.output_id for item in output.measures)
    return CompiledQuery(sql=sql, params=tuple(params), columns=columns, purpose="main")


def compile_coverage(bound: BoundAttendanceQuery, *, table: str, catalog: AttendanceCatalog = ATTENDANCE_CATALOG) -> CompiledQuery:
    params: list[object] = []
    where = _where(bound, catalog, params)
    date_expression = catalog.physical_expression("date")
    return CompiledQuery(
        sql=f"SELECT MIN({date_expression}) AS available_start, MAX({date_expression}) AS available_end FROM {_identifier(table)}{where}",
        params=tuple(params),
        columns=("available_start", "available_end"),
        purpose="coverage",
    )


def compile_witness(bound: BoundAttendanceQuery, *, table: str, limit: int = 25, catalog: AttendanceCatalog = ATTENDANCE_CATALOG) -> CompiledQuery:
    params: list[object] = []
    where = _where(bound, catalog, params)
    params.append(limit)
    return CompiledQuery(
        sql=f'SELECT {catalog.physical_expression("employee_id")} AS "employee_id", {catalog.physical_expression("date")} AS "date" FROM {_identifier(table)}{where} ORDER BY "date" ASC LIMIT %s',
        params=tuple(params),
        columns=("employee_id", "date"),
        purpose="witness",
    )


def _requested_dates(expression: BooleanExpr, catalog: AttendanceCatalog, bounds: list[date]) -> None:
    if isinstance(expression, Condition) and expression.field_id == "date":
        value = catalog.canonicalize_value("date", expression.value)
        try:
            bounds.append(date.fromisoformat(str(value)))
        except ValueError:
            return
    elif isinstance(expression, Not):
        _requested_dates(expression.item, catalog, bounds)
    elif isinstance(expression, (All, Any)):
        for item in expression.items:
            _requested_dates(item, catalog, bounds)


def execute_postgres(
    bound: BoundAttendanceQuery,
    *,
    dsn: str,
    table: str,
    connect_timeout: int = 5,
    statement_timeout_ms: int = 30000,
    lock_timeout_ms: int = 3000,
    idle_timeout_ms: int = 30000,
    result_limit: int = 1000,
) -> ExecutionResult:
    import psycopg
    from psycopg.rows import dict_row

    main = compile_main(bound, table=table)
    coverage_query = compile_coverage(bound, table=table)
    witness_query = compile_witness(bound, table=table)
    with psycopg.connect(dsn, connect_timeout=connect_timeout, row_factory=dict_row) as connection:
        connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL statement_timeout = %s", (statement_timeout_ms,))
        connection.execute("SET LOCAL lock_timeout = %s", (lock_timeout_ms,))
        connection.execute("SET LOCAL idle_in_transaction_session_timeout = %s", (idle_timeout_ms,))
        rows = tuple(dict(item) for item in connection.execute(main.sql, main.params).fetchmany(result_limit + 1))
        if len(rows) > result_limit:
            raise RuntimeError("result bound exceeded")
        coverage_row = connection.execute(coverage_query.sql, coverage_query.params).fetchone() or {}
        witnesses = tuple(dict(item) for item in connection.execute(witness_query.sql, witness_query.params).fetchall())
        connection.rollback()
    requested: list[date] = []
    for expression in bound.query.filters:
        _requested_dates(expression, ATTENDANCE_CATALOG, requested)
    available_start = coverage_row.get("available_start")
    available_end = coverage_row.get("available_end")
    requested_start = min(requested) if requested else None
    requested_end = max(requested) if requested else None
    complete = bool(
        available_start
        and available_end
        and (requested_start is None or available_start <= requested_start)
        and (requested_end is None or available_end >= requested_end)
    )
    coverage = Coverage(
        requested_start=requested_start,
        requested_end=requested_end,
        available_start=available_start,
        available_end=available_end,
        complete=complete,
    )
    return ExecutionResult(rows=rows, coverage=coverage, witnesses=witnesses)


def load_employee_directory(*, dsn: str, table: str, connect_timeout: int = 5):
    """Load the authoritative identity pairs without exposing physical names upstream."""

    import psycopg
    from psycopg.rows import dict_row

    from .reference import Employee

    employee = ATTENDANCE_CATALOG.physical_expression("employee_id")
    name = ATTENDANCE_CATALOG.physical_expression("employee_name")
    sql = (
        f'SELECT DISTINCT {employee} AS "employee_id", {name} AS "name" '
        f"FROM {_identifier(table)} WHERE {employee} IS NOT NULL AND {name} IS NOT NULL "
        f'ORDER BY "employee_id"'
    )
    with psycopg.connect(dsn, connect_timeout=connect_timeout, row_factory=dict_row) as connection:
        connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = connection.execute(sql).fetchall()
        connection.rollback()
    return tuple(Employee(employee_id=str(row["employee_id"]), name=str(row["name"])) for row in rows)


def retrieve_narrative_postgres(
    search_text: str,
    *,
    employee_ids: tuple[str, ...],
    access: AccessContext,
    dsn: str,
    chunks_table: str,
    embedding_model: str,
    limit: int = 8,
):
    """Retrieve narrative evidence only when identical row scope is enforceable."""

    from openai import OpenAI
    import psycopg
    from psycopg.rows import dict_row

    from .answering import Result

    scope = access.attendance_scope
    if scope is None:
        raise AuthorizationError("narrative access requires attendance row scope")
    selected = employee_ids
    if scope.kind == "employee_ids":
        if selected and not set(selected) <= set(scope.employee_ids):
            raise AuthorizationError("narrative employee is outside the authorized scope")
        selected = selected or scope.employee_ids
    if not selected:
        raise AuthorizationError("narrative retrieval requires an explicit enforceable employee scope")
    vector = OpenAI().embeddings.create(model=embedding_model, input=[search_text], timeout=30).data[0].embedding
    vector_literal = "[" + ",".join(str(value) for value in vector) + "]"
    sql = (
        f"SELECT content, metadata FROM {_identifier(chunks_table)} "
        "WHERE metadata->>'domain' = %s AND metadata->>'Employee_ID' = ANY(%s) "
        "ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = connection.execute(sql, ("attendance", list(selected), vector_literal, limit)).fetchall()
        connection.rollback()
    return tuple(Result(page_content=row["content"], metadata=row["metadata"] or {}) for row in rows)


__all__ = [
    "AccessContext",
    "AttendanceRowScope",
    "AuthorizationError",
    "BoundAttendanceQuery",
    "CompiledQuery",
    "Coverage",
    "ExecutionResult",
    "LOCAL_DEMO_ACCESS",
    "bind_query",
    "compile_coverage",
    "compile_main",
    "compile_witness",
    "execute_postgres",
    "load_employee_directory",
    "retrieve_narrative_postgres",
]
