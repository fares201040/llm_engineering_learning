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
    def test_value_concept_uses_generic_resolver(self):
        context = ResolutionContext(
            catalog={"Day_Type": ("Working Day", "OFF Day", "OFF Day (ZAS)")}
        )

        facts = detect_semantic_facts("off days for A11017", context)

        fact = next(item for item in facts if item.concept_name == "off_day")
        self.assertEqual(fact.field, "Day_Type")
        self.assertEqual(fact.values, ("OFF Day", "OFF Day (ZAS)"))
        self.assertEqual(fact.evidence_text.casefold(), "off days")

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
