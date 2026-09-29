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
    rank_employee_candidates,
    search_employee_directory_postgres,
    validate_read_query,
)
from week5.new_implementation.online.reference import Employee, EmployeeOption


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
    def test_two_ordered_fuzzy_name_parts_select_one_confirmation(self):
        options = (
            EmployeeOption(employee_id="A10218", employee_name="Wael Nageeb Mahmoud"),
            EmployeeOption(employee_id="A11026", employee_name="Wail Saleh Awadh"),
            EmployeeOption(employee_id="A10771", employee_name="Waheeb Saleh Mohammed"),
            EmployeeOption(employee_id="A10044", employee_name="Adel Abdulla Saleh"),
        )

        self.assertEqual(
            rank_employee_candidates("Wael Saleh", options),
            (options[1],),
        )

    def test_shared_fuzzy_first_and_second_names_remain_ambiguous(self):
        options = (
            EmployeeOption(employee_id="A1", employee_name="Wail Saleh Awadh"),
            EmployeeOption(employee_id="A2", employee_name="Wael Saleh Ahmed"),
            EmployeeOption(employee_id="A3", employee_name="Adel Saleh"),
        )

        self.assertEqual(
            rank_employee_candidates("Wael Saleh", options),
            (options[1], options[0]),
        )

    def test_third_name_mismatch_does_not_select_single_confirmation(self):
        options = (
            EmployeeOption(employee_id="A1", employee_name="Wail Saleh Awadh"),
            EmployeeOption(employee_id="A2", employee_name="Wael Saleh Ahmed"),
        )

        self.assertEqual(
            rank_employee_candidates("Wael Saleh Omar", options),
            (options[1], options[0]),
        )

    def test_first_name_candidates_outrank_surname_only_matches(self):
        options = (
            EmployeeOption(employee_id="A1", employee_name="Wael Nageeb Mahmoud"),
            EmployeeOption(employee_id="A2", employee_name="Adel Abdulla Saleh"),
            EmployeeOption(employee_id="A3", employee_name="Waheeb Saleh Mohammed"),
        )

        self.assertEqual(
            rank_employee_candidates("Wael Saleh", options),
            (options[0],),
        )

    def test_unmatched_name_returns_short_closest_list(self):
        options = tuple(
            EmployeeOption(employee_id=f"A{index}", employee_name=f"Person {index}")
            for index in range(20)
        )

        self.assertEqual(
            len(rank_employee_candidates("Unknown Name", options)),
            5,
        )

    def test_authorized_directory_is_ranked_without_second_database_connection(self):
        directory = (
            Employee(employee_id="A1", name="Wail Saleh Awadh"),
            Employee(employee_id="A2", name="Wael Nageeb Mahmoud"),
        )
        with patch("psycopg.connect", side_effect=AssertionError("unexpected DB call")):
            options = search_employee_directory_postgres(
                "Wael Saleh",
                dsn="postgresql://test",
                table="attendance_records",
                allowed_employee_ids=("A1",),
                directory=directory,
            )

        self.assertEqual(
            options,
            (EmployeeOption(employee_id="A1", employee_name="Wail Saleh Awadh"),),
        )

    def test_query_boundary_accepts_ctes_and_rejects_other_statements(self):
        validate_read_query(
            "WITH a AS (SELECT employee_id FROM attendance_records) SELECT * FROM a",
            allowed_tables=("public.attendance_records",),
        )
        for sql in (
            "SELECT 1; SELECT 2",
            "DELETE FROM attendance_records",
            "SELECT * FROM private.payroll",
            "SELECT * FROM pg_catalog.pg_tables",
            "SELECT 1 INTO new_table",
            "SELECT * FROM attendance_records FOR UPDATE",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_read_query(sql, allowed_tables=("public.attendance_records",))

    def test_employee_scope_applies_inside_aggregate_cte_and_join(self):
        sql = (
            "WITH totals AS (SELECT employee_id, COUNT(*) n FROM attendance_records "
            "GROUP BY employee_id) SELECT totals.employee_id, totals.n, "
            "ar.attendance_date FROM totals JOIN attendance_records ar "
            "ON totals.employee_id = ar.employee_id"
        )
        connection = Connection(Cursor(description=(Description("n", 23),)))
        with fake_psycopg(connection):
            execute_sql(
                sql,
                dsn="postgresql://test",
                allowed_tables=("public.attendance_records",),
                scope_employee_ids=("A1", "A'2"),
            )
        query = next(item[0] for item in connection.calls if "WITH totals" in item[0])
        self.assertEqual(query.count("employee_id IN"), 2)
        self.assertIn("A''2", query)

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

    def test_fuzzy_directory_search_loads_only_authorized_names(self):
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

        sql, params = next(
            item for item in connection.calls if item[0].startswith("SELECT DISTINCT")
        )
        self.assertNotIn("similarity", sql)
        self.assertIn('"employee_id" = ANY(%s)', sql)
        self.assertIn(["A1"], params)
        self.assertEqual(options[0].employee_id, "A1")

    def test_fuzzy_directory_search_always_returns_closest_options(self):
        class CandidateConnection(Connection):
            def execute(self, sql, params=None):
                self.calls.append((sql, params))
                if sql.startswith("BEGIN"):
                    return Cursor()
                return Cursor(
                    rows=({"employee_id": "A1", "name": "Faris Synthetic One"},)
                )

        connection = CandidateConnection(Cursor())
        with fake_psycopg(connection):
            options = search_employee_directory_postgres(
                "Fares Other",
                dsn="postgresql://test",
                table="attendance_records",
                allowed_employee_ids=("A1",),
            )

        self.assertEqual(
            options,
            (EmployeeOption(employee_id="A1", employee_name="Faris Synthetic One"),),
        )
        searches = [
            sql for sql, _ in connection.calls if sql.startswith("SELECT DISTINCT")
        ]
        self.assertEqual(len(searches), 1)
        self.assertNotIn("similarity", searches[0])

    def test_fuzzy_directory_search_includes_third_and_later_given_names(self):
        options = (
            EmployeeOption(employee_id="A1", employee_name="Wail Ahmed Saleh Mahmoud"),
            EmployeeOption(employee_id="A2", employee_name="Wail Ahmed Saleh Omar"),
        )

        self.assertEqual(
            rank_employee_candidates("Wael Ahmed Saleh Mahmoud", options),
            (options[0],),
        )


if __name__ == "__main__":
    unittest.main()
