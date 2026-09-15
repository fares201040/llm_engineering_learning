"""Synthetic regressions for compile-first, atomic attendance turns."""

import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from week5.new_implementation import answer


def segmented_decision(request):
    """Fake only the provider: the real validator owns spans and fact coverage."""
    c = answer.conversation
    units = []
    start = 0
    for clause in request.context.message.split(";"):
        end = start + len(clause)
        units.append(
            {
                "route": "attendance",
                "relation": "new",
                "source_span": (start, end),
                "fact_ids": tuple(
                    key
                    for key, fact in request.facts
                    if fact.strength == "strong"
                    and fact.evidence_span
                    and start <= fact.evidence_span[0] < end
                ),
            }
        )
        mentions = []
        for binding in request.context.employee_span_choices:
            if start <= binding.source_span[0] < end:
                mentions.append(
                    {
                        "kind": "reference_choice",
                        "source_span": binding.source_span,
                        "choice_id": binding.choice_ids[0],
                    }
                    if binding.choice_ids
                    else {"kind": "resolve", "source_span": binding.source_span}
                )
        units[-1]["employee_mentions"] = tuple(mentions)
        start = end + 1
    decision = c.validate_conversation_decision(
        c.ConversationDecision.model_validate({"status": "resolved", "units": units}),
        request.context,
    )
    return c.ValidatedConversation(
        request, decision, tuple(f"unit-{i}" for i in range(len(units)))
    )


class CompoundTurnTests(unittest.TestCase):
    question = "count attendance records; count distinct dates"

    def setUp(self):
        self.stack = __import__("contextlib").ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("load_attendance_catalog_candidates", {}),
            ("load_employee_directory", []),
            ("_postgres_enabled", False),
        ):
            self.stack.enter_context(patch.object(answer, name, return_value=value))
        self.provider = self.stack.enter_context(
            patch.object(
                answer.conversation,
                "request_conversation_decision",
                side_effect=segmented_decision,
            )
        )
        self.collection = self.stack.enter_context(patch.object(answer, "collection"))
        self.stack.enter_context(
            patch.object(
                answer,
                "completion",
                side_effect=AssertionError("unexpected network completion"),
            )
        )
        self.collection.get.return_value = {
            "documents": ["one", "two", "three"],
            "metadatas": [
                {"domain": "attendance", "chunk_type": "attendance_record", "Date": day}
                for day in ("2026-09-01", "2026-09-01", "2026-09-02")
            ],
        }

    def test_public_turn_prepares_all_before_retrieval_and_commits_one_complete_frame(
        self,
    ):
        events = []
        original = answer.revalidate_executable_plan

        def validate(*args, **kwargs):
            events.append("validated")
            return original(*args, **kwargs)

        stored = self.collection.get.return_value
        self.collection.get.side_effect = (
            lambda **kw: events.append("retrieved") or stored
        )
        initial = answer.ConversationState()
        with patch.object(answer, "revalidate_executable_plan", side_effect=validate):
            text, _, state = answer.answer_question_with_state(
                self.question, [], initial
            )
        self.assertEqual(len(state.recent_frames), 1, text)
        self.assertEqual(
            [unit.result.scalar_value for unit in state.recent_frames[0].units], [3, 2]
        )
        self.assertEqual(events, ["validated", "validated", "retrieved"])
        self.assertEqual(initial, answer.ConversationState())
        self.assertEqual(state.recent_frames[0].original_question, self.question)

    def test_later_blocker_prevents_all_execution_and_retains_full_request(self):
        question = "count attendance records; attendance"
        text, chunks, state = answer.answer_question_with_state(question, [], None)
        self.assertIsNotNone(state.pending_request, text)
        self.assertEqual(state.pending_request.original_question, question)
        self.assertEqual(len(state.pending_request.compound_units), 2)
        self.assertEqual(state.recent_frames, [])
        self.assertEqual(chunks, [])
        self.collection.get.assert_not_called()

    def test_multiple_blockers_resume_in_order_without_provider_or_partial_results(
        self,
    ):
        question = "count attendance records; attendance; attendance"
        text, _, state = answer.answer_question_with_state(question, [], None)
        self.assertIsNotNone(state.pending_request, text)
        first_kind = state.pending_clarification.kind
        self.assertEqual(getattr(state.pending_request, "compound_index", None), 1)
        text, chunks, state = answer.answer_question_with_state("1", [], state)
        self.assertIsNotNone(state.pending_request, text)
        self.assertEqual(state.pending_request.compound_index, 2)
        self.assertEqual(state.pending_clarification.kind, first_kind)
        self.assertEqual(chunks, [])
        self.collection.get.assert_not_called()
        text, _, state = answer.answer_question_with_state("1", [], state)
        self.assertIsNone(state.pending_request, text)
        self.assertEqual(len(state.recent_frames[-1].units), 3)
        self.provider.assert_called_once()

    def test_execution_failure_preserves_state_and_hides_backend_payload(self):
        self.collection.get.side_effect = RuntimeError("private backend payload")
        initial = answer.ConversationState()
        text, chunks, state = answer.answer_question_with_state(
            self.question, [], initial
        )
        self.assertIn("could not complete", text.lower())
        self.assertNotIn("private", text)
        self.assertEqual(chunks, [])
        self.assertEqual(state, initial)

    def test_plan_budget_prevents_execution_and_state_changes(self):
        with patch.object(
            answer,
            "settings",
            replace(answer.settings, conversation_compiled_plan_limit=1),
        ):
            text, chunks, state = answer.answer_question_with_state(
                self.question, [], None
            )
        self.assertIn("split", text.lower())
        self.assertNotIn("1", text)
        self.assertEqual(chunks, [])
        self.assertEqual(state, answer.ConversationState())
        self.collection.get.assert_not_called()

    def test_preparation_enforces_unit_and_employee_budgets_without_retrieval(self):
        def employee(index):
            return answer.EmployeeReferent(
                employee_id=f"A1000{index}", name=f"Person {index}"
            )

        unit = answer.PendingRequestFrame(
            original_question="count attendance records",
            reply_locale="en",
            unit_id="one",
            relation="new",
            employees=(employee(1), employee(2)),
        )
        for settings, units in (
            (replace(answer.settings, conversation_unit_limit=1), (unit, unit)),
            (replace(answer.settings, conversation_employee_binding_limit=1), (unit,)),
        ):
            with (
                self.subTest(unit_count=len(units)),
                patch.object(answer, "settings", settings),
            ):
                prepared = answer._prepare_turn(units)
                self.assertIsNone(prepared.turn)
                self.assertIn("split", prepared.blockers[0].text)
        self.collection.get.assert_not_called()

    def test_two_uncertain_employees_resume_complete_compound_without_scope_loss(self):
        employees = [
            answer.EmployeeCandidate(employee_id="A10001", name="Morgan River"),
            answer.EmployeeCandidate(employee_id="A10002", name="Taylor Stone"),
        ]
        question = (
            "count attendance records for Morgan; count distinct dates for Taylor"
        )
        with patch.object(answer, "load_employee_directory", return_value=employees):
            text, _, state = answer.answer_question_with_state(question, [], None)
            self.assertIsNotNone(state.pending_clarification, text)
            self.assertEqual(state.pending_clarification.kind, "employee_selection")
            text, chunks, state = answer.answer_question_with_state("yes", [], state)
            self.assertIsNotNone(state.pending_clarification, text)
            self.assertIn("Taylor Stone", text)
            self.assertEqual(chunks, [])
            self.collection.get.assert_not_called()
            self.assertEqual(state.referents, [])
            text, _, state = answer.answer_question_with_state("yes", [], state)
        self.assertIsNone(state.pending_request, text)
        self.assertEqual(
            [
                [e.employee_id for e in u.employees]
                for u in state.recent_frames[-1].units
            ],
            [["A10001"], ["A10002"]],
        )
        self.assertEqual(state.recent_frames[-1].original_question, question)

    def test_catalog_blockers_resume_each_original_clause(self):
        self.collection.get.return_value = {"documents": [], "metadatas": []}
        question = "Show department Op; Show department Op"
        units = tuple(
            answer._pending_unit(
                answer.conversation.MaterializedConversationUnit(
                    unit_id=f"catalog-{index}",
                    route="attendance",
                    relation="new",
                    source_text=answer.conversation.materialize_unit_mask(
                        question, span
                    ),
                ),
                "en",
            )
            for index, span in enumerate(((0, 18), (20, len(question))))
        )
        propose = answer.propose_query

        def catalog_proposal(question, *args, **kwargs):
            if any(f.origin == "user_clarification" for f in kwargs["semantic_facts"]):
                return propose(question, *args, **kwargs)
            return answer.PlannerProposal(
                status="ready",
                filters=[
                    answer.ProposedFilter(
                        field="Department",
                        operator="eq",
                        value="Op",
                        evidence_text="Op",
                    )
                ],
                answer_contract=answer.AnswerContract(
                    shape="rows", unit="value", grain=[]
                ),
            )

        with (
            patch.object(
                answer,
                "load_attendance_catalog_candidates",
                return_value={"Department": ("Operations East", "Operations West")},
            ),
            patch.object(answer, "propose_query", side_effect=catalog_proposal),
            patch.object(
                answer,
                "completion",
                return_value=SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="No matching attendance records."
                            )
                        )
                    ]
                ),
            ),
        ):
            text, _, state = answer._answer_compound_turn(
                question, units, answer.ConversationState(), locale="en"
            )
            self.assertIsNotNone(state.pending_request, text)
            self.assertEqual(state.pending_clarification.kind, "catalog_value")
            text, _, state = answer.answer_question_with_state("1", [], state)
            self.assertIsNotNone(state.pending_request, text)
            self.collection.get.assert_not_called()
            text, _, state = answer.answer_question_with_state("2", [], state)
        self.assertIsNone(state.pending_request, text)
        self.assertEqual(len(state.recent_frames[-1].units), 2)
        filters = [
            [
                f.values
                for f in u.facts
                if f.field == "Department" and f.origin == "user_clarification"
            ]
            for u in state.recent_frames[-1].units
        ]
        self.assertEqual(filters, [[("Operations East",)], [("Operations West",)]])

    def test_pending_units_keep_original_mask_offsets(self):
        question = "count attendance records; workd"
        mask = answer.conversation.materialize_unit_mask(question, (26, len(question)))
        saved = answer._pending_unit(
            answer.conversation.MaterializedConversationUnit(
                unit_id="second",
                route="attendance",
                relation="new",
                source_text=mask,
            ),
            "en",
        )
        self.assertEqual(saved.original_question, mask)

    def test_later_fuzzy_meaning_preserves_source_span_and_resumes(self):
        question = "count attendance records; workd"
        text, _, state = answer.answer_question_with_state(question, [], None)
        self.assertIsNotNone(state.pending_request, text)
        pending = state.pending_clarification
        self.assertEqual(
            pending.original_question[
                pending.options[0].evidence_span[0] : pending.options[0].evidence_span[
                    1
                ]
            ],
            "workd",
        )
        self.assertEqual(pending.options[0].evidence_span, (26, 31))
        self.collection.get.assert_not_called()
        text, _, state = answer.answer_question_with_state("yes", [], state)
        self.assertIsNone(state.pending_request, text)
        self.assertEqual(len(state.recent_frames[-1].units), 2)

    def test_pending_meaning_survives_state_serialization(self):
        text, _, state = answer.answer_question_with_state(
            "count attendance records; workd", [], None
        )
        self.assertIsNotNone(state.pending_request, text)
        restored = answer.ConversationState.model_validate_json(state.model_dump_json())
        text, _, completed = answer.answer_question_with_state("yes", [], restored)
        self.assertIsNone(completed.pending_request, text)
        self.assertEqual(len(completed.recent_frames[-1].units), 2)

    def test_provider_only_employee_mentions_resume_without_resegmentation(self):
        question = "Morgan: count attendance records; Taylor: count distinct dates"
        employees = [
            answer.EmployeeCandidate(employee_id="A10001", name="Morgan River"),
            answer.EmployeeCandidate(employee_id="A10002", name="Taylor Stone"),
        ]

        def decide(request):
            validated = segmented_decision(request)
            units = tuple(
                unit.model_copy(
                    update={
                        "employee_mentions": (
                            answer.conversation.ResolveEmployeeMention(
                                source_span=(start, start + 6)
                            ),
                        )
                    }
                )
                for unit, start in zip(validated.decision.units, (0, 34))
            )
            decision = answer.conversation.ConversationDecision.model_validate(
                {"status": "resolved", "units": units}
            )
            answer.conversation.validate_conversation_decision(
                decision, request.context
            )
            return replace(validated, decision=decision)

        with patch.object(answer, "load_employee_directory", return_value=employees):
            self.provider.side_effect = decide
            text, _, state = answer.answer_question_with_state(question, [], None)
            self.assertIsNotNone(state.pending_request, text)
            text, _, state = answer.answer_question_with_state("yes", [], state)
            self.assertIsNotNone(state.pending_request, text)
            self.assertIn("Taylor Stone", text)
            self.collection.get.assert_not_called()
            text, _, state = answer.answer_question_with_state("yes", [], state)
        self.assertIsNone(state.pending_request, text)
        self.assertEqual(
            [
                [e.employee_id for e in u.employees]
                for u in state.recent_frames[-1].units
            ],
            [["A10001"], ["A10002"]],
        )
        self.provider.assert_called_once()

    def test_retained_employee_is_revalidated_after_another_unit_clarifies(self):
        employee = answer.EmployeeReferent(employee_id="A10001", name="Morgan River")
        question = "count attendance records; attendance"
        units = (
            answer._pending_unit(
                answer.conversation.MaterializedConversationUnit(
                    unit_id="one",
                    route="attendance",
                    relation="new",
                    source_text="count attendance records",
                    employees=(employee,),
                ),
                "en",
            ),
            answer._pending_unit(
                answer.conversation.MaterializedConversationUnit(
                    unit_id="two",
                    route="attendance",
                    relation="new",
                    source_text="attendance",
                ),
                "en",
            ),
        )
        with patch.object(
            answer,
            "load_employee_directory",
            return_value=[
                answer.EmployeeCandidate(
                    employee_id=employee.employee_id, name=employee.name
                )
            ],
        ):
            text, _, state = answer._answer_compound_turn(
                question, units, answer.ConversationState(), locale="en"
            )
        self.assertIsNotNone(state.pending_request, text)
        text, chunks, resumed = answer.answer_question_with_state("1", [], state)
        self.assertEqual(resumed, state)
        self.assertEqual(chunks, [])
        self.collection.get.assert_not_called()

    def test_context_choice_resumes_all_materialized_units(self):
        fact = answer.SemanticFact(
            kind="measure",
            concept_name="attendance_records",
            evidence_text="records",
            origin="question",
            strength="strong",
        )
        state = answer.ConversationState(
            recent_frames=[
                answer.ConversationTurnFrame(
                    original_question="previous requests",
                    reply_locale="en",
                    units=tuple(
                        answer.AttendanceUnitFrame(
                            unit_id=f"prior-{i}",
                            source_text="count attendance records",
                            facts=(fact,),
                            result=answer.ResultSnapshot(),
                        )
                        for i in (1, 2)
                    ),
                )
            ]
        )
        question = "again; again"
        c = answer.conversation

        def decide(request):
            if len(request.prior_units) == 2:
                return c.ValidatedConversation(
                    request,
                    c.ConversationDecision.model_validate(
                        {"status": "ambiguous", "reason": "ambiguous_reference"}
                    ),
                    (),
                )
            decision = c.ConversationDecision.model_validate(
                {
                    "status": "resolved",
                    "units": [
                        {
                            "route": "attendance",
                            "relation": "repeat",
                            "source_span": span,
                            "base_unit_choice_id": request.context.prior_unit_choice_ids[
                                0
                            ],
                        }
                        for span in ((0, 5), (7, 12))
                    ],
                }
            )
            c.validate_conversation_decision(decision, request.context)
            return c.ValidatedConversation(request, decision, ("one", "two"))

        self.provider.side_effect = decide
        text, _, pending = answer.answer_question_with_state(question, [], state)
        self.assertIsNotNone(pending.pending_request, text)
        self.collection.get.assert_not_called()
        text, _, resumed = answer.answer_question_with_state("1", [], pending)
        self.assertIsNone(resumed.pending_request, text)
        self.assertEqual(
            [u.result.scalar_value for u in resumed.recent_frames[-1].units], [3, 3]
        )

    def test_render_failure_withholds_all_results_state_and_trace(self):
        original = answer._answer_from_context
        rendered = []

        def render(*args, **kwargs):
            rendered.append(args[3].aggregation)
            if len(rendered) == 2:
                raise RuntimeError("private renderer payload")
            return original(*args, **kwargs)

        trace = []
        token = answer._EVALUATION_TRACE_SINK.set(trace)
        initial = answer.ConversationState()
        try:
            with patch.object(answer, "_answer_from_context", side_effect=render):
                text, chunks, state = answer.answer_question_with_state(
                    self.question, [], initial
                )
        finally:
            answer._EVALUATION_TRACE_SINK.reset(token)
        self.assertEqual(rendered, ["count", "distinct_count"])
        self.assertEqual(state, initial)
        self.assertEqual((chunks, trace), ([], []))
        self.assertNotIn("private", text)

    def test_arabic_compound_and_budget_responses(self):
        question = "كم عدد أيام العمل المجدولة؟; كم عدد أيام الغياب؟"
        text, _, state = answer.answer_question_with_state(question, [], None)
        self.assertEqual(len(state.recent_frames), 1, text)
        self.assertEqual(state.recent_frames[-1].reply_locale, "ar")
        self.assertEqual(
            [u.result.scalar_value for u in state.recent_frames[-1].units], [0, 0]
        )
        self.collection.get.reset_mock()
        with patch.object(
            answer,
            "settings",
            replace(answer.settings, conversation_compiled_plan_limit=1),
        ):
            text, chunks, state = answer.answer_question_with_state(question, [], None)
        self.assertIn("تقسيم", text)
        self.assertEqual(state, answer.ConversationState())
        self.assertEqual(chunks, [])
        self.collection.get.assert_not_called()


class PreparationTests(unittest.TestCase):
    def test_standalone_postgres_reuses_prepared_queries_without_recompilation(self):
        executed = []
        psycopg = MagicMock()

        def scalar(query, connection):
            executed.append(query)
            if query.purpose == "coverage":
                return {
                    "date_min": answer.date(2026, 9, 1),
                    "date_max": answer.date(2026, 9, 2),
                }
            return {"value": 7}

        def rows(query, connection):
            executed.append(query)
            return []

        with (
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "_import_psycopg", return_value=(psycopg, None)),
            patch.object(answer, "_execute_scalar_query", side_effect=scalar),
            patch.object(answer, "_execute_rows_query", side_effect=rows),
        ):
            prepared = answer._prepare_context_request(
                "count attendance records from 2026-09-01 to 2026-09-07"
            )
            self.assertEqual(executed, [])
            with (
                patch.object(
                    answer,
                    "_prepare_postgres_queries",
                    side_effect=lambda *a, **kw: self.fail(
                        "execution rebuilt the prepared PostgreSQL bundle"
                    ),
                ),
                patch.object(
                    answer,
                    "compile_coverage_query",
                    side_effect=lambda *a, **kw: self.fail(
                        "execution compiled coverage after preparation"
                    ),
                ),
            ):
                result = answer._execute_prepared_context(prepared)
        self.assertEqual(result.matched_count, 7)
        self.assertEqual(result.aggregation["value"], 7)
        self.assertFalse(result.aggregation["coverage"]["complete"])
        self.assertEqual(
            [query.purpose for query in executed],
            ["aggregation", "coverage", "count", "sample"],
        )
        bundle = prepared.postgres_queries
        for actual, expected in zip(
            executed,
            (bundle.aggregation[0], bundle.coverage, bundle.count, bundle.sample),
        ):
            self.assertIs(actual, expected)
        self.assertIn("%s", executed[0].sql)
        self.assertIn("2026-09-01", executed[0].params)
        self.assertNotIn("2026-09-01", executed[0].sql)

    def test_preparation_is_frozen_and_does_not_retrieve(self):
        self.assertTrue(callable(getattr(answer, "_prepare_context_request", None)))
        with (
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(answer, "_postgres_enabled", return_value=False),
            patch.object(answer, "fetch_exact_chroma") as retrieval,
        ):
            prepared = answer._prepare_context_request("count attendance records")
        self.assertEqual(prepared.plan.aggregation, "count")
        self.assertEqual(prepared.plan.answer_contract.unit, "records")
        self.assertTrue(prepared.facts)
        retrieval.assert_not_called()
        with self.assertRaises(FrozenInstanceError):
            prepared.question = "changed"

    def test_single_request_composition_preserves_results_and_trace(self):
        self.assertTrue(callable(getattr(answer, "_execute_prepared_context", None)))
        rows = [
            answer.Result(
                page_content="synthetic",
                metadata={
                    "domain": "attendance",
                    "chunk_type": "attendance_record",
                    "Employee_ID": "A10001",
                    "Date": "2026-09-01",
                },
            )
        ]
        with (
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
            patch.object(answer, "_postgres_enabled", return_value=False),
            patch.object(answer, "fetch_exact_chroma", return_value=rows),
        ):
            prepared = answer._prepare_context_request("count attendance records")
            direct = answer._execute_prepared_context(prepared)
            trace = []
            token = answer._EVALUATION_TRACE_SINK.set(trace)
            try:
                composed = answer._fetch_context_result("count attendance records")
            finally:
                answer._EVALUATION_TRACE_SINK.reset(token)
        self.assertEqual(direct, composed)
        self.assertEqual(composed.aggregation["value"], 1)
        self.assertEqual(composed.matched_count, 1)
        self.assertEqual(trace, [composed])


class BatchExecutionTests(unittest.TestCase):
    def prepare(self):
        with patch.object(
            answer, "load_attendance_catalog_candidates", return_value={}
        ):
            return tuple(
                answer._prepare_context_request(question)
                for question in (
                    "count attendance records from 2026-09-01 to 2026-09-07",
                    "count distinct dates from 2026-09-01 to 2026-09-07",
                )
            )

    def test_chroma_batch_uses_one_snapshot_and_complete_results(self):
        self.assertTrue(callable(getattr(answer, "_execute_prepared_turn", None)))
        stored = {
            "documents": ["one", "two", "three"],
            "metadatas": [
                {"domain": "attendance", "chunk_type": "attendance_record", "Date": day}
                for day in ("2026-09-01", "2026-09-01", "2026-09-02")
            ],
        }
        with (
            patch.object(answer, "_postgres_enabled", return_value=False),
            patch.object(answer, "collection") as collection,
        ):
            collection.get.return_value = stored
            prepared = self.prepare()
            self.assertEqual(collection.get.call_count, 0)
            result = answer._execute_prepared_turn(answer.PreparedTurn(prepared))
        self.assertEqual([item.aggregation["value"] for item in result.results], [3, 2])
        self.assertTrue(
            all(not item.aggregation["coverage"]["complete"] for item in result.results)
        )
        collection.get.assert_called_once()

    def test_postgres_batch_compiles_first_and_reuses_connection_and_coverage(self):
        self.assertTrue(callable(getattr(answer, "_execute_prepared_turn", None)))
        events = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def execute(self, sql, params=()):
                self.sql = sql
                events.append((sql, params))

            def fetchone(self):
                if "MIN(attendance_date)" in self.sql:
                    return {
                        "date_min": answer.date(2026, 9, 1),
                        "date_max": answer.date(2026, 9, 2),
                    }
                return {"value": 2 if "COUNT(DISTINCT" in self.sql else 3}

            def fetchall(self):
                return []

        class Connection:
            def __enter__(self):
                events.append("connect")
                return self

            def __exit__(self, *args):
                events.append("close")

            def cursor(self):
                return Cursor()

        with (
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(
                answer,
                "_import_psycopg",
                return_value=(
                    SimpleNamespace(connect=lambda *a, **kw: Connection()),
                    None,
                ),
            ),
        ):
            prepared = self.prepare()
            self.assertEqual(events, [])
            with patch.object(
                answer,
                "compile_aggregation_queries",
                side_effect=AssertionError("late compilation"),
            ):
                result = answer._execute_prepared_turn(answer.PreparedTurn(prepared))
        self.assertEqual([item.aggregation["value"] for item in result.results], [3, 2])
        self.assertEqual(events.count("connect"), 1)
        self.assertEqual(events[-1], "close")
        queries = [event for event in events if isinstance(event, tuple)]
        self.assertEqual(sum("MIN(attendance_date)" in sql for sql, _ in queries), 1)
        self.assertEqual(
            queries[0],
            ("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY", ()),
        )
        self.assertTrue(
            any("%s" in sql and "2026-09-01" in params for sql, params in queries)
        )

    def test_late_execution_failure_does_not_publish_trace(self):
        self.assertTrue(callable(getattr(answer, "_execute_prepared_turn", None)))
        with (
            patch.object(answer, "_postgres_enabled", return_value=False),
            patch.object(answer, "collection") as collection,
        ):
            collection.get.return_value = {"documents": [], "metadatas": []}
            prepared = self.prepare()
            trace = []
            token = answer._EVALUATION_TRACE_SINK.set(trace)
            calculate = answer.calculate_aggregation_chroma
            calls = []

            def fail_second(plan, chunks):
                calls.append(plan.aggregation)
                if len(calls) == 2:
                    raise RuntimeError("private backend payload")
                return calculate(plan, chunks)

            try:
                with patch.object(
                    answer, "calculate_aggregation_chroma", side_effect=fail_second
                ):
                    with self.assertRaises(RuntimeError):
                        answer._execute_prepared_turn(answer.PreparedTurn(prepared))
            finally:
                answer._EVALUATION_TRACE_SINK.reset(token)
        self.assertEqual(trace, [])
