from __future__ import annotations

import unittest

import psycopg

from week5.new_implementation.online.execution import (
    ExecutionCoverage,
    LOCAL_DEMO_ACCESS,
    SqlExecutionResult,
)
from week5.new_implementation.online.pipeline import (
    Answered,
    Clarification,
    Failed,
    RuntimeDependencies,
    TurnRequest,
    Unsupported,
    run_turn,
)
from week5.new_implementation.online.provider import ProviderFailure
from week5.new_implementation.online.reference import (
    Employee,
    EmployeeOption,
    ReadyReference,
    ReferenceResponse,
    UnsupportedReference,
)
from week5.new_implementation.online.state import ConversationState
from week5.new_implementation.tests.online.test_query import database_context


def ready_reference(*_args, **_kwargs):
    return ReferenceResponse(
        decision=ReadyReference(
            rewritten_request="Show absence dates for employee A1.",
            locale="en",
            request_relationship="new",
            subject_relationship="employees",
            employee_ids=("A1",),
        )
    )


def sql_result():
    return SqlExecutionResult(
        columns=(),
        rows=({"attendance_date": "2026-09-03"},),
        coverage=ExecutionCoverage(
            fetched_rows=1,
            result_limit=100,
            response_bytes=34,
        ),
    )


class PipelineTests(unittest.TestCase):
    def dependencies(self):
        return RuntimeDependencies(
            reference_writer=ready_reference,
            directory_loader=lambda **_kwargs: (
                Employee(employee_id="A1", name="Wail Ali"),
            ),
            employee_fuzzy_search=lambda *_args, **_kwargs: (),
            context_loader=lambda **_kwargs: database_context(),
            planner=lambda **_kwargs: "SELECT attendance_date FROM \"attendance_records\" WHERE employee_id = 'A1'",
            executor=lambda *_args, **_kwargs: sql_result(),
            answer_writer=lambda **_kwargs: "Wail Ali was absent on 2026-09-03.",
        )

    def test_answered_turn_publishes_rewrite_sql_result_and_answer_atomically(self):
        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=self.dependencies(),
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(outcome.state.verified_turns), 1)
        turn = outcome.state.verified_turns[0]
        self.assertIn("Resolved employees", turn.rewritten_request)
        self.assertTrue(turn.executed_sql.startswith("SELECT"))
        self.assertEqual(turn.result["rows"][0]["attendance_date"], "2026-09-03")
        self.assertEqual(outcome.state.active_employee_ids, ("A1",))

    def test_all_downstream_stages_receive_one_shared_context_instance(self):
        seen = {}
        dependencies = self.dependencies()

        def planner(**kwargs):
            seen["planner"] = kwargs["shared_context"]
            return "SELECT 1"

        def answer_writer(**kwargs):
            seen["answer"] = kwargs["shared_context"]
            return "Wail Ali has one matching row."

        dependencies.planner = planner
        dependencies.answer_writer = answer_writer
        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                history=({"role": "user", "content": "attendance for A1"},),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertIs(seen["planner"], seen["answer"])
        self.assertEqual(
            set(seen["planner"].model_payload()),
            {
                "current_question",
                "updated_request",
                "conversation_history",
                "trusted_context",
                "database_type",
                "database_context",
                "resolution_statement",
            },
        )
        self.assertEqual(seen["planner"].current_question, "show A1 absence dates")
        self.assertEqual(
            seen["planner"].conversation_history,
            ({"role": "user", "content": "attendance for A1"},),
        )

    def test_pipeline_logs_each_application_layer_output(self):
        with self.assertLogs(
            "week5.new_implementation.online.layers", level="DEBUG"
        ) as captured:
            outcome = run_turn(
                TurnRequest(
                    question="show A1 absence dates",
                    access_context=LOCAL_DEMO_ACCESS,
                ),
                dependencies=self.dependencies(),
            )

        self.assertIsInstance(outcome, Answered)
        combined = "\n".join(captured.output)
        for layer in (
            "employee_directory",
            "employee_resolution",
            "database_context",
            "sql_planner",
            "sql_execution",
            "answer",
            "publication",
        ):
            self.assertIn(f"layer={layer}", combined)

    def test_provider_or_verifier_failure_preserves_exact_prior_state(self):
        state = ConversationState(active_employee_ids=("A1",))
        dependencies = self.dependencies()
        dependencies.answer_writer = lambda **_kwargs: (_ for _ in ()).throw(
            ProviderFailure("answer_verifier", "answer_verdict_failed", "rejected")
        )

        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.state, state)

    def test_database_failure_preserves_exact_prior_state(self):
        state = ConversationState()
        dependencies = self.dependencies()
        dependencies.executor = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("statement timeout")
        )

        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.state, state)

    def test_postgres_query_rejection_is_replanned_once_then_succeeds(self):
        dependencies = self.dependencies()
        planner_calls = []
        execution_calls = []

        def planner(**kwargs):
            planner_calls.append(kwargs)
            return f"SELECT attempt_{len(planner_calls)}"

        def executor(sql, **_kwargs):
            execution_calls.append(sql)
            if len(execution_calls) == 1:
                raise psycopg.errors.UndefinedColumn(
                    f"column attempt_{len(execution_calls)} does not exist"
                )
            return sql_result()

        dependencies.planner = planner
        dependencies.executor = executor

        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(planner_calls), 2)
        self.assertEqual(len(execution_calls), 2)
        self.assertNotIn("sql_execution_failure", planner_calls[0])
        for retry_number, call in enumerate(planner_calls[1:], start=1):
            failure = call["sql_execution_failure"]
            self.assertEqual(failure["retry_number"], retry_number)
            self.assertEqual(failure["failed_sql"], f"SELECT attempt_{retry_number}")
            self.assertEqual(failure["error_type"], "UndefinedColumn")
            self.assertIn("does not exist", failure["database_error"])
        self.assertEqual(
            outcome.state.verified_turns[-1].executed_sql, "SELECT attempt_2"
        )

    def test_second_postgres_query_rejection_fails_without_publishing_state(self):
        state = ConversationState()
        dependencies = self.dependencies()
        planner_calls = []
        execution_calls = []

        def planner(**kwargs):
            planner_calls.append(kwargs)
            return f"SELECT attempt_{len(planner_calls)}"

        def executor(sql, **_kwargs):
            execution_calls.append(sql)
            raise psycopg.errors.UndefinedFunction("function does not exist")

        dependencies.planner = planner
        dependencies.executor = executor

        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.state, state)
        self.assertEqual(len(planner_calls), 2)
        self.assertEqual(len(execution_calls), 2)

    def test_missing_access_fails_before_planning_and_preserves_state(self):
        dependencies = self.dependencies()
        dependencies.planner = lambda **_kwargs: self.fail("planning must not run")
        state = ConversationState()

        outcome = run_turn(
            TurnRequest(question="show A1 absence dates", state=state),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.code, "authorization_failed")
        self.assertEqual(outcome.state, state)

    def test_malformed_employee_identifier_is_unsupported_before_planning(self):
        dependencies = self.dependencies()
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A1", name="Wail Ali"),
        )
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show employee 12345 attendance.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_ids=("12345",),
            )
        )
        dependencies.planner = lambda **_kwargs: self.fail("planning must not run")

        outcome = run_turn(
            TurnRequest(
                question="Show employee 12345 attendance.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "malformed_identifier")

    def test_non_finite_numeric_comparison_is_unsupported_before_reference(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "reference resolution must not run"
        )

        outcome = run_turn(
            TurnRequest(
                question="Show lateness greater than NaN hours.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "malformed_value")

    def test_numeric_threshold_before_metric_name_is_not_rejected(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show records with more than 1 lateness hour.",
                locale="en",
                request_relationship="new",
                subject_relationship="criteria",
                employee_criteria=("more than 1 lateness hour",),
            )
        )

        outcome = run_turn(
            TurnRequest(
                question="Show concerning records with more than 1 lateness hour.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Answered)

    def test_malformed_id_is_rejected_before_reference_or_fuzzy_search(self):
        dependencies = self.dependencies()
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A10017", name="Iftikhar Hasson Ismail"),
        )
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "reference resolution must not run"
        )
        dependencies.employee_fuzzy_search = lambda *_args, **_kwargs: self.fail(
            "fuzzy search must not run"
        )

        outcome = run_turn(
            TurnRequest(
                question="Show attendance for A10.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "malformed_identifier")

    def test_impossible_calendar_date_is_unsupported_before_reference(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: self.fail(
            "reference resolution must not run"
        )

        outcome = run_turn(
            TurnRequest(
                question="Count records on September 31, 2026.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "malformed_value")

    def test_planner_unsupported_protocol_returns_explicit_unsupported_outcome(self):
        dependencies = self.dependencies()
        dependencies.planner = lambda **_kwargs: (
            "SELECT 'requested concept is not represented by the attendance schema' "
            "AS unsupported_capability"
        )
        dependencies.executor = lambda *_args, **_kwargs: SqlExecutionResult(
            columns=(),
            rows=(
                {
                    "unsupported_capability": (
                        "requested concept is not represented by the attendance schema"
                    )
                },
            ),
            coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=90
            ),
        )
        dependencies.answer_writer = lambda **_kwargs: self.fail(
            "unsupported results must not reach answer writing"
        )

        outcome = run_turn(
            TurnRequest(
                question="How much loan balance does A1 have?",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "schema")

    def test_reference_unsupported_domain_stops_before_planning(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=UnsupportedReference(
                rewritten_request="List repayment records for employee A1.",
                locale="en",
                capability="outside_attendance_domain",
            )
        )
        dependencies.planner = lambda **_kwargs: self.fail("planning must not run")

        outcome = run_turn(
            TurnRequest(
                question="List repayment records for A1.",
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.capability, "outside_attendance_domain")

    def test_unresolved_name_uses_confirm_only_chroma_options(self):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                rewritten_request="Show attendance for XX.",
                locale="en",
                request_relationship="new",
                subject_relationship="employees",
                employee_names=("XX",),
            )
        )
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A1", name="Wail Ali"),
            Employee(employee_id="A2", name="Faris Hassan"),
        )
        dependencies.employee_fallback_search = lambda *_args, **_kwargs: (
            EmployeeOption(employee_id="A2", employee_name="Faris Hassan"),
        )

        first = run_turn(
            TurnRequest(
                question="show XX attendance", access_context=LOCAL_DEMO_ACCESS
            ),
            dependencies=dependencies,
        )
        second = run_turn(
            TurnRequest(
                question="yes", state=first.state, access_context=LOCAL_DEMO_ACCESS
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(first, Clarification)
        self.assertIsInstance(second, Answered)
        self.assertEqual(second.state.active_employee_ids, ("A2",))
        self.assertIn(
            "Faris Hassan (A2)", second.state.verified_turns[0].rewritten_request
        )

    def test_incompatible_state_resets_instead_of_migrating(self):
        reset = ConversationState.from_untrusted(
            {"runtime_version": "attendance-state/v3", "session_id": "old"}
        )
        self.assertEqual(reset.runtime_version, "attendance-online/v1")
        self.assertNotEqual(reset.session_id, "old")


if __name__ == "__main__":
    unittest.main()
