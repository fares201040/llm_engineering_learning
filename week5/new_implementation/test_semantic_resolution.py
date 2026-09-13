import unittest
from datetime import date

from week5.new_implementation.attendance_schema import FIELD_DEFINITIONS
from week5.new_implementation.semantic_resolution import (
    EmployeeReference,
    ResolutionContext,
    ResolverRegistry,
    SemanticFact,
    detect_semantic_facts,
    evidence_occurs,
    merge_semantic_facts,
)


class TemporalCompositionDetectorTests(unittest.TestCase):
    def test_non_temporal_on_preserves_employee_and_catalog_constraint(self):
        for question, field, value in (
            (
                "Count records for morgan river on Leave Type Annual Leave",
                "Leave_Type",
                "Annual Leave",
            ),
            (
                "Count records for morgan river on Department Human Resources",
                "Department",
                "Human Resources",
            ),
        ):
            with self.subTest(question=question):
                facts = detect_semantic_facts(
                    question,
                    ResolutionContext(
                        {field: (value,)},
                        employees=(
                            EmployeeReference(
                                employee_id="A10018", name="Morgan River"
                            ),
                        ),
                    ),
                )
                self.assertEqual(
                    [(f.evidence_text, f.values) for f in facts if f.kind == "entity"],
                    [("morgan river", ("A10018",))],
                )
                self.assertIn(
                    (field, "eq", (value,)),
                    {
                        (f.field, f.operator, f.values)
                        for f in facts
                        if f.kind == "filter"
                    },
                )

    def test_temporal_evidence_preserves_prior_constraints(self):
        for question, expected in (
            (
                "Count Authorized records before 2026-09-03",
                ("predicate", "authorized", None, ()),
            ),
            (
                "Count records for Department Human Resources before 2026-09-03",
                ("filter", None, "Department", ("Human Resources",)),
            ),
        ):
            with self.subTest(question=question):
                facts = detect_semantic_facts(
                    question, ResolutionContext({"Department": ("Human Resources",)})
                )
                self.assertIn(
                    expected,
                    {(f.kind, f.concept_name, f.field, f.values) for f in facts},
                )
                date_fact = next(
                    f for f in facts if f.kind == "filter" and f.field == "Date"
                )
                self.assertEqual(date_fact.evidence_text, "before 2026-09-03")
                self.assertEqual(
                    question[slice(*date_fact.evidence_span)], date_fact.evidence_text
                )

    def test_name_boundary_preserves_complete_temporal_negation(self):
        question = "Count records for morgan river not before 2026-09-03"
        facts = detect_semantic_facts(question, ResolutionContext({}))
        self.assertEqual(
            [f.evidence_text for f in facts if f.kind == "entity"], ["morgan river"]
        )
        self.assertIn(
            ("Date", "gte", ("2026-09-03",)),
            {(f.field, f.operator, f.values) for f in facts},
        )

    def test_name_boundary_handles_field_positions_and_calendar_references(self):
        for expression in (
            "not before Date 2026-09-03",
            "Date on or after 2026-09-03",
            "before today",
        ):
            with self.subTest(expression=expression):
                facts = detect_semantic_facts(
                    f"Count records for morgan river {expression}",
                    ResolutionContext({}),
                )
                self.assertEqual(
                    [f.evidence_text for f in facts if f.kind == "entity"],
                    ["morgan river"],
                )


class EntityTemporalReviewRegressionTests(unittest.TestCase):
    def test_temporal_reference_spans_are_not_lowercase_employee_names(self):
        for phrase in ("last month", "September 2026", "this week"):
            with self.subTest(phrase=phrase):
                facts = detect_semantic_facts(
                    f"Show records for {phrase}", ResolutionContext({})
                )
                self.assertFalse(any(f.kind == "entity" for f in facts))

    def test_entity_mask_preserves_independent_month_occurrence(self):
        facts = detect_semantic_facts(
            "Count May's records before May 3, 2026",
            ResolutionContext(
                {}, employees=(EmployeeReference(employee_id="A10017", name="May"),)
            ),
        )
        self.assertIn(
            ("Date", "lt", ("2026-05-03",)),
            {(f.field, f.operator, f.values) for f in facts},
        )
        self.assertEqual(len([f for f in facts if f.kind == "entity"]), 1)

    def test_catalog_field_and_value_spans_are_not_employee_candidates(self):
        facts = detect_semantic_facts(
            "Count records for Department Human Resources",
            ResolutionContext({"Department": ("Human Resources",)}),
        )
        self.assertFalse(any(f.kind == "entity" for f in facts))
        self.assertIn(
            ("Department", ("Human Resources",)),
            {(f.field, f.values) for f in facts if f.kind == "filter"},
        )

    def test_complete_temporal_operator_expressions_preserve_meaning(self):
        for expression, operator in (
            ("not before 2026-09-03", "gte"),
            ("not after 2026-09-03", "lte"),
            ("Date > 2026-09-03", "gt"),
            ("Date <= 2026-09-03", "lte"),
            ("before Date 2026-09-03", "lt"),
            ("on or after Date 2026-09-03", "gte"),
        ):
            with self.subTest(expression=expression):
                facts = detect_semantic_facts(
                    f"Count records {expression}", ResolutionContext({})
                )
                self.assertEqual(
                    [(f.operator, f.values) for f in facts if f.kind == "filter"],
                    [(operator, ("2026-09-03",))],
                )
        for expression in (
            "Date approximately 2026-09-03",
            "not not before 2026-09-03",
            "Date >< 2026-09-03",
        ):
            with self.subTest(expression=expression):
                self.assertTrue(
                    any(
                        f.kind == "unsupported"
                        for f in detect_semantic_facts(
                            f"Count records {expression}", ResolutionContext({})
                        )
                    )
                )

    def test_date_candidate_shapes_cannot_disappear(self):
        for literal in ("09/03/26", "2026-09-03xyz", "2026-09-03/extra"):
            with self.subTest(literal=literal):
                facts = detect_semantic_facts(
                    f"Count records before {literal}", ResolutionContext({})
                )
                self.assertTrue(
                    any(
                        f.kind == "unsupported" and literal in f.evidence_text
                        for f in facts
                    )
                )
        facts = detect_semantic_facts(
            "Count records from Sept 1, 2026 to Sept 3, 2026", ResolutionContext({})
        )
        self.assertEqual(
            [(f.operator, f.values) for f in facts if f.kind == "filter"],
            [("gte", ("2026-09-01",)), ("lte", ("2026-09-03",))],
        )

    def test_every_temporal_literal_is_bound_and_validated_independently(self):
        for question in (
            "Count records with Date 2026-09-03 after 09:00",
            "Count records with last_Updated_date 2026-02-30",
            "Count records with last_Updated_date 2026-02-30T09:00:00",
        ):
            with self.subTest(question=question):
                self.assertTrue(
                    any(
                        f.kind == "unsupported"
                        for f in detect_semantic_facts(question, ResolutionContext({}))
                    )
                )
        facts = detect_semantic_facts(
            "Count records with Date 2026-09-03 and Actual_From_Time after 09:00",
            ResolutionContext({}),
        )
        self.assertIn(
            ("Actual_From_Time", "gt", ("09:00:00",)),
            {(f.field, f.operator, f.values) for f in facts},
        )

    def test_yearless_endpoint_inherits_explicit_paired_year(self):
        for phrase in (
            "from September 1, 2025 to September 10",
            "between September 1, 2025 and September 10",
        ):
            with self.subTest(phrase=phrase):
                facts = detect_semantic_facts(
                    f"Count records {phrase}",
                    ResolutionContext({}, reference_date=date(2026, 9, 13)),
                )
                self.assertEqual(
                    [(f.operator, f.values) for f in facts if f.kind == "filter"],
                    [("gte", ("2025-09-01",)), ("lte", ("2025-09-10",))],
                )


class SemanticResolutionTests(unittest.TestCase):
    def test_named_employee_reference_survives_authorized_count_planning(self):
        context = ResolutionContext(
            catalog={},
            employees=(
                EmployeeReference(employee_id="A10017", name="Selected Employee"),
            ),
        )
        facts = detect_semantic_facts(
            "Count Selected Employee's Authorized records.", context
        )
        self.assertIn(
            ("entity", ("A10017",), "Selected Employee"),
            {(fact.kind, fact.values, fact.evidence_text) for fact in facts},
        )

    def test_unresolved_possessive_name_is_an_entity_candidate(self):
        facts = detect_semantic_facts(
            "Count Morgan River's records.", ResolutionContext({})
        )
        self.assertIn(
            ("entity", "Morgan River", "candidate"),
            {(fact.kind, fact.evidence_text, fact.strength) for fact in facts},
        )

    def test_identifier_candidates_reject_malformed_tokens(self):
        for token in ("A10017", "A1001", "AA10017", "A10017extra", "A-10017"):
            with self.subTest(token=token):
                facts = detect_semantic_facts(
                    f"Count records for {token}.", ResolutionContext({})
                )
                if token == "A10017":
                    self.assertTrue(
                        any(f.kind == "entity" and f.values == (token,) for f in facts)
                    )

                else:
                    self.assertTrue(
                        any(
                            f.kind == "unsupported" and f.strength == "strong"
                            for f in facts
                        )
                    )

    def test_possessive_id_does_not_create_an_unresolved_name(self):
        facts = detect_semantic_facts(
            "Count A10017's Authorized records.", ResolutionContext({})
        )
        self.assertEqual(
            [(fact.field, fact.values) for fact in facts if fact.kind == "entity"],
            [("Employee_ID", ("A10017",))],
        )

    def test_before_date_emits_temporal_comparison_fact(self):
        facts = detect_semantic_facts(
            "Count records before 2026-09-03", ResolutionContext(catalog={})
        )
        self.assertIn(
            ("Date", "lt", ("2026-09-03",)),
            {(f.field, f.operator, f.values) for f in facts},
        )

    def test_temporal_literals_and_comparisons_are_canonical(self):
        for phrase, operator, value in (
            ("on 2026-09-03", "eq", "2026-09-03"),
            ("after September 3, 2026", "gt", "2026-09-03"),
            ("on or before 3 September 2026", "lte", "2026-09-03"),
            ("on or after 2026-09-03", "gte", "2026-09-03"),
        ):
            with self.subTest(phrase=phrase):
                facts = detect_semantic_facts(
                    f"Count records {phrase}.", ResolutionContext({})
                )
                self.assertEqual(
                    [
                        (f.field, f.operator, f.values)
                        for f in facts
                        if f.kind == "filter"
                    ],
                    [("Date", operator, (value,))],
                )

    def test_ambiguous_and_invalid_temporal_literals_fail_closed(self):
        for literal in ("09/03/2026", "2026-02-30", "February 30, 2026", "2026-13-03"):
            with self.subTest(literal=literal):
                facts = detect_semantic_facts(
                    f"Count records before {literal}.", ResolutionContext({})
                )
                self.assertTrue(
                    any(
                        f.kind == "unsupported" and f.strength == "strong"
                        for f in facts
                    )
                )

    def test_named_subject_without_possession_is_detected(self):
        facts = detect_semantic_facts(
            "Count records for Morgan River.", ResolutionContext({})
        )
        self.assertTrue(
            any(f.kind == "entity" and f.evidence_text == "Morgan River" for f in facts)
        )

    def test_entity_span_does_not_erase_a_prefix_of_a_catalog_value(self):
        context = ResolutionContext(
            {"Leave_Type": ("Annual Leave",)},
            employees=(EmployeeReference(employee_id="A10017", name="Ann"),),
        )
        facts = detect_semantic_facts("Count Ann's Annual Leave records.", context)
        self.assertTrue(
            any(
                f.field == "Leave_Type" and f.values == ("Annual Leave",) for f in facts
            )
        )

    def test_explicit_date_field_and_range_bind_their_operators(self):
        for question, expected in (
            (
                "Count records with Actual_From_Date before 2026-09-03",
                [("Actual_From_Date", "lt", ("2026-09-03",))],
            ),
            (
                "Count records from 2026-09-01 to 2026-09-03",
                [("Date", "gte", ("2026-09-01",)), ("Date", "lte", ("2026-09-03",))],
            ),
        ):
            with self.subTest(question=question):
                facts = detect_semantic_facts(question, ResolutionContext({}))
                self.assertEqual(
                    [
                        (f.field, f.operator, f.values)
                        for f in facts
                        if f.kind == "filter"
                    ],
                    expected,
                )

    def test_unrepresentable_and_reversed_temporal_constraints_fail_closed(self):
        for question in (
            "Count records after 09:00",
            "Count records from 2026-09-03 to 2026-09-01",
        ):
            with self.subTest(question=question):
                facts = detect_semantic_facts(question, ResolutionContext({}))
                self.assertTrue(any(f.kind == "unsupported" for f in facts))

    def test_yearless_invalid_and_reversed_dates_do_not_disappear(self):
        for question in (
            "Count records before February 30",
            "Count records between September 8 and September 3",
        ):
            with self.subTest(question=question):
                facts = detect_semantic_facts(question, ResolutionContext({}))
                self.assertTrue(any(f.kind == "unsupported" for f in facts))

    def test_temporal_literals_bind_to_their_own_field_occurrences(self):
        facts = detect_semantic_facts(
            "Count records with Date before 2026-09-03 and Actual_From_Date after 2026-09-01",
            ResolutionContext({}),
        )
        self.assertEqual(
            [(f.field, f.operator, f.values) for f in facts if f.kind == "filter"],
            [
                ("Date", "lt", ("2026-09-03",)),
                ("Actual_From_Date", "gt", ("2026-09-01",)),
            ],
        )

    def test_worked_hours_numeric_constraint_outranks_embedded_work_predicate(self):
        facts = detect_semantic_facts(
            "How many days had worked hours above 2?",
            ResolutionContext(catalog={}),
        )

        self.assertIn(
            ("Total_Worked_Hrs", "gt", (2.0,)),
            {
                (fact.field, fact.operator, fact.values)
                for fact in facts
                if fact.kind == "filter"
            },
        )
        self.assertNotIn(
            "worked",
            {fact.concept_name for fact in facts if fact.kind == "predicate"},
        )

    def test_independent_positive_work_occurrence_survives_negated_occurrence(self):
        facts = detect_semantic_facts(
            "How many days did not work and work?",
            ResolutionContext(catalog={}),
        )

        self.assertEqual(
            {fact.concept_name for fact in facts if fact.kind == "predicate"},
            {"worked", "not_worked"},
        )

    def test_work_language_emits_positive_work_predicate(self):
        cases = (
            "How many days did employee A10017 work?",
            "How many days did employee A10017 worked?",
            "How many days did employee A10017 attend?",
            "How many days did employee A10017 attended?",
            "How many days had positive worked hours?",
        )

        for question in cases:
            with self.subTest(question=question):
                facts = detect_semantic_facts(question, ResolutionContext(catalog={}))
                predicates = {
                    fact.concept_name for fact in facts if fact.kind == "predicate"
                }
                self.assertIn("worked", predicates)
                self.assertNotIn("not_worked", predicates)

    def test_non_work_language_is_compositional_without_positive_collision(self):
        cases = (
            (
                "How many days had zero worked hours?",
                {"not_worked"},
            ),
            (
                "How many scheduled days did A10017 not attend?",
                {"scheduled_working_day", "not_worked"},
            ),
        )

        for question, expected in cases:
            with self.subTest(question=question):
                facts = detect_semantic_facts(question, ResolutionContext(catalog={}))
                predicates = {
                    fact.concept_name for fact in facts if fact.kind == "predicate"
                }
                self.assertEqual(predicates, expected)
                self.assertNotIn("worked", predicates)

    def test_scheduled_and_off_day_exclusion_language_emit_schedule_predicate(self):
        cases = (
            "How many scheduled working days were there?",
            "How many days remain when we exclude off days?",
        )

        for question in cases:
            with self.subTest(question=question):
                facts = detect_semantic_facts(question, ResolutionContext(catalog={}))
                predicates = {
                    fact.concept_name for fact in facts if fact.kind == "predicate"
                }
                self.assertEqual(predicates, {"scheduled_working_day"})
                self.assertFalse(
                    any(fact.concept_name == "off_day" for fact in facts),
                    "an exclusion must not emit the positive off-day value concept",
                )

    def test_numeric_comparison_operator_is_detected_generically(self):
        facts = detect_semantic_facts(
            "How many attendance records have Lateness_Hrs at least 2?",
            ResolutionContext({}),
        )

        self.assertIn(
            ("Lateness_Hrs", "gte", (2.0,)),
            {(fact.field, fact.operator, fact.values) for fact in facts},
        )

    def test_invalid_temporal_and_non_finite_numeric_values_do_not_resolve(self):
        registry = ResolverRegistry.default()
        context = ResolutionContext({})

        invalid_date = registry.canonicalize(
            "Date", "2026-02-30", "2026-02-30", context
        )
        non_finite = registry.canonicalize("Total_OT", "NaN", "NaN", context)

        self.assertEqual(invalid_date.status, "unknown")
        self.assertEqual(non_finite.status, "unknown")

    def test_calculation_operation_and_subject_are_detected(self):
        percentage = detect_semantic_facts(
            "What percentage of all attendance records have Status equal to Authorized?",
            ResolutionContext({}),
        )
        average = detect_semantic_facts(
            "What is average Lateness_Hrs by Department?",
            ResolutionContext({}),
        )

        self.assertTrue(
            any(
                fact.kind == "calculation"
                and fact.concept_name == "percentage"
                and fact.field is None
                for fact in percentage
            )
        )
        self.assertTrue(
            any(
                fact.kind == "calculation"
                and fact.concept_name == "average"
                and fact.field == "Lateness_Hrs"
                for fact in average
            )
        )

    def test_grouping_is_a_distinct_strong_fact(self):
        facts = detect_semantic_facts(
            "What is average Lateness_Hrs by Department?",
            ResolutionContext({}),
        )

        self.assertTrue(
            any(
                fact.kind == "group_by" and fact.field == "Department" for fact in facts
            )
        )

    def test_total_numeric_field_is_detected_as_sum_not_count(self):
        facts = detect_semantic_facts(
            "What was total overtime for A10017?",
            ResolutionContext({}),
        )

        calculations = [fact for fact in facts if fact.kind == "calculation"]
        self.assertEqual(len(calculations), 1)
        self.assertEqual(calculations[0].concept_name, "sum")
        self.assertEqual(calculations[0].field, "Total_OT")
        self.assertFalse(
            any(fact.kind == "filter" and fact.field == "Total_OT" for fact in facts)
        )

    def test_value_concept_uses_generic_resolver(self):
        context = ResolutionContext(
            catalog={"Day_Type": ("Working Day", "OFF Day", "OFF Day (ZAS)")}
        )

        facts = detect_semantic_facts("off days for A11017", context)

        fact = next(item for item in facts if item.concept_name == "off_day")
        self.assertEqual(fact.field, "Day_Type")
        self.assertEqual(fact.values, ("OFF Day", "OFF Day (ZAS)"))
        self.assertEqual(fact.evidence_text.casefold(), "off days")

    def test_business_value_language_is_not_a_field_alias(self):
        facts = detect_semantic_facts(
            "How many draft records were there?", ResolutionContext(catalog={})
        )

        canonical = {
            (fact.concept_name, fact.field, fact.operator, fact.values)
            for fact in facts
        }
        self.assertIn(("draft_status", "Status", "eq", ("Draft",)), canonical)
        self.assertIn(
            "attendance_records",
            {fact.concept_name for fact in facts if fact.kind == "measure"},
        )
        self.assertFalse(
            any(fact.kind == "field" and fact.field == "Status" for fact in facts)
        )

    def test_partial_catalog_collision_requires_clarification(self):
        context = ResolutionContext(catalog={"Shift": ("Night A", "Night B")})

        result = ResolverRegistry.default().canonicalize(
            field="Shift",
            raw_value="night",
            evidence_text="night shift",
            context=context,
        )

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.candidates, ("Night A", "Night B"))

    def test_every_field_resolution_kind_has_one_implementation(self):
        registry = ResolverRegistry.default()
        for definition in FIELD_DEFINITIONS.values():
            self.assertTrue(registry.supports(definition.resolution_kind))
            self.assertEqual(registry.owner_count(definition.resolution_kind), 1)

    def test_every_canonical_field_name_is_detectable(self):
        context = ResolutionContext(catalog={})
        for field, definition in FIELD_DEFINITIONS.items():
            if not definition.planner_visible:
                continue
            with self.subTest(field=field):
                facts = detect_semantic_facts(definition.natural_names[0], context)
                self.assertTrue(
                    any(item.kind == "field" and item.field == field for item in facts)
                )

    def test_closed_values_resolve_to_their_canonical_spelling(self):
        registry = ResolverRegistry.default()
        for field, definition in FIELD_DEFINITIONS.items():
            for value in definition.closed_values:
                with self.subTest(field=field, value=value):
                    result = registry.canonicalize(
                        field, value.casefold(), value.casefold(), ResolutionContext({})
                    )
                    self.assertEqual(result.status, "resolved")
                    self.assertEqual(result.values, (value,))

    def test_evidence_matching_uses_token_boundaries(self):
        self.assertTrue(evidence_occurs("show off days", "off day"))
        self.assertFalse(evidence_occurs("office days", "off"))

    def test_merge_replaces_candidate_but_keeps_strong_contradictions(self):
        candidate = self._fact("candidate", ("Night A", "Night B"))
        resolved = self._fact("strong", ("Night A",))
        contradiction = self._fact("strong", ("Night B",))

        merged = merge_semantic_facts((candidate,), (resolved,), (contradiction,))

        self.assertEqual(merged, (resolved, contradiction))

    @staticmethod
    def _fact(strength, values):
        return SemanticFact(
            kind="filter",
            field="Shift",
            operator="eq",
            values=values,
            concept_name=None,
            evidence_text="night shift",
            origin="question",
            strength=strength,
        )


if __name__ == "__main__":
    unittest.main()
