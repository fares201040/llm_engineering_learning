"""Bounded direct execution of model-produced PostgreSQL SQL."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from difflib import SequenceMatcher
import json
import re
from typing import TYPE_CHECKING, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from .limits import MAX_EMPLOYEE_CANDIDATES

if TYPE_CHECKING:
    from psycopg import Connection
    from .reference import Employee, EmployeeOption


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


def _relation_names_for_select(
    select: exp.Select, table_names: frozenset[str]
) -> frozenset[str]:
    sources: list[exp.Expression] = []
    from_clause = select.args.get("from_")
    if isinstance(from_clause, exp.From) and from_clause.this is not None:
        sources.append(from_clause.this)
    sources.extend(join.this for join in select.args.get("joins") or () if join.this)
    return frozenset(
        source.alias_or_name.casefold()
        for source in sources
        if isinstance(source, exp.Table) and source.name.casefold() in table_names
    )


def _projects_private_source(
    item: exp.Expression, *, relation_names: frozenset[str] = frozenset()
) -> bool:
    """Detect direct source payload output while allowing documented JSON fields."""

    projection = item.this if isinstance(item, exp.Alias) else item
    if isinstance(projection, exp.Star) and relation_names:
        return True
    if (
        isinstance(projection, exp.Column)
        and isinstance(projection.this, exp.Star)
        and projection.table.casefold() in relation_names
    ):
        return True
    if not isinstance(projection, (exp.Star, exp.Column)) and any(
        isinstance(node, exp.Star)
        and node.find_ancestor(exp.Count) is None
        for node in projection.find_all(exp.Star)
    ):
        return True
    for column in projection.find_all(exp.Column):
        name = column.name.casefold()
        if not column.table and name in relation_names:
            return True
        if name == "raw_row_key":
            return True
        if name != "record_json":
            continue
        parent = column.parent
        while parent is not None and parent is not projection.parent:
            if isinstance(parent, (exp.JSONExtract, exp.JSONExtractScalar)):
                break
            parent = parent.parent
        else:
            return True
    return False


def validate_read_query(
    sql: str, *, allowed_tables: tuple[str, ...] | None = None
) -> None:
    """Accept one read query over the exposed tables; the DB role remains read only."""

    try:
        statements = parse(sql, read="postgres")
    except ParseError as exc:
        raise ValueError("SQL is not valid PostgreSQL") from exc
    if len(statements) != 1 or not isinstance(
        statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
    ):
        raise ValueError("SQL must be one SELECT query")
    statement = statements[0]
    if any(
        isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop))
        for node in statement.walk()
    ) or any(
        select.args.get("into") or select.args.get("locks")
        for select in statement.find_all(exp.Select)
    ):
        raise ValueError("SQL must be a read-only SELECT query")
    if allowed_tables is None:
        return
    allowed = {table.casefold() for table in allowed_tables}
    allowed_unqualified = {table.rsplit(".", 1)[-1] for table in allowed}
    # Full source payloads and ingestion keys are not part of the attendance
    # question interface. JSON field extraction remains available for valid
    # attendance measures that are not represented by typed columns.
    for select in statement.find_all(exp.Select):
        relation_names = _relation_names_for_select(select, frozenset(allowed_unqualified))
        for item in select.expressions:
            if _projects_private_source(item, relation_names=relation_names):
                raise ValueError("SQL projects a private source payload")
    cte_names = {
        cte.alias.casefold() for cte in statement.find_all(exp.CTE) if cte.alias
    }
    for table in statement.find_all(exp.Table):
        name = table.name.casefold()
        if not table.db and name in cte_names:
            continue
        available = (
            f"{table.db}.{name}".casefold() in allowed
            if table.db
            else name in allowed_unqualified
        )
        if not available:
            raise ValueError(f"SQL references an unavailable table: {table.sql()}")


def _scoped_sql(
    sql: str, *, employee_ids: tuple[str, ...], allowed_tables: tuple[str, ...]
) -> str:
    """Restrict each exposed base table before any model-selected aggregation or join."""

    statement = parse(sql, read="postgres")[0]
    cte_names = {
        cte.alias.casefold() for cte in statement.find_all(exp.CTE) if cte.alias
    }
    for table in list(statement.find_all(exp.Table)):
        if not table.db and table.name.casefold() in cte_names:
            continue
        alias = table.alias or table.name
        restricted = (
            exp.select("*")
            .from_(table.copy())
            .where(
                exp.column("employee_id").isin(
                    *(exp.Literal.string(item) for item in employee_ids)
                )
            )
            .subquery(alias=alias)
        )
        table.replace(restricted)
    return statement.sql(dialect="postgres")


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
    allowed_tables: tuple[str, ...] | None = None,
    scope_employee_ids: tuple[str, ...] | None = None,
    connection: Connection | None = None,
) -> SqlExecutionResult:
    """Execute one bounded read query with any authorized row scope."""

    if not sql.strip():
        raise ValueError("SQL must not be empty")
    validate_read_query(sql, allowed_tables=allowed_tables)
    if scope_employee_ids is not None:
        if not scope_employee_ids or allowed_tables is None:
            raise ValueError("employee row scope requires IDs and allowed tables")
        sql = _scoped_sql(
            sql, employee_ids=scope_employee_ids, allowed_tables=allowed_tables
        )

    def run_query(active_connection) -> SqlExecutionResult:
        cursor = active_connection.execute(sql)
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
                type_code=(str(item.type_code) if item.type_code is not None else None),
            )
            for item in description
        )
        return SqlExecutionResult(
            columns=columns,
            rows=rows,
            coverage=ExecutionCoverage(
                fetched_rows=len(rows), result_limit=result_limit,
                response_bytes=response_bytes,
            ),
        )

    if connection is not None:
        connection.execute("SAVEPOINT attendance_step")
        try:
            result = run_query(connection)
            connection.execute("RELEASE SAVEPOINT attendance_step")
            return result
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT attendance_step")
            connection.execute("RELEASE SAVEPOINT attendance_step")
            raise

    with open_read_snapshot(
        dsn=dsn, connect_timeout=connect_timeout,
        statement_timeout_ms=statement_timeout_ms,
        lock_timeout_ms=lock_timeout_ms, idle_timeout_ms=idle_timeout_ms,
    ) as single_connection:
        return run_query(single_connection)


@contextmanager
def open_read_snapshot(
    *, dsn: str, connect_timeout: int = 5,
    statement_timeout_ms: int = 30000, lock_timeout_ms: int = 3000,
    idle_timeout_ms: int = 30000,
) -> Iterator[Connection]:
    """Keep multiple read queries on one repeatable-read snapshot."""

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        dsn, connect_timeout=connect_timeout, row_factory=dict_row,
    ) as connection:
        try:
            connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
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
            yield connection
        finally:
            connection.rollback()


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


def _name_parts(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", value.casefold()))


def _name_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    score = SequenceMatcher(None, left, right).ratio()
    return score if left[0] == right[0] else score * 0.6


def rank_employee_candidates(
    mention: str,
    options: tuple[EmployeeOption, ...],
    *,
    part_threshold: float = 0.7,
    limit: int = MAX_EMPLOYEE_CANDIDATES,
) -> tuple[EmployeeOption, ...]:
    """Prefer ordered given-name matches; offer a short fallback when none exist."""

    requested = _name_parts(mention)
    if not requested or limit < 1:
        return ()
    scored: list[tuple[int, float, float, float, EmployeeOption]] = []
    for option in options:
        candidate = _name_parts(option.employee_name)
        if not candidate:
            continue
        positional = tuple(
            _name_similarity(part, candidate[index]) if index < len(candidate) else 0.0
            for index, part in enumerate(requested)
        )
        prefix_matches = 0
        for score in positional:
            if score < part_threshold:
                break
            prefix_matches += 1
        ordered_score = sum(positional) / len(requested)
        coverage_score = sum(
            max(_name_similarity(part, name_part) for name_part in candidate)
            for part in requested
        ) / len(requested)
        full_score = _name_similarity(" ".join(requested), " ".join(candidate))
        scored.append(
            (prefix_matches, ordered_score, coverage_score, full_score, option)
        )
    scored.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            -item[2],
            -item[3],
            item[4].employee_id,
        )
    )
    if not scored:
        return ()
    display_limit = min(limit, 5)
    best_prefix = scored[0][0]
    if len(requested) >= 2 and best_prefix == len(requested):
        return tuple(item[4] for item in scored if item[0] == best_prefix)[
            :display_limit
        ]
    if len(requested) >= 2 and best_prefix:
        scored = [item for item in scored if item[0] == best_prefix]
    return tuple(item[4] for item in scored[:display_limit])


def search_employee_directory_postgres(
    mention: str,
    *,
    dsn: str,
    table: str,
    allowed_employee_ids: tuple[str, ...] | None,
    directory: tuple[Employee, ...] | None = None,
    limit: int = MAX_EMPLOYEE_CANDIDATES,
    connect_timeout: int = 5,
):
    """Rank authorized directory names, loading PostgreSQL only when needed."""

    if limit < 1 or allowed_employee_ids == ():
        return ()
    from .reference import EmployeeOption

    if directory is not None:
        allowed = (
            set(allowed_employee_ids) if allowed_employee_ids is not None else None
        )
        options = tuple(
            EmployeeOption(employee_id=item.employee_id, employee_name=item.name)
            for item in directory
            if allowed is None or item.employee_id in allowed
        )
        return rank_employee_candidates(mention, options, limit=limit)

    import psycopg
    from psycopg.rows import dict_row

    if not _name_parts(mention):
        return ()
    params: list[object] = []
    scope_sql = ""
    if allowed_employee_ids is not None:
        scope_sql = ' AND "employee_id" = ANY(%s)'
        params.append(list(allowed_employee_ids))
    sql = (
        'SELECT DISTINCT "employee_id", "name" '
        f"FROM {_identifier(table)} "
        'WHERE "employee_id" IS NOT NULL AND "name" IS NOT NULL'
        f'{scope_sql} ORDER BY "employee_id"'
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
            connection.rollback()
        except Exception:
            connection.rollback()
            raise
    options = tuple(
        EmployeeOption(
            employee_id=str(row["employee_id"]),
            employee_name=str(row["name"]),
        )
        for row in rows
    )
    return rank_employee_candidates(mention, options, limit=limit)


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
    "rank_employee_candidates",
    "search_employee_directory_postgres",
]
