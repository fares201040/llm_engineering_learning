from dataclasses import dataclass
import unittest

from week5.new_implementation.attendance_schema import (
    BUSINESS_PREDICATE_DEFINITIONS,
    FIELD_DEFINITIONS,
    VALUE_CONCEPT_DEFINITIONS,
    AnswerContract,
    FilterOperator,
    PlannerProposal,
    ProposedFilter,
)
from week5.new_implementation.plan_compiler import CompilationContext, compile_proposal
from week5.new_implementation.semantic_resolution import (
    EmployeeReference,
    ResolutionContext,
    detect_semantic_facts,
    evidence_occurs,
)


@dataclass(frozen=True)
class SemanticMatrixCase:
    question: str
    proposal: PlannerProposal
    expected_field: str | None
    expected_operator: FilterOperator | None
    expected_value: object | None
    context: ResolutionContext


def generate_registry_cases() -> tuple[SemanticMatrixCase, ...]:
    cases = []
    for field, definition in FIELD_DEFINITIONS.items():
        if not definition.planner_visible or not definition.filterable:
            continue
        phrase = definition.natural_names[0]
        catalog = {}
        employees = ()
        if definition.resolution_kind == "free_text":
            continue
        if definition.resolution_kind == "closed_value":
            semantic_phrases = tuple(
                phrase
                for registry in (
                    BUSINESS_PREDICATE_DEFINITIONS,
                    VALUE_CONCEPT_DEFINITIONS,
                )
                for item in registry.values()
                for phrase in item.natural_names
            )
            value = min(
                definition.closed_values,
                key=lambda candidate: sum(
                    evidence_occurs(candidate, phrase) for phrase in semantic_phrases
                ),
            )
        elif definition.resolution_kind == "catalog":
            value = f"{field} Sample"
            catalog[field] = (value,)
        elif definition.resolution_kind == "identifier":
            value = "A10001"
        elif definition.resolution_kind == "entity":
            value = "Registry Person"
            employees = (EmployeeReference(employee_id="A10001", name=value),)
        elif definition.resolution_kind == "numeric":
            value = 2.0
        else:
            value = "2026-09-01"
        evidence = str(value)
        question = f"show {phrase} {evidence}"
        proposal = PlannerProposal(
            status="ready",
            filters=[
                ProposedFilter(
                    field=field,
                    operator="eq",
                    value=value,
                    evidence_text=evidence,
                )
            ],
            answer_contract=AnswerContract(
                shape="scalar", unit="value", subject_field=None, grain=[]
            ),
        )
        cases.append(
            SemanticMatrixCase(
                question,
                proposal,
                field,
                "eq",
                value,
                ResolutionContext(catalog=catalog, employees=employees),
            )
        )
    return tuple(cases)


class SemanticMatrixTests(unittest.TestCase):
    def test_representative_filter_for_every_planner_visible_field(self):
        covered = set()
        for case in generate_registry_cases():
            with self.subTest(field=case.expected_field):
                facts = detect_semantic_facts(case.question, case.context)
                result = compile_proposal(
                    case.proposal,
                    CompilationContext(case.question, facts, case.context),
                )
                self.assertTrue(result.ready, result.violations)
                condition = result.executable_plan.filters[0]
                self.assertEqual(condition.field, case.expected_field)
                self.assertEqual(condition.operator, case.expected_operator)
                self.assertEqual(condition.value, case.expected_value)
                covered.add(case.expected_field)
        expected = {
            field
            for field, definition in FIELD_DEFINITIONS.items()
            if definition.planner_visible
            and definition.filterable
            and definition.resolution_kind != "free_text"
        }
        self.assertEqual(covered, expected)

    def test_every_additional_field_name_detects_its_registered_field(self):
        for field, definition in FIELD_DEFINITIONS.items():
            if not definition.planner_visible:
                continue
            for phrase in definition.natural_names:
                with self.subTest(field=field, phrase=phrase):
                    facts = detect_semantic_facts(phrase, ResolutionContext({}))
                    self.assertTrue(
                        any(item.kind == "field" and item.field == field for item in facts)
                    )

    def test_internal_field_cannot_cross_planner_boundary(self):
        proposal = PlannerProposal(
            status="ready",
            filters=[
                ProposedFilter(
                    field="chunk_type",
                    operator="eq",
                    value="record",
                    evidence_text="chunk type record",
                )
            ],
            answer_contract=AnswerContract(
                shape="scalar", unit="value", subject_field=None, grain=[]
            ),
        )
        context = ResolutionContext({})
        result = compile_proposal(
            proposal,
            CompilationContext(
                "chunk type record", detect_semantic_facts("chunk type record", context), context
            ),
        )
        self.assertFalse(result.ready)
        self.assertIn("invalid_schema", {item.code for item in result.violations})

    def test_free_text_field_routes_with_semantic_intent(self):
        question = "unusual employee remarks"
        context = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            answer_contract=AnswerContract(
                shape="narrative", unit="value", subject_field=None, grain=[]
            ),
        )
        result = compile_proposal(
            proposal,
            CompilationContext(question, detect_semantic_facts(question, context), context),
        )
        self.assertTrue(result.ready, result.violations)
        self.assertEqual(result.executable_plan.mode, "semantic")


if __name__ == "__main__":
    unittest.main()
