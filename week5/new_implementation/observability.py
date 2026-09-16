from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
import json
import logging
import math
import re
from time import perf_counter


_SAFE_KEYS = frozenset(
    {
        "event",
        "stage",
        "state",
        "status",
        "mode",
        "operation",
        "purpose",
        "backend",
        "locale",
        "request_id",
        "clarification_kind",
        "candidate_count",
        "correction_count",
        "selection_count",
        "need_count",
        "filter_count",
        "predicate_count",
        "parameter_count",
        "fact_count",
        "violation_count",
        "duration_seconds",
        "fingerprint",
        "attempt_number",
        "selected_field",
        "failure_code",
        "violation_codes",
        "unsupported_capabilities",
        "fact_kinds",
        "match_method_counts",
    }
)
_SAFE_INT_KEYS = frozenset(
    {
        "candidate_count",
        "correction_count",
        "selection_count",
        "need_count",
        "filter_count",
        "predicate_count",
        "parameter_count",
        "fact_count",
        "violation_count",
        "attempt_number",
    }
)
_SAFE_STRING_KEYS = frozenset(
    {
        "event",
        "stage",
        "state",
        "status",
        "mode",
        "operation",
        "purpose",
        "backend",
        "locale",
        "request_id",
        "clarification_kind",
        "failure_code",
        "fingerprint",
        "selected_field",
    }
)
_SAFE_LIST_KEYS = frozenset(
    {"violation_codes", "unsupported_capabilities", "fact_kinds"}
)
_CONTROLLED_CODES = frozenset(
    {
        "invalid_schema",
        "ungrounded_constraint",
        "uncovered_fact",
        "contradiction",
        "answer_contract_mismatch",
        "unsupported_capability",
        "ambiguous_value",
        "nested_boolean_filters",
        "having_filter",
        "window_calculation",
        "cross_period_comparison",
        "multi_stage_aggregation",
        "unsupported_calculation",
        "narrative_explanation",
        "percentage_population",
        "grouped_percentage",
        "unsupported_constraint",
        "access_denied",
        "plan_validation",
        "semantic_validation",
        "planning_decision",
        "conversation_validation",
        "provider_timeout",
        "schema_validation",
        "validation_error",
        "type_error",
        "missing_value",
        "internal_error",
        "provider_failure",
        "invalid_candidate_ids",
        "invalid_logical_sql",
    }
)
_CONTROLLED_EVENTS = frozenset(
    {
        "planning_draft_built",
        "planner_decision_rejected",
        "planner_decision_received",
        "postgres_query_compiled",
        "input_surface_analyzed",
        "input_clarification_required",
        "semantic_facts_detected",
        "planner_proposal_assembled",
        "proposal_rejected",
        "executable_plan_compiled",
        "query_compiled",
        "retrieval_complete",
        "stage_complete",
        "generated_sql_decision",
    }
)
_CONTROLLED_STAGES = frozenset(
    {
        "planning",
        "postgres_compilation",
        "input_understanding",
        "semantic_detection",
        "semantic_validation",
        "structured_retrieval",
        "semantic_search",
        "provider",
        "generated_sql",
    }
)
_CONTROLLED_STATES = frozenset({"success", "rejected", "paused", "failure"})
_CONTROLLED_STATUSES = frozenset({"ready", "ambiguous", "unsupported"})
_CONTROLLED_MODES = frozenset({"exact", "semantic", "hybrid"})
_CONTROLLED_OPERATIONS = frozenset(
    {"none", "count", "distinct_count", "sum", "average", "min", "max", "percentage"}
)
_CONTROLLED_PURPOSES = frozenset(
    {"sample", "count", "aggregation", "coverage", "profile"}
)
_CONTROLLED_BACKENDS = frozenset({"postgres", "postgres+pgvector", "chroma"})
_CONTROLLED_CLARIFICATIONS = frozenset(
    {
        "employee_selection",
        "semantic_interpretation",
        "missing_intent",
        "context_choice",
        "constraint_value",
    }
)
_CONTROLLED_FACT_KINDS = frozenset(
    {
        "entity",
        "calculation",
        "filter",
        "measure",
        "predicate",
        "projection",
        "result_intent",
        "semantic_intent",
        "temporal",
        "numeric",
        "unsupported",
    }
)
_CONTROLLED_MATCH_METHODS = frozenset(
    {
        "exact",
        "localized_alias",
        "reordered_tokens",
        "transliteration",
        "edit_distance",
        "fuzzy",
    }
)
_QUERY_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^(?:request-[0-9]{1,20}|[0-9a-f]{32})$")
_FAILURE_CODES_BY_TYPE = {
    "DomainAccessDeniedError": "access_denied",
    "PlanValidationError": "plan_validation",
    "SemanticPlanValidationError": "semantic_validation",
    "PlanningDecisionError": "planning_decision",
    "ConversationDecisionValidationError": "conversation_validation",
    "TimeoutError": "provider_timeout",
    "ValidationError": "schema_validation",
    "ValueError": "validation_error",
    "TypeError": "type_error",
    "KeyError": "missing_value",
    "RuntimeError": "internal_error",
}


def _failure_code(error: BaseException) -> str:
    for error_type in type(error).__mro__:
        code = _FAILURE_CODES_BY_TYPE.get(error_type.__name__)
        if code is not None:
            return code
    return "internal_error"


def _safe_string(value: str, key: str):
    if key == "locale":
        return value if value in {"en", "ar"} else None
    if key == "event":
        return value if value in _CONTROLLED_EVENTS else None
    if key == "stage":
        return value if value in _CONTROLLED_STAGES else None
    if key == "state":
        return value if value in _CONTROLLED_STATES else None
    if key == "status":
        return value if value in _CONTROLLED_STATUSES else None
    if key == "mode":
        return value if value in _CONTROLLED_MODES else None
    if key == "operation":
        return value if value in _CONTROLLED_OPERATIONS else None
    if key == "purpose":
        return value if value in _CONTROLLED_PURPOSES else None
    if key == "backend":
        return value if value in _CONTROLLED_BACKENDS else None
    if key == "clarification_kind":
        return value if value in _CONTROLLED_CLARIFICATIONS else None
    if key == "failure_code":
        return value if value in _CONTROLLED_CODES else None
    if key == "request_id":
        return value if _REQUEST_ID.fullmatch(value) else None
    if key == "fingerprint":
        return value if _QUERY_FINGERPRINT.fullmatch(value) else None
    if key == "selected_field":
        try:
            from .attendance_schema import FIELD_DEFINITIONS
        except ImportError:
            from attendance_schema import FIELD_DEFINITIONS
        return value if value in FIELD_DEFINITIONS else None
    return None


def _sanitize(value, *, key: str | None = None):
    if key in _SAFE_STRING_KEYS:
        return _safe_string(value, key) if isinstance(value, str) else None
    if key in _SAFE_INT_KEYS:
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else None
        )
    if key == "duration_seconds":
        return (
            value
            if isinstance(value, float) and math.isfinite(value) and value >= 0
            else None
        )
    if key == "match_method_counts":
        if not isinstance(value, Mapping):
            return None
        return {
            item_key: int(item_value)
            for item_key, item_value in value.items()
            if isinstance(item_key, str)
            and item_key in _CONTROLLED_MATCH_METHODS
            and isinstance(item_value, int)
            and not isinstance(item_value, bool)
            and item_value >= 0
        }
    if key in _SAFE_LIST_KEYS:
        if not isinstance(value, (list, tuple, set, frozenset)):
            return None
        allowed = {
            "violation_codes": _CONTROLLED_CODES,
            "unsupported_capabilities": _CONTROLLED_CODES,
            "fact_kinds": _CONTROLLED_FACT_KINDS,
        }[key]
        items = [item for item in value if isinstance(item, str) and item in allowed]
        if isinstance(value, (set, frozenset)):
            items.sort()
        return items
    if isinstance(value, BaseException):
        return {"failure_code": _failure_code(value)}
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            value = value.model_dump(mode="python")
        except Exception:
            return "[REDACTED]"
    elif is_dataclass(value) and not isinstance(value, type):
        try:
            value = asdict(value)
        except Exception:
            return "[REDACTED]"
    elif isinstance(value, Mapping):
        sanitized = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                continue
            normalized_key = raw_key.casefold()
            if normalized_key not in _SAFE_KEYS:
                continue
            clean = _sanitize(item, key=normalized_key)
            if clean is not None:
                sanitized[normalized_key] = clean
        return sanitized
    elif isinstance(value, (list, tuple, set, frozenset)):
        return None
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        try:
            return _sanitize(vars(value), key=key)
        except Exception:
            return "[REDACTED]"
    if value is None:
        return None
    if isinstance(value, bool):
        return value if key in _SAFE_KEYS else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value if key in _SAFE_INT_KEYS and value >= 0 else None
    if isinstance(value, float):
        return (
            value
            if key == "duration_seconds" and math.isfinite(value) and value >= 0
            else None
        )
    if isinstance(value, str):
        if key in _SAFE_STRING_KEYS:
            return _safe_string(value, key)
        return None
    return None


def redact(value):
    """Return only controlled telemetry fields and recursively safe values."""
    clean = _sanitize(value)
    return clean if clean is not None else "[REDACTED]"


class EventLogger:
    def __init__(self, sink=None, *, json_format=False, redact_pii=True):
        del redact_pii
        self.sink = sink or logging.getLogger("attendance.events").info
        self.json_format = json_format

    def emit(self, event, **fields):
        payload = redact({"event": event, **fields})
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
                failure_code=_failure_code(exc),
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
