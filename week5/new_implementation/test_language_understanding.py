import unittest

from pydantic import ValidationError

from week5.new_implementation.attendance_schema import (
    AnswerContract,
    PlannerProposal,
    ProposedResultIntentChoice,
    RESULT_INTENT_DEFINITIONS,
)
from week5.new_implementation.language_understanding import (
    EmployeeClarification,
    EmployeeOption,
    InputUnderstanding,
    LocalizedAliasDefinition,
    LOCALIZED_ALIAS_DEFINITIONS,
    MissingIntentClarification,
    QuestionSurface,
    SurfaceCandidate,
    validate_localized_alias_registry,
)


class TolerantInputContractTests(unittest.TestCase):
    def test_surface_candidate_is_strict_and_rejects_invalid_evidence(self):
        candidate = SurfaceCandidate(
            candidate_id="predicate:worked:0",
            target_kind="predicate",
            target_name="worked",
            evidence_text="worked",
            evidence_span=(0, 6),
            method="exact",
            score=1.0,
        )
        self.assertEqual(candidate.evidence_span, (0, 6))

        invalid_values = (
            {"evidence_text": ""},
            {"evidence_span": (6, 0)},
            {"evidence_span": (-1, 2)},
            {"score": 1.01},
            {"unexpected": "value"},
        )
        for replacement in invalid_values:
            with self.subTest(replacement=replacement), self.assertRaises(
                ValidationError
            ):
                SurfaceCandidate.model_validate(
                    {**candidate.model_dump(), **replacement}
                )

    def test_question_surface_rejects_duplicate_candidate_ids_and_bad_spans(self):
        candidate = SurfaceCandidate(
            candidate_id="predicate:worked:0",
            target_kind="predicate",
            target_name="worked",
            evidence_text="worked",
            evidence_span=(0, 6),
            method="exact",
            score=1.0,
        )
        with self.assertRaises(ValidationError):
            QuestionSurface(
                original_text="worked",
                reply_locale="en",
                candidates=(candidate, candidate),
            )
        with self.assertRaises(ValidationError):
            QuestionSurface(
                original_text="work",
                reply_locale="en",
                candidates=(candidate,),
            )

    def test_understanding_status_and_clarification_are_consistent(self):
        option = EmployeeOption(employee_id="A10018", name="Faris Ahmed")
        clarification = EmployeeClarification(
            original_question="who is Faris",
            reply_locale="en",
            options=(option,),
            confirmation_required=True,
        )
        result = InputUnderstanding(
            status="employee_clarification",
            original_question="who is Faris",
            reply_locale="en",
            clarification=clarification,
        )
        self.assertEqual(result.clarification.kind, "employee_selection")

        invalid_cases = (
            {"status": "ready", "clarification": clarification},
            {"status": "employee_clarification", "clarification": None},
            {
                "status": "missing_intent",
                "clarification": clarification,
            },
            {
                "status": "unsupported",
                "clarification": MissingIntentClarification(
                    original_question="Faris",
                    reply_locale="en",
                    employee=option,
                ),
            },
        )
        for values in invalid_cases:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                InputUnderstanding(
                    original_question="who is Faris",
                    reply_locale="en",
                    **values,
                )

    def test_employee_profile_is_a_registered_strict_result_intent(self):
        definition = RESULT_INTENT_DEFINITIONS["employee_profile"]
        self.assertEqual(
            definition.projection,
            ("Employee_ID", "Name", "Department", "Position", "Work_Location"),
        )
        choice = ProposedResultIntentChoice(
            name="employee_profile", evidence_text="who is"
        )
        proposal = PlannerProposal(
            status="ready",
            result_intent=choice,
            answer_contract=AnswerContract(shape="profile", unit="value"),
        )
        self.assertEqual(proposal.result_intent.name, "employee_profile")

        with self.assertRaises(ValidationError):
            PlannerProposal(
                status="ready",
                answer_contract=AnswerContract(shape="profile", unit="value"),
            )
        with self.assertRaises(ValidationError):
            PlannerProposal(
                status="ready",
                result_intent=choice,
                answer_contract=AnswerContract(shape="rows", unit="value"),
            )

    def test_every_localized_alias_points_to_a_live_registry_item(self):
        validate_localized_alias_registry(LOCALIZED_ALIAS_DEFINITIONS)

        with self.assertRaises(ValueError):
            LocalizedAliasDefinition(
                locale="ar",
                target_kind="predicate",
                target_name="does_not_exist",
                phrases=("غير موجود",),
            )

    def test_alias_contract_rejects_blank_or_duplicate_phrases(self):
        with self.assertRaises(ValueError):
            LocalizedAliasDefinition(
                locale="ar",
                target_kind="predicate",
                target_name="worked",
                phrases=("حضر", "حضر"),
            )
        with self.assertRaises(ValueError):
            LocalizedAliasDefinition(
                locale="ar",
                target_kind="predicate",
                target_name="worked",
                phrases=(" ",),
            )


if __name__ == "__main__":
    unittest.main()
