import unittest

from week5.new_implementation.attendance_schema import FIELD_DEFINITIONS
from week5.new_implementation.semantic_resolution import (
    ResolutionContext,
    ResolverRegistry,
    SemanticFact,
    detect_semantic_facts,
    evidence_occurs,
    merge_semantic_facts,
)


class SemanticResolutionTests(unittest.TestCase):
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
