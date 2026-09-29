"""Build SQL-planner examples from the discovered database schema."""

from __future__ import annotations

import re

from .context import DatabaseColumn, DatabaseContext


_NUMERIC_TYPES = frozenset(
    {
        "bigint",
        "double precision",
        "integer",
        "numeric",
        "real",
        "smallint",
    }
)


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _typed_name(column: DatabaseColumn) -> str:
    return column.name.casefold().replace("_", "")


def planner_examples(context: DatabaseContext) -> str:
    """Illustrate query shapes with only fields and relations in this schema."""

    if not context.tables:
        return ""
    table = context.tables[0]
    source = f"{_identifier(table.schema_name)}.{_identifier(table.table_name)}"
    columns = {column.name: column for column in table.columns}
    date_field = (
        _identifier(table.date_coverage.field)
        if table.date_coverage and table.date_coverage.field in columns
        else None
    )
    measure = next(
        (
            _identifier(column.name)
            for column in table.columns
            if column.data_type.casefold() in _NUMERIC_TYPES
            and any(
                term in column.description.casefold()
                for term in ("hours", "duration", "amount", "units", "quantity")
            )
            and not any(
                term in column.description.casefold()
                for term in ("identifier", "source row", "line number")
            )
        ),
        None,
    )
    group_column = max(
        (
            column
            for column in table.columns
            if column.standard_values
            and column.data_type.casefold()
            in {"text", "character varying", "character"}
        ),
        key=lambda column: len(column.standard_values),
        default=None,
    )
    group = _identifier(group_column.name) if group_column else None
    key = next(
        (
            _identifier(column.name)
            for column in table.columns
            if "unique identifier" in column.description.casefold()
        ),
        None,
    )
    examples = [
        f"1. Row count, without an invented filter:\n"
        f"SELECT COUNT(*) AS row_count FROM {source};"
    ]
    if date_field:
        examples.append(
            "2. Distinct dates, when the question asks for a date count:\n"
            f"SELECT COUNT(DISTINCT {date_field}) AS date_count FROM {source};"
        )
        detail_columns = ", ".join(filter(None, (key, date_field)))
        examples.append(
            "3. Bounded date/detail rows, when the question asks to see values:\n"
            f"SELECT {detail_columns}, COUNT(*) OVER() AS matched_count "
            f"FROM {source} ORDER BY {date_field} LIMIT 100;"
        )
        category_example = next(
            (
                (column, value)
                for column in table.columns
                if column.standard_values
                and "IS DISTINCT FROM" in column.description.upper()
                for value in column.standard_values
                if f"'{value}'" in column.description
            ),
            None,
        )
        if category_example is not None:
            category_column, category_value = category_example
            category_name = _identifier(category_column.name)
            category_literal = "'" + category_value.replace("'", "''") + "'"
            examples.append(
                "Recorded category: use the category field and exact schema "
                "value, without adding an unrelated measure:\n"
                f"SELECT {date_field}, COUNT(*) OVER() AS matched_count "
                f"FROM {source} WHERE {category_name} = {category_literal} "
                f"ORDER BY {date_field} LIMIT 100;"
            )
            examples.append(
                "Negated recorded category: follow its schema description for "
                "NULL semantics; do not substitute another measure:\n"
                f"SELECT {date_field}, COUNT(*) OVER() AS matched_count "
                f"FROM {source} WHERE {category_name} IS DISTINCT FROM "
                f"{category_literal} ORDER BY {date_field} LIMIT 100;"
            )
    if {"employee_id", "name"}.issubset(columns):
        attribute = next(
            (
                column
                for column in table.columns
                if column.name not in {"employee_id", "name"}
                and column.data_type.casefold()
                in {"text", "character varying", "character"}
                and "assigned to the employee" in column.description.casefold()
            ),
            None,
        )
        if attribute:
            examples.append(
                "Employee attribute lookup: use distinct requested values rather "
                "than enumerating source records. Replace the shown attribute "
                "with the one the current question requests:\n"
                f'SELECT DISTINCT "employee_id", "name", '
                f"{_identifier(attribute.name)} FROM {source};"
            )
    if group and measure:
        examples.append(
            "4. Grouped sum and ranking. Add the resolved filters and any "
            "documented meaning predicates required for this turn:\n"
            f"SELECT {group}, SUM({measure}) AS group_total, "
            f"COUNT(*) OVER() AS matched_count FROM {source} GROUP BY {group} "
            "ORDER BY group_total DESC LIMIT 100;"
        )
        examples.append(
            "5. Example request: groups with a positive total. Change the HAVING "
            "comparison to match the current request's threshold and measure:\n"
            f"SELECT {group}, SUM({measure}) AS group_total, "
            f"COUNT(*) OVER() AS matched_count FROM {source} GROUP BY {group} "
            f"HAVING SUM({measure}) > 0 "
            "ORDER BY group_total DESC LIMIT 100;"
        )
    if date_field and measure:
        examples.append(
            "6. Cumulative total: first make one value per date, then window over "
            "those daily values. Carry the resolved scope and relevant filters:\n"
            f"WITH daily AS (SELECT {date_field}, SUM({measure}) AS daily_value "
            f"FROM {source} GROUP BY {date_field}) "
            f"SELECT {date_field}, daily_value, COUNT(*) OVER() AS matched_count, "
            f"SUM(daily_value) OVER (ORDER BY {date_field} ROWS BETWEEN "
            "UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total "
            f"FROM daily ORDER BY {date_field} LIMIT 100;"
        )
        if group:
            examples.append(
                "7. Example request: compare every observed calendar month with "
                "the preceding observed month for each group. Explicit requested "
                "periods need their own date bounds; a missing month is not zero:\n"
                f"WITH monthly AS (SELECT {group}, "
                f"DATE_TRUNC('month', {date_field})::date AS month_start, "
                f"SUM({measure}) AS period_value, COUNT(*) AS observed_rows "
                f"FROM {source} GROUP BY {group}, "
                f"DATE_TRUNC('month', {date_field})::date) "
                f"SELECT {group}, month_start, period_value, observed_rows, "
                f"LAG(period_value) OVER (PARTITION BY {group} "
                "ORDER BY month_start) AS previous_observed_period_value, "
                "COUNT(*) OVER() AS matched_count FROM monthly "
                f"ORDER BY {group}, month_start LIMIT 100;"
            )
    typed_names = {_typed_name(column) for column in table.columns}
    json_fields = (
        field
        for column in table.columns
        for field in column.json_fields
        if field.name.casefold().replace("_", "") not in typed_names
    )
    json_fields = tuple(json_fields)
    if json_fields:
        field = json_fields[0]
        examples.append(
            "8. Object field with no equivalent typed column: use the exact "
            "sql_text_expression from json_fields, not a guessed identifier:\n"
            f"SELECT {key + ', ' if key else ''}"
            f"{field.sql_text_expression} AS object_value, "
            f"COUNT(*) OVER() AS matched_count FROM {source} "
            f"WHERE {field.sql_text_expression} IS NOT NULL LIMIT 100;"
        )
        numeric_field = next(
            (item for item in json_fields if item.json_type == "number"), None
        )
        if numeric_field:
            expression = numeric_field.sql_text_expression
            examples.append(
                "9. Numeric value inside an object: extract text, map empty text "
                "to NULL, then cast before aggregation:\n"
                f"SELECT SUM(NULLIF({expression}, '')::numeric) "
                f"AS numeric_total FROM {source};"
            )
    overtime_fields = {
        field.name: field
        for column in table.columns
        for field in column.json_fields
        if field.name in {"OT_Type_1", "OT_Value_1", "OT_Type_2", "OT_Value_2"}
    }
    if len(overtime_fields) == 4:
        type_1 = overtime_fields["OT_Type_1"].sql_text_expression
        value_1 = overtime_fields["OT_Value_1"].sql_text_expression
        type_2 = overtime_fields["OT_Type_2"].sql_text_expression
        value_2 = overtime_fields["OT_Value_2"].sql_text_expression

        def overtime_value(
            type_expression: str, value_expression: str, label: str
        ) -> str:
            return (
                f"CASE WHEN {type_expression} = '{label}' "
                f"THEN COALESCE(NULLIF({value_expression}, '')::numeric, 0) "
                "ELSE 0 END"
            )

        category_values = (
            ("normal_ot", overtime_value(type_1, value_1, "Normal OT")),
            ("week_off_ot", overtime_value(type_1, value_1, "Week Off OT")),
            ("night_ot", overtime_value(type_2, value_2, "Night OT")),
        )
        current_total = (
            f"COALESCE(NULLIF({value_1}, '')::numeric, 0) + "
            f"COALESCE(NULLIF({value_2}, '')::numeric, 0)"
        )
        report_columns = [
            _identifier(name)
            for name in ("record_id", "employee_id", "name", "attendance_date")
            if name in columns
        ]
        report_columns.extend(
            f"{expression} AS {alias}" for alias, expression in category_values
        )
        report_columns.append(f"({current_total}) AS current_ot")
        report_columns.append("COUNT(*) OVER() AS matched_count")
        order_by = f" ORDER BY {date_field}" if date_field else ""
        examples.append(
            "Attendance report with the requested overtime kinds: use the "
            "same type/value conditions with the current employee and date "
            "filters; current_ot is the sum of the two mutable values. These "
            "aliases are calculated result columns, not physical table columns:\n"
            f"SELECT {', '.join(report_columns)} FROM {source}"
            f"{order_by} LIMIT 100;"
        )
        total_columns = [
            f"SUM({expression}) AS {alias}" for alias, expression in category_values
        ]
        total_columns.append(f"SUM({current_total}) AS current_ot")
        examples.append(
            "Current overtime type totals. When current/category overtime is requested, return "
            "all three current kinds and their overall current total over the "
            "requested employee, date, and group "
            "scope. Use current mutable values for the total, not the immutable "
            "OT_Authorized audit baseline:\n"
            f"SELECT {', '.join(total_columns)} FROM {source};"
        )
        if "ot_authorized" in columns:
            audit_columns = [
                _identifier(name)
                for name in ("record_id", "employee_id", "attendance_date")
                if name in columns
            ]
            audit_columns.extend(
                (
                    '"ot_authorized" AS original_authorized_ot',
                    f"({current_total}) AS current_ot",
                    f'({current_total}) - "ot_authorized" AS adjustment_delta',
                    "COUNT(*) OVER() AS matched_count",
                )
            )
            examples.append(
                "Overtime adjustment audit only when requested: compare the "
                "immutable original authorized total with the current mutable "
                "values. Retain requested employee/date filters:\n"
                f"SELECT {', '.join(audit_columns)} FROM {source}"
                f"{order_by} LIMIT 100;"
            )
    if context.relationships:
        relation = context.relationships[0]
        if relation.from_columns and relation.to_columns:
            left = ".".join(
                _identifier(part) for part in relation.from_table.split(".")
            )
            right = ".".join(_identifier(part) for part in relation.to_table.split("."))
            joins = " AND ".join(
                f"a.{_identifier(source_column)} = b.{_identifier(target_column)}"
                for source_column, target_column in zip(
                    relation.from_columns, relation.to_columns
                )
            )
            examples.append(
                "11. Join only through a supplied relationship, never an invented "
                "table or key. Select only fields needed by the request:\n"
                f"SELECT a.{_identifier(relation.from_columns[0])} "
                f"AS source_key, b.{_identifier(relation.to_columns[0])} "
                f"AS related_key FROM {left} AS a JOIN {right} AS b "
                f"ON {joins} LIMIT 100;"
            )
    else:
        examples.append(
            "11. No relationship is supplied. If a request requires another "
            "table that is absent from database_context, return the "
            "unsupported_capability protocol instead of inventing a JOIN."
        )
    return (
        "\n\nSCHEMA-GROUNDED SQL EXAMPLES\n"
        "These examples illustrate syntax and output shapes only; they are not "
        "requests to answer. Use the current request and database_context to "
        "choose fields, filters, dates, thresholds, and grouping. Never copy a "
        "sample condition, value, date, or measure unless the current request "
        "requires it. When a "
        "sample shape conflicts with the current request, "
        "follow the current request. Emit only final SQL, without example labels.\n\n"
        + "\n\n".join(
            re.sub(r"^\d+\.", f"{index}.", example)
            for index, example in enumerate(examples, start=1)
        )
    )


__all__ = ["planner_examples"]
