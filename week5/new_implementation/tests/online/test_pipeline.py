from __future__ import annotations

import unittest
from unittest.mock import patch

from week5.new_implementation.online.execution import (
    AccessContext,
    AttendanceRowScope,
    ExecutionResult,
    LOCAL_DEMO_ACCESS,
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
from week5.new_implementation.online.planner import PlannerResponse, ReadyPlan, UnsupportedPlan
from week5.new_implementation.online.query import (
    EvidenceSpan,
    FilterComponent,
    Ordering,
    OutputComponent,
    Predicate,
    RowsOutput,
)
from week5.new_implementation.online.reference import (
    CurrentReference,
    Employee,
    EmployeeOption,
    ReadyReference,
    ReferenceResponse,
)
from week5.new_implementation.online.state import ConversationState
from week5.new_implementation.online.provider import ProviderFailure


def reference_writer(*_args, **_kwargs):
    return ReferenceResponse(
        decision=ReadyReference(
            references=(
                CurrentReference(
                    key="employee",
                    mention=EvidenceSpan(start=5, end=7, text="A1"),
                ),
            )
        )
    )


def ready_planner(**_kwargs):
    return PlannerResponse(
        decision=ReadyPlan(
            locale="en",
            relationship="new",
            filters=(
                FilterComponent(
                    component_key="absence",
                    evidence=EvidenceSpan(start=8, end=15, text="absence"),
                    expression=Predicate(predicate_id="absent"),
                ),
            ),
            output=OutputComponent(
                component_key="dates",
                evidence=EvidenceSpan(start=16, end=21, text="dates"),
                output=RowsOutput(
                    fields=("date",),
                    ordering=(Ordering(output_id="date", direction="asc"),),
                    limit=100,
                ),
            ),
        )
    )


class PipelineTests(unittest.TestCase):
    def dependencies(self, planner=ready_planner):
        return RuntimeDependencies(
            reference_writer=reference_writer,
            directory_loader=lambda **_kwargs: (Employee(employee_id="A1", name="Wail Ali"),),
            planner=planner,
            executor=lambda *_args, **_kwargs: ExecutionResult(rows=({"date": "2026-09-03"},)),
            answer_writer=lambda **_kwargs: "A1 was absent on 2026-09-03.",
        )

    @patch("week5.new_implementation.online.pipeline.audit_with_one_repair", side_effect=lambda initial, **_kwargs: initial)
    def test_answered_turn_publishes_verified_state_once(self, _audit):
        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                state=ConversationState(),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=self.dependencies(),
        )
        self.assertIsInstance(outcome, Answered)
        self.assertEqual(len(outcome.state.verified_turns), 1)
        self.assertEqual(outcome.state.active_employee_ids, ("A1",))

    def test_unsupported_preserves_prior_state(self):
        state = ConversationState(active_employee_ids=("A1",))

        def unsupported(**_kwargs):
            return PlannerResponse(
                decision=UnsupportedPlan(
                    locale="en",
                    capability="ranking",
                    detail="ranking is outside the flat query contract",
                )
            )

        outcome = run_turn(
            TurnRequest(
                question="show A1 absence dates",
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=self.dependencies(unsupported),
        )
        self.assertIsInstance(outcome, Unsupported)
        self.assertEqual(outcome.state, state)

    @patch("week5.new_implementation.online.pipeline.audit_with_one_repair", side_effect=lambda initial, **_kwargs: initial)
    def test_answer_verdict_failure_publishes_no_turn(self, _audit):
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

    @patch("week5.new_implementation.online.pipeline.audit_with_one_repair", side_effect=lambda initial, **_kwargs: initial)
    def test_authorization_failure_publishes_no_turn(self, _audit):
        state = ConversationState(active_employee_ids=("A1",))
        outcome = run_turn(
            TurnRequest(question="show A1 absence dates", state=state),
            dependencies=self.dependencies(),
        )
        self.assertIsInstance(outcome, Failed)
        self.assertEqual(outcome.code, "authorization_failed")
        self.assertEqual(outcome.state, state)

    def test_incompatible_state_resets_instead_of_migrating(self):
        reset = ConversationState.from_untrusted(
            {"runtime_version": "attendance-state/v3", "session_id": "old"}
        )
        self.assertEqual(reset.runtime_version, "attendance-online/v1")
        self.assertNotEqual(reset.session_id, "old")

    def test_unresolved_reference_uses_confirm_only_semantic_fallback(self):
        def unresolved_writer(*_args, **_kwargs):
            return ReferenceResponse(
                decision=ReadyReference(
                    references=(
                        CurrentReference(
                            key="employee",
                            mention=EvidenceSpan(start=5, end=7, text="XX"),
                        ),
                    )
                )
            )

        dependencies = self.dependencies()
        dependencies.reference_writer = unresolved_writer
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A1", name="Wail Ali"),
            Employee(employee_id="A2", name="Faris Ahmed"),
        )
        dependencies.employee_fallback_search = lambda *_args, **_kwargs: (
            EmployeeOption(employee_id="A1", employee_name="Wail Ali"),
            EmployeeOption(employee_id="A2", employee_name="Faris Ahmed"),
        )

        outcome = run_turn(
            TurnRequest(
                question="show XX absence dates",
                state=ConversationState(),
                access_context=LOCAL_DEMO_ACCESS,
            ),
            dependencies=dependencies,
        )

        self.assertIsInstance(outcome, Clarification)
        self.assertIn("1. Wail Ali (A1)", outcome.reply)
        self.assertIn("2. Faris Ahmed (A2)", outcome.reply)
        self.assertEqual(outcome.state.active_employee_ids, ())

    @patch("week5.new_implementation.online.pipeline.audit_with_one_repair", side_effect=lambda initial, **_kwargs: initial)
    def test_numbered_candidate_selection_resumes_original_question(self, _audit):
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                references=(
                    CurrentReference(key="employee", mention=EvidenceSpan(start=5, end=7, text="XX")),
                )
            )
        )
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A1", name="Wail Ali"),
            Employee(employee_id="A2", name="Faris Ahmed"),
        )
        dependencies.employee_fallback_search = lambda *_args, **_kwargs: (
            EmployeeOption(employee_id="A1", employee_name="Wail Ali"),
            EmployeeOption(employee_id="A2", employee_name="Faris Ahmed"),
        )
        first = run_turn(
            TurnRequest(question="show XX absence dates", access_context=LOCAL_DEMO_ACCESS),
            dependencies=dependencies,
        )

        second = run_turn(
            TurnRequest(question="2", state=first.state, access_context=LOCAL_DEMO_ACCESS),
            dependencies=dependencies,
        )

        self.assertIsInstance(second, Answered)
        self.assertEqual(second.state.active_employee_ids, ("A2",))
        self.assertEqual(second.state.verified_turns[0].question, "show XX absence dates")

    def test_employee_candidates_are_filtered_before_fallback(self):
        seen = {}
        dependencies = self.dependencies()
        dependencies.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
            decision=ReadyReference(
                references=(
                    CurrentReference(key="employee", mention=EvidenceSpan(start=5, end=7, text="XX")),
                )
            )
        )
        dependencies.directory_loader = lambda **_kwargs: (
            Employee(employee_id="A1", name="Wail Ali"),
            Employee(employee_id="A2", name="Faris Ahmed"),
        )

        def fallback(_mention, directory, **kwargs):
            seen["directory"] = directory
            seen["scope"] = kwargs["allowed_employee_ids"]
            return ()

        dependencies.employee_fallback_search = fallback
        access = AccessContext(
            principal_id="restricted",
            domain="attendance",
            allowed_domains=frozenset({"attendance"}),
            attendance_scope=AttendanceRowScope("employee_ids", ("A2",)),
        )

        run_turn(
            TurnRequest(question="show XX absence dates", access_context=access),
            dependencies=dependencies,
        )

        self.assertEqual(seen["directory"], (Employee(employee_id="A2", name="Faris Ahmed"),))
        self.assertEqual(seen["scope"], ("A2",))


if __name__ == "__main__":
    unittest.main()
