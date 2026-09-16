"""Pure compilation of verified plans into parameterized PostgreSQL artifacts."""

from collections.abc import Collection
from dataclasses import dataclass
import hashlib
import re
from typing import Literal

try:
    from .attendance_schema import (
        FIELD_DEFINITIONS,
        POSTGRES_FIELD_MAP,
        RESULT_INTENT_DEFINITIONS,
        ExecutableQueryPlan,
        FilterCondition,
        canonicalize_storage_value,
        effective_grouping_fields,
    )
except ImportError:  # Direct execution from week5/new_implementation.
    from attendance_schema import (
        FIELD_DEFINITIONS,
        POSTGRES_FIELD_MAP,
        RESULT_INTENT_DEFINITIONS,
        ExecutableQueryPlan,
        FilterCondition,
        canonicalize_storage_value,
        effective_grouping_fields,
    )


@dataclass(frozen=True)
class SqlFragment:
    sql: str
    params: tuple[object, ...]


@dataclass(frozen=True)
class CompiledPostgresQuery:
    sql: str
    params: tuple[object, ...]
    purpose: Literal["sample", "count", "aggregation", "coverage", "profile"]
    fingerprint: str


GeneratedAggregateOperation = Literal[
    "count", "distinct_count", "sum", "average", "min", "max"
]


@dataclass(frozen=True)
class GeneratedAggregateChoice:
    operation: GeneratedAggregateOperation
    field: str | None


_GENERATED_AGGREGATE_PATTERN = re.compile(
    r"""
    \A\s*SELECT\s+
    (?:
        (?P<count_all>COUNT\s*\(\s*\*\s*\))
        |
        (?P<function>COUNT|SUM|AVG|MIN|MAX)\s*\(\s*
        (?:(?P<distinct>DISTINCT)\s+)?
        (?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*\)
    )
    \s+AS\s+value\s+FROM\s+attendance_scope\s*\Z
    """,
    re.IGNORECASE | re.VERBOSE,
)


def validate_generated_aggregate_sql(
    sql: str,
    *,
    candidate_fields: Collection[str],
    expected_operation: GeneratedAggregateOperation,
) -> GeneratedAggregateChoice:
    """Reduce one provider-authored logical SELECT to a trusted registry choice."""
    if not isinstance(sql, str):
        raise TypeError("Generated aggregate SQL must be a string.")
    supported_operations = {
        "count",
        "distinct_count",
        "sum",
        "average",
        "min",
        "max",
    }
    if expected_operation not in supported_operations:
        raise ValueError("Unsupported expected aggregate operation.")
    if isinstance(candidate_fields, (str, bytes)) or any(
        not isinstance(field, str) for field in candidate_fields
    ):
        raise TypeError("Candidate fields must be a collection of field names.")

    match = _GENERATED_AGGREGATE_PATTERN.fullmatch(sql)
    if match is None:
        raise ValueError("Generated SQL is not an allowed scalar aggregate statement.")

    if match.group("count_all") is not None:
        choice = GeneratedAggregateChoice("count", None)
    else:
        function = match.group("function").upper()
        distinct = match.group("distinct") is not None
        if function == "COUNT":
            if not distinct:
                raise ValueError("COUNT over a field must use DISTINCT.")
            operation: GeneratedAggregateOperation = "distinct_count"
        else:
            if distinct:
                raise ValueError("DISTINCT is supported only with COUNT.")
            operation = {
                "SUM": "sum",
                "AVG": "average",
                "MIN": "min",
                "MAX": "max",
            }[function]

        requested_field = match.group("field")
        registry_fields = {
            registry_field.casefold(): registry_field
            for registry_field in FIELD_DEFINITIONS
        }
        field = registry_fields.get(requested_field.casefold())
        candidate_field_keys = {candidate.casefold() for candidate in candidate_fields}
        if field is None or field.casefold() not in candidate_field_keys:
            raise ValueError(
                "Generated SQL field is not a request-local registry field."
            )
        definition = FIELD_DEFINITIONS[field]
        if operation == "distinct_count" and not definition.aggregatable:
            raise ValueError("distinct_count requires an aggregatable field.")
        if operation in {"sum", "average", "min", "max"} and (
            definition.storage_type != "number"
        ):
            raise ValueError(f"{operation} requires a numeric field.")
        choice = GeneratedAggregateChoice(operation, field)

    if choice.operation != expected_operation:
        raise ValueError(
            "Generated SQL operation does not match the grounded operation."
        )
    return choice


def _fingerprint(sql: str) -> str:
    normalized = " ".join(sql.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _query(sql, params, purpose):
    return CompiledPostgresQuery(sql, tuple(params), purpose, _fingerprint(sql))


def _require_table_name(table_name: str) -> str:
    if (
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", table_name)
        is None
    ):
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
            raise ValueError(
                "The attendance table contains attendance_record rows only."
            )
        if condition.operator == "in":
            if not isinstance(condition.value, list) or not condition.value:
                raise ValueError("Operator 'in' requires a non-empty list.")
            clauses.append(
                f"{expression} = ANY(%s::{array_types[definition.storage_type]})"
            )
            params.append(
                [
                    canonicalize_storage_value(condition.field, value)
                    for value in condition.value
                ]
            )
        elif isinstance(condition.value, list):
            raise ValueError(f"Operator {condition.operator!r} requires one value.")
        elif condition.operator in operators:
            cast = "::date" if definition.storage_type == "date" else ""
            clauses.append(f"{expression} {operators[condition.operator]} %s{cast}")
            params.append(canonicalize_storage_value(condition.field, condition.value))
        elif condition.operator in {"contains", "starts_with"}:
            text_expression = (
                expression
                if definition.storage_type == "text"
                else f"CAST({expression} AS TEXT)"
            )
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


def compile_chunk_where(filters, *, domain: str = "attendance") -> SqlFragment:
    """Compile metadata constraints inside the trusted attendance domain."""
    if domain != "attendance":
        raise ValueError("Semantic retrieval supports the attendance domain only.")
    clauses = ["metadata ->> %s = %s"]
    params: list[object] = ["domain", domain]
    operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    casts = {
        "number": "double precision",
        "date": "date",
        "time": "time",
        "datetime": "timestamp",
        "text": "text",
    }
    for condition in filters:
        if not isinstance(condition, FilterCondition):
            raise TypeError("Filters must be executable FilterCondition objects.")
        definition = FIELD_DEFINITIONS.get(condition.field)
        if definition is None or condition.operator not in definition.operators:
            raise ValueError("Unsupported semantic metadata field or operator.")
        cast = casts[definition.storage_type]
        expression = "metadata ->> %s"
        if cast != "text":
            expression = f"({expression})::{cast}"
        if condition.operator == "in":
            if not isinstance(condition.value, list) or not condition.value:
                raise ValueError("Operator 'in' requires a non-empty list.")
            value = [
                canonicalize_storage_value(condition.field, item)
                for item in condition.value
            ]
            clauses.append(f"{expression} = ANY(%s::{cast}[])")
        else:
            if isinstance(condition.value, list):
                raise ValueError("Scalar operators require one value.")
            value = canonicalize_storage_value(condition.field, condition.value)
            if condition.operator in operators:
                clauses.append(f"{expression} {operators[condition.operator]} %s")
            else:
                clauses.append(f"{expression} ILIKE %s")
                value = (
                    f"%{value}%" if condition.operator == "contains" else f"{value}%"
                )
        params.extend((condition.field, value))
    return SqlFragment(" AND ".join(clauses), tuple(params))


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
    order_field = (
        plan.order_by
        if plan.order_by in POSTGRES_FIELD_MAP and plan.order_by != "chunk_type"
        else "Date"
    )
    order_expression = POSTGRES_FIELD_MAP[order_field]
    direction = "DESC" if plan.order_direction == "desc" else "ASC"
    sql = f"""SELECT record_id, employee_id, name, attendance_date,
department, work_location, position, shift, status, exception,
total_worked_hrs, lateness_hrs, early_out_hrs, total_ot, leave_type,
leave_hrs, source_file, search_text, record_json
FROM {table} WHERE {where.sql}
ORDER BY {order_expression} {direction}, employee_id ASC, record_id ASC LIMIT %s"""
    return _query(sql, (*where.params, limit), "sample")


def compile_profile_query(
    plan: ExecutableQueryPlan,
    table_name: str = "attendance_records",
) -> CompiledPostgresQuery:
    plan = _require_executable(plan)
    if plan.result_intent != "employee_profile":
        raise ValueError("Profile compilation requires employee_profile intent.")
    definition = RESULT_INTENT_DEFINITIONS["employee_profile"]
    if tuple(plan.projection) != definition.projection:
        raise ValueError("Profile projection does not match the registered fields.")
    table = _require_table_name(table_name)
    where = compile_where(plan.filters)
    columns = ", ".join(
        f'{POSTGRES_FIELD_MAP[field]} AS "{field}"' for field in definition.projection
    )
    order = ", ".join(POSTGRES_FIELD_MAP[field] for field in definition.projection)
    return _query(
        f"SELECT DISTINCT {columns} FROM {table} WHERE {where.sql} ORDER BY {order}",
        where.params,
        "profile",
    )


def _aggregation_expression(plan: ExecutableQueryPlan) -> tuple[str, str | None]:
    if plan.aggregation == "count":
        return "COUNT(*)", None
    if plan.aggregation == "distinct_count":
        expression = POSTGRES_FIELD_MAP.get(plan.aggregation_field or "")
        if (
            expression is None
            or not FIELD_DEFINITIONS[plan.aggregation_field].aggregatable
        ):
            raise ValueError("distinct_count requires an aggregatable field.")
        return f"COUNT(DISTINCT {expression})", plan.aggregation_field
    if plan.aggregation in {"sum", "average", "min", "max"}:
        field = plan.aggregation_field or ""
        definition = FIELD_DEFINITIONS.get(field)
        expression = POSTGRES_FIELD_MAP.get(field)
        if (
            definition is None
            or expression is None
            or definition.storage_type != "number"
        ):
            raise ValueError(f"{plan.aggregation} requires a numeric field.")
        function = {"sum": "SUM", "average": "AVG", "min": "MIN", "max": "MAX"}[
            plan.aggregation
        ]
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
            _query(
                f"SELECT {expression} AS value FROM {table} WHERE {where.sql}",
                where.params,
                "aggregation",
            ),
            _query(
                f"SELECT {expression} AS value FROM {table} WHERE {numerator.sql}",
                numerator.params,
                "aggregation",
            ),
        )
    expression, _field = _aggregation_expression(plan)
    group_by = effective_grouping_fields(plan.group_by)
    if group_by:
        if any(
            field not in POSTGRES_FIELD_MAP or not FIELD_DEFINITIONS[field].groupable
            for field in group_by
        ):
            raise ValueError("Unsupported grouping field.")
        columns = [POSTGRES_FIELD_MAP[field] for field in group_by]
        groups = ", ".join(
            f"{column} AS group_{index}" for index, column in enumerate(columns)
        )
        sql = f"SELECT {groups}, {expression} AS value FROM {table} WHERE {where.sql} GROUP BY {', '.join(columns)}"
    else:
        sql = f"SELECT {expression} AS value FROM {table} WHERE {where.sql}"
    return (_query(sql, where.params, "aggregation"),)


def compile_generated_aggregate_query(
    choice: GeneratedAggregateChoice,
    plan: ExecutableQueryPlan,
    table_name: str = "attendance_records",
) -> CompiledPostgresQuery:
    """Compile a validated logical choice through the trusted plan compiler."""
    if type(choice) is not GeneratedAggregateChoice:
        raise TypeError(
            "Generated aggregation compilation requires a validated choice."
        )
    plan = _require_executable(plan)
    expected_subject = choice.field
    expected_grain = [choice.field] if choice.field is not None else []
    if (
        plan.answer_contract.shape != "scalar"
        or plan.answer_contract.subject_field != expected_subject
        or plan.answer_contract.grain != expected_grain
        or plan.group_by
        or plan.projection
    ):
        raise ValueError("Generated aggregation compilation requires a scalar plan.")
    if choice.operation != plan.aggregation or choice.field != plan.aggregation_field:
        raise ValueError(
            "Generated aggregate choice does not match the executable plan."
        )

    queries = compile_aggregation_queries(plan, table_name=table_name)
    if len(queries) != 1 or queries[0].purpose != "aggregation":
        raise ValueError("Generated aggregation must compile to exactly one query.")
    return queries[0]


def compile_multi_employee_date_query(
    plan: ExecutableQueryPlan,
    *,
    execution_group_limit: int,
    table_name: str = "attendance_records",
) -> CompiledPostgresQuery:
    """Compile a bounded grouped date primitive for a verified composite view."""
    plan = _require_executable(plan)
    if not isinstance(execution_group_limit, int) or isinstance(
        execution_group_limit, bool
    ):
        raise ValueError("Execution group limit must be a positive integer.")
    if execution_group_limit < 1:
        raise ValueError("Execution group limit must be a positive integer.")
    group_by = effective_grouping_fields(plan.group_by)
    if (
        plan.aggregation != "distinct_count"
        or plan.aggregation_field not in {"Date", "Employee_ID"}
        or not group_by
    ):
        raise ValueError(
            "Multi-employee date compilation requires a grouped distinct count."
        )
    if any(field not in {"Date", "Employee_ID"} for field in group_by):
        raise ValueError(
            "Multi-employee date grouping supports Date and Employee_ID only."
        )
    table = _require_table_name(table_name)
    where = compile_where(plan.filters)
    expression = POSTGRES_FIELD_MAP[plan.aggregation_field]
    columns = [POSTGRES_FIELD_MAP[field] for field in group_by]
    groups = ", ".join(
        f"{column} AS group_{index}" for index, column in enumerate(columns)
    )
    sql = (
        f"SELECT {groups}, COUNT(DISTINCT {expression}) AS value FROM {table} "
        f"WHERE {where.sql} GROUP BY {', '.join(columns)} "
        "ORDER BY " + ", ".join(columns) + " LIMIT %s"
    )
    return _query(sql, (*where.params, execution_group_limit + 1), "aggregation")


def compile_coverage_query(
    table_name: str = "attendance_records",
) -> CompiledPostgresQuery:
    table = _require_table_name(table_name)
    return _query(
        f"SELECT MIN(attendance_date) AS date_min, MAX(attendance_date) AS date_max FROM {table}",
        (),
        "coverage",
    )
