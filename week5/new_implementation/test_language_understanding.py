import unittest

from pydantic import ValidationError

from week5.new_implementation.attendance_schema import (
    AnswerContract,
    PlannerProposal,
    ProposedResultIntentChoice,
    RESULT_INTENT_DEFINITIONS,
)
from week5.new_implementation.language_understanding import (
    CatalogClarification,
    CatalogOption,
    ContextChoiceClarification,
    ContextChoiceOption,
    EmployeeClarification,
    EmployeeOption,
    InputUnderstanding,
    LocalizedAliasDefinition,
    LOCALIZED_ALIAS_DEFINITIONS,
    MeaningClarification,
    MeaningOption,
    MissingIntentClarification,
    PendingConstraintSnapshot,
    PendingRequestFrame,
    QuestionSurface,
    SurfaceCandidate,
    analyze_question_surface,
    automatically_accepted_candidates,
    has_unregistered_arabic_meaning_modifier,
    is_conversation_control_reference,
    validate_localized_alias_registry,
)


class TolerantInputContractTests(unittest.TestCase):
    def test_pending_request_rejects_employee_choices_not_shown_in_clarification(
        self,
    ):
        shown = EmployeeOption(employee_id="A10001", name="Shown Person")
        hidden = EmployeeOption(employee_id="A10002", name="Hidden Person")
        clarification = EmployeeClarification(
            original_question="worked days for person",
            reply_locale="en",
            options=(shown,),
            confirmation_required=False,
        )

        with self.assertRaisesRegex(ValidationError, "employee choices"):
            PendingRequestFrame(
                original_question=clarification.original_question,
                reply_locale="en",
                clarification=clarification,
                pending_candidates=(hidden,),
            )

    def test_pending_request_rejects_catalog_choices_not_shown_in_clarification(
        self,
    ):
        clarification = CatalogClarification(
            original_question="worked days in Op",
            reply_locale="en",
            options=(
                CatalogOption(
                    option_id="1",
                    display_value="Operations",
                    field="Department",
                    value="Operations",
                ),
            ),
        )

        with self.assertRaisesRegex(ValidationError, "catalog choices"):
            PendingRequestFrame(
                original_question=clarification.original_question,
                reply_locale="en",
                clarification=clarification,
                pending_constraint=PendingConstraintSnapshot.model_validate(
                    {
                        "field": "Department",
                        "reference": "Op",
                        "candidates": (
                            {
                                "field": "Department",
                                "value": "Office",
                                "label": "Office",
                            },
                        ),
                    }
                ),
            )

    def test_pending_request_rejects_interpretations_not_shown_in_clarification(
        self,
    ):
        clarification = MeaningClarification(
            original_question="attendance for person",
            reply_locale="en",
            options=(
                MeaningOption(
                    option_id="worked_days",
                    label="Worked days",
                    target_kind="interpretation",
                    target_name="worked_days",
                ),
            ),
        )

        with self.assertRaisesRegex(ValidationError, "interpretation choices"):
            PendingRequestFrame(
                original_question=clarification.original_question,
                reply_locale="en",
                clarification=clarification,
                pending_interpretations=("scheduled_working_days",),
            )

    def test_context_choice_clarification_is_finite_and_strict(self):
        options = (
            ContextChoiceOption(option_id="context:0", label="Previous result"),
            ContextChoiceOption(option_id="context:1", label="Earlier result"),
        )
        clarification = ContextChoiceClarification(
            original_question="what about that one?",
            reply_locale="en",
            options=options,
        )

        self.assertEqual(clarification.kind, "context_choice")
        for invalid_options in ((), (options[0], options[0])):
            with (
                self.subTest(invalid_options=invalid_options),
                self.assertRaises(ValidationError),
            ):
                ContextChoiceClarification(
                    original_question="what about that one?",
                    reply_locale="en",
                    options=invalid_options,
                )
        with self.assertRaises(ValidationError):
            ContextChoiceOption(
                option_id="context:0",
                label="Previous result",
                unit_id="provider-cannot-select-runtime-ids",
            )

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
            with (
                self.subTest(replacement=replacement),
                self.assertRaises(ValidationError),
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

    def test_employee_clarification_preserves_resolved_scope_and_source_reference(self):
        resolved = EmployeeOption(employee_id="A10001", name="Morgan River")
        option = EmployeeOption(employee_id="A10002", name="Sam North")
        question = "worked days for Morgan River and A100022"
        start = question.index("A100022")

        clarification = EmployeeClarification(
            original_question=question,
            reply_locale="en",
            options=(option,),
            resolved_options=(resolved,),
            reference_text="A100022",
            reference_span=(start, start + len("A100022")),
            confirmation_required=True,
        )

        self.assertEqual(clarification.resolved_options, (resolved,))
        self.assertEqual(
            question[slice(*clarification.reference_span)],
            clarification.reference_text,
        )
        with self.assertRaises(ValidationError):
            EmployeeClarification.model_validate(
                {
                    **clarification.model_dump(),
                    "reference_span": (0, len("A100022")),
                }
            )

    def test_former_and_latter_controls_are_never_person_references(self):
        for control in (
            "former",
            "the latter",
            "السابق",
            "السابقة",
            "الأول",
            "الثانية",
            "الأخير",
            "الأخيرة",
        ):
            with self.subTest(control=control):
                self.assertTrue(is_conversation_control_reference(control))

    def test_meaning_clarification_rejects_evidence_outside_saved_question(self):
        option = MeaningOption(
            option_id="predicate:worked:0:5:fuzzy",
            label="worked",
            target_kind="predicate",
            target_name="worked",
            evidence_text="absent",
            evidence_span=(0, 6),
        )

        with self.assertRaises(ValidationError):
            MeaningClarification(
                original_question="workd",
                reply_locale="en",
                options=(option,),
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


class QuestionSurfaceAnalysisTests(unittest.TestCase):
    def test_english_normalization_preserves_original_text_and_offsets(self):
        question = "  WOrKeD,   DAYS?!  "
        surface = analyze_question_surface(question)

        self.assertEqual(surface.original_text, question)
        self.assertEqual(surface.reply_locale, "en")
        interpretation = next(
            candidate
            for candidate in surface.candidates
            if candidate.target_kind == "interpretation"
            and candidate.target_name == "worked_days"
        )
        start, end = interpretation.evidence_span
        self.assertEqual(question[start:end], interpretation.evidence_text)
        self.assertEqual(interpretation.method, "exact")

    def test_arabic_normalization_handles_diacritics_tatweel_and_digits(self):
        question = "أَيّام الـغــياب للموظف A10018 في ١٤/٠٩/٢٠٢٦"
        surface = analyze_question_surface(question)

        self.assertEqual(surface.reply_locale, "ar")
        self.assertTrue(
            any(
                candidate.target_kind == "interpretation"
                and candidate.target_name == "absent_days"
                and candidate.method == "localized_alias"
                for candidate in surface.candidates
            )
        )
        self.assertEqual(surface.original_text, question)

    def test_arabic_exclusion_prefix_never_accepts_positive_absence_meaning(self):
        for question in (
            "بدون أيام الغياب",
            "لا أيام الغياب",
            "استبعد أيام الغياب",
            "لا أريد أيام الغياب",
            "لا تعرض أيام الغياب",
            "بدون عرض أيام الغياب",
            "استبعد لي أيام الغياب",
            "لا أريد أن تعرض لي أي أيام الغياب",
        ):
            with self.subTest(question=question):
                accepted = automatically_accepted_candidates(
                    analyze_question_surface(question)
                )
                self.assertFalse(
                    [
                        candidate
                        for candidate in accepted
                        if candidate.target_name in {"absent", "absent_days"}
                    ]
                )

    def test_long_reversal_prefixes_are_detected_within_current_clause(self):
        for question in (
            "لا أريد أن تعرض لي أي أيام الغياب",
            "I do not want you to show me any absent days",
        ):
            with self.subTest(question=question):
                self.assertTrue(has_unregistered_arabic_meaning_modifier(question))

    def test_meaning_reversal_does_not_cross_a_fresh_clause_boundary(self):
        for question in (
            "Do not show late days, show absent days",
            "Do not show late days but show absent days",
            "لا تعرض أيام التأخير، اعرض أيام الغياب",
            "لا تعرض أيام التأخير ثم اعرض أيام الغياب",
        ):
            with self.subTest(question=question):
                self.assertFalse(has_unregistered_arabic_meaning_modifier(question))
                accepted = automatically_accepted_candidates(
                    analyze_question_surface(question)
                )
                self.assertTrue(
                    [
                        candidate
                        for candidate in accepted
                        if candidate.target_name in {"absent", "absent_days"}
                    ]
                )

    def test_registered_negative_predicates_and_operators_are_not_modifiers(self):
        for question in (
            "How many days did not work?",
            "Count records where Status does not contain Authorized",
            "Count records where Department is not Engineering and Status is Authorized",
        ):
            with self.subTest(question=question):
                self.assertFalse(has_unregistered_arabic_meaning_modifier(question))

    def test_longer_arabic_meaning_suppresses_conflicting_nested_alias(self):
        surface = analyze_question_surface("كم عدد أيام العمل المجدولة؟")

        accepted = automatically_accepted_candidates(surface)

        self.assertTrue(
            any(
                candidate.target_kind == "interpretation"
                and candidate.target_name == "scheduled_working_days"
                for candidate in accepted
            )
        )
        self.assertFalse(
            any(
                candidate.target_kind == "interpretation"
                and candidate.target_name == "worked_days"
                for candidate in accepted
            )
        )

    def test_mixed_language_locale_uses_alphabetic_token_majority(self):
        self.assertEqual(
            analyze_question_surface("Faris أيام الغياب").reply_locale,
            "ar",
        )
        self.assertEqual(
            analyze_question_surface("worked days للموظف Faris").reply_locale,
            "en",
        )

    def test_uniquely_dominant_high_confidence_typo_is_automatic(self):
        surface = analyze_question_surface("Faris wokred days")
        accepted = automatically_accepted_candidates(surface)

        correction = next(
            candidate
            for candidate in accepted
            if candidate.target_kind == "interpretation"
            and candidate.target_name == "worked_days"
        )
        self.assertEqual(correction.method, "fuzzy")
        self.assertGreaterEqual(correction.score, 0.90)

    def test_low_confidence_or_tied_typo_is_not_automatic(self):
        surface = analyze_question_surface("Faris attendence")
        self.assertFalse(
            [
                candidate
                for candidate in automatically_accepted_candidates(surface)
                if candidate.method == "fuzzy"
            ]
        )

    def test_protected_values_and_logic_are_never_fuzzy_corrected(self):
        protected_questions = (
            "employee A1001B",
            "on 31/13/2026",
            "more than 12.5 hours",
            "not wokred",
            "worked nad absent",
            "worked or absent",
        )
        for question in protected_questions:
            with self.subTest(question=question):
                fuzzy_evidence = {
                    candidate.evidence_text.casefold()
                    for candidate in analyze_question_surface(question).candidates
                    if candidate.method == "fuzzy"
                }
                self.assertFalse(
                    fuzzy_evidence
                    & {"a1001b", "31/13/2026", "12.5", "wokred", "nad", "or"}
                )


if __name__ == "__main__":
    unittest.main()
