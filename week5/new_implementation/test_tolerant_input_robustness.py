import unittest

from week5.new_implementation import answer
from week5.new_implementation.language_understanding import (
    analyze_question_surface,
    automatically_accepted_candidates,
)


class PublicTolerantInputRobustnessMatrix(unittest.TestCase):
    def test_surface_noise_and_multilingual_matrix(self):
        cases = (
            ("  WORKED, days?! ", "en", "interpretation", "worked_days"),
            ("please please show worked days", "en", "interpretation", "worked_days"),
            ("wokred days", "en", "interpretation", "worked_days"),
            ("أَيّام الـغــياب", "ar", "interpretation", "absent_days"),
            ("Faris أيام الغياب", "ar", "interpretation", "absent_days"),
            ("who is Faris", "en", "result_intent", "employee_profile"),
            ("من هو Faris", "ar", "result_intent", "employee_profile"),
        )
        for question, locale, target_kind, target_name in cases:
            with self.subTest(question=question):
                surface = analyze_question_surface(question)
                accepted = automatically_accepted_candidates(surface)
                self.assertEqual(surface.reply_locale, locale)
                self.assertTrue(
                    any(
                        candidate.target_kind == target_kind
                        and candidate.target_name == target_name
                        for candidate in accepted
                    )
                )

    def test_protected_values_never_become_fuzzy_corrections(self):
        cases = (
            "employee A1001B",
            "31/13/2026",
            "more than 12.5 hours",
            "not wokred",
            "worked nad absent",
            "worked or absent",
        )
        for question in cases:
            with self.subTest(question=question):
                accepted_fuzzy = [
                    candidate
                    for candidate in automatically_accepted_candidates(
                        analyze_question_surface(question)
                    )
                    if candidate.method == "fuzzy"
                ]
                self.assertEqual(accepted_fuzzy, [])

    def test_employee_identity_matrix(self):
        employees = [
            answer.EmployeeCandidate(employee_id="A10018", name="Faris Ahmed"),
            answer.EmployeeCandidate(employee_id="A10019", name="Faris North"),
            answer.EmployeeCandidate(employee_id="A10020", name="Duplicate Name"),
            answer.EmployeeCandidate(employee_id="A10021", name="Duplicate Name"),
        ]
        cases = (
            ("A10018", "unique", "exact_id"),
            ("Faris Ahmed", "unique", "exact_name"),
            ("Faris", "ambiguous", "prefix"),
            ("Ahmed Faris", "confirmation", "reordered_tokens"),
            ("فارس", "ambiguous", "transliteration"),
            ("Duplicate Name", "ambiguous", "exact_name"),
            ("A99999", "none", "none"),
        )
        for reference, outcome, method in cases:
            with self.subTest(reference=reference):
                resolution = answer.resolve_employee_reference(reference, employees)
                self.assertEqual(resolution.outcome, outcome)
                self.assertEqual(resolution.match_method, method)

    def test_registered_short_predicate_matrix(self):
        cases = (
            ("absent", "absent", "distinct_dates"),
            ("worked", "worked", "distinct_dates"),
            ("authorized", "authorized", "attendance_records"),
        )
        for question, predicate, measure in cases:
            with self.subTest(question=question):
                facts = answer.detect_semantic_facts(
                    question, answer.ResolutionContext({})
                )
                completed = answer._complete_registered_short_form(question, facts)
                self.assertTrue(
                    any(
                        fact.kind == "predicate" and fact.concept_name == predicate
                        for fact in completed
                    )
                )
                self.assertTrue(
                    any(
                        fact.kind == "measure" and fact.concept_name == measure
                        for fact in completed
                    )
                )

    def test_profile_rendering_deduplicates_but_preserves_changing_values(self):
        chunks = [
            answer.Result(
                page_content="ignored",
                metadata={
                    "Employee_ID": "A10018",
                    "Name": "Faris Ahmed",
                    "Department": department,
                    "Position": "Analyst",
                    "Work_Location": "Aden",
                },
            )
            for department in ("Operations", "People", "Operations")
        ]
        rendered = answer._format_employee_profile(chunks)
        self.assertIn("Operations; People", rendered)
        self.assertEqual(rendered.count("Operations"), 1)


if __name__ == "__main__":
    unittest.main()
