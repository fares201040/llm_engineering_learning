"""Regression checks for shared review failures, without replaying passed evaluations."""

import unittest

from week5.new_implementation.online.execution import validate_read_query
from week5.new_implementation.online.pipeline import (
    _required_date_scope,
    _sql_semantic_issue,
    _strip_private_projections,
)
from week5.new_implementation.online.reference import (
    Employee,
    ReadyReference,
    ReferenceResponse,
    bind_references,
)


class ReviewRegressionTests(unittest.TestCase):
    def test_model_star_projection_expands_only_public_columns(self):
        sql = _strip_private_projections(
            "SELECT * FROM attendance_records LIMIT 100",
            public_columns=("record_id", "employee_id", "record_json", "raw_row_key"),
        )
        self.assertIsNotNone(sql)
        self.assertIn("record_id", sql.casefold())
        self.assertIn("employee_id", sql.casefold())
        self.assertNotIn("record_json", sql.casefold())
        self.assertNotIn("raw_row_key", sql.casefold())

    def test_private_extras_are_removed_from_valid_attendance_list(self):
        sql = _strip_private_projections(
            "SELECT record_id, attendance_date, record_json, raw_row_key, "
            "COUNT(*) OVER() AS matched_count FROM attendance_records "
            "WHERE attendance_date < '2026-09-05' LIMIT 100"
        )
        self.assertIsNotNone(sql)
        self.assertNotIn("record_json", sql.casefold())
        self.assertNotIn("raw_row_key", sql.casefold())
        self.assertIn("attendance_date", sql.casefold())
        self.assertIn("matched_count", sql.casefold())

    def test_private_only_projection_has_no_public_answer(self):
        self.assertIsNone(
            _strip_private_projections(
                "SELECT raw_row_key, COUNT(*) OVER() AS matched_count "
                "FROM attendance_records LIMIT 100"
            )
        )

    def test_status_percentage_keeps_all_record_denominator(self):
        self.assertIsNone(
            _sql_semantic_issue(
                'What percentage of all attendance records have Status "Authorized"?',
                "SELECT 100.0 * COUNT(*) FILTER (WHERE status = 'Authorized') "
                "/ NULLIF(COUNT(*), 0) AS percentage FROM attendance_records",
            )
        )

    def test_status_filter_cannot_validate_an_unfiltered_answer_count(self):
        self.assertEqual(
            _sql_semantic_issue(
                "How many Draft attendance records are there?",
                "SELECT COUNT(*) AS record_count, "
                "COUNT(*) FILTER (WHERE status = 'Draft') AS other_count "
                "FROM attendance_records",
            ),
            "missing_workflow_status_filter",
        )

    def test_status_percentage_rejects_status_narrowed_denominator(self):
        self.assertEqual(
            _sql_semantic_issue(
                'What percentage of all attendance records have Status "Authorized"?',
                "SELECT 100.0 * COUNT(*) FILTER (WHERE status = 'Authorized') "
                "/ NULLIF(COUNT(*), 0) AS percentage "
                "FROM attendance_records WHERE status = 'Draft'",
            ),
            "missing_workflow_status_filter",
        )

    def test_open_ended_date_filter_is_not_a_single_day(self):
        question = "Show attendance before 2026-09-05."
        scope = _required_date_scope(
            question,
            request_relationship="new",
            subject_relationship="all_authorized",
            previous_scope=None,
        )
        self.assertIsNone(
            _sql_semantic_issue(
                question,
                "SELECT record_id, attendance_date FROM attendance_records "
                "WHERE attendance_date < '2026-09-05' LIMIT 100",
                required_date_scope=scope,
            )
        )

    def test_malformed_identifier_cannot_resolve_by_punctuation_stripping(self):
        question = "Show attendance for employee A-10017."
        bound = bind_references(
            ReferenceResponse(
                decision=ReadyReference(
                    rewritten_request=question,
                    locale="en",
                    request_relationship="new",
                    subject_relationship="employees",
                    employee_ids=("A-10017",),
                )
            ),
            (Employee(employee_id="A10017", name="Example Employee"),),
            original_question=question,
        )
        self.assertTrue(bound.ambiguous)
        self.assertEqual(bound.reason, "malformed_identifier")

    def test_private_full_payload_projection_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_read_query(
                "SELECT record_id, raw_row_key, record_json FROM attendance_records",
                allowed_tables=("attendance_records",),
            )
        with self.assertRaises(ValueError):
            validate_read_query(
                "SELECT record_json::text AS raw_payload FROM attendance_records",
                allowed_tables=("attendance_records",),
            )

    def test_whole_row_serialization_cannot_expose_private_columns(self):
        for sql in (
            "SELECT row_to_json(a) AS payload FROM attendance_records AS a",
            "SELECT to_jsonb(a) AS payload FROM attendance_records AS a",
        ):
            with self.subTest(sql=sql):
                with self.assertRaises(ValueError):
                    validate_read_query(sql, allowed_tables=("attendance_records",))
                self.assertIsNone(_strip_private_projections(sql))

    def test_direct_attendance_star_is_rejected_below_pipeline(self):
        for sql in (
            "SELECT * FROM attendance_records",
            "SELECT a.* FROM attendance_records AS a",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_read_query(sql, allowed_tables=("attendance_records",))


if __name__ == "__main__":
    unittest.main()
