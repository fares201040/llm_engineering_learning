import unittest

from pydantic import ValidationError

from week5.new_implementation import attendance_schema as schema
from week5.new_implementation.plan_compiler import CompilationContext, compile_proposal
from week5.new_implementation.semantic_resolution import (
    ResolutionContext,
    detect_semantic_facts,
)


class PlannerDecisionBoundaryTests(unittest.TestCase):
    def test_provider_decision_has_no_executable_plan_slots(self):
        with self.assertRaises(ValidationError):
            schema.PlannerDecision.model_validate(
                {"status": "resolved", "filters": [{"field": "Status"}]}
            )

    def test_decision_rejects_duplicate_or_structurally_inconsistent_selections(self):
        with self.assertRaises(ValidationError):
            schema.PlannerDecision.model_validate(
                {
                    "status": "resolved",
                    "selections": [
                        {"need_id": "need-0", "candidate_id": "candidate-0"},
                        {"need_id": "need-0", "candidate_id": "candidate-1"},
                    ],
                }
            )

    def test_decision_must_select_only_request_local_candidates(self):
        from week5.new_implementation.planning_decisions import (
            PlanningCandidate,
            PlanningDecisionError,
            PlanningDraft,
            PlanningNeed,
            validate_planner_decision,
        )

        draft = PlanningDraft(
            question="Synthetic ambiguous attendance request",
            facts=(),
            needs=(
                PlanningNeed(
                    need_id="need-0",
                    kind="interpretation",
                    candidates=(PlanningCandidate("candidate-0", 0),),
                ),
            ),
        )
        decision = schema.PlannerDecision(
            status="resolved",
            selections=[
                schema.PlannerDecisionSelection(
                    need_id="need-0", candidate_id="invented-candidate"
                )
            ],
        )

        with self.assertRaises(PlanningDecisionError):
            validate_planner_decision(draft, decision)
        with self.assertRaises(ValidationError):
            schema.PlannerDecision.model_validate(
                {
                    "status": "unsupported",
                    "unsupported_capabilities": [],
                }
            )


class DeterministicPlanningDraftTests(unittest.TestCase):
    def _assemble(self, question, catalog=None):
        from week5.new_implementation.planning_decisions import (
            assemble_grounded_proposal,
            build_planning_draft,
        )

        context = ResolutionContext(catalog or {})
        facts = detect_semantic_facts(question, context)
        draft = build_planning_draft(question, facts)
        self.assertEqual(draft.needs, ())
        proposal = assemble_grounded_proposal(draft)
        compiled = compile_proposal(
            proposal,
            CompilationContext(question, facts, context),
        )
        self.assertTrue(compiled.ready, compiled.violations)
        return proposal, compiled.executable_plan

    def test_registered_worked_days_are_fully_deterministic(self):
        proposal, plan = self._assemble("How many days were worked?")

        self.assertEqual(proposal.measure.name, "distinct_dates")
        self.assertEqual(
            [item.name for item in proposal.business_predicates], ["worked"]
        )
        self.assertEqual(plan.answer_contract.unit, "dates")

    def test_registered_percentage_keeps_numerator_out_of_denominator(self):
        proposal, plan = self._assemble(
            "What percentage of records are Authorized?",
            {"Status": ("Authorized", "Draft")},
        )

        self.assertEqual(proposal.calculation.operation, "percentage")
        self.assertEqual(proposal.calculation.percentage_condition.field, "Status")
        self.assertEqual(proposal.filters, [])
        self.assertEqual(plan.answer_contract.unit, "percentage")

    def test_projection_temporal_scope_is_assembled_without_provider_choices(self):
        proposal, plan = self._assemble(
            "Show Date and Status from records on 2026-09-01"
        )

        self.assertEqual(
            [item.field for item in proposal.projection], ["Date", "Status"]
        )
        self.assertEqual(plan.projection, ["Date", "Status"])
        self.assertIn(
            schema.FilterCondition(field="Date", operator="eq", value="2026-09-01"),
            plan.filters,
        )

    def test_semantic_intent_assembles_a_narrative_contract(self):
        proposal, plan = self._assemble("Which attendance records look abnormal?")

        self.assertEqual(proposal.answer_contract.shape, "narrative")
        self.assertEqual(plan.mode, "semantic")

    def test_unsupported_structure_never_assembles_a_partial_ready_proposal(self):
        from week5.new_implementation.planning_decisions import (
            assemble_grounded_proposal,
            build_planning_draft,
        )

        question = "Count records where Status is Authorized or Exception is Absent"
        context = ResolutionContext(
            {"Status": ("Authorized",), "Exception": ("Absent",)}
        )
        facts = detect_semantic_facts(question, context)
        draft = build_planning_draft(question, facts)

        proposal = assemble_grounded_proposal(draft)

        self.assertEqual(proposal.status, "unsupported")
        self.assertTrue(proposal.unsupported_capabilities)

    def test_registered_operation_families_compile_without_planner_decisions(self):
        cases = (
            ("How many scheduled working days?", "distinct_count", "Date"),
            ("How many scheduled days did not attend?", "distinct_count", "Date"),
            ("How many absent days?", "distinct_count", "Date"),
            ("How many zero worked hours dates?", "distinct_count", "Date"),
            ("Count Authorized records", "count", None),
            ("Sum total overtime", "sum", "Total_OT"),
            ("Average worked hours by Department", "average", "Total_Worked_Hrs"),
            ("Show latest 2 attendance records", "none", None),
        )
        catalog = {
            "Status": ("Authorized", "Draft"),
            "Department": ("Operations", "Services"),
        }

        for question, operation, field in cases:
            with self.subTest(question=question):
                _proposal, plan = self._assemble(question, catalog)
                self.assertEqual(
                    (plan.aggregation, plan.aggregation_field), (operation, field)
                )


if __name__ == "__main__":
    unittest.main()
