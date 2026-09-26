from __future__ import annotations

import json
import inspect
import unittest
from unittest.mock import patch

from week5.new_implementation.online import answering, context, planner, reference
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
    ResultColumn,
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
        self.assertIn("do not call it a sample at all", writer_prompt)
        self.assertIn("matched_count", verifier_prompt)
        self.assertIn("fetched row count", verifier_prompt)
        self.assertIn("date_coverage", writer_prompt)
        self.assertIn("database_date_coverage", writer_prompt)
        self.assertIn("do not infer table coverage from result rows", writer_prompt)
        self.assertIn("only the available portion is covered", writer_prompt)
        self.assertIn("date_coverage", verifier_prompt)
        self.assertIn("reject an answer that omits", verifier_prompt)
        self.assertIn("calls the result a sample", verifier_prompt)
        self.assertIn("table-coverage dates", verifier_prompt)
        self.assertIn("filtered result dates", verifier_prompt)
        self.assertIn("do not reject", verifier_prompt)
        self.assertIn("correct every listed rejection code", writer_prompt)
        self.assertIn("request_has_date_period", writer_prompt)
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
        self.assertIn("postgresql or the request-scope guard", planner_prompt)
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
            as_of_date="2026-09-25",
            model="reference-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        payload = call.call_args.kwargs["payload"]
        self.assertEqual(payload["current_question"], "what about September?")
        self.assertEqual(payload["as_of_date"], "2026-09-25")
        self.assertIn("conversation_history", payload)
        self.assertIn("trusted_context", payload)
        self.assertNotIn("database_context", payload)
        self.assertNotIn("database_schema", payload)

    def test_reference_response_schema_omits_unsupported_classification(self):
        schema = json.dumps(ReferenceResponse.model_json_schema())
        self.assertNotIn("outside_attendance_domain", schema)

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

    def test_single_active_employee_survives_incorrect_missing_employee_decision(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request=(
                        "Show the absence dates for Synthetic Employee One (A11017)."
                    ),
                    employee_mention="Synthetic Employee One",
                )
            ),
            (employee,),
            original_question="Show the absence dates instead.",
            active_employees=(employee,),
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ("A11017",))
        self.assertIn("Synthetic Employee One (A11017)", bound.updated_request)

    def test_general_scope_does_not_inherit_active_employee(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Which employees were absent?",
                )
            ),
            (employee,),
            original_question="Which employees were absent?",
            active_employees=(employee,),
        )

        self.assertEqual(bound.employee_ids, ())
        self.assertEqual(bound.subject_relationship, "all_authorized")

    def test_grouped_aggregate_without_person_uses_general_scope(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="ambiguous_reference",
                    rewritten_request="Group worked hours by department.",
                )
            ),
            (employee,),
            original_question="Group worked hours by department.",
            active_employees=(employee,),
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.employee_ids, ())
        self.assertEqual(bound.subject_relationship, "all_authorized")

    def test_date_aggregate_without_person_uses_general_scope(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Show a running total over dates.",
                )
            ),
            (),
            original_question="Show a running total over dates.",
            has_verified_turns=True,
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.subject_relationship, "all_authorized")
        self.assertEqual(
            bound.updated_request, "Request:\nShow a running total over dates."
        )

    def test_grouped_aggregate_ignores_person_copied_only_from_history(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request=(
                        "Group Synthetic Employee One's worked hours by department."
                    ),
                    employee_mention="Synthetic Employee One",
                )
            ),
            (employee,),
            original_question="Group worked hours by department.",
            active_employees=(employee,),
        )

        self.assertEqual(bound.employee_ids, ())
        self.assertEqual(bound.subject_relationship, "all_authorized")
        self.assertEqual(
            bound.updated_request, "Request:\nGroup worked hours by department."
        )

    def test_grouped_hours_with_person_pronoun_keeps_active_employee(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Group his hours by department.",
                    employee_mention="Synthetic Employee One",
                )
            ),
            (employee,),
            original_question="Group his hours by department.",
            active_employees=(employee,),
        )

        self.assertEqual(bound.employee_ids, ("A11017",))

    def test_ready_grouping_does_not_inherit_an_unmentioned_person(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            response(
                rewritten_request="Group Synthetic Employee One's hours by department.",
                request_relationship="follow_up",
                employee_names=("Synthetic Employee One",),
                employee_ids=("A11017",),
            ),
            (employee,),
            original_question="Group worked hours by department.",
            active_employees=(employee,),
        )

        self.assertEqual(bound.employee_ids, ())
        self.assertEqual(bound.subject_relationship, "all_authorized")

    def test_that_follow_up_inherits_latest_general_scope_not_older_person(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Compare Synthetic Employee One with last month.",
                    employee_mention="Synthetic Employee One",
                )
            ),
            (employee,),
            original_question="Compare that with last month.",
            active_employees=(),
            has_verified_turns=True,
        )

        self.assertEqual(bound.employee_ids, ())
        self.assertEqual(bound.request_relationship, "follow_up")
        self.assertEqual(bound.subject_relationship, "all_authorized")
        self.assertEqual(
            bound.updated_request, "Request:\nCompare that with last month."
        )

    def test_that_is_not_an_employee_mention(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="ambiguous_reference",
                    rewritten_request="The subject of that is unclear.",
                    employee_mention="that",
                )
            ),
            (),
            original_question="Compare that with last month.",
            has_verified_turns=True,
        )

        self.assertEqual(bound.request_relationship, "follow_up")
        self.assertEqual(bound.subject_relationship, "all_authorized")
        self.assertFalse(bound.ambiguous)

    def test_active_employee_fallback_keeps_current_question_polarity(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request=(
                        "Show absence dates for Synthetic Employee One (A11017)."
                    ),
                    employee_mention="Synthetic Employee One",
                )
            ),
            (employee,),
            original_question="Show dates that are not absent.",
            active_employees=(employee,),
        )

        self.assertEqual(bound.employee_ids, ("A11017",))
        self.assertIn("Show dates that are not absent.", bound.updated_request)
        self.assertNotIn("Show absence dates for", bound.updated_request)

    def test_short_follow_up_inherits_single_active_employee_when_model_is_uncertain(
        self,
    ):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="ambiguous_reference",
                    rewritten_request="Show the absence dates instead.",
                )
            ),
            (employee,),
            original_question="Show the absence dates instead.",
            active_employees=(employee,),
        )

        self.assertEqual(bound.employee_ids, ("A11017",))
        self.assertEqual(bound.request_relationship, "follow_up")

    def test_short_follow_up_does_not_replace_an_explicit_unknown_name(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="ambiguous_reference",
                    rewritten_request="Show Faris's dates.",
                    employee_mention="Faris",
                )
            ),
            (employee,),
            original_question="Show Faris's dates.",
            active_employees=(employee,),
        )

        self.assertTrue(bound.ambiguous)

    def test_short_ready_request_for_active_employee_is_follow_up(self):
        employee = Employee(employee_id="A11017", name="Synthetic Employee One")
        bound = bind_references(
            response(
                rewritten_request="Show dates that are not absent.",
                request_relationship="new",
                employee_names=("Synthetic Employee One",),
            ),
            (employee,),
            original_question="Show dates that are not absent.",
            active_employees=(employee,),
        )

        self.assertEqual(bound.request_relationship, "follow_up")

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

    def test_general_pattern_question_ignores_model_generated_employee_mention(self):
        bound = bind_references(
            ReferenceResponse(
                decision=AmbiguousReference(
                    locale="en",
                    reason="missing_employee",
                    rewritten_request="Find unusual attendance patterns.",
                    employee_mention="unusual attendance patterns",
                )
            ),
            (Employee(employee_id="A1", name="Wail Ali"),),
            original_question="Which employees show unusual attendance patterns?",
        )

        self.assertFalse(bound.ambiguous)
        self.assertEqual(bound.subject_relationship, "all_authorized")
        self.assertIsNotNone(bound.updated_request)

    def test_partial_name_shared_by_multiple_employees_requires_confirmation(self):
        bound = bind_references(
            response(
                employee_names=("Mukhtar Ahmed Meer",),
                identity_claims=(
                    IdentityClaim(employee_id="A2", employee_name="Mukhtar Ahmed Meer"),
                ),
            ),
            (
                Employee(employee_id="A1", name="Mukhtar Ahmed Ali"),
                Employee(employee_id="A2", name="Mukhtar Ahmed Meer"),
            ),
            original_question="Count Mukhtar Ahmed's attendance records.",
        )

        self.assertTrue(bound.ambiguous)
        self.assertEqual(
            tuple(option.employee_id for option in bound.fallback_options),
            ("A1", "A2"),
        )

    def test_unknown_written_person_is_not_converted_to_general_record_scope(self):
        bound = bind_references(
            response(
                subject_relationship="criteria",
                employee_criteria=("authorized records for Unknown Human",),
            ),
            (Employee(employee_id="A1", name="Wail Ali"),),
            original_question="Count authorized records for Unknown Human.",
        )

        self.assertTrue(bound.ambiguous)
        self.assertEqual(bound.unresolved_mention, "Unknown Human")

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

    def test_punctuated_unknown_identifier_is_rejected_before_model_resolution(self):
        directory = (Employee(employee_id="A10029", name="Suhail Mustafa Yousuf"),)

        self.assertTrue(
            reference.has_malformed_identifier(
                "Show attendance for NOBODY-123.", directory
            )
        )

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
    @patch("week5.new_implementation.online.reference.get_embeddings", create=True)
    @patch("openai.OpenAI", side_effect=AssertionError("paid embedding called"))
    def test_chroma_candidates_use_local_embeddings_and_authoritative_scope(
        self, _openai, local_embeddings, chroma
    ):
        local_embeddings.return_value.embed_documents.return_value = [[0.1] * 384]
        local_embeddings.return_value.embed_query.return_value = [0.1] * 384
        collection = chroma.return_value.get_or_create_collection.return_value
        collection.get.return_value = {"ids": [], "metadatas": []}
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
            embedding_provider="huggingface",
            collection_name="docs",
            allowed_employee_ids=("A2",),
        )

        self.assertEqual(
            options, (EmployeeOption(employee_id="A2", employee_name="Faris Hassan"),)
        )
        local_embeddings.assert_called_with("embedding-test", "huggingface")
        self.assertEqual(collection.upsert.call_args.kwargs["ids"], ["A2"])
        self.assertEqual(
            len(collection.query.call_args.kwargs["query_embeddings"][0]), 384
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
        expected_database = shared.model_payload()["database_context"]
        self.assertEqual(payload["database_context"], expected_database)
        self.assertNotIn("schema_projection", payload)

    @patch("week5.new_implementation.online.planner.call_text")
    def test_sql_planner_receives_every_described_json_field(self, call):
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
            ["Date", "Actual_From_Time", "Employee_Remarks"],
        )

    @patch("week5.new_implementation.online.planner.call_text")
    def test_sql_planner_keeps_json_fields_for_unrelated_wording(self, call):
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
        self.assertEqual(
            [field["name"] for field in record_json["json_fields"]],
            ["Schedule_From_Time"],
        )

    @patch("week5.new_implementation.online.planner.call_text")
    def test_sql_planner_receives_all_58_record_json_fields(self, call):
        call.return_value = "SELECT 1"
        shared = SharedModelContext(
            current_question="List employees with manual swipes.",
            updated_request="Request:\nList employees with manual swipes.",
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
                                name="record_json",
                                data_type="jsonb",
                                nullable=False,
                                description="Normalized source record.",
                                json_fields=context._record_json_fields(),
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

        fields = call.call_args.kwargs["payload"]["database_context"]["tables"][0][
            "columns"
        ][0]["json_fields"]
        self.assertEqual(len(fields), 58)
        self.assertEqual(fields[0]["name"], "Actual_From_Date")
        self.assertEqual(fields[-1]["name"], "pre_ot_hrs")

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
            AnswerDraft(
                answer="Wail Ali worked 8 hours. Records are available from 2026-09-01 to 2026-09-07."
            ),
            VerdictResponse(decision=VerdictPass()),
        ]
        shared = SharedModelContext(
            current_question="Show hours.",
            as_of_date="2026-09-25",
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

        self.assertEqual(
            answer,
            "Wail Ali worked 8 hours. Records are available from 2026-09-01 to 2026-09-07.",
        )
        self.assertEqual(
            [item.kwargs["model"] for item in call.call_args_list],
            ["answer-model", "answer-model"],
        )
        writer_payload = call.call_args_list[0].kwargs["payload"]
        verifier_payload = call.call_args_list[1].kwargs["payload"]
        self.assertNotIn("database_context", writer_payload)
        self.assertNotIn("database_context", verifier_payload)
        self.assertNotIn("as_of_date", writer_payload)
        self.assertNotIn("as_of_date", verifier_payload)
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
        self.assertEqual(
            writer_payload["last_calendar_month_coverage"],
            [
                {
                    "schema_name": "public",
                    "table_name": "attendance_records",
                    "fully_available": False,
                    "available_overlap": None,
                }
            ],
        )
        for key, value in writer_payload.items():
            self.assertEqual(verifier_payload[key], value)

    def test_last_month_overlap_uses_actual_table_bounds(self):
        shared = SharedModelContext(
            current_question="Compare last month.",
            as_of_date="2026-10-25",
            updated_request="Request:\nCompare last month.",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(),
            rows=(),
            coverage=ExecutionCoverage(
                fetched_rows=0, result_limit=100, response_bytes=2
            ),
        )
        payload = answering._base_payload(
            shared, sql="SELECT 1", result=result, employees=(), locale="en"
        )

        self.assertEqual(
            payload["last_calendar_month_coverage"][0]["available_overlap"],
            {"start": "2026-09-01", "end": "2026-09-07"},
        )

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_aggregate_answer_does_not_call_the_result_a_sample(self, call):
        call.side_effect = [
            AnswerDraft(
                answer="The returned sample is complete. Data available 2026-09-01 to 2026-09-07."
            ),
            AnswerDraft(
                answer="Total hours: 8. Data available 2026-09-01 to 2026-09-07."
            ),
            VerdictResponse(decision=VerdictPass()),
        ]
        shared = SharedModelContext(
            current_question="Total hours?",
            updated_request="Request:\nTotal hours?",
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
            employees=(),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertEqual(
            answer, "Total hours: 8. Data available 2026-09-01 to 2026-09-07."
        )
        self.assertEqual(len(call.call_args_list), 3)

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_one_rejected_answer_is_rewritten_and_reverified(self, call):
        call.side_effect = [
            AnswerDraft(
                answer="Wail Ali worked 7 hours. Records are available from 2026-09-01 to 2026-09-07."
            ),
            VerdictResponse(decision=VerdictReject(codes=("wrong_value",))),
            AnswerDraft(
                answer="Wail Ali worked 8 hours. Records are available from 2026-09-01 to 2026-09-07."
            ),
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

        self.assertEqual(
            answer,
            "Wail Ali worked 8 hours. Records are available from 2026-09-01 to 2026-09-07.",
        )
        self.assertEqual(call.call_count, 4)

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_answer_without_date_request_does_not_invent_requested_period(self, call):
        call.side_effect = [
            AnswerDraft(answer="The requested period is 2026-08-03 to 2026-09-06."),
            AnswerDraft(answer="Available records are from 2026-09-01 to 2026-09-07."),
            VerdictResponse(decision=VerdictPass()),
        ]
        shared = SharedModelContext(
            current_question="Group worked hours by department.",
            updated_request="Request:\nGroup worked hours by department.",
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
            employees=(),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertEqual(answer, "Available records are from 2026-09-01 to 2026-09-07.")
        self.assertEqual(call.call_count, 3)
        self.assertEqual(
            call.call_args_list[1].kwargs["payload"]["rewrite_after_rejection"][
                "codes"
            ],
            ["wrong_coverage"],
        )

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_answer_without_date_request_rejects_unrequested_as_of_date(self, call):
        call.side_effect = [
            AnswerDraft(
                answer=(
                    "Wail Ali's total overtime as of 2026-09-26 is 0.0. "
                    "Records are available from 2026-09-01 to 2026-09-07."
                )
            ),
            AnswerDraft(
                answer=(
                    "Wail Ali's total overtime is 0.0. "
                    "Records are available from 2026-09-01 to 2026-09-07."
                )
            ),
            VerdictResponse(decision=VerdictPass()),
        ]
        shared = SharedModelContext(
            current_question="What was total overtime for A1?",
            as_of_date="2026-09-26",
            updated_request="Request:\nWhat was total overtime for A1?",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(),
            rows=({"total_overtime": 0.0},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=25
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT SUM(overtime) AS total_overtime FROM attendance_records",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertEqual(
            answer,
            "Wail Ali's total overtime is 0.0. Records are available from "
            "2026-09-01 to 2026-09-07.",
        )
        repair = call.call_args_list[1].kwargs["payload"]["rewrite_after_rejection"]
        self.assertEqual(repair["codes"], ["wrong_date"])
        self.assertIn("2026-09-26", repair["detail"])

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_complete_scalar_answer_is_rendered_without_model_calls(self, call):
        shared = SharedModelContext(
            current_question="How many days did A1 work?",
            updated_request="Request:\nHow many days did A1 work?",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(ResultColumn(name="worked_days", type_code="20"),),
            rows=({"worked_days": 5},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=20
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT COUNT(DISTINCT attendance_date) AS worked_days",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertEqual(
            answer,
            "Wail Ali (A1) — worked days: 5. Attendance records are available "
            "from 2026-09-01 to 2026-09-07.",
        )
        call.assert_not_called()

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_month_scalar_explicitly_reports_partial_table_coverage(self, call):
        shared = SharedModelContext(
            current_question=("How many days did A1 work during September 2026?"),
            updated_request="Request:\nCount worked days for A1 in September 2026.",
            request_has_date_period=True,
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(ResultColumn(name="matched_count", type_code="20"),),
            rows=({"matched_count": 5},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=20
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT COUNT(DISTINCT attendance_date) AS matched_count",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertIn("not the full requested period", answer)
        self.assertIn("September 1-7, 2026", answer)
        call.assert_not_called()

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_scalar_matched_count_uses_the_question_as_its_metric(self, call):
        shared = SharedModelContext(
            current_question="On how many dates did A1 have positive worked hours?",
            updated_request="Request:\nCount positive worked dates for A1.",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(ResultColumn(name="matched_count", type_code="20"),),
            rows=({"matched_count": 5},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=20
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT COUNT(DISTINCT attendance_date) AS matched_count",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertEqual(
            answer,
            "Wail Ali (A1) — On how many dates did A1 have positive worked hours: "
            "5. Attendance records are available from 2026-09-01 to 2026-09-07.",
        )
        call.assert_not_called()

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_bounded_record_details_are_rendered_without_model_calls(self, call):
        shared = SharedModelContext(
            current_question="Show attendance for A1.",
            updated_request="Request:\nShow attendance for A1.",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(
                ResultColumn(name="record_id", type_code="25"),
                ResultColumn(name="matched_count", type_code="20"),
            ),
            rows=(
                {"record_id": "attendance:a1:one", "matched_count": 2},
                {"record_id": "attendance:a1:two", "matched_count": 2},
            ),
            coverage=ExecutionCoverage(
                fetched_rows=2, result_limit=100, response_bytes=100
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT record_id, COUNT(*) OVER() AS matched_count LIMIT 100",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertEqual(
            answer,
            "Wail Ali (A1) — 2 matching attendance records; returned 2 record IDs: "
            "attendance:a1:one, attendance:a1:two. Attendance records are available "
            "from 2026-09-01 to 2026-09-07.",
        )
        call.assert_not_called()

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_complete_record_rows_without_window_count_are_rendered_locally(self, call):
        shared = SharedModelContext(
            current_question="Which A1 records look abnormal?",
            updated_request="Request:\nWhich A1 records look abnormal?",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(
                ResultColumn(name="record_id", type_code="25"),
                ResultColumn(name="attendance_date", type_code="1082"),
                ResultColumn(name="exception", type_code="25"),
            ),
            rows=(
                {
                    "record_id": "attendance:a1:one",
                    "attendance_date": "2026-09-01",
                    "exception": "Lateness",
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=100
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT record_id, attendance_date, exception FROM attendance_records",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertIn("Wail Ali (A1)", answer)
        self.assertIn("attendance:a1:one", answer)
        self.assertIn("attendance date=2026-09-01", answer)
        self.assertIn("exception=Lateness", answer)
        call.assert_not_called()

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_complete_multi_metric_row_is_rendered_locally(self, call):
        shared = SharedModelContext(
            current_question="Find concerning overtime behavior for A1.",
            updated_request="Request:\nFind concerning overtime behavior for A1.",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(
                ResultColumn(name="concerning_days", type_code="20"),
                ResultColumn(name="total_concerning_hours", type_code="701"),
            ),
            rows=(({"concerning_days": 5, "total_concerning_hours": 45.03}),),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=80
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT 5 AS concerning_days, 45.03 AS total_concerning_hours",
            result=result,
            employees=(Employee(employee_id="A1", name="Wail Ali"),),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertIn("concerning days=5", answer)
        self.assertIn("total concerning hours=45.03", answer)
        call.assert_not_called()

    @patch("week5.new_implementation.online.answering.call_structured")
    def test_answer_repair_adds_missing_database_coverage_bounds(self, call):
        call.side_effect = [
            AnswerDraft(answer="Engineering had 56 hours."),
            AnswerDraft(
                answer="Engineering had 56 hours. Records are available from 2026-09-01 to 2026-09-07."
            ),
            VerdictResponse(decision=VerdictPass()),
        ]
        shared = SharedModelContext(
            current_question="Group hours by department.",
            updated_request="Request:\nGroup hours by department.",
            database_context=database_context(),
        )
        result = SqlExecutionResult(
            columns=(),
            rows=({"hours": 56},),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=14
            ),
        )

        answer = generate_answer(
            shared_context=shared,
            sql="SELECT 56 AS hours",
            result=result,
            employees=(),
            locale="en",
            model="answer-model",
            budget=CallBudget(),
            timeout=1,
            max_output_tokens=100,
        )

        self.assertIn("2026-09-01 to 2026-09-07", answer)
        self.assertEqual(call.call_count, 3)


if __name__ == "__main__":
    unittest.main()
