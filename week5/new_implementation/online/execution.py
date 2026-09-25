"""Bounded direct execution of model-produced PostgreSQL SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class ResultColumn(_Strict):
    name: str = Field(min_length=1, max_length=256)
    type_code: str | None = Field(default=None, max_length=256)


class ExecutionCoverage(_Strict):
    fetched_rows: int = Field(ge=0)
    result_limit: int = Field(ge=1)
    response_bytes: int = Field(ge=0)
    complete: bool = True


class SqlExecutionResult(_Strict):
    columns: tuple[ResultColumn, ...] = Field(default=(), max_length=256)
    rows: tuple[dict[str, object], ...]
    coverage: ExecutionCoverage


def authorize_access(access: AccessContext | None) -> AttendanceRowScope:
    if (
        access is None
        or access.domain != "attendance"
        or "attendance" not in access.allowed_domains
    ):
        raise AuthorizationError("attendance access is required")
    if access.attendance_scope is None:
        raise AuthorizationError("attendance row scope is required")
    return access.attendance_scope


def _identifier(value: str) -> str:
    part = r"[A-Za-z_][A-Za-z0-9_]*"
    if not re.fullmatch(rf"{part}(?:\.{part})?", value):
        raise ValueError("unsafe configured attendance table identifier")
    return ".".join(f'"{item}"' for item in value.split("."))


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return value
    return str(value)


def execute_sql(
    sql: str,
    *,
    dsn: str,
    connect_timeout: int = 5,
    statement_timeout_ms: int = 30000,
    lock_timeout_ms: int = 3000,
    idle_timeout_ms: int = 30000,
    result_limit: int = 1000,
    max_response_bytes: int = 1000000,
) -> SqlExecutionResult:
    """Execute the exact model SQL; PostgreSQL and the read-only role are the boundary."""

    if not sql.strip():
        raise ValueError("SQL must not be empty")

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        dsn,
        connect_timeout=connect_timeout,
        row_factory=dict_row,
    ) as connection:
        try:
            connection.execute(
                "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            connection.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(statement_timeout_ms),),
            )
            connection.execute(
                "SELECT set_config('lock_timeout', %s, true)",
                (str(lock_timeout_ms),),
            )
            connection.execute(
                "SELECT set_config('idle_in_transaction_session_timeout', %s, true)",
                (str(idle_timeout_ms),),
            )
            cursor = connection.execute(sql)
            description = cursor.description or ()
            raw_rows = cursor.fetchmany(result_limit + 1) if description else ()
            if len(raw_rows) > result_limit:
                raise RuntimeError("result row bound exceeded")
            rows = tuple(
                {str(key): _json_value(value) for key, value in dict(row).items()}
                for row in raw_rows
            )
            response_bytes = len(
                json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if response_bytes > max_response_bytes:
                raise RuntimeError("result response-size bound exceeded")
            columns = tuple(
                ResultColumn(
                    name=str(item.name),
                    type_code=(
                        str(item.type_code) if item.type_code is not None else None
                    ),
                )
                for item in description
            )
            result = SqlExecutionResult(
                columns=columns,
                rows=rows,
                coverage=ExecutionCoverage(
                    fetched_rows=len(rows),
                    result_limit=result_limit,
                    response_bytes=response_bytes,
                ),
            )
            connection.rollback()
            return result
        except Exception:
            connection.rollback()
            raise


def load_employee_directory(
    *,
    dsn: str,
    table: str,
    connect_timeout: int = 5,
):
    """Retrieve only authoritative employee ID/name pairs."""

    import psycopg
    from psycopg.rows import dict_row

    from .reference import Employee

    sql = (
        'SELECT DISTINCT "employee_id" AS "employee_id", "name" AS "name" '
        f"FROM {_identifier(table)} "
        'WHERE "employee_id" IS NOT NULL AND "name" IS NOT NULL '
        'ORDER BY "employee_id"'
    )
    with psycopg.connect(
        dsn,
        connect_timeout=connect_timeout,
        row_factory=dict_row,
    ) as connection:
        try:
            connection.execute(
                "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            rows = connection.execute(sql).fetchall()
            connection.rollback()
        except Exception:
            connection.rollback()
            raise
    return tuple(
        Employee(employee_id=str(row["employee_id"]), name=str(row["name"]))
        for row in rows
    )


def search_employee_directory_postgres(
    mention: str,
    *,
    dsn: str,
    table: str,
    allowed_employee_ids: tuple[str, ...] | None,
    threshold: float = 0.62,
    token_threshold: float = 0.3,
    limit: int = 5,
    connect_timeout: int = 5,
):
    """Return deterministic pg_trgm candidates from the authorized directory."""

    if limit < 1 or allowed_employee_ids == ():
        return ()
    import psycopg
    from psycopg.rows import dict_row

    from .reference import EmployeeOption

    params: list[object] = [mention, mention, threshold]
    scope_sql = ""
    if allowed_employee_ids is not None:
        scope_sql = ' AND "employee_id" = ANY(%s)'
        params.append(list(allowed_employee_ids))
    params.append(limit)
    sql = (
        'SELECT DISTINCT "employee_id", "name", '
        'similarity(lower("name"), lower(%s)) AS match_score '
        f"FROM {_identifier(table)} "
        'WHERE "employee_id" IS NOT NULL AND "name" IS NOT NULL '
        'AND similarity(lower("name"), lower(%s)) >= %s'
        f'{scope_sql} ORDER BY match_score DESC, "employee_id" ASC LIMIT %s'
    )
    with psycopg.connect(
        dsn,
        connect_timeout=connect_timeout,
        row_factory=dict_row,
    ) as connection:
        try:
            connection.execute(
                "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            rows = connection.execute(sql, tuple(params)).fetchall()
            if not rows:
                token_sql = (
                    'SELECT DISTINCT "employee_id", "name", '
                    "similarity(lower(split_part(\"name\", ' ', 1)), "
                    "lower(split_part(%s, ' ', 1))) AS match_score "
                    f"FROM {_identifier(table)} "
                    'WHERE "employee_id" IS NOT NULL AND "name" IS NOT NULL '
                    "AND similarity(lower(split_part(\"name\", ' ', 1)), "
                    "lower(split_part(%s, ' ', 1))) >= %s"
                    f'{scope_sql} ORDER BY match_score DESC, "employee_id" ASC LIMIT %s'
                )
                token_params: list[object] = [mention, mention, token_threshold]
                if allowed_employee_ids is not None:
                    token_params.append(list(allowed_employee_ids))
                token_params.append(limit)
                rows = connection.execute(token_sql, tuple(token_params)).fetchall()
            connection.rollback()
        except Exception:
            connection.rollback()
            raise
    return tuple(
        EmployeeOption(
            employee_id=str(row["employee_id"]),
            employee_name=str(row["name"]),
        )
        for row in rows
    )


__all__ = [
    "AccessContext",
    "AttendanceRowScope",
    "AuthorizationError",
    "ExecutionCoverage",
    "LOCAL_DEMO_ACCESS",
    "ResultColumn",
    "SqlExecutionResult",
    "authorize_access",
    "execute_sql",
    "load_employee_directory",
    "search_employee_directory_postgres",
]
