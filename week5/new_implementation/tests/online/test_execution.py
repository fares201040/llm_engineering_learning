from __future__ import annotations

from contextlib import contextmanager
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from week5.new_implementation.online.execution import (
    AccessContext,
    AttendanceRowScope,
    AuthorizationError,
    authorize_access,
    execute_sql,
    load_employee_directory,
    search_employee_directory_postgres,
)


class Description:
    def __init__(self, name, type_code):
        self.name = name
        self.type_code = type_code


class Cursor:
    def __init__(self, rows=(), description=()):
        self._rows = list(rows)
        self.description = description

    def fetchmany(self, count):
        return self._rows[:count]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class Connection:
    def __init__(self, result_cursor):
        self.result_cursor = result_cursor
        self.calls = []
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.startswith("SELECT set_config") or sql.startswith("BEGIN"):
            return Cursor()
        return self.result_cursor

    def rollback(self):
        self.rollbacks += 1


@contextmanager
def fake_psycopg(connection):
    module = SimpleNamespace(connect=lambda *_args, **_kwargs: connection)
    rows = SimpleNamespace(dict_row=object())
    with patch.dict(sys.modules, {"psycopg": module, "psycopg.rows": rows}):
        yield


class DirectExecutionTests(unittest.TestCase):
    def test_access_context_still_scopes_employee_directory_resolution(self):
        scope = authorize_access(
            AccessContext(
                principal_id="manager",
                domain="attendance",
                allowed_domains=frozenset({"attendance"}),
                attendance_scope=AttendanceRowScope("employee_ids", ("A1",)),
            )
        )
        self.assertEqual(scope.employee_ids, ("A1",))
        with self.assertRaises(AuthorizationError):
            authorize_access(None)

    def test_model_sql_is_executed_exactly_without_parameters_or_rewriting(self):
        sql = "WITH totals AS (SELECT employee_id, SUM(total_worked_hrs) AS hours FROM attendance_records GROUP BY employee_id) SELECT * FROM totals"
        connection = Connection(
            Cursor(
                rows=({"employee_id": "A1", "hours": 8.0},),
                description=(Description("employee_id", 25), Description("hours", 701)),
            )
        )
        with fake_psycopg(connection):
            result = execute_sql(sql, dsn="postgresql://test", result_limit=10)

        self.assertIn((sql, None), connection.calls)
        self.assertEqual(result.rows[0]["hours"], 8.0)
        self.assertEqual(result.columns[0].name, "employee_id")
        self.assertEqual(connection.rollbacks, 1)

    def test_result_row_bound_rolls_back_and_fails(self):
        connection = Connection(
            Cursor(rows=({"n": 1}, {"n": 2}), description=(Description("n", 23),))
        )
        with (
            fake_psycopg(connection),
            self.assertRaisesRegex(RuntimeError, "row bound"),
        ):
            execute_sql(
                "SELECT n FROM generated", dsn="postgresql://test", result_limit=1
            )
        self.assertEqual(connection.rollbacks, 1)

    def test_response_size_bound_rolls_back_and_fails(self):
        connection = Connection(
            Cursor(rows=({"text": "x" * 100},), description=(Description("text", 25),))
        )
        with (
            fake_psycopg(connection),
            self.assertRaisesRegex(RuntimeError, "response-size"),
        ):
            execute_sql(
                "SELECT text FROM generated",
                dsn="postgresql://test",
                result_limit=10,
                max_response_bytes=10,
            )
        self.assertEqual(connection.rollbacks, 1)

    def test_directory_query_retrieves_only_authoritative_id_and_name(self):
        connection = Connection(
            Cursor(rows=({"employee_id": "A1", "name": "Wail Ali"},))
        )
        with fake_psycopg(connection):
            directory = load_employee_directory(
                dsn="postgresql://test",
                table="attendance_records",
            )

        sql = next(
            item[0]
            for item in connection.calls
            if item[0].startswith("SELECT DISTINCT")
        )
        self.assertIn('"employee_id"', sql)
        self.assertIn('"name"', sql)
        self.assertNotIn("department", sql)
        self.assertNotIn("total_worked_hrs", sql)
        self.assertEqual(directory[0].name, "Wail Ali")

    def test_fuzzy_directory_search_runs_in_postgres_with_scope_and_ordering(self):
        connection = Connection(
            Cursor(rows=({"employee_id": "A1", "name": "Wail Ali"},))
        )
        with fake_psycopg(connection):
            options = search_employee_directory_postgres(
                "Wael Ali",
                dsn="postgresql://test",
                table="attendance_records",
                allowed_employee_ids=("A1",),
            )

        sql, params = next(item for item in connection.calls if "similarity" in item[0])
        self.assertIn("ORDER BY match_score DESC", sql)
        self.assertIn('"employee_id" = ANY(%s)', sql)
        self.assertEqual(params[3], ["A1"])
        self.assertEqual(options[0].employee_id, "A1")


if __name__ == "__main__":
    unittest.main()
