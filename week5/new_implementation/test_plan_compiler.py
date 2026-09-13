import unittest

from week5.new_implementation.attendance_schema import (
    AnswerContract,
    PlannerProposal,
    ProposedCalculation,
    ProposedFilter,
    ProposedMeasureChoice,
    ProposedPredicateChoice,
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
    def test_fieldless_unsupported_predicate_operator_rejects_measure_only_plan(self):
        question = "How many attendance records match Authorized?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            measure=ProposedMeasureChoice(
                name="attendance_records", evidence_text="attendance records"
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="records", subject_field=None, grain=[]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn(
            "unsupported_capability", {item.code for item in result.violations}
        )

    def test_unsupported_or_malformed_constraint_rejects_measure_only_plan(self):
        cases = (
            "How many days have status matches Authorized?",
            "How many days have Date equal to 2026-02-30?",
            "How many days have worked hours above bananas?",
        )

        for question in cases:
            with self.subTest(question=question):
                resolution = ResolutionContext({})
                proposal = PlannerProposal(
                    status="ready",
                    measure=ProposedMeasureChoice(
                        name="distinct_dates", evidence_text="days"
                    ),
                    answer_contract=AnswerContract(
                        shape="scalar",
                        unit="dates",
                        subject_field="Date",
                        grain=["Date"],
                    ),
                )

                result = compile_proposal(
                    proposal,
                    CompilationContext(
                        question,
                        detect_semantic_facts(question, resolution),
                        resolution,
                    ),
                )

                self.assertFalse(result.ready)
                self.assertIn(
                    "unsupported_capability",
                    {violation.code for violation in result.violations},
                )

    def test_numeric_worked_hours_constraint_compiles_without_work_predicate(self):
        question = "How many days had worked hours above 2?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            filters=[
                ProposedFilter(
                    field="Total_Worked_Hrs",
                    operator="gt",
                    value=2,
                    evidence_text="worked hours above 2",
                )
            ],
            measure=ProposedMeasureChoice(name="distinct_dates", evidence_text="days"),
            answer_contract=AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertTrue(result.ready, result.violations)
        self.assertEqual(
            result.executable_plan.filters[0].model_dump(),
            {"field": "Total_Worked_Hrs", "operator": "gt", "value": 2.0},
        )
        self.assertEqual(result.executable_plan.business_predicates, [])

    def test_omitting_independent_positive_work_occurrence_is_rejected(self):
        question = "How many days did not work and work?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            measure=ProposedMeasureChoice(name="distinct_dates", evidence_text="days"),
            business_predicates=[
                ProposedPredicateChoice(name="not_worked", evidence_text="not work")
            ],
            answer_contract=AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn("uncovered_fact", {item.code for item in result.violations})

    def test_percentage_condition_is_canonicalized_grounded_and_preserved(self):
        question = (
            "What percentage of all attendance records have Status equal to Authorized?"
        )
        resolution = ResolutionContext({})
        facts = detect_semantic_facts(question, resolution)
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="percentage",
                field=None,
                percentage_condition=ProposedFilter(
                    field="Status",
                    operator="eq",
                    value="authorized",
                    evidence_text="Status equal to Authorized",
                ),
                evidence_text="percentage",
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="percentage", subject_field=None, grain=[]
            ),
        )

        result = compile_proposal(
            proposal, CompilationContext(question, facts, resolution)
        )

        self.assertTrue(result.ready, result.violations)
        self.assertEqual(
            result.executable_plan.percentage_condition.model_dump(),
            {"field": "Status", "operator": "eq", "value": "Authorized"},
        )

    def test_unresolvable_percentage_condition_fails_during_compilation(self):
        question = "What percentage have Status equal to Not A Real Status?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="percentage",
                field=None,
                percentage_condition=ProposedFilter(
                    field="Status",
                    operator="eq",
                    value="Not A Real Status",
                    evidence_text="Status equal to Not A Real Status",
                ),
                evidence_text="percentage",
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="percentage", subject_field=None, grain=[]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn("invalid_schema", {item.code for item in result.violations})

    def test_wrong_calculation_operation_is_rejected(self):
        question = "What is average Lateness_Hrs?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="max",
                field="Lateness_Hrs",
                evidence_text="average Lateness_Hrs",
            ),
            answer_contract=AnswerContract(
                shape="scalar",
                unit="hours",
                subject_field="Lateness_Hrs",
                grain=["Lateness_Hrs"],
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertEqual(
            {item.code for item in result.violations},
            {"ungrounded_constraint", "uncovered_fact"},
        )

    def test_requested_grouping_cannot_be_omitted(self):
        question = "What is average Lateness_Hrs by Department?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="average",
                field="Lateness_Hrs",
                evidence_text="average Lateness_Hrs",
            ),
            answer_contract=AnswerContract(
                shape="scalar",
                unit="hours",
                subject_field="Lateness_Hrs",
                grain=["Lateness_Hrs"],
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn("uncovered_fact", {item.code for item in result.violations})

    def test_numeric_calculation_rejects_non_numeric_field_before_execution(self):
        question = "What is the sum of Date?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="sum", field="Date", evidence_text="sum of Date"
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="value", subject_field="Date", grain=["Date"]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn("invalid_schema", {item.code for item in result.violations})

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
            measure=ProposedMeasureChoice(name="distinct_dates", evidence_text="days"),
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
