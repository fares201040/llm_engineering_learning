import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from week5.new_implementation import answer
from week5.new_implementation import conversation_understanding as conversation
from week5.new_implementation.decision_budget import (
    DecisionBudgetExhausted,
    claim_provider_call,
    decision_budget_scope,
)
from week5.new_implementation.observability import EventLogger


def _response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content),
            )
        ]
    )


class DecisionBudgetTests(unittest.TestCase):
    def test_nested_scopes_share_hard_three_call_limit(self):
        with decision_budget_scope():
            claim_provider_call()
            with decision_budget_scope():
                claim_provider_call()
                claim_provider_call()
                with self.assertRaises(DecisionBudgetExhausted):
                    claim_provider_call()

    def test_standalone_claim_remains_usable(self):
        claim_provider_call()
        claim_provider_call()


class GeneratedSqlFallbackTests(unittest.TestCase):
    def test_resolved_employee_identity_is_redacted_from_production_prompt(self):
        generated = _response(
            '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) AS value '
            'FROM attendance_scope"}'
        )
        cases = (
            ("sam", "how many total working hours for sam"),
            ("رامي", "مجموع ساعات الشغل للموظف رامي"),
        )

        for name, question in cases:
            with (
                self.subTest(name=name),
                patch.object(
                    answer, "completion", return_value=generated
                ) as completion,
                patch.object(answer, "_postgres_enabled", return_value=True),
                patch.object(
                    answer,
                    "load_employee_directory",
                    return_value=[
                        answer.EmployeeCandidate(employee_id="A10001", name=name)
                    ],
                ),
                patch.object(
                    answer, "load_attendance_catalog_candidates", return_value={}
                ),
            ):
                prepared = answer._prepare_context_request(question)

            prompt = completion.call_args.kwargs["messages"][0]["content"]
            self.assertNotIn(name, prompt)
            self.assertNotIn("A10001", prompt)
            self.assertEqual(
                prepared.postgres_queries.aggregation[0].params,
                ("A10001",),
            )

    def test_pure_arabic_localized_operation_can_use_request_local_field_fallback(self):
        generated = _response(
            '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) AS value '
            'FROM attendance_scope"}'
        )
        with (
            patch.object(answer, "completion", return_value=generated) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("مجموع ساعات الشغل")

        self.assertEqual(prepared.plan.aggregation, "sum")
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        calculation = next(
            fact for fact in prepared.facts if fact.kind == "calculation"
        )
        self.assertEqual(calculation.origin, "provider_decision")
        self.assertEqual(
            answer.analyze_question_surface(prepared.question).reply_locale, "ar"
        )
        completion.assert_called_once()

    def test_arabic_dominant_mixed_language_fallback_preserves_arabic_locale(self):
        question = "فضلا احسب مجموع total working hours"
        generated = _response(
            '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) AS value '
            'FROM attendance_scope"}'
        )
        with (
            patch.object(answer, "completion", return_value=generated) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(
                answer,
                "execute_exact_postgres",
                return_value=(
                    [],
                    {"operation": "sum", "field": "Total_Worked_Hrs", "value": 8.0},
                    1,
                ),
            ) as execute,
        ):
            text, chunks, state = answer.answer_question_with_state(
                question, [], answer.ConversationState()
            )

        self.assertEqual(chunks, [])
        self.assertRegex(text, r"[؀-ۿ]")
        self.assertEqual(state.recent_frames[-1].reply_locale, "ar")
        plan = execute.call_args.args[0]
        self.assertEqual(plan.aggregation, "sum")
        self.assertEqual(plan.aggregation_field, "Total_Worked_Hrs")
        completion.assert_called_once()

    def test_already_grounded_arabic_field_keeps_zero_call_deterministic_path(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("مجموع ساعات العمل")

        self.assertEqual(prepared.plan.aggregation, "sum")
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        calculation = next(
            fact for fact in prepared.facts if fact.kind == "calculation"
        )
        self.assertEqual(calculation.origin, "question")
        completion.assert_not_called()

    def test_arabic_field_ambiguity_resumes_without_another_provider_call(self):
        ambiguous = _response('{"status":"ambiguous","candidate_ids":["field-1"]}')
        with (
            patch.object(answer, "completion", return_value=ambiguous),
            patch.object(answer, "_postgres_enabled", return_value=True),
        ):
            text, chunks, state = answer.answer_question_with_state(
                "مجموع ساعات الشغل", [], answer.ConversationState()
            )

        self.assertTrue(text)
        self.assertEqual(chunks, [])
        self.assertEqual(state.pending_request.reply_locale, "ar")
        self.assertIsInstance(
            state.pending_request.clarification, answer.MeaningClarification
        )
        self.assertEqual(
            state.pending_request.clarification.options[0].target_kind, "field"
        )

        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(
                answer,
                "execute_exact_postgres",
                return_value=(
                    [],
                    {"operation": "sum", "field": "Total_Worked_Hrs", "value": 8.0},
                    1,
                ),
            ),
        ):
            resumed_text, _chunks, resumed = answer.answer_question_with_state(
                "1", [], state
            )

        completion.assert_not_called()
        self.assertIn("8", resumed_text)
        self.assertRegex(resumed_text, r"[؀-ۿ]")
        self.assertIsNone(resumed.pending_request)

    def test_contextual_he_has_fallback_retains_employee_scope(self):
        generated = _response(
            '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) AS value '
            'FROM attendance_scope"}'
        )
        contextual = conversation.MaterializedConversationUnit(
            unit_id="context-1",
            route="attendance",
            relation="new",
            source_text="how many total working hours he has",
            employees=(
                answer.EmployeeReferent(employee_id="A10001", name="Private Name"),
            ),
        )
        with (
            patch.object(answer, "completion", return_value=generated) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._answer_question_with_state(
                "ignored",
                [],
                answer.ConversationState(),
                contextual_unit=contextual,
                preparation_only=True,
            )

        self.assertIsInstance(prepared, answer.PreparedContextRequest)
        self.assertEqual(prepared.plan.aggregation, "sum")
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        employee_filter = next(
            condition
            for condition in prepared.plan.filters
            if condition.field == "Employee_ID"
        )
        self.assertEqual(employee_filter.value, "A10001")
        self.assertEqual(prepared.postgres_queries.aggregation[0].params, ("A10001",))
        completion.assert_called_once()

    def test_multiple_unresolved_meanings_inside_field_phrase_block_fallback(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            self.assertRaises(answer.SurfaceMeaningClarificationRequired),
        ):
            answer._prepare_context_request("total workd working hours")

        completion.assert_not_called()

    def test_unresolved_meaning_outside_primary_field_phrase_blocks_fallback(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            self.assertRaises(answer.SurfaceMeaningClarificationRequired) as raised,
        ):
            answer._prepare_context_request("how many total working hours workd")

        completion.assert_not_called()
        self.assertEqual(raised.exception.candidates[0].target_kind, "predicate")
        self.assertEqual(raised.exception.candidates[0].evidence_text, "workd")

    def test_total_working_hours_uses_trusted_sum_and_bound_employee_scope(self):
        generated = _response(
            '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) AS value FROM attendance_scope"}'
        )
        employee = answer.EmployeeCandidate(employee_id="A10001", name="Private Name")
        with (
            patch.object(answer, "completion", return_value=generated) as completion,
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_employee_directory", return_value=[employee]),
        ):
            prepared = answer._prepare_context_request(
                "how many total working hours for A10001",
                default_employees=[employee],
            )

        self.assertEqual(prepared.plan.aggregation, "sum")
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        calculation = next(f for f in prepared.facts if f.kind == "calculation")
        self.assertEqual(calculation.origin, "provider_decision")
        query = prepared.postgres_queries.aggregation[0]
        self.assertIn("SUM(total_worked_hrs)", query.sql)
        self.assertNotIn("attendance_scope", query.sql)
        self.assertEqual(query.params, ("A10001",))
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 0)
        self.assertEqual(kwargs["num_retries"], 0)
        self.assertLessEqual(kwargs["max_tokens"], 1200)

    def test_grounded_and_postgres_disabled_paths_make_no_fallback_call(self):
        for question, enabled in (
            ("What is total overtime?", True),
            ("how many total working hours", False),
        ):
            with (
                self.subTest(question=question, enabled=enabled),
                patch.object(answer, "completion") as completion,
                patch.object(
                    answer, "load_attendance_catalog_candidates", return_value={}
                ),
                patch.object(answer, "_postgres_enabled", return_value=enabled),
            ):
                if enabled:
                    answer._prepare_context_request(question)
                else:
                    with self.assertRaises(answer.SemanticPlanValidationError):
                        answer._prepare_context_request(question)
            completion.assert_not_called()

    def test_invalid_generation_repairs_twice_then_clarifies_without_retrieval(self):
        responses = [
            _response("PRIVATE malformed response"),
            _response(
                '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) AS value '
                "FROM attendance_records WHERE employee_id = 'PRIVATE'\"}"
            ),
            _response('{"status":"ambiguous","candidate_ids":["unknown-private-id"]}'),
        ]
        with (
            patch.object(answer, "completion", side_effect=responses) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "execute_exact_postgres") as execute,
            self.assertRaises(answer.SurfaceMeaningClarificationRequired),
        ):
            answer._fetch_context_result("how many total working hours")

        self.assertEqual(completion.call_count, 3)
        execute.assert_not_called()
        first_prompt = completion.call_args_list[0].kwargs["messages"][0]["content"]
        self.assertIn("how many total working hours", first_prompt)
        for repair in completion.call_args_list[1:]:
            prompt = repair.kwargs["messages"][0]["content"]
            self.assertNotIn("how many total working hours", prompt)
        self.assertNotIn(
            "PRIVATE", completion.call_args_list[1].kwargs["messages"][0]["content"]
        )
        self.assertIn(
            "prior_sql", completion.call_args_list[2].kwargs["messages"][0]["content"]
        )

    def test_valid_ambiguity_and_unsupported_do_not_retry_or_retrieve(self):
        cases = (
            '{"status":"ambiguous","candidate_ids":["field-1"]}',
            '{"status":"unsupported"}',
        )
        for payload in cases:
            with (
                self.subTest(payload=payload),
                patch.object(
                    answer, "completion", return_value=_response(payload)
                ) as call,
                patch.object(answer, "_postgres_enabled", return_value=True),
                patch.object(answer, "execute_exact_postgres") as execute,
            ):
                with self.assertRaises(
                    (
                        answer.SurfaceMeaningClarificationRequired
                        if "ambiguous" in payload
                        else answer.SemanticPlanValidationError
                    )
                ):
                    answer._fetch_context_result("how many total working hours")
            self.assertEqual(call.call_count, 1)
            execute.assert_not_called()

    def test_initial_prompt_redacts_private_question_values(self):
        question = (
            "how many total working hours for A10001 Private Name on 2026-09-01 "
            "where Department is Engineering"
        )
        facts = (
            answer.SemanticFact(
                kind="unsupported",
                concept_name="unsupported_calculation",
                evidence_text="total",
                origin="question",
                strength="strong",
            ),
            answer.SemanticFact(
                kind="filter",
                field="Department",
                operator="eq",
                values=("Engineering",),
                evidence_text="Engineering",
                origin="question",
                strength="strong",
            ),
        )
        candidates = answer._generated_aggregate_candidates(question, facts)
        prompt = answer._generated_aggregate_prompt(question, facts, candidates)

        for secret in (
            "A10001",
            "Private Name",
            "2026-09-01",
            "Engineering",
            answer.POSTGRES_ATTENDANCE_TABLE,
            answer.POSTGRES_DSN,
        ):
            if secret:
                self.assertNotIn(secret, prompt)
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(payload["operation"], "sum")
        self.assertEqual(payload["logical_table"], "attendance_scope")

    def test_prompt_redacts_relative_presentation_and_configured_database_text(self):
        dsn = "postgresql://private-user:private-pass@private-host/private-db"
        table = "private_schema.private_attendance"
        question = (
            "how many total working hours yesterday on 2026/09/15 and 15-09-2026 "
            f"using {table} at {dsn}"
        )
        facts = (
            answer.SemanticFact(
                kind="unsupported",
                concept_name="unsupported_calculation",
                evidence_text="total",
                origin="question",
                strength="strong",
            ),
        )
        candidates = answer._generated_aggregate_candidates(question, facts)
        with (
            patch.object(answer, "POSTGRES_DSN", dsn),
            patch.object(answer, "POSTGRES_ATTENDANCE_TABLE", table),
        ):
            prompt = answer._generated_aggregate_prompt(question, facts, candidates)

        for private in (
            "yesterday",
            "2026/09/15",
            "15-09-2026",
            table,
            dsn,
            "private-user",
            "private-host",
        ):
            self.assertNotIn(private, prompt)

    def test_missing_finish_reason_is_repaired_and_never_accepted_as_ready(self):
        missing_finish = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) '
                            'AS value FROM attendance_scope"}'
                        )
                    )
                )
            ]
        )
        ready = _response(
            '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) AS value '
            'FROM attendance_scope"}'
        )
        with (
            patch.object(
                answer, "completion", side_effect=[missing_finish, ready]
            ) as call,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("how many total working hours")

        self.assertEqual(call.call_count, 2)
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")

    def test_redaction_does_not_mutate_original_analyst_question(self):
        question = "how many total working hours yesterday"
        ready = _response(
            '{"status":"ready","sql":"SELECT SUM(Total_Worked_Hrs) AS value '
            'FROM attendance_scope"}'
        )
        with (
            patch.object(answer, "completion", return_value=ready),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request(question)

        self.assertEqual(prepared.question, question)

    def test_contextual_call_leaves_at_most_two_sql_calls(self):
        request = conversation.ConversationDecisionContext(
            message="repeat it", max_units=1
        )
        conversation_response = _response(
            '{"status":"ambiguous","reason":"missing_context"}'
        )
        invalid_sql = _response("not json")
        with (
            decision_budget_scope(),
            patch.object(
                conversation, "completion", return_value=conversation_response
            ),
            patch.object(answer, "completion", return_value=invalid_sql) as sql_call,
            patch.object(answer, "_postgres_enabled", return_value=True),
        ):
            conversation.request_conversation_decision(request)
            with self.assertRaises(answer.SurfaceMeaningClarificationRequired):
                answer._prepare_context_request("how many total working hours")
        self.assertEqual(sql_call.call_count, 2)

    def test_state_and_telemetry_never_retain_provider_sql_or_response(self):
        raw_sql = "SELECT SUM(Total_Worked_Hrs) AS value FROM attendance_scope"
        events = []
        logger = EventLogger(sink=events.append, json_format=True)
        with (
            patch.object(answer, "event_logger", logger),
            patch.object(
                answer,
                "completion",
                return_value=_response(json.dumps({"status": "ready", "sql": raw_sql})),
            ),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("how many total working hours")

        rendered = "\n".join(events)
        self.assertNotIn(raw_sql, rendered)
        self.assertNotIn("how many total working hours", rendered)
        self.assertIn("Total_Worked_Hrs", rendered)
        self.assertNotIn(raw_sql, prepared.plan.model_dump_json())
        self.assertNotIn(raw_sql, repr(prepared.facts))

    def test_planning_request_uses_remaining_shared_budget_only(self):
        response = _response('{"status":"resolved","selections":[]}')
        with (
            decision_budget_scope(),
            patch.object(answer, "completion", return_value=response) as call,
        ):
            claim_provider_call()
            claim_provider_call()
            answer._request_planning_decision("safe prompt")
            with self.assertRaises(DecisionBudgetExhausted):
                answer._request_planning_decision("safe prompt")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs["num_retries"], 0)
        self.assertLessEqual(call.call_args.kwargs["max_tokens"], 1200)

    def test_provider_failure_is_typed_and_public_state_is_unchanged(self):
        with (
            patch.object(answer, "completion", side_effect=TimeoutError("PRIVATE")),
            patch.object(answer, "_postgres_enabled", return_value=True),
            self.assertRaises(answer.GeneratedSqlProviderError) as raised,
        ):
            answer.fetch_context("how many total working hours")
        self.assertNotIn("PRIVATE", str(raised.exception))

        original = answer.ConversationState()
        with (
            patch.object(answer, "completion", side_effect=RuntimeError("PRIVATE")),
            patch.object(answer, "_postgres_enabled", return_value=True),
        ):
            text, chunks, returned = answer.answer_question_with_state(
                "how many total working hours", [], original
            )
        self.assertIn("could not complete", text)
        self.assertEqual(chunks, [])
        self.assertEqual(returned, original)

    def test_field_clarification_resumes_without_provider_call(self):
        ambiguous = _response('{"status":"ambiguous","candidate_ids":["field-1"]}')
        with (
            patch.object(answer, "completion", return_value=ambiguous),
            patch.object(answer, "_postgres_enabled", return_value=True),
        ):
            _text, _chunks, state = answer.answer_question_with_state(
                "how many total working hours", [], answer.ConversationState()
            )
        self.assertIsInstance(
            state.pending_request.clarification, answer.MeaningClarification
        )
        self.assertEqual(
            state.pending_request.clarification.options[0].target_kind, "field"
        )

        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(
                answer,
                "execute_exact_postgres",
                return_value=(
                    [],
                    {"operation": "sum", "field": "Total_Worked_Hrs", "value": 8.0},
                    1,
                ),
            ),
        ):
            text, _chunks, resumed = answer.answer_question_with_state("1", [], state)
        completion.assert_not_called()
        self.assertIn("8", text)
        self.assertIsNone(resumed.pending_request)


if __name__ == "__main__":
    unittest.main()
