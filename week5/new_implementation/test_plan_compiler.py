import unittest

from week5.new_implementation.attendance_schema import (
    AnswerContract,
    PlannerProposal,
    ProposedFilter,
    ProposedMeasureChoice,
)
from week5.new_implementation.plan_compiler import (
    PLAN_INVARIANTS,
    CompilationContext,
    compile_proposal,
)
from week5.new_implementation.semantic_resolution import (
    ResolutionContext,
    detect_semantic_facts,
)


class PlanCompilerTests(unittest.TestCase):
    def setUp(self):
        self.question = "how many off days for A11017"
        self.resolution = ResolutionContext(
            catalog={
                "Day_Type": ("Working Day", "OFF Day", "OFF Day (ZAS)"),
                "Exception": ("Absent", "Lateness", "OK"),
            }
        )
        self.facts = detect_semantic_facts(self.question, self.resolution)

    def test_valid_but_ungrounded_different_column_is_rejected(self):
        proposal = self._proposal(
            ProposedFilter(
                field="Exception",
                operator="eq",
                value="Absent",
                evidence_text="off days",
            )
        )

        result = compile_proposal(proposal, self._context())

        self.assertFalse(result.ready)
        self.assertEqual(
            {item.code for item in result.violations},
            {"ungrounded_constraint", "uncovered_fact"},
        )

    def test_registered_value_family_compiles_without_special_case(self):
        proposal = self._proposal(
            ProposedFilter(
                field="Day_Type",
                operator="in",
                value=["OFF Day", "OFF Day (ZAS)"],
                evidence_text="off days",
            )
        )

        result = compile_proposal(proposal, self._context())

        self.assertTrue(result.ready, result.violations)
        self.assertEqual(
            result.executable_plan.filters[0].model_dump(),
            {
                "field": "Day_Type",
                "operator": "in",
                "value": ["OFF Day", "OFF Day (ZAS)"],
            },
        )
        self.assertEqual(result.executable_plan.mode, "exact")

    def test_false_evidence_is_rejected(self):
        proposal = self._proposal(
            ProposedFilter(
                field="Day_Type",
                operator="eq",
                value="Working Day",
                evidence_text="working day",
            )
        )
        result = compile_proposal(proposal, self._context())
        self.assertIn("ungrounded_constraint", {v.code for v in result.violations})

    def test_answer_contract_must_match_measure(self):
        proposal = PlannerProposal(
            status="ready",
            measure=ProposedMeasureChoice(
                name="attendance_records", evidence_text="days"
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )
        result = compile_proposal(proposal, self._context())
        self.assertIn("answer_contract_mismatch", {v.code for v in result.violations})

    def test_invariant_pipeline_is_unique_and_ordered(self):
        self.assertEqual(
            [type(item).__name__ for item in PLAN_INVARIANTS],
            [
                "SchemaInvariant",
                "GroundingInvariant",
                "CoverageInvariant",
                "ContradictionInvariant",
                "AnswerContractInvariant",
                "CapabilityInvariant",
            ],
        )

    def _proposal(self, proposed_filter):
        return PlannerProposal(
            status="ready",
            filters=[
                proposed_filter,
                ProposedFilter(
                    field="Employee_ID",
                    operator="eq",
                    value="A11017",
                    evidence_text="A11017",
                ),
            ],
            measure=ProposedMeasureChoice(
                name="distinct_dates", evidence_text="days"
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )

    def _context(self):
        return CompilationContext(
            question=self.question,
            facts=self.facts,
            resolution_context=self.resolution,
        )


if __name__ == "__main__":
    unittest.main()
