import unittest

from pydantic import ValidationError

from week5.new_implementation.attendance_schema import (
    AnswerContract,
    ExecutableQueryPlan,
    FilterCondition,
    GeneratedAggregateSqlDecision,
    QueryPlan,
    validate_generated_aggregate_decision,
)
from week5.new_implementation.postgres_compiler import (
    GeneratedAggregateChoice,
    compile_generated_aggregate_query,
    validate_generated_aggregate_sql,
)


class GeneratedAggregateSqlDecisionTests(unittest.TestCase):
    def test_accepts_each_valid_status_shape_and_strips_sql(self):
        ready = GeneratedAggregateSqlDecision(
            status="ready",
            sql="  SELECT COUNT(*) AS value FROM attendance_scope  ",
        )
        ambiguous = GeneratedAggregateSqlDecision(
            status="ambiguous", candidate_ids=["field-1", "field-2"]
        )
        unsupported = GeneratedAggregateSqlDecision(status="unsupported")

        self.assertEqual(ready.sql, "SELECT COUNT(*) AS value FROM attendance_scope")
        self.assertEqual(ready.candidate_ids, [])
        self.assertIsNone(ambiguous.sql)
        self.assertEqual(ambiguous.candidate_ids, ["field-1", "field-2"])
        self.assertIsNone(unsupported.sql)
        self.assertEqual(unsupported.candidate_ids, [])

    def test_rejects_unknown_properties_and_invalid_status_shapes(self):
        invalid_payloads = [
            {
                "status": "ready",
                "sql": "SELECT COUNT(*) AS value FROM attendance_scope",
                "extra": True,
            },
            {"status": "ready"},
            {
                "status": "ready",
                "sql": "SELECT COUNT(*) AS value FROM attendance_scope",
                "candidate_ids": ["field-1"],
            },
            {"status": "ambiguous"},
            {
                "status": "ambiguous",
                "candidate_ids": ["field-1"],
                "sql": "SELECT COUNT(*) AS value FROM attendance_scope",
            },
            {
                "status": "unsupported",
                "sql": "SELECT COUNT(*) AS value FROM attendance_scope",
            },
            {"status": "unsupported", "candidate_ids": ["field-1"]},
            {"status": "other"},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                GeneratedAggregateSqlDecision.model_validate(payload)

    def test_rejects_blank_or_overlength_sql_and_invalid_candidate_ids(self):
        invalid_payloads = [
            {"status": "ready", "sql": "   "},
            {"status": "ready", "sql": "x" * 513},
            {"status": "ambiguous", "candidate_ids": [""]},
            {"status": "ambiguous", "candidate_ids": ["   "]},
            {"status": "ambiguous", "candidate_ids": ["field-1", "field-1"]},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                GeneratedAggregateSqlDecision.model_validate(payload)

    def test_request_local_validator_rejects_unknown_ambiguous_candidate_ids(self):
        decision = GeneratedAggregateSqlDecision(
            status="ambiguous", candidate_ids=["field-1", "field-2"]
        )

        self.assertIs(
            validate_generated_aggregate_decision(
                decision, allowed_candidate_ids={"field-1", "field-2", "field-3"}
            ),
            decision,
        )
        with self.assertRaises(ValueError):
            validate_generated_aggregate_decision(
                decision, allowed_candidate_ids={"field-1", "field-3"}
            )

    def test_python_model_boundary_rejects_coercion_but_json_remains_valid(self):
        invalid_payloads = [
            {
                "status": "ready",
                "sql": b"SELECT COUNT(*) AS value FROM attendance_scope",
            },
            {"status": "ambiguous", "candidate_ids": ("field-1",)},
            {"status": "ambiguous", "candidate_ids": [b"field-1"]},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                GeneratedAggregateSqlDecision.model_validate(payload)

        decision = GeneratedAggregateSqlDecision.model_validate_json(
            '{"status":"ambiguous","candidate_ids":["field-1"]}'
        )
        self.assertEqual(decision.candidate_ids, ["field-1"])


class GeneratedAggregateSqlValidationTests(unittest.TestCase):
    CANDIDATE_FIELDS = ("Employee_ID", "Date", "Total_OT", "Department")

    def test_accepts_each_allowed_statement_and_returns_canonical_choice(self):
        cases = [
            (
                "SELECT COUNT(*) AS value FROM attendance_scope",
                "count",
                GeneratedAggregateChoice("count", None),
            ),
            (
                "SELECT COUNT(DISTINCT Employee_ID) AS value FROM attendance_scope",
                "distinct_count",
                GeneratedAggregateChoice("distinct_count", "Employee_ID"),
            ),
            (
                "SELECT SUM(Total_OT) AS value FROM attendance_scope",
                "sum",
                GeneratedAggregateChoice("sum", "Total_OT"),
            ),
            (
                "SELECT AVG(Total_OT) AS value FROM attendance_scope",
                "average",
                GeneratedAggregateChoice("average", "Total_OT"),
            ),
            (
                "SELECT MIN(Total_OT) AS value FROM attendance_scope",
                "min",
                GeneratedAggregateChoice("min", "Total_OT"),
            ),
            (
                "SELECT MAX(Total_OT) AS value FROM attendance_scope",
                "max",
                GeneratedAggregateChoice("max", "Total_OT"),
            ),
        ]

        for sql, expected_operation, expected in cases:
            with self.subTest(sql=sql):
                self.assertEqual(
                    validate_generated_aggregate_sql(
                        sql,
                        candidate_fields=self.CANDIDATE_FIELDS,
                        expected_operation=expected_operation,
                    ),
                    expected,
                )

    def test_accepts_case_insensitive_keywords_fields_and_harmless_whitespace(self):
        choice = validate_generated_aggregate_sql(
            "\n select  avg ( total_ot )  as VALUE\nfrom  ATTENDANCE_SCOPE \t",
            candidate_fields=("Total_OT",),
            expected_operation="average",
        )

        self.assertEqual(choice, GeneratedAggregateChoice("average", "Total_OT"))

    def test_rejects_unknown_noncandidate_wrong_type_and_operation_mismatch(self):
        cases = [
            (
                "SELECT SUM(Unknown_Field) AS value FROM attendance_scope",
                self.CANDIDATE_FIELDS,
                "sum",
            ),
            (
                "SELECT SUM(Lateness_Hrs) AS value FROM attendance_scope",
                self.CANDIDATE_FIELDS,
                "sum",
            ),
            (
                "SELECT SUM(Department) AS value FROM attendance_scope",
                self.CANDIDATE_FIELDS,
                "sum",
            ),
            (
                "SELECT COUNT(DISTINCT Department) AS value FROM attendance_scope",
                self.CANDIDATE_FIELDS,
                "distinct_count",
            ),
            (
                "SELECT MAX(Total_OT) AS value FROM attendance_scope",
                self.CANDIDATE_FIELDS,
                "min",
            ),
        ]

        for sql, candidate_fields, expected_operation in cases:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_generated_aggregate_sql(
                    sql,
                    candidate_fields=candidate_fields,
                    expected_operation=expected_operation,
                )

    def test_rejects_every_non_scalar_or_extended_sql_shape(self):
        rejected_sql = [
            "SELECT COUNT(*) AS value FROM attendance_records",
            "SELECT COUNT(*) AS value FROM public.attendance_scope",
            "SELECT 1 AS value FROM attendance_scope",
            "SELECT $1 AS value FROM attendance_scope",
            "SELECT COUNT(*) AS value FROM attendance_scope WHERE status = 'ok'",
            "SELECT COUNT(*) AS value FROM attendance_scope -- comment",
            "SELECT COUNT(*) AS value FROM attendance_scope /* comment */",
            "SELECT COUNT(*) AS value FROM attendance_scope;",
            "SELECT COUNT(*) AS value FROM attendance_scope; DROP TABLE employees",
            "SELECT COUNT(*) AS value FROM attendance_scope JOIN employees USING (employee_id)",
            "WITH rows AS (SELECT * FROM attendance_scope) SELECT COUNT(*) AS value FROM rows",
            "SELECT (SELECT COUNT(*) FROM attendance_scope) AS value FROM attendance_scope",
            "SELECT COUNT(*) AS value FROM attendance_scope GROUP BY Department",
            "SELECT COUNT(*) AS value FROM attendance_scope ORDER BY value",
            "SELECT Department, COUNT(*) AS value FROM attendance_scope",
            "SELECT COUNT(*) AS total FROM attendance_scope",
            "SELECT NOW() AS value FROM attendance_scope",
            'SELECT SUM("Total_OT") AS value FROM attendance_scope',
            "SELECT SUM(Total_OT + 1) AS value FROM attendance_scope",
            "SELECT * FROM attendance_scope",
            "SELECT COUNT(*) AS value FROM (SELECT * FROM attendance_scope) rows",
        ]

        for sql in rejected_sql:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_generated_aggregate_sql(
                    sql,
                    candidate_fields=self.CANDIDATE_FIELDS,
                    expected_operation="count",
                )


class GeneratedAggregateCompilationTests(unittest.TestCase):
    def test_rebuilds_trusted_sql_and_retains_scope_only_in_bound_parameters(self):
        choice = GeneratedAggregateChoice("sum", "Total_OT")
        plan = self._plan(
            employee_ids=["A10001", "A10002' OR 1=1 --"], department="Engineering"
        )

        query = compile_generated_aggregate_query(
            choice, plan, table_name="analytics.attendance_records"
        )

        self.assertEqual(query.purpose, "aggregation")
        self.assertIn("SUM(total_ot) AS value", query.sql)
        self.assertIn("FROM analytics.attendance_records", query.sql)
        self.assertNotIn("attendance_scope", query.sql)
        self.assertNotIn("A10001", query.sql)
        self.assertNotIn("Engineering", query.sql)
        self.assertEqual(
            query.params,
            (["A10001", "A10002' OR 1=1 --"], "Engineering"),
        )

    def test_rejects_plan_choice_mismatch_and_non_scalar_plans(self):
        scalar = self._plan()
        invalid_cases = [
            (GeneratedAggregateChoice("average", "Total_OT"), scalar),
            (GeneratedAggregateChoice("sum", "Lateness_Hrs"), scalar),
            (
                GeneratedAggregateChoice("sum", "Total_OT"),
                scalar.model_copy(update={"group_by": ["Department"]}),
            ),
            (
                GeneratedAggregateChoice("sum", "Total_OT"),
                scalar.model_copy(update={"projection": ["Employee_ID"]}),
            ),
            (
                GeneratedAggregateChoice("sum", "Total_OT"),
                scalar.model_copy(
                    update={
                        "answer_contract": AnswerContract(
                            shape="grouped", unit="hours", grain=["Department"]
                        )
                    }
                ),
            ),
        ]

        for choice, plan in invalid_cases:
            with self.subTest(choice=choice, plan=plan), self.assertRaises(ValueError):
                compile_generated_aggregate_query(choice, plan)

        with self.assertRaises(TypeError):
            compile_generated_aggregate_query(
                GeneratedAggregateChoice("sum", "Total_OT"),
                QueryPlan(mode="exact", search_query="overtime"),
            )

    def test_count_requires_plan_to_have_no_aggregation_field(self):
        plan = self._plan().model_copy(
            update={"aggregation": "count", "aggregation_field": "Total_OT"}
        )

        with self.assertRaises(ValueError):
            compile_generated_aggregate_query(
                GeneratedAggregateChoice("count", None), plan
            )

    def test_rejects_scalar_answer_contract_with_nonempty_grain(self):
        plan = self._plan().model_copy(
            update={
                "answer_contract": AnswerContract(
                    shape="scalar", unit="hours", grain=["Department"]
                )
            }
        )

        with self.assertRaises(ValueError):
            compile_generated_aggregate_query(
                GeneratedAggregateChoice("sum", "Total_OT"), plan
            )

    def test_fingerprint_is_stable_across_different_bound_values(self):
        choice = GeneratedAggregateChoice("sum", "Total_OT")
        first = compile_generated_aggregate_query(
            choice, self._plan(employee_ids=["A10001"], department="Engineering")
        )
        second = compile_generated_aggregate_query(
            choice, self._plan(employee_ids=["A99999"], department="Finance")
        )

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.params, second.params)

    @staticmethod
    def _plan(
        employee_ids: list[str] | None = None,
        department: str = "Engineering",
    ) -> ExecutableQueryPlan:
        return ExecutableQueryPlan(
            mode="exact",
            search_query="total overtime",
            filters=[
                FilterCondition(
                    field="Employee_ID",
                    operator="in",
                    value=employee_ids or ["A10001"],
                ),
                FilterCondition(field="Department", operator="eq", value=department),
            ],
            aggregation="sum",
            aggregation_field="Total_OT",
            answer_contract=AnswerContract(
                shape="scalar", unit="hours", subject_field="Total_OT", grain=[]
            ),
        )


if __name__ == "__main__":
    unittest.main()
