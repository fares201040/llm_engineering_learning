"""Minimal trusted state for the attendance direct-SQL runtime."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import RUNTIME_VERSION
from .reference import Employee, PendingEmployeeConfirmation, ScopeClause


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, str_strip_whitespace=True
    )


class VerifiedTurn(_Strict):
    turn_id: str = Field(min_length=1, max_length=128)
    original_question: str = Field(min_length=1, max_length=50000)
    rewritten_request: str = Field(min_length=1, max_length=60000)
    answer: str = Field(min_length=1, max_length=1000000)
    locale: Literal["en", "ar"]
    employees: tuple[Employee, ...] = Field(default=(), max_length=20)
    executed_sql: str = Field(min_length=1, max_length=100000)
    date_scope: tuple[str, str] | None = None
    requested_date_scope: tuple[str, str] | None = None
    result: dict[str, object] = Field(default_factory=dict)
    scope_clauses: tuple[ScopeClause, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def _valid_date_scope(self):
        for scope in (self.date_scope, self.requested_date_scope):
            if scope is not None:
                start, end = (date.fromisoformat(value) for value in scope)
                if start > end:
                    raise ValueError("date scope must be ordered")
        return self

    @property
    def employee_ids(self) -> tuple[str, ...]:
        return tuple(item.employee_id for item in self.employees)


class ConversationState(_Strict):
    runtime_version: str = RUNTIME_VERSION
    session_id: str = Field(
        default_factory=lambda: uuid4().hex, min_length=1, max_length=128
    )
    verified_turns: tuple[VerifiedTurn, ...] = Field(default=(), max_length=50)
    active_employee_ids: tuple[str, ...] = Field(default=(), max_length=20)
    active_employees: tuple[Employee, ...] = Field(default=(), max_length=20)
    pending_employee_confirmation: PendingEmployeeConfirmation | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.runtime_version != RUNTIME_VERSION:
            raise ValueError("incompatible attendance runtime state")
        if len(self.active_employee_ids) != len(set(self.active_employee_ids)):
            raise ValueError("active employee IDs must be unique")
        if self.active_employees and self.active_employee_ids != tuple(
            item.employee_id for item in self.active_employees
        ):
            raise ValueError("active employee IDs must match active employees")
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
        return {
            "verified_turns": [
                {
                    "turn_id": turn.turn_id,
                    "original_question": turn.original_question,
                    "rewritten_request": turn.rewritten_request,
                    "locale": turn.locale,
                    "employees": [
                        item.model_dump(mode="json") for item in turn.employees
                    ],
                    "date_scope": turn.date_scope,
                    "requested_date_scope": turn.requested_date_scope,
                }
                for turn in self.verified_turns[-1:]
            ],
            "active_employees": [
                item.model_dump(mode="json") for item in self.active_employees
            ],
        }


__all__ = ["ConversationState", "VerifiedTurn"]
