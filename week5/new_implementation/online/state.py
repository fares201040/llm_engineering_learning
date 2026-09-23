"""Minimal trusted state for the attendance-online/v1 runtime."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import RUNTIME_VERSION
from .query import QueryComponent, TrustedQueryComponent
from .reference import PendingEmployeeConfirmation


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class VerifiedTurn(_Strict):
    turn_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=50000)
    answer: str = Field(min_length=1, max_length=12000)
    locale: str = Field(pattern="^(en|ar)$")
    employee_ids: tuple[str, ...] = Field(default=(), max_length=20)
    components: tuple[QueryComponent, ...] = Field(default=(), max_length=64)
    result: dict[str, object] = Field(default_factory=dict)


class ConversationState(_Strict):
    runtime_version: str = RUNTIME_VERSION
    session_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)
    verified_turns: tuple[VerifiedTurn, ...] = Field(default=(), max_length=50)
    active_employee_ids: tuple[str, ...] = Field(default=(), max_length=20)
    pending_employee_confirmation: PendingEmployeeConfirmation | None = None

    @model_validator(mode="after")
    def _version(self):
        if self.runtime_version != RUNTIME_VERSION:
            raise ValueError("incompatible attendance runtime state")
        if len(self.active_employee_ids) != len(set(self.active_employee_ids)):
            raise ValueError("active employee IDs must be unique")
        return self

    @classmethod
    def from_untrusted(cls, value: Any) -> "ConversationState":
        if isinstance(value, cls) and value.runtime_version == RUNTIME_VERSION:
            return value
        if isinstance(value, dict) and value.get("runtime_version") == RUNTIME_VERSION:
            try:
                return cls.model_validate(value, strict=True)
            except (TypeError, ValueError):
                pass
        return cls()

    def trusted_context(self) -> dict[str, object]:
        turns = []
        for turn in self.verified_turns:
            turns.append(
                {
                    "turn_id": turn.turn_id,
                    "question": turn.question,
                    "answer": turn.answer,
                    "locale": turn.locale,
                    "employee_ids": list(turn.employee_ids),
                    "component_ids": [f"{turn.turn_id}:{index}" for index, _ in enumerate(turn.components)],
                    "result": turn.result,
                }
            )
        return {"verified_turns": turns, "active_employee_ids": list(self.active_employee_ids)}

    def trusted_components(self) -> tuple[TrustedQueryComponent, ...]:
        return tuple(
            TrustedQueryComponent(
                component_id=f"{turn.turn_id}:{index}",
                owner_turn_id=turn.turn_id,
                component=component,
            )
            for turn in self.verified_turns
            for index, component in enumerate(turn.components)
        )


__all__ = ["ConversationState", "VerifiedTurn"]
