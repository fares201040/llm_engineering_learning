from __future__ import annotations

import unittest

from week5.new_implementation.online.answering import (
    AnswerDraft,
    FactAtom,
    GroundedFact,
    validate_draft,
)
from week5.new_implementation.online.execution import (
    AccessContext,
    AttendanceRowScope,
    AuthorizationError,
    bind_query,
    compile_coverage,
    compile_main,
    compile_witness,
)
from week5.new_implementation.online.query import (
    AggregateMeasure,
    AggregateOutput,
    All,
    AttendanceQuery,
    Condition,
    Not,
)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.query = AttendanceQuery(
            filters=(
                All(
                    items=(
                        Condition(field_id="date", operator="gte", value="2026-09-01"),
                        Not(item=Condition(field_id="exception", operator="eq", value="Absent' OR 1=1 --")),
                    )
                ),
            ),
            output=AggregateOutput(
                measures=(AggregateMeasure(output_id="days", function="distinct_count", field_id="date"),)
            ),
        )

    def test_authoritative_scope_is_bound_once_and_all_literals_are_parameters(self):
        access = AccessContext(
            principal_id="manager",
            domain="attendance",
            allowed_domains=frozenset({"attendance"}),
            attendance_scope=AttendanceRowScope("employee_ids", ("A1",)),
        )
        bound = bind_query(self.query, ("A1",), access)
        main = compile_main(bound, table="attendance_records")
        coverage = compile_coverage(bound, table="attendance_records")
        witness = compile_witness(bound, table="attendance_records")
        self.assertNotIn("Absent' OR 1=1 --", main.sql)
        self.assertIn("Absent' OR 1=1 --", main.params)
        self.assertEqual(coverage.params[0], ["A1"])
        self.assertEqual(witness.params[0], ["A1"])

    def test_out_of_scope_employee_is_rejected_before_compilation(self):
        access = AccessContext(
            principal_id="manager",
            domain="attendance",
            allowed_domains=frozenset({"attendance"}),
            attendance_scope=AttendanceRowScope("employee_ids", ("A1",)),
        )
        with self.assertRaises(AuthorizationError):
            bind_query(self.query, ("A2",), access)

    def test_answer_validation_rejects_unknown_facts_and_missing_values(self):
        facts = (
            GroundedFact(
                fact_id="result",
                kind="structured",
                atoms=(FactAtom(atom_id="value", role="days", value=4),),
            ),
        )
        with self.assertRaises(ValueError):
            validate_draft(AnswerDraft(answer="Three days", fact_ids=("result",)), facts)
        with self.assertRaises(ValueError):
            validate_draft(AnswerDraft(answer="4 days", fact_ids=("other",)), facts)


if __name__ == "__main__":
    unittest.main()
