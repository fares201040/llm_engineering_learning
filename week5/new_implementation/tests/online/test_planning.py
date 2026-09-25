from __future__ import annotations

import json
import inspect
import unittest
from unittest.mock import MagicMock, patch

from week5.new_implementation.online import answering, planner, reference
from week5.new_implementation.online.answering import (
    AnswerDraft,
    VerdictPass,
    VerdictReject,
    VerdictResponse,
    generate_answer,
)
from week5.new_implementation.online.context import (
    DatabaseColumn,
    DatabaseContext,
    DatabaseJsonField,
    DatabaseTable,
    SharedModelContext,
)
from week5.new_implementation.online.execution import (
    ExecutionCoverage,
    SqlExecutionResult,
)
from week5.new_implementation.online.provider import CallBudget, ProviderFailure
from week5.new_implementation.online.reference import (
    AmbiguousReference,
    Employee,
    EmployeeOption,
    IdentityClaim,
    ReadyReference,
    ReferenceResponse,
    UnsupportedReference,
    bind_references,
    search_employee_candidates,
)
from week5.new_implementation.tests.online.test_query import database_context


def response(**updates):
    values = {
        "rewritten_request": "Show attendance for Wail Ali in September 2026.",
        "locale": "en",
        "request_relationship": "new",
        "subject_relationship": "employees",
        "employee_names": ("Wail Ali",),
    }
    values.update(updates)
    return ReferenceResponse(decision=ReadyReference(**values))


class ReferenceAndPlanningTests(unittest.TestCase):
    def test_reference_schema_uses_openai_supported_union_shape(self):
        schema = ReferenceResponse.model_json_schema()

        self.assertNotIn("oneOf", json.dumps(schema))
        self.assertIn("anyOf", schema["properties"]["decision"])

    def test_reference_can_reject_requests_outside_the_attendance_domain(self):
        response = ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request="List repayment records for employee A10054.",
                locale="en",
                capability="outside_attendance_domain",
            )
        )

        self.assertEqual(response.decision.status, "unsupported")
        self.assertEqual(response.decision.capability, "outside_attendance_domain")

    def test_verifier_schema_uses_openai_supported_union_shape(self):
        schema = VerdictResponse.model_json_schema()

        self.assertNotIn("oneOf", json.dumps(schema))
        self.assertIn("anyOf", schema["properties"]["decision"])

    def test_every_model_prompt_has_a_complete_distinct_role(self):
        prompts = (
            reference._SYSTEM,
            planner._SYSTEM,
            answering._WRITER,
            answering._VERIFIER,
        )
        planner_prompt = " ".join(planner._SYSTEM.split())
        self.assertTrue(all(item.startswith("You are ") for item in prompts))
        self.assertEqual(len(set(prompts)), 4)
        self.assertIn("carefully read the complete database schema", planner_prompt)
        for detail in (
            "data type",
            "nullability",
            "description",
            "standard stored values",
        ):
            self.assertIn(detail, planner_prompt)
        self.assertIn(
            "Add a WHERE predicate only when the current question requires it",
            planner_prompt,
        )
        self.assertIn("Do not combine plausible", planner_prompt)
        self.assertIn("read every supplied json_fields entry", planner_prompt)
        self.assertIn("exact sql_text_expression", planner_prompt)
        self.assertIn("cast it to the supplied json_type", planner_prompt)
        self.assertIn("count(distinct attendance_date)", planner_prompt.casefold())
        self.assertIn("how many days", planner_prompt.casefold())
        self.assertIn("one aggregate row", planner_prompt.casefold())
        self.assertIn("do not return detail rows", planner_prompt.casefold())
        self.assertIn("must not include limit", planner_prompt.casefold())
        self.assertIn(
            "do not infer a request for individual dates", planner_prompt.casefold()
        )
        self.assertIn("total_worked_hrs > 0", planner_prompt.casefold())
        self.assertIn("coalesce(total_worked_hrs, 0) <= 0", planner_prompt.casefold())
        self.assertIn("scheduled working day", planner_prompt.casefold())
        self.assertIn("day_type = 'working day'", planner_prompt.casefold())
        self.assertIn(
            "\"scheduled work dates\" always means day_type = 'working day'",
            planner_prompt.casefold(),
        )
        self.assertIn("do not express it as day_type !=", planner_prompt.casefold())
        self.assertIn("mandatory attendance meanings", planner_prompt.casefold())
        self.assertIn("exception = 'absent'", planner_prompt.casefold())
        self.assertIn("use exactly the predicate", planner_prompt.casefold())
        self.assertIn("must use that typed", planner_prompt.casefold())
        self.assertIn(
            "prefer an equivalent typed relational column", planner_prompt.casefold()
        )
        self.assertIn("never or", planner_prompt.casefold())
        self.assertIn("include record_id", planner_prompt.casefold())
        self.assertIn(
            "attendance records means all matching rows", planner_prompt.casefold()
        )
        self.assertIn("count attendance records", planner_prompt.casefold())
        self.assertIn("use count(*)", planner_prompt.casefold())
        self.assertIn("count(*) over() as matched_count", planner_prompt.casefold())
        self.assertIn("limit 100", planner_prompt.casefold())
        self.assertIn("mandatory hard bound", planner_prompt.casefold())
        self.assertIn("explicitly requested detail/list", planner_prompt.casefold())
        self.assertIn("do not use union", planner_prompt.casefold())
        self.assertIn("one select only", planner_prompt.casefold())
        self.assertIn("validate literal dates", planner_prompt.casefold())
        self.assertIn("impossible calendar date", planner_prompt.casefold())
        self.assertIn("malformed identifier", planner_prompt.casefold())
        verifier_prompt = " ".join(answering._VERIFIER.split()).casefold()
        self.assertIn("aggregate value of zero", verifier_prompt)
        self.assertIn("valid evidence for zero", verifier_prompt)
        self.assertIn(
            "do not require the answer to repeat sql filters", verifier_prompt
        )
        writer_prompt = " ".join(answering._WRITER.split()).casefold()
        self.assertIn("matched_count", writer_prompt)
        self.assertIn("bounded sample", writer_prompt)
        self.assertIn(
            "always state its inclusive available_start-to-available_end range",
            writer_prompt,
        )
        self.assertIn("must not call an aggregate", writer_prompt)
        self.assertIn("matched_count", verifier_prompt)
        self.assertIn("fetched row count", verifier_prompt)
        self.assertIn("date_coverage", writer_prompt)
        self.assertIn("database_date_coverage", writer_prompt)
        self.assertIn("do not infer table coverage from result rows", writer_prompt)
        self.assertIn("not the full requested period", writer_prompt)
        self.assertIn("date_coverage", verifier_prompt)
        self.assertIn("reject an answer that omits", verifier_prompt)
        self.assertIn("aggregate row a bounded sample", verifier_prompt)
        self.assertIn("table-coverage dates", verifier_prompt)
        self.assertIn("filtered result dates", verifier_prompt)
        self.assertIn("do not reject", verifier_prompt)
        self.assertIn("correct every listed rejection code", writer_prompt)
        self.assertIn("did not request a date interval", writer_prompt)
        self.assertIn("sole database-result row is authoritative", verifier_prompt)
        reference_prompt = " ".join(reference._SYSTEM.split()).casefold()
        self.assertIn("which employees", reference_prompt)
        self.assertIn("never mark it as missing_employee", reference_prompt)
        self.assertIn("outside the attendance domain", reference_prompt)

    def test_sql_planner_review_instruction_is_conditional_on_execution_failure(self):
        planner_prompt = " ".join(planner._SYSTEM.split()).casefold()

        self.assertNotIn("before returning sql, silently review", planner_prompt)
        self.assertIn("only when sql_execution_failure is supplied", planner_prompt)
        self.assertIn("review the failed sql step by step", planner_prompt)
        self.assertIn("database error", planner_prompt)
        self.assertIn("return only corrected sql", planner_prompt)

    def test_sql_planner_bounds_grouped_aggregates_and_signals_unsupported_schema(self):
        planner_prompt = " ".join(planner._SYSTEM.split()).casefold()

        self.assertIn("scalar aggregate", planner_prompt)
        self.assertIn("grouped aggregate", planner_prompt)
        self.assertIn("count(*) over() as matched_count", planner_prompt)
        self.assertIn("unsupported_capability", planner_prompt)

    @patch("week5.new_implementation.online.reference.call_structured")
    def test_reference_receives_original_history_and_trusted_context_separately(
        self, call
    ):
        call.return_value = response()

        reference.request_references(
            "what about September?",
            history=({"role": "user", "content": "show Wail"},),
            trusted_context={
                "active_employees": [{"employee_id": "A1", "name": "Wail Ali"}]
            },
            active_employees=(Employee(employee_id="A1", name="Wail Ali"),),
            model="reference-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        payload = call.call_args.kwargs["payload"]
        self.assertEqual(payload["current_question"], "what about September?")
        self.assertIn("conversation_history", payload)
        self.assertIn("trusted_context", payload)
        self.assertNotIn("database_context", payload)

    def test_exact_multiple_employees_attach_one_stable_authoritative_block(self):
        bound = bind_references(
            response(
                rewritten_request="Compare their total hours in September 2026.",
                employee_names=("Wail Ali", "Faris Hassan"),
            ),
            (
                Employee(employee_id="A1", name="Wail Ali"),
                Employee(employee_id="A2", name="Faris Hassan"),
            ),
            original_question="compare Wail and Faris",
        )

        self.assertEqual(bound.employee_ids, ("A1", "A2"))
        self.assertEqual(
            bound.updated_request,
            "Resolved employees:\n- Wail Ali (A1)\n- Faris Hassan (A2)\n\n"
            "Request:\nCompare their total hours in September 2026.",
        )

    def test_exact_authorized_id_overrides_incorrect_model_missing_employee(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request=(
                        "Count attendance days for employee A11017 in September 2026."
                    ),
                )
            ),
            (Employee(employee_id="A11017", name="Faris Nasser Ali"),),
            original_question=(
                "How many days did A11017 attend during September 2026?"
            ),
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A11017",))
        self.assertIn("Faris Nasser Ali (A11017)", bound.updated_request)

    def test_identity_claim_uses_id_first_and_mismatch_requires_confirmation(self):
        bound = bind_references(
            response(
                employee_names=(),
                identity_claims=(
                    IdentityClaim(employee_id="A1", employee_name="Faris Hassan"),
                ),
            ),
            (
                Employee(employee_id="A1", name="Ahmed Ali"),
                Employee(employee_id="A2", name="Faris Hassan"),
            ),
            original_question="is Faris A1",
        )

        self.assertIsNotNone(bound.confirmation)
        self.assertEqual(
            tuple(item.employee_id for item in bound.confirmation.options),
            ("A2", "A1"),
        )

    def test_invented_identity_name_is_ignored_when_exact_id_is_authoritative(self):
        bound = bind_references(
            response(
                employee_names=(),
                employee_ids=("A1",),
                identity_claims=(IdentityClaim(employee_id="A1", employee_name="/"),),
            ),
            (Employee(employee_id="A1", name="Ahmed Ali"),),
            original_question="show attendance for A1",
        )

        self.assertIsNone(bound.confirmation)
        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A1",))

    def test_possessive_explicit_id_remains_grounded_with_unknown_model_name(self):
        bound = bind_references(
            response(
                employee_names=(),
                employee_ids=("A10017",),
                identity_claims=(
                    IdentityClaim(
                        employee_id="A10017",
                        employee_name="/unknown/",
                    ),
                ),
            ),
            (Employee(employee_id="A10017", name="Iftikhar Hasson Ismail"),),
            original_question=(
                "Exclude off days and count A10017's scheduled work dates."
            ),
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A10017",))

    def test_id_duplicated_into_name_fields_remains_an_exact_id_resolution(self):
        bound = bind_references(
            response(
                employee_ids=("A1",),
                employee_names=("A1",),
                identity_claims=(IdentityClaim(employee_id="A1", employee_name="A1"),),
            ),
            (Employee(employee_id="A1", name="Ahmed Ali"),),
            original_question="how many days did A1 attend",
        )

        self.assertIsNone(bound.confirmation)
        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A1",))

    def test_employee_label_plus_exact_id_is_not_an_identity_mismatch(self):
        bound = bind_references(
            response(
                employee_names=("Employee A10029",),
                employee_ids=("A10029",),
                identity_claims=(
                    IdentityClaim(
                        employee_id="A10029",
                        employee_name="Employee A10029",
                    ),
                ),
            ),
            (Employee(employee_id="A10029", name="Suhail Mustafa Yousuf"),),
            original_question="How many days did employee A10029 work?",
        )

        self.assertIsNone(bound.confirmation)
        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A10029",))

    def test_exact_authorized_name_is_resolved_even_if_model_omits_references(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Count records with Authorized status.",
                )
            ),
            (Employee(employee_id="A10017", name="Iftikhar Hasson Ismail"),),
            original_question=(
                "Count Iftikhar Hasson Ismail's records with Authorized status."
            ),
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A10017",))

    def test_duplicate_exact_authorized_name_requires_direct_confirmation(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="ambiguous_reference",
                    rewritten_request="Show attendance for Adel Mohammed Abdulla.",
                )
            ),
            (
                Employee(employee_id="A10439", name="Adel Mohammed Abdulla"),
                Employee(employee_id="A10663", name="Adel Mohammed Abdulla"),
            ),
            original_question="Show attendance for Adel Mohammed Abdulla.",
        )

        self.assertFalse(bound.ambiguous)
        self.assertIsNotNone(bound.confirmation)
        self.assertEqual(
            tuple(item.employee_id for item in bound.confirmation.options),
            ("A10439", "A10663"),
        )
        self.assertEqual(bound.employee_ids, ())

    def test_duplicate_exact_name_cannot_be_bypassed_by_model_field_choice(self):
        bound = bind_references(
            response(
                employee_names=(),
                employee_ids=("Adel Mohammed Abdulla",),
                identity_claims=(
                    IdentityClaim(
                        employee_id="Adel Mohammed Abdulla",
                        employee_name="Adel Mohammed Abdulla",
                    ),
                ),
            ),
            (
                Employee(employee_id="A10439", name="Adel Mohammed Abdulla"),
                Employee(employee_id="A10663", name="Adel Mohammed Abdulla"),
            ),
            original_question="What was Adel Mohammed Abdulla's overtime?",
        )

        self.assertIsNotNone(bound.confirmation)
        self.assertEqual(
            tuple(item.employee_id for item in bound.confirmation.options),
            ("A10439", "A10663"),
        )

    def test_shorter_name_inside_explicit_full_identity_is_not_an_extra_employee(self):
        bound = bind_references(
            response(
                employee_names=(
                    "Mohammed Ali Mohammed Abdulla",
                    "Ali Mohammed Abdulla",
                ),
                employee_ids=("A10055",),
            ),
            (
                Employee(employee_id="A10055", name="Mohammed Ali Mohammed Abdulla"),
                Employee(employee_id="A10927", name="Ali Mohammed Abdulla"),
            ),
            original_question=(
                "How many scheduled working days did "
                "Mohammed Ali Mohammed Abdulla (A10055) have?"
            ),
        )

        self.assertEqual(bound.employee_ids, ("A10055",))

    def test_unmentioned_model_invented_employee_is_not_bound_on_new_request(self):
        bound = bind_references(
            response(
                employee_names=("Invented Extra",),
                employee_ids=("A10017", "A10029", "A10999"),
                identity_claims=(
                    IdentityClaim(employee_id="A10999", employee_name="Invented Extra"),
                ),
            ),
            (
                Employee(employee_id="A10017", name="Iftikhar Hasson Ismail"),
                Employee(employee_id="A10029", name="Suhail Mustafa Yousuf"),
                Employee(employee_id="A10999", name="Invented Extra"),
            ),
            original_question=(
                "How many attendance records belong to employees "
                "A10017 and A10029 combined?"
            ),
        )

        self.assertEqual(bound.employee_ids, ("A10017", "A10029"))

    def test_general_pattern_question_overrides_model_missing_employee(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Find employees with unusual attendance patterns.",
                )
            ),
            (),
            original_question="Which employees show unusual attendance patterns?",
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.subject_relationship, "all_authorized")
        self.assertIsNotNone(bound.updated_request)

    def test_unusual_attendance_criteria_override_model_missing_employee(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Find unusual Working Day attendance.",
                )
            ),
            (),
            original_question="Find unusual Working Day attendance.",
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.subject_relationship, "all_authorized")

    def test_plural_record_filter_overrides_model_missing_employee(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Count records on September 31, 2026.",
                )
            ),
            (),
            original_question="Count records on September 31, 2026.",
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.subject_relationship, "all_authorized")

    def test_subjectless_single_employee_request_remains_ambiguous(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Show attendance.",
                )
            ),
            (),
            original_question="Show attendance.",
        )

        self.assertTrue(bound.ambiguous)

    def test_temporally_scoped_attendance_request_is_general_scope(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Show attendance before September 5, 2026.",
                )
            ),
            (),
            original_question="Show attendance before 2026-09-05.",
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.subject_relationship, "all_authorized")

    def test_ambiguous_reference_preserves_typed_unresolved_name_for_search(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="ambiguous_reference",
                    rewritten_request="Show overtime for Mukhtar Ahmed.",
                    employee_mention="Mukhtar Ahmed",
                )
            ),
            (
                Employee(employee_id="A11000", name="Mukhtar Ahmed Saeed"),
                Employee(employee_id="A10651", name="Mukhtar Ahmed Mohammed"),
            ),
            original_question="What was Mukhtar Ahmed's overtime?",
        )

        self.assertTrue(bound.ambiguous)
        self.assertEqual(bound.unresolved_mention, "Mukhtar Ahmed")
        self.assertIsNotNone(bound.pending_resolution)

    def test_partial_name_misclassified_as_id_is_preserved_for_search(self):
        bound = bind_references(
            response(
                employee_ids=("Mukhtar Ahmed",),
                employee_names=(),
            ),
            (
                Employee(employee_id="A11000", name="Mukhtar Ahmed Saeed"),
                Employee(employee_id="A10651", name="Mukhtar Ahmed Mohammed"),
            ),
            original_question="Show leave information for Mukhtar Ahmed.",
        )

        self.assertTrue(bound.ambiguous)
        self.assertEqual(bound.unresolved_mention, "Mukhtar Ahmed")
        self.assertIsNotNone(bound.pending_resolution)

    def test_inconsistent_model_relationship_is_normalized_by_authoritative_binder(
        self,
    ):
        decision = ReferenceResponse.model_validate_json(
            json.dumps(
                {
                    "decision": {
                        "status": "ready",
                        "rewritten_request": "Show Mukhtar Ahmed attendance.",
                        "locale": "en",
                        "request_relationship": "new",
                        "subject_relationship": "criteria",
                        "employee_ids": [],
                        "employee_names": ["Mukhtar Ahmed"],
                        "identity_claims": [],
                        "employee_criteria": [],
                    }
                }
            ),
            strict=True,
        )
        bound = bind_references(
            decision,
            (
                Employee(employee_id="A11000", name="Mukhtar Ahmed Meer"),
                Employee(employee_id="A10651", name="Mukhtar Ahmed Mohammed"),
            ),
            original_question="Show Mukhtar Ahmed attendance.",
        )

        self.assertEqual(bound.subject_relationship, "employees")
        self.assertEqual(bound.unresolved_mention, "Mukhtar Ahmed")
        self.assertIsNotNone(bound.pending_resolution)

    def test_exact_name_misclassified_as_employee_id_still_resolves_by_name(self):
        bound = bind_references(
            response(
                employee_ids=("Iftikhar Hasson Ismail",),
                employee_names=(),
            ),
            (Employee(employee_id="A10017", name="Iftikhar Hasson Ismail"),),
            original_question="Count Iftikhar Hasson Ismail's worked days.",
        )

        self.assertIsNone(bound.confirmation)
        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A10017",))

    def test_invented_unmentioned_id_does_not_override_exact_authorized_name(self):
        invented_id = "employee_ID_for_Iftikhar_Hasson_Ismail"
        bound = bind_references(
            response(
                employee_ids=(invented_id,),
                employee_names=("Iftikhar Hasson Ismail",),
                identity_claims=(
                    IdentityClaim(
                        employee_id=invented_id,
                        employee_name="Iftikhar Hasson Ismail",
                    ),
                ),
            ),
            (Employee(employee_id="A10017", name="Iftikhar Hasson Ismail"),),
            original_question="Count Iftikhar Hasson Ismail's worked days.",
        )

        self.assertIsNone(bound.confirmation)
        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A10017",))

    def test_unknown_standalone_id_has_no_options_or_semantic_search_mention(self):
        bound = bind_references(
            response(employee_names=(), employee_ids=("A999",)),
            (),
            original_question="show A999",
        )

        self.assertTrue(bound.ambiguous)
        self.assertEqual(bound.reason, "unknown_employee_id")
        self.assertIsNone(bound.unresolved_mention)
        self.assertIsNone(bound.confirmation)

    def test_numeric_only_identifier_is_malformed_by_authorized_directory_shape(self):
        bound = bind_references(
            response(employee_names=(), employee_ids=("12345",)),
            (Employee(employee_id="A10029", name="Suhail Mustafa Yousuf"),),
            original_question="Show employee 12345 attendance.",
        )

        self.assertTrue(bound.ambiguous)
        self.assertEqual(bound.reason, "malformed_identifier")

    def test_identifier_segment_length_and_order_must_match_authoritative_shape(self):
        directory = (Employee(employee_id="A10029", name="Suhail Mustafa Yousuf"),)

        for malformed in ("A10", "10017A", "AABCDE"):
            with self.subTest(malformed=malformed):
                bound = bind_references(
                    response(employee_names=(), employee_ids=(malformed,)),
                    directory,
                    original_question=f"Show attendance for {malformed}.",
                )

                self.assertTrue(bound.ambiguous)
                self.assertEqual(bound.reason, "malformed_identifier")

    def test_general_criteria_remain_natural_language(self):
        bound = bind_references(
            response(
                rewritten_request="Show employees absent in September 2026.",
                subject_relationship="criteria",
                employee_names=(),
                employee_criteria=("employees absent in September 2026",),
            ),
            (),
            original_question="show absent employees",
        )

        self.assertEqual(
            bound.employee_criteria, ("employees absent in September 2026",)
        )
        self.assertNotIn("field_id", bound.model_dump_json())

    @patch("week5.new_implementation.chroma_client.create_chroma_client")
    @patch("openai.OpenAI")
    def test_chroma_candidates_are_scope_filtered_and_postgres_cross_checked(
        self, openai, chroma
    ):
        openai.return_value.embeddings.create.return_value.data = [
            MagicMock(embedding=[0.1])
        ]
        collection = chroma.return_value.get_collection.return_value
        collection.query.return_value = {
            "metadatas": [
                [
                    {"Employee_ID": "A2", "Name": "Faris Hassan"},
                    {"Employee_ID": "A9", "Name": "Injected"},
                ]
            ]
        }

        options = search_employee_candidates(
            "Fares",
            (Employee(employee_id="A2", name="Faris Hassan"),),
            embedding_model="embedding-test",
            collection_name="docs",
            allowed_employee_ids=("A2",),
        )

        self.assertEqual(
            options, (EmployeeOption(employee_id="A2", employee_name="Faris Hassan"),)
        )
        self.assertEqual(
            collection.query.call_args.kwargs["where"],
            {"$and": [{"domain": "attendance"}, {"Employee_ID": {"$in": ["A2"]}}]},
        )

    @patch("week5.new_implementation.online.planner.call_text")
    def test_sql_planner_receives_the_shared_context_and_returns_complex_sql(
        self, call
    ):
        call.return_value = "WITH totals AS (SELECT employee_id, SUM(total_worked_hrs) AS hours FROM public.attendance_records GROUP BY employee_id) SELECT * FROM totals"
        shared = SharedModelContext(
            current_question="Show totals.",
            updated_request="Request:\nShow totals.",
            database_context=database_context(),
        )

        sql = planner.request_sql(
            shared_context=shared,
            model="planner-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=1000,
        )

        self.assertEqual(sql, call.return_value)
        payload = call.call_args.kwargs["payload"]
        self.assertEqual(payload["current_question"], shared.current_question)
        self.assertEqual(
            payload["database_context"],
            shared.model_payload()["database_context"],
        )
        self.assertIn("schema_projection", payload)

    @patch("week5.new_implementation.online.planner.call_text")
    def test_sql_planner_projects_json_fallbacks_to_requested_unique_fields(self, call):
        call.return_value = "SELECT 1"
        shared = SharedModelContext(
            current_question="Show the actual device swipe time for A1.",
            updated_request="Request:\nShow the actual device swipe time for A1.",
            database_context=DatabaseContext(
                server_version="17.2",
                tables=(
                    DatabaseTable(
                        schema_name="public",
                        table_name="attendance_records",
                        object_type="BASE TABLE",
                        description="Authoritative attendance records.",
                        columns=(
                            DatabaseColumn(
                                name="attendance_date",
                                data_type="date",
                                nullable=False,
                                description="Authoritative attendance date.",
                            ),
                            DatabaseColumn(
                                name="record_json",
                                data_type="jsonb",
                                nullable=False,
                                description="Normalized source record.",
                                json_fields=(
                                    DatabaseJsonField(
                                        name="Date",
                                        json_type="ISO date string (YYYY-MM-DD)",
                                        sql_text_expression="record_json ->> 'Date'",
                                        description="Duplicate fallback date.",
                                    ),
                                    DatabaseJsonField(
                                        name="Actual_From_Time",
                                        json_type="ISO time string (HH:MM:SS)",
                                        sql_text_expression=(
                                            "record_json ->> 'Actual_From_Time'"
                                        ),
                                        description="First device swipe time.",
                                    ),
                                    DatabaseJsonField(
                                        name="Employee_Remarks",
                                        json_type="string",
                                        sql_text_expression=(
                                            "record_json ->> 'Employee_Remarks'"
                                        ),
                                        description="Employee remarks.",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )

        planner.request_sql(
            shared_context=shared,
            model="planner-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        table = call.call_args.kwargs["payload"]["database_context"]["tables"][0]
        columns = {column["name"]: column for column in table["columns"]}
        self.assertIn("attendance_date", columns)
        self.assertEqual(
            [field["name"] for field in columns["record_json"]["json_fields"]],
            ["Actual_From_Time"],
        )

    @patch("week5.new_implementation.online.planner.call_text")
    def test_sql_planner_omits_unrequested_json_fallback_catalog(self, call):
        call.return_value = "SELECT 1"
        shared = SharedModelContext(
            current_question="Count A1's scheduled work dates.",
            updated_request="Request:\nCount A1's scheduled work dates.",
            database_context=DatabaseContext(
                server_version="17.2",
                tables=(
                    DatabaseTable(
                        schema_name="public",
                        table_name="attendance_records",
                        object_type="BASE TABLE",
                        description="Authoritative attendance records.",
                        columns=(
                            DatabaseColumn(
                                name="day_type",
                                data_type="text",
                                nullable=False,
                                description="Schedule classification.",
                            ),
                            DatabaseColumn(
                                name="record_json",
                                data_type="jsonb",
                                nullable=False,
                                description="Normalized source record.",
                                json_fields=(
                                    DatabaseJsonField(
                                        name="Schedule_From_Time",
                                        json_type="ISO time string (HH:MM:SS)",
                                        sql_text_expression=(
                                            "record_json ->> 'Schedule_From_Time'"
                                        ),
                                        description="Scheduled shift start time.",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )

        planner.request_sql(
            shared_context=shared,
            model="planner-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        table = call.call_args.kwargs["payload"]["database_context"]["tables"][0]
        record_json = next(
            column for column in table["columns"] if column["name"] == "record_json"
        )
        self.assertEqual(record_json["json_fields"], [])

    @patch("week5.new_implementation.online.planner.call_text")
    def test_sql_planner_forwards_execution_failure_only_on_retry(self, call):
        self.assertIn(
            "sql_execution_failure", inspect.signature(planner.request_sql).parameters
        )
        call.return_value = "SELECT corrected"
        shared = SharedModelContext(
            current_question="Show totals.",
            updated_request="Request:\nShow totals.",
            database_context=database_context(),
        )
        failure = {
            "retry_number": 2,
            "failed_sql": "SELECT missing_column",
            "error_type": "UndefinedColumn",
            "database_error": "column missing_column does not exist",
        }

        sql = planner.request_sql(
            shared_context=shared,
            model="planner-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=1000,
            sql_execution_failure=failure,
            attempt=3,
        )

        self.assertEqual(sql, "SELECT corrected")
        self.assertEqual(
            call.call_args.kwargs["payload"]["sql_execution_failure"], failure
        )
        self.assertEqual(call.call_args.kwargs["attempt"], 3)

    @patch(
        "week5.new_implementation.online.planner.call_text",
        return_value="```sql\nSELECT 1\n```",
    )
    def test_sql_planner_rejects_markdown_wrapping(self, _call):
        with self.assertRaises(ProviderFailure):
            planner.request_sql(
                shared_context=SharedModelContext(
                    current_question="Test.",
                    updated_request="Request:\nTest.",
                    database_context=database_context(),
                ),
                model="planner-model",
                budget=CallBudget(),
                timeout=1,
                max_output_tokens=100,
            )

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_writer_and_verifier_use_same_model_and_shared_base_payload(self, call):
        call.side_effect = [
            AnswerDraft(answer="Wail Ali worked 8 hours."),
            VerdictResponse(decision=VerdictPass()),
        ]
        shared = SharedModelContext(
            current_question="Show hours.",
            updated_request="Resolved employees:\n- Wail Ali (A1)\n\nRequest:\nShow hours.",
            conversation_history=({"role": "user", "content": "attendance for Wail"},),
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(),
            rows=({"hours": 8},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=13
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT 8 AS hours",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertEqual(answer, "Wail Ali worked 8 hours.")
        self.assertEqual(
            [item.kwargs["model"] for item in call.call_args_list],
            ["answer-model", "answer-model"],
        )
        writer_payload = call.call_args_list[0].kwargs["payload"]
        verifier_payload = call.call_args_list[1].kwargs["payload"]
        self.assertNotIn("database_context", writer_payload)
        self.assertNotIn("database_context", verifier_payload)
        self.assertEqual(writer_payload["current_question"], "Show hours.")
        self.assertEqual(
            writer_payload["conversation_history"],
            [{"role": "user", "content": "attendance for Wail"}],
        )
        self.assertEqual(
            writer_payload["database_date_coverage"],
            [
                {
                    "schema_name": "public",
                    "table_name": "attendance_records",
                    "field": "attendance_date",
                    "available_start": "2026-09-01",
                    "available_end": "2026-09-07",
                }
            ],
        )
        for key, value in writer_payload.items():
            self.assertEqual(verifier_payload[key], value)

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_one_rejected_answer_is_rewritten_and_reverified(self, call):
        call.side_effect = [
            AnswerDraft(answer="Wail Ali worked 7 hours."),
            VerdictResponse(decision=VerdictReject(codes=("wrong_value",))),
            AnswerDraft(answer="Wail Ali worked 8 hours."),
            VerdictResponse(decision=VerdictPass()),
        ]
        shared = SharedModelContext(
            current_question="Show hours.",
            updated_request="Request:\nShow hours.",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(),
            rows=({"hours": 8},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=13
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT 8 AS hours",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertEqual(answer, "Wail Ali worked 8 hours.")
        self.assertEqual(call.call_count, 4)


if __name__ == "__main__":
    unittest.main()
