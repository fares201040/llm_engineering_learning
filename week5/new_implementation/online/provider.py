"""One structured-output boundary shared by every online decision stage."""

from __future__ import annotations

from dataclasses import dataclass
import json
from time import perf_counter
from typing import Callable, Protocol, TypeVar

from litellm import completion
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError


class ProviderFailure(RuntimeError):
    def __init__(self, stage: str, code: str, message: str):
        self.stage = stage
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class StageEvent:
    stage: str
    status: str
    attempt: int = 0
    duration_ms: int = 0
    detail: str | None = None


class TurnObserver(Protocol):
    def __call__(self, event: StageEvent) -> None: ...


@dataclass
class CallBudget:
    limit: int = 11
    used: int = 0

    def claim(self, stage: str) -> None:
        if self.used >= self.limit:
            raise ProviderFailure(stage, "call_budget_exceeded", "provider call budget exhausted")
        self.used += 1


T = TypeVar("T", bound=BaseModel)


def _emit(observer: TurnObserver | None, event: StageEvent) -> None:
    if observer is not None:
        observer(event)


def _content(response: object, stage: str) -> str:
    try:
        content = response.choices[0].message.content  # type: ignore[attr-defined]
    except (AttributeError, IndexError, TypeError) as exc:
        raise ProviderFailure(stage, "malformed_response", "provider response has no message") from exc
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text = "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
        if text.strip():
            return text
    raise ProviderFailure(stage, "empty_response", "provider returned no structured content")


def call_structured(
    *,
    stage: str,
    model: str,
    system: str,
    payload: dict[str, object],
    response_model: type[T],
    budget: CallBudget,
    timeout: float,
    max_output_tokens: int,
    reasoning_effort: str = "low",
    attempt: int = 1,
    observer: TurnObserver | None = None,
    completion_fn: Callable[..., object] = completion,
) -> T:
    """Perform exactly one transport attempt and validate the strict response."""

    budget.claim(stage)
    started = perf_counter()
    _emit(observer, StageEvent(stage, "started", attempt))
    schema = to_strict_json_schema(response_model)
    try:
        response = completion_fn(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
            timeout=timeout,
            num_retries=0,
            max_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
        parsed = response_model.model_validate_json(_content(response, stage), strict=True)
    except ProviderFailure:
        raise
    except ValidationError as exc:
        raise ProviderFailure(stage, "invalid_schema", str(exc)) from exc
    except Exception as exc:
        raise ProviderFailure(stage, "provider_unavailable", str(exc)) from exc
    finally:
        duration = int((perf_counter() - started) * 1000)
    _emit(observer, StageEvent(stage, "completed", attempt, duration))
    return parsed


__all__ = [
    "CallBudget",
    "ProviderFailure",
    "StageEvent",
    "TurnObserver",
    "call_structured",
]
