"""Request-local accounting for provider decisions."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class DecisionBudgetExhausted(RuntimeError):
    """The current turn has already made its allowed provider requests."""


@dataclass
class _DecisionBudget:
    limit: int = 3
    used: int = 0


_CURRENT_BUDGET: ContextVar[_DecisionBudget | None] = ContextVar(
    "attendance_decision_budget", default=None
)


@contextmanager
def decision_budget_scope(limit: int = 3):
    """Create one budget at a public boundary and reuse it in nested calls."""
    current = _CURRENT_BUDGET.get()
    if current is not None:
        yield current
        return
    budget = _DecisionBudget(limit=limit)
    token = _CURRENT_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _CURRENT_BUDGET.reset(token)


def claim_provider_call() -> None:
    """Claim immediately before a provider request, failing before the call."""
    budget = _CURRENT_BUDGET.get()
    if budget is None:
        return
    if budget.used >= budget.limit:
        raise DecisionBudgetExhausted("provider decision budget exhausted")
    budget.used += 1
