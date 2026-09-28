"""Compatibility façade for the single attendance-online/v1 runtime."""

from __future__ import annotations

from typing import Sequence

from .online.evidence import Result
from .online.execution import AccessContext, LOCAL_DEMO_ACCESS
from .online.pipeline import TurnRequest, run_turn
from .online.state import ConversationState


def _history(value: Sequence[dict] | None) -> tuple[dict[str, str], ...]:
    normalized: list[dict[str, str]] = []
    for item in value or ():
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            normalized.append({"role": role, "content": content})
    return tuple(normalized)


def answer_question_with_state(
    question,
    history,
    state,
    *,
    access_context=None,
):
    outcome = run_turn(
        TurnRequest(
            question=str(question),
            history=_history(history),
            state=ConversationState.from_untrusted(state),
            access_context=access_context,
        )
    )
    return outcome.reply, list(outcome.evidence), outcome.state


def answer_question(question, history=None, *, access_context=None):
    reply, evidence, _state = answer_question_with_state(
        question,
        history,
        ConversationState(),
        access_context=access_context,
    )
    return reply, evidence


__all__ = [
    "AccessContext",
    "ConversationState",
    "LOCAL_DEMO_ACCESS",
    "Result",
    "answer_question",
    "answer_question_with_state",
]
