import unittest

from week5.new_implementation.attendance_schema import (
    AnswerContract,
    ExecutableQueryPlan,
    FilterCondition,
    PlannerProposal,
    QueryPlan,
)
from week5.new_implementation.postgres_compiler import (
    compile_aggregation_queries,
    compile_count_query,
    compile_sample_query,
    compile_where,
)


class PostgresCompilerTests(unittest.TestCase):
    def test_question_value_is_a_bound_parameter(self):
        fragment = compile_where(
            [
                FilterCondition(
                    field="Department", operator="eq", value="HR' OR 1=1 --"
                )
            ]
        )
        self.assertNotIn("HR' OR 1=1 --", fragment.sql)
        self.assertEqual(fragment.params, ("HR' OR 1=1 --",))

    def test_plain_query_plan_is_rejected(self):
        with self.assertRaises(TypeError):
            compile_count_query(QueryPlan(mode="exact", search_query="records"))

    def test_planner_proposal_is_rejected(self):
        with self.assertRaises(TypeError):
            compile_count_query(
                PlannerProposal(
                    status="unsupported",
                    unsupported_capabilities=["nested_boolean_filters"],
                )
            )

    def test_all_filter_operators_bind_values(self):
        conditions = [
            FilterCondition(field="Department", operator="eq", value="HR"),
            FilterCondition(field="Department", operator="ne", value="IT"),
            FilterCondition(field="Total_OT", operator="gt", value=1.0),
            FilterCondition(field="Total_OT", operator="gte", value=2.0),
            FilterCondition(field="Total_OT", operator="lt", value=8.0),
            FilterCondition(field="Total_OT", operator="lte", value=7.0),
            FilterCondition(field="Status", operator="in", value=["Draft", "Authorized"]),
            FilterCondition(field="Name", operator="contains", value="Ali"),
            FilterCondition(field="Name", operator="starts_with", value="A"),
        ]
        fragment = compile_where(conditions)
        for malicious_or_plain in ("HR", "IT", "Draft", "Authorized", "Ali"):
            self.assertNotIn(malicious_or_plain, fragment.sql)
        self.assertEqual(len(fragment.params), len(conditions))

    def test_empty_in_and_unknown_identifiers_fail(self):
        with self.assertRaises(ValueError):
            compile_where([FilterCondition(field="Status", operator="in", value=[])])
        with self.assertRaises(ValueError):
            compile_where([FilterCondition(field="unknown", operator="eq", value="x")])
        with self.assertRaises(ValueError):
            compile_sample_query(self._plan(), table_name="records; DROP TABLE x")

    def test_compiled_queries_have_stable_value_free_fingerprints(self):
        first = compile_count_query(self._plan("HR"))
        second = compile_count_query(self._plan("IT"))
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.params, second.params)

    def test_grouped_and_percentage_queries_are_compiled(self):
        grouped = self._plan()
        grouped.aggregation = "distinct_count"
        grouped.aggregation_field = "Date"
        grouped.group_by = ["Department"]
        self.assertEqual(len(compile_aggregation_queries(grouped)), 1)

        percentage = self._plan()
        percentage.aggregation = "percentage"
        percentage.aggregation_field = "Employee_ID"
        percentage.percentage_condition = FilterCondition(
            field="Status", operator="eq", value="Authorized"
        )
        self.assertEqual(len(compile_aggregation_queries(percentage)), 2)

    @staticmethod
    def _plan(department="HR"):
        return ExecutableQueryPlan(
            mode="exact",
            search_query="records",
            filters=[FilterCondition(field="Department", operator="eq", value=department)],
            answer_contract=AnswerContract(
                shape="scalar", unit="records", subject_field=None, grain=[]
            ),
        )


if __name__ == "__main__":
    unittest.main()
