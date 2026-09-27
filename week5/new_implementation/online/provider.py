"""One structured-output boundary shared by every online decision stage."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from time import perf_counter
from typing import Callable, Protocol, TypeVar

from litellm import completion
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError


_LAYER_LOGGER = logging.getLogger("week5.new_implementation.online.layers")
_SECRET_PATTERNS = (
    (re.compile(r"(postgres(?:ql)?://[^:\s/]+:)[^@\s]+(@)"), r"\1***\2"),
    (
        re.compile(
            r"(?i)(password|passwd|pwd|api[_-]?key|authorization|access[_-]?token|secret)"
            r'(["\s:=]+)([^,}\s]+)'
        ),
        r"\1\2***",
    ),
)
_SECRET_KEYS = {
    "password",
    "passwd",
    "pwd",
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "accesstoken",
    "secret",
}


def _redact(value: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _layer_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")

    def sanitize(item: object) -> object:
        if isinstance(item, dict):
            return {
                str(key): (
                    "***"
                    if str(key).casefold().replace("-", "_") in _SECRET_KEYS
                    else sanitize(nested)
                )
                for key, nested in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [sanitize(nested) for nested in item]
        if isinstance(item, str):
            return _redact(item)
        return item

    return json.dumps(
        sanitize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def log_layer_output(
    layer: str,
    output: object,
    *,
    attempt: int | None = None,
) -> None:
    """Log a safe summary and the credential-redacted produced value at DEBUG."""

    attempt_number = attempt if attempt is not None else 0
    _LAYER_LOGGER.info(
        "attendance_layer layer=%s status=completed attempt=%s",
        layer,
        attempt_number,
    )
    _LAYER_LOGGER.debug(
        "attendance_layer_output layer=%s attempt=%s output=%s",
        layer,
        attempt_number,
        _layer_json(output),
    )


def log_layer_failure(layer: str, code: str, error: object) -> None:
    """Log failure classification normally and redacted detail at DEBUG."""

    _LAYER_LOGGER.warning(
        "attendance_layer layer=%s status=failed code=%s error_type=%s",
        layer,
        code,
        type(error).__name__,
    )
    _LAYER_LOGGER.debug(
        "attendance_layer_failure layer=%s code=%s detail=%s",
        layer,
        code,
        _redact(str(error)),
    )


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
    limit: int = 7
    used: int = 0

    def claim(self, stage: str) -> None:
        if self.used >= self.limit:
            raise ProviderFailure(
                stage, "call_budget_exceeded", "provider call budget exhausted"
            )
        self.used += 1


T = TypeVar("T", bound=BaseModel)


def _emit(observer: TurnObserver | None, event: StageEvent) -> None:
    if observer is not None:
        observer(event)


def _content(response: object, stage: str) -> str:
    try:
        content = response.choices[0].message.content  # type: ignore[attr-defined]
    except (AttributeError, IndexError, TypeError) as exc:
        raise ProviderFailure(
            stage, "malformed_response", "provider response has no message"
        ) from exc
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text = "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
        if text.strip():
            return text
    raise ProviderFailure(
        stage, "empty_response", "provider returned no structured content"
    )


def _local_model_options(model: str, stage: str) -> dict[str, object]:
    if model.startswith(("ollama/", "ollama_chat/")):
        local_model = model.rsplit("/", 1)[-1].casefold()
        return {
            "reasoning_effort": (
                "medium" if local_model.startswith("gpt-oss:") else "none"
            ),
            "temperature": 0,
            "num_ctx": (
                65536
                if stage in {"sql_planner", "answer_writer", "answer_verifier"}
                else 8192
            ),
        }
    return {}


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
                    "content": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
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
            **_local_model_options(model, stage),
        )
        parsed = response_model.model_validate_json(
            _content(response, stage), strict=True
        )
    except ProviderFailure as exc:
        log_layer_failure(stage, exc.code, exc)
        raise
    except ValidationError as exc:
        log_layer_failure(stage, "invalid_schema", exc)
        raise ProviderFailure(stage, "invalid_schema", str(exc)) from exc
    except Exception as exc:
        log_layer_failure(stage, "provider_unavailable", exc)
        raise ProviderFailure(stage, "provider_unavailable", str(exc)) from exc
    finally:
        duration = int((perf_counter() - started) * 1000)
    _emit(observer, StageEvent(stage, "completed", attempt, duration))
    log_layer_output(stage, parsed, attempt=attempt)
    return parsed


def call_text(
    *,
    stage: str,
    model: str,
    system: str,
    payload: dict[str, object],
    budget: CallBudget,
    timeout: float,
    max_output_tokens: int,
    attempt: int = 1,
    observer: TurnObserver | None = None,
    completion_fn: Callable[..., object] = completion,
) -> str:
    """Perform exactly one transport attempt and return its non-empty text."""

    budget.claim(stage)
    started = perf_counter()
    _emit(observer, StageEvent(stage, "started", attempt))
    try:
        response = completion_fn(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            timeout=timeout,
            num_retries=0,
            max_tokens=max_output_tokens,
            **_local_model_options(model, stage),
        )
        content = _content(response, stage).strip()
    except ProviderFailure as exc:
        log_layer_failure(stage, exc.code, exc)
        raise
    except Exception as exc:
        log_layer_failure(stage, "provider_unavailable", exc)
        raise ProviderFailure(stage, "provider_unavailable", str(exc)) from exc
    finally:
        duration = int((perf_counter() - started) * 1000)
    _emit(observer, StageEvent(stage, "completed", attempt, duration))
    log_layer_output(stage, content, attempt=attempt)
    return content


__all__ = [
    "CallBudget",
    "ProviderFailure",
    "StageEvent",
    "TurnObserver",
    "call_text",
    "call_structured",
    "log_layer_failure",
    "log_layer_output",
]
