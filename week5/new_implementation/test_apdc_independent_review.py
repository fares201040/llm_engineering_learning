import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from week5.new_implementation import answer


def _response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content),
            )
        ]
    )


class AggregateDecisionBoundaryReviewTests(unittest.TestCase):
    def test_grounded_count_needs_no_provider_field_decision(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("count attendance records")

        self.assertEqual(prepared.plan.aggregation, "count")
        self.assertIsNone(prepared.plan.aggregation_field)
        self.assertIn("COUNT(*)", prepared.postgres_queries.aggregation[0].sql)
        completion.assert_not_called()

    def test_repair_prompt_repeats_operation_and_request_local_candidates(self):
        prompts = []

        def respond(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            prompts.append(prompt)
            if len(prompts) == 1:
                return _response("not json")
            payload = json.loads(prompt.split("\n", 1)[-1])
            self.assertNotIn("question", payload)
            self.assertEqual(payload["operation"], "sum")
            self.assertEqual(payload["candidates"][0]["candidate_id"], "field-1")
            self.assertEqual(payload["candidates"][0]["storage_type"], "number")
            self.assertTrue(payload["candidates"][0]["description"])
            self.assertTrue(payload["candidates"][0]["output_unit"])
            self.assertTrue(payload["candidates"][0]["natural_names"])
            self.assertNotIn("field", payload["candidates"][0])
            self.assertEqual(payload["validation_code"], "invalid_schema")
            return _response(
                '{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}'
            )

        with (
            patch.object(answer, "completion", side_effect=respond),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("sum total working hours")

        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        self.assertEqual(len(prompts), 2)
        self.assertNotIn("not json", prompts[1])
        self.assertNotIn("prior_sql", prompts[1])
        self.assertNotIn("SELECT", prompts[1].upper())

    def test_resolved_candidate_id_compiles_through_trusted_parameterized_aggregate(
        self,
    ):
        generated = _response(
            '{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}'
        )
        with (
            patch.object(answer, "completion", return_value=generated) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("sum total working hours")

        prompt = completion.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("SELECT", prompt.upper())
        payload = json.loads(prompt.split("\n", 1)[-1])
        self.assertNotIn("question", payload)
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        self.assertIn(
            "SUM(total_worked_hrs)", prepared.postgres_queries.aggregation[0].sql
        )

    def test_typed_provider_payload_excludes_question_and_diverse_suffixes(self):
        generated = _response(
            '{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}'
        )
        base_question = "sum total working hours"
        suffixes = (
            " WHERE secret = canary_where",
            " VALUES (canary_values)",
            " CALL canary_call()",
            " EXECUTE canary_execute",
            " PRAGMA table_info(canary_pragma)",
            " EXPLAIN SELECT canary_explain FROM secret_table",
            " CREATE TABLE canary_schema (secret text)",
            " ALTER TABLE canary_schema ADD COLUMN canary_column text",
            "; CREATE TABLE canary_statement (secret text)",
            "; DROP TABLE canary_drop",
        )
        prompts = []
        plans = []

        def respond(**kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return generated

        with (
            patch.object(answer, "completion", side_effect=respond),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            base = answer._prepare_context_request(base_question)
            plans.append(base)
            for suffix in suffixes:
                plans.append(answer._prepare_context_request(base_question + suffix))

        self.assertEqual(len(prompts), len(suffixes) + 1)
        expected = (
            base.plan.aggregation,
            base.plan.aggregation_field,
            base.postgres_queries.aggregation[0].sql,
            base.postgres_queries.aggregation[0].params,
        )
        for prepared in plans:
            self.assertEqual(
                (
                    prepared.plan.aggregation,
                    prepared.plan.aggregation_field,
                    prepared.postgres_queries.aggregation[0].sql,
                    prepared.postgres_queries.aggregation[0].params,
                ),
                expected,
            )
        for prompt in prompts:
            payload = json.loads(prompt.split("\n", 1)[-1])
            self.assertNotIn("question", payload)
            self.assertEqual(payload["operation"], "sum")
            self.assertNotIn(base_question, prompt.lower())
            self.assertNotIn("canary", prompt.lower())
            self.assertNotIn("secret_table", prompt.lower())
            self.assertNotIn("employee_table", prompt.lower())

    def test_ordinary_from_and_field_phrasing_keeps_overtime_field_grounded(self):
        for question in ("sum hours from overtime", "sum the overtime field"):
            with (
                self.subTest(question=question),
                patch.object(answer, "completion") as completion,
                patch.object(answer, "_postgres_enabled", return_value=True),
                patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            ):
                prepared = answer._prepare_context_request(question)

            self.assertEqual(prepared.plan.aggregation, "sum")
            self.assertEqual(prepared.plan.aggregation_field, "Total_OT")
            self.assertIn("SUM(total_ot)", prepared.postgres_queries.aggregation[0].sql)
            completion.assert_not_called()

    def test_conflicting_operation_suffix_fails_closed_without_provider_call(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            self.assertRaises(answer.SemanticPlanValidationError),
        ):
            answer._prepare_context_request(
                "sum total working hours EXPLAIN SELECT average overtime"
            )

        completion.assert_not_called()

    def test_unrelated_unresolved_meaning_is_not_ignored_for_aggregate_subject(self):
        unresolved = answer.SurfaceCandidate(
            candidate_id="unresolved-interpretation",
            target_kind="interpretation",
            target_name="absent_days",
            evidence_text="absent",
            evidence_span=(24, 30),
            method="fuzzy",
            score=0.8,
        )
        selected = answer.SurfaceCandidate(
            candidate_id="selected-field",
            target_kind="field",
            target_name="Total_Worked_Hrs",
            evidence_text="total working hours",
            evidence_span=(4, 23),
            method="fuzzy",
            score=0.8,
        )
        candidate = answer._GeneratedAggregateCandidate(
            "field-1", "Total_Worked_Hrs", selected
        )
        with patch.object(
            answer, "_unresolved_surface_candidates", return_value=(unresolved,)
        ):
            self.assertTrue(
                answer._generated_aggregate_has_external_ambiguity(
                    "sum total working hours absent", (candidate,)
                )
            )

    def test_provider_and_default_operation_conflict_fails_closed(self):
        facts = (
            answer.SemanticFact(
                kind="calculation",
                field="Total_Worked_Hrs",
                concept_name="sum",
                evidence_text="provider sum",
                origin="provider_decision",
                strength="strong",
            ),
            answer.SemanticFact(
                kind="calculation",
                field="Total_Worked_Hrs",
                concept_name="average",
                evidence_text="default average",
                origin="deterministic_default",
                strength="strong",
            ),
        )
        self.assertIsNone(
            answer._generated_aggregate_operation("sum total working hours", facts)
        )

    def test_trusted_operation_conflicting_with_surface_fails_closed(self):
        for origin in ("trusted_state", "user_clarification"):
            with self.subTest(origin=origin):
                facts = (
                    answer.SemanticFact(
                        kind="calculation",
                        field="Total_Worked_Hrs",
                        concept_name="average",
                        evidence_text="trusted average",
                        origin=origin,
                        strength="strong",
                    ),
                )
                self.assertIsNone(
                    answer._generated_aggregate_operation(
                        "sum total working hours", facts
                    )
                )

    def test_postfix_registered_field_does_not_replace_intended_subject(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            self.assertRaises(answer.SurfaceMeaningClarificationRequired),
        ):
            answer._prepare_context_request("sum total working hours overtime")

        completion.assert_not_called()

    def test_separate_field_suffix_constraint_is_retained_and_fails_closed(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            self.assertRaises(answer.SemanticPlanValidationError),
        ):
            answer._prepare_context_request(
                "sum total working hours and overtime field"
            )

        completion.assert_not_called()

    def test_generated_sql_provider_error_name_remains_compatible(self):
        self.assertIs(
            answer.GeneratedSqlProviderError,
            answer.GeneratedAggregateProviderError,
        )
        with (
            patch.object(answer, "completion", side_effect=TimeoutError("PRIVATE")),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            self.assertRaises(answer.GeneratedSqlProviderError) as raised,
        ):
            answer._prepare_context_request("sum total working hours")
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_generated_fallback_count_with_candidates_has_no_field_surface_lookup(self):
        unsupported = answer.SemanticFact(
            kind="unsupported",
            concept_name="unsupported_calculation",
            evidence_text="count",
            origin="question",
            strength="strong",
        )
        surface = answer.SurfaceCandidate(
            candidate_id="surface-field-1",
            target_kind="field",
            target_name="Total_Worked_Hrs",
            evidence_text="working hours",
            evidence_span=(0, 14),
            method="fuzzy",
            score=0.8,
        )
        candidate = answer._GeneratedAggregateCandidate(
            "field-1", "Total_Worked_Hrs", surface
        )
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(answer, "_facts_from_question_surface", return_value=()),
            patch.object(answer, "detect_semantic_facts", return_value=()),
            patch.object(
                answer, "_generated_aggregate_operation", return_value="count"
            ),
            patch.object(
                answer,
                "_generated_aggregate_candidates",
                return_value=(candidate,),
            ),
            patch.object(
                answer,
                "_generated_aggregate_has_external_ambiguity",
                return_value=False,
            ),
        ):
            prepared = answer._prepare_context_request(
                "count attendance records", prepared_facts=(unsupported,)
            )

        self.assertEqual(prepared.plan.aggregation, "count")
        self.assertIsNone(prepared.plan.aggregation_field)
        self.assertIn("COUNT(*)", prepared.postgres_queries.aggregation[0].sql)
        completion.assert_not_called()
        count_facts = tuple(
            fact
            for fact in prepared.facts
            if fact.kind == "calculation" and fact.concept_name == "count"
        )
        self.assertEqual(len(count_facts), 1)
        self.assertIsNone(count_facts[0].field)
        self.assertEqual(count_facts[0].origin, "deterministic_default")
        self.assertEqual(count_facts[0].strength, "strong")

    def test_public_meaning_clarification_resume_makes_progress(self):
        question = "sum total working hours overtime"

        def execute(prepared, *, resources=None):
            del resources
            return answer.ContextFetchResult(
                chunks=[],
                plan=prepared.plan,
                aggregation={
                    "operation": prepared.plan.aggregation,
                    "field": prepared.plan.aggregation_field,
                    "value": 12,
                },
                matched_count=1,
                resolved_employees=[],
                facts=prepared.facts,
            )

        with (
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(answer, "_execute_prepared_context", side_effect=execute),
        ):
            first_text, first_chunks, pending = answer.answer_question_with_state(
                question, [], None
            )
            self.assertEqual(first_chunks, [])
            self.assertIsNotNone(pending.pending_request)
            self.assertIsInstance(
                pending.pending_request.clarification,
                answer.MeaningClarification,
            )

            resumed_text, resumed_chunks, resumed = answer.answer_question_with_state(
                "1", [], pending
            )

        self.assertNotEqual(resumed_text, first_text)
        self.assertIn("12", resumed_text)
        self.assertEqual(resumed_chunks, [])
        self.assertIsNone(resumed.pending_request)

    def test_aggregate_operation_conflicts_reject_in_prepare_caller(self):
        cases = (
            (
                "trusted surface conflict",
                "sum total working hours overtime",
                (
                    answer.SemanticFact(
                        kind="calculation",
                        field="Total_Worked_Hrs",
                        concept_name="average",
                        evidence_text="trusted average",
                        origin="trusted_state",
                        strength="strong",
                    ),
                ),
            ),
            (
                "provider default conflict",
                "sum total working hours overtime",
                (
                    answer.SemanticFact(
                        kind="calculation",
                        field="Total_Worked_Hrs",
                        concept_name="sum",
                        evidence_text="provider sum",
                        origin="provider_decision",
                        strength="strong",
                    ),
                    answer.SemanticFact(
                        kind="calculation",
                        field="Total_Worked_Hrs",
                        concept_name="average",
                        evidence_text="default average",
                        origin="deterministic_default",
                        strength="strong",
                    ),
                ),
            ),
        )
        for label, question, facts in cases:
            with self.subTest(label=label):
                with (
                    patch.object(answer, "_postgres_enabled", return_value=True),
                    patch.object(
                        answer, "load_attendance_catalog_candidates", return_value={}
                    ),
                    self.assertRaises(answer.SemanticPlanValidationError) as raised,
                ):
                    answer._prepare_context_request(
                        question, prepared_facts=facts
                    )

                self.assertIn(
                    "contradiction",
                    {violation.code for violation in raised.exception.violations},
                )


class ArabicAggregationScopeReviewTests(unittest.TestCase):
    @staticmethod
    def _plan(employee, start, end, *, operation="count", field=None):
        unit = "records" if operation == "count" else "value"
        return answer.ExecutableQueryPlan(
            mode="exact",
            search_query="synthetic public fixture",
            filters=[
                answer.FilterCondition(field="Name", operator="eq", value=employee),
                answer.FilterCondition(field="Date", operator="gte", value=start),
                answer.FilterCondition(field="Date", operator="lte", value=end),
            ],
            aggregation=operation,
            aggregation_field=field,
            answer_contract=answer.AnswerContract(
                shape="scalar", unit=unit, subject_field=field
            ),
        )

    def test_direct_arabic_scalar_keeps_verified_employee_and_period_scope(self):
        plan = self._plan("Morgan River", "2026-09-01", "2026-09-07")

        text = answer._format_aggregation_answer(
            plan, {"operation": "count", "value": 3}, locale="ar"
        )

        self.assertIn("للموظف Morgan River", text)
        self.assertIn("خلال سبتمبر 1-7، 2026", text)
        self.assertNotIn("Taylor Stone", text)
        self.assertNotIn("أكتوبر", text)

    def test_arabic_compound_scalars_keep_their_distinct_verified_scopes(self):
        first_plan = self._plan("Morgan River", "2026-09-01", "2026-09-07")
        second_plan = self._plan("Taylor Stone", "2026-10-01", "2026-10-07")
        first_unit = answer.PendingRequestFrame(
            original_question="كم عدد سجلات الحضور لموظف مورغان؟",
            reply_locale="ar",
            unit_id="first",
            relation="new",
        )
        second_unit = answer.PendingRequestFrame(
            original_question="كم عدد سجلات الحضور لموظف تايلور؟",
            reply_locale="ar",
            unit_id="second",
            relation="new",
        )
        prepared = answer.TurnPreparationResult(
            turn=answer.PreparedTurn((None, None)),
            blockers=(),
            units=(first_unit, second_unit),
        )
        execution = answer.TurnExecutionResult(
            results=(
                answer.ContextFetchResult(
                    chunks=[],
                    plan=first_plan,
                    aggregation={"operation": "count", "value": 3},
                    matched_count=3,
                    resolved_employees=[],
                ),
                answer.ContextFetchResult(
                    chunks=[],
                    plan=second_plan,
                    aggregation={"operation": "count", "value": 3},
                    matched_count=3,
                    resolved_employees=[],
                ),
            )
        )

        with (
            patch.object(answer, "_prepare_turn", return_value=prepared),
            patch.object(answer, "_execute_prepared_turn", return_value=execution),
        ):
            text, chunks, state = answer._answer_compound_turn(
                "كم عدد سجلات الحضور لموظف مورغان؛ كم عدد سجلات الحضور لموظف تايلور",
                (first_unit, second_unit),
                answer.ConversationState(),
                locale="ar",
            )

        paragraphs = text.split("\n\n")
        self.assertEqual(len(paragraphs), 2)
        self.assertIn("للموظف Morgan River", paragraphs[0])
        self.assertIn("خلال سبتمبر 1-7، 2026", paragraphs[0])
        self.assertNotIn("Taylor Stone", paragraphs[0])
        self.assertNotIn("أكتوبر", paragraphs[0])
        self.assertIn("للموظف Taylor Stone", paragraphs[1])
        self.assertIn("خلال أكتوبر 1-7، 2026", paragraphs[1])
        self.assertNotIn("Morgan River", paragraphs[1])
        self.assertNotIn("سبتمبر", paragraphs[1])
        self.assertEqual(chunks, [])
        self.assertIsNone(state.pending_request)


if __name__ == "__main__":
    unittest.main()
