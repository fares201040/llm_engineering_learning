"""Structured, privacy-safe APDC stage events."""

from contextlib import contextmanager
from time import perf_counter
import json
import logging
import re


_SENSITIVE_KEYS = {
    "query",
    "question",
    "name",
    "employee_id",
    "dsn",
    "search_query",
    "evidence_text",
    "catalog_value",
    "params",
    "sql",
}


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.casefold() in _SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\b[A-Za-z]\d{5}\b", "[EMPLOYEE_ID]", value)
        value = re.sub(r"postgres(?:ql)?://\S+", "[DSN]", value)
    return value


class EventLogger:
    def __init__(self, sink=None, *, json_format=False, redact_pii=True):
        self.sink = sink or logging.getLogger("attendance.events").info
        self.json_format = json_format
        self.redact_pii = redact_pii

    def emit(self, event, **fields):
        payload = {"event": event, **fields}
        if self.redact_pii:
            payload = redact(payload)
        rendered = (
            json.dumps(payload, sort_keys=True, default=str)
            if self.json_format
            else " ".join(f"{key}={value}" for key, value in payload.items())
        )
        self.sink(rendered)
        return payload

    @contextmanager
    def stage(self, stage, **fields):
        started = perf_counter()
        try:
            yield
        except Exception as exc:
            self.emit(
                "stage_complete",
                stage=stage,
                state="failure",
                duration_seconds=perf_counter() - started,
                error_type=type(exc).__name__,
                **fields,
            )
            raise
        else:
            self.emit(
                "stage_complete",
                stage=stage,
                state="success",
                duration_seconds=perf_counter() - started,
                **fields,
            )
