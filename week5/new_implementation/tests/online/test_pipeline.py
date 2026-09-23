from __future__ import annotations

import unittest
from unittest.mock import patch

from week5.new_implementation.online.execution import ExecutionResult, LOCAL_DEMO_ACCESS
from week5.new_implementation.online.pipeline import (
    Answered,
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
    ReadyReference,
    ReferenceResponse,
)
from week5.new_implementation.online.state import ConversationState


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


if __name__ == "__main__":
    unittest.main()
