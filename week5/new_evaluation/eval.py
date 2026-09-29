"""Deterministic evaluator for the attendance direct-SQL runtime."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import re

from pydantic import BaseModel
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from ..new_implementation.online.execution import LOCAL_DEMO_ACCESS
from ..new_implementation.online.pipeline import (
    Answered,
    Clarification,
    TurnRequest,
    Unsupported,
    run_turn,
)
from ..new_implementation.online.state import ConversationState, VerifiedTurn
from .test import TEST_FILE, TestQuestion, load_tests


class BehaviorEval(BaseModel):
    outcome_ok: bool = True
    plan_ok: bool = True
    employee_ids_ok: bool = True
    matched_count_ok: bool = True
    calculation_ok: bool = True
    clarification_ok: bool = True
    answer_facts_ok: bool = True
    record_ids_ok: bool = True
    group_values_ok: bool = True
    group_order_ok: bool = True
    unsupported_capabilities_ok: bool = True
    multi_turn_ok: bool = True

    @property
    def passed(self) -> bool:
        return all(self.model_dump().values())


_PHYSICAL_FIELD = {
    "Employee_ID": "employee_id",
    "Name": "name",
    "Date": "attendance_date",
    "Day_Type": "day_type",
    "Status": "status",
    "Exception": "exception",
    "Total_Worked_Hrs": "total_worked_hrs",
    "Lateness_Hrs": "lateness_hrs",
    "Early_Out_Hrs": "early_out_hrs",
    "Overbreak_Hrs": "overbreak_hrs",
    "Total_OT": "total_ot",
    "OT_Authorized": "ot_authorized",
    "OT_Not_Authorized": "ot_not_authorized",
    "Leave_Hrs": "leave_hrs",
}

_CLARIFIABLE_CAPABILITIES = {"unsupported_constraint", "reversed_temporal_range"}


def _last_turn(state: ConversationState) -> VerifiedTurn | None:
    return state.verified_turns[-1] if state.verified_turns else None


def _result_rows(turn: VerifiedTurn | None) -> list[dict]:
    if turn is None:
        return []
    rows = turn.result.get("rows", [])
    return (
        rows
        if isinstance(rows, list)
        else list(rows)
        if isinstance(rows, tuple)
        else []
    )


def _plan_matches(sql: str, expected: dict | None) -> bool:
    if expected is None:
        return True
    folded = " ".join(sql.casefold().split())
    try:
        statements = parse(sql, read="postgres")
    except ParseError:
        statements = []
    predicate_scope = " ".join(
        node.sql(dialect="postgres").casefold()
        for statement in statements
        if statement is not None
        for node in statement.find_all(exp.Where, exp.Having)
    )
    predicate_scope = re.sub(r'"([a-z_][a-z0-9_]*)"', r"\1", predicate_scope)
    predicate_scope = re.sub(
        r"cast\('([0-9]{4}-[0-9]{2}-[0-9]{2})' as date\)",
        r"date '\1'",
        predicate_scope,
    )

    def contains_filter(item: dict) -> bool:
        field = str(
            _PHYSICAL_FIELD.get(item.get("field"), item.get("field", ""))
        ).casefold()
        value = item.get("value", "")
        operator_name = str(item.get("operator", "")).casefold()
        operator = {
            "eq": r"=",
            "neq": r"(?:<>|!=)",
            "gt": r">",
            "gte": r">=",
            "lt": r"<",
            "lte": r"<=",
        }.get(operator_name)
        if not field or field not in predicate_scope:
            return False
        if operator_name == "in" and isinstance(value, list):
            match = re.search(
                rf"\b{re.escape(field)}\b\s+in\s*\(([^)]*(?:\)[^)]*)?)\)",
                predicate_scope,
            )
            return match is not None and all(
                str(item_value).casefold() in match.group(1) for item_value in value
            )
        if operator is None:
            return not value or str(value).casefold() in predicate_scope
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            literal = (
                rf"{int(numeric)}(?:\.0+)?"
                if numeric.is_integer()
                else re.escape(format(numeric, "g"))
            )
            quote = ""
        else:
            literal = re.escape(str(value).casefold())
            quote = "(?:date\\s+)?'?"
        if operator_name in {"gte", "lte"}:
            between = re.search(
                rf"\b{re.escape(field)}\b\s+between\s+"
                rf"(?:date\s+)?'?([^'\s]+)'?\s+and\s+"
                rf"(?:date\s+)?'?([^'\s]+)'?",
                predicate_scope,
            )
            if between is not None:
                bound = between.group(1 if operator_name == "gte" else 2)
                return re.fullmatch(literal, bound) is not None
        if operator_name == "lte" and isinstance(value, str):
            try:
                exclusive_end = str(date.fromisoformat(value) + timedelta(days=1))
            except ValueError:
                exclusive_end = ""
            if exclusive_end and re.search(
                rf"\b{re.escape(field)}\b\s*<\s*"
                rf"(?:cast\('?{re.escape(exclusive_end)}'?\s+as\s+date\)"
                rf"|(?:date\s+)?'?{re.escape(exclusive_end)}'?)",
                predicate_scope,
            ):
                return True
        field_expression = (
            rf"(?:\b{re.escape(field)}\b|"
            rf"coalesce\(\s*\b{re.escape(field)}\b\s*,\s*[^)]+\))"
        )
        return (
            re.search(
                rf"{field_expression}\s*{operator}\s*{quote}{literal}{quote}",
                predicate_scope,
            )
            is not None
        )

    for key, value in expected.items():
        if key == "required_filter" and not contains_filter(value):
            return False
        if key == "required_filters" and not all(
            contains_filter(item) for item in value
        ):
            return False
        if key == "business_predicates":
            markers = {
                "absent": ("exception", "absent"),
                "not_worked": ("total_worked_hrs",),
                "worked": ("total_worked_hrs",),
                "scheduled_working_day": ("day_type", "working day"),
            }
            if any(
                not all(marker in folded for marker in markers.get(item, (item,)))
                for item in value
            ):
                return False
        if key == "aggregation":
            if value == "none":
                continue
            if value == "percentage":
                if (
                    re.search(r"\bcount\s*\(", folded) is None
                    or "100" not in folded
                    or "/" not in folded
                ):
                    return False
                continue
            pattern = {
                "distinct_count": r"\bcount\s*\(\s*distinct\b",
                "average": r"\bavg\s*\(",
                "count": r"\bcount\s*\(",
                "sum": r"\bsum\s*\(",
            }.get(value, rf"\b{re.escape(str(value))}\s*\(")
            if re.search(pattern, folded) is None:
                return False
        if (
            key == "aggregation_field"
            and _PHYSICAL_FIELD.get(value, value).casefold() not in folded
        ):
            return False
        if key == "group_by" and (
            "group by" not in folded
            or any(
                _PHYSICAL_FIELD.get(item, item).casefold() not in folded
                for item in value
            )
        ):
            return False
        if key == "mode":
            # Retrieval-mode metadata belongs to the retired flat-query runtime.
            # Direct SQL is evaluated by its observable predicates and result.
            continue
    return True


def _expected_subset(actual, expected) -> bool:
    if expected is None:
        return True
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _expected_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) > len(actual):
            return False
        matches = [
            [
                index
                for index, candidate in enumerate(actual)
                if _expected_subset(candidate, item)
            ]
            for item in expected
        ]
        assigned: dict[int, int] = {}

        def assign(expected_index: int, seen: set[int]) -> bool:
            for actual_index in matches[expected_index]:
                if actual_index in seen:
                    continue
                seen.add(actual_index)
                if actual_index not in assigned or assign(assigned[actual_index], seen):
                    assigned[actual_index] = expected_index
                    return True
            return False

        return all(assign(index, set()) for index in range(len(expected)))
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        if isinstance(expected, int):
            return float(actual) == float(expected)
        expected_text = format(expected, "g")
        decimal_places = (
            len(expected_text.split(".", 1)[1]) if "." in expected_text else 0
        )
        absolute_tolerance = 0.5 * (10 ** (-decimal_places))
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=0.0,
            abs_tol=absolute_tolerance,
        )
    return actual == expected


def _calculation(
    turn: VerifiedTurn | None, expected: dict | None
) -> dict[str, object] | None:
    if expected is None or turn is None:
        return None
    rows = _result_rows(turn)
    if not rows:
        return None
    numeric_items = [
        (str(key).casefold(), value)
        for key, value in rows[0].items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    numeric_value = numeric_items[0][1] if numeric_items else None
    if expected.get("operation") == "percentage":
        percentage_values = [
            value for key, value in numeric_items if "percent" in key or "pct" in key
        ]
        if percentage_values:
            numeric_value = percentage_values[0]
    return {
        "operation": expected.get("operation"),
        "field": _PHYSICAL_FIELD.get(expected.get("field"), expected.get("field")),
        "value": numeric_value,
        "group_by": [
            _PHYSICAL_FIELD.get(item, item) for item in expected.get("group_by", [])
        ],
        "total_groups": len(rows),
        "rows": rows,
    }


def _group_values(
    turn: VerifiedTurn | None, expected_calculation: dict | None
) -> list[dict[str, object]]:
    if turn is None or expected_calculation is None:
        return []
    group_fields = [
        _PHYSICAL_FIELD.get(item, item).casefold()
        for item in expected_calculation.get("group_by", [])
    ]
    normalized = []
    for row in _result_rows(turn):
        folded = {str(key).casefold(): value for key, value in row.items()}
        group = [folded.get(field) for field in group_fields]
        group_keys = set(group_fields)
        numeric = [
            value
            for key, value in folded.items()
            if key not in group_keys
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ]
        if all(field in folded for field in group_fields) and numeric:
            normalized.append({"group": group, "value": numeric[0]})
    return normalized


def _group_values_by_shape(
    rows: list[dict[str, object]], expected: list[dict[str, object]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]] | None:
    """Compare one-measure grouped rows without depending on the SQL alias."""

    if not expected or not all(isinstance(item, dict) for item in expected):
        return None
    group_fields = tuple(
        key for key, value in expected[0].items() if isinstance(value, str)
    )
    measure_fields = tuple(
        key
        for key, value in expected[0].items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )
    if (
        not group_fields
        or len(measure_fields) != 1
        or any(set(item) != set(expected[0]) for item in expected)
    ):
        return None
    actual_values = []
    for row in rows:
        numbers = [
            value
            for key, value in row.items()
            if key not in {*group_fields, "matched_count"}
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ]
        if all(field in row for field in group_fields) and len(numbers) == 1:
            actual_values.append(
                {"group": [row[field] for field in group_fields], "value": numbers[0]}
            )
    expected_values = [
        {
            "group": [item[field] for field in group_fields],
            "value": item[measure_fields[0]],
        }
        for item in expected
    ]
    return actual_values, expected_values


def _record_ids(turn: VerifiedTurn | None) -> list[str]:
    return [str(row["record_id"]) for row in _result_rows(turn) if "record_id" in row]


def _normalized_answer_text(value: str) -> str:
    normalized = " ".join(value.casefold().replace("–", "-").replace("—", "-").split())
    normalized = re.sub(r"(?<=\d),(?=\d{3}\b)", "", normalized)
    month_names = (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    )

    def expand_iso_date(match: re.Match[str]) -> str:
        year, month_number, day = (int(item) for item in match.groups())
        if not 1 <= month_number <= 12:
            return match.group(0)
        return f"{month_names[month_number - 1]} {day}, {year}"

    normalized = re.sub(
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
        expand_iso_date,
        normalized,
    )
    month = (
        r"january|february|march|april|may|june|july|august|september|"
        r"october|november|december"
    )
    normalized = re.sub(
        rf"\b({month})\s+(\d{{1,2}})\s+(?:to|through)\s+"
        rf"(?:\1\s+)?(\d{{1,2}}),\s*(\d{{4}})\b",
        r"\1 \2-\3, \4",
        normalized,
    )
    normalized = normalized.replace("requested month", "requested period")
    normalized = normalized.replace("the entire month", "the full requested period")
    normalized = normalized.replace("the full month", "the full requested period")
    normalized = normalized.replace("does not encompass", "is not")
    normalized = normalized.replace("does not cover", "is not")
    return normalized


def _answer_tokens(value: str) -> set[str]:
    normalized = _normalized_answer_text(value)
    normalized = normalized.replace("no positive worked hours", "zero worked hours")
    normalized = normalized.replace("working day", "scheduled working day")
    words = re.findall(r"[a-z]+|\d+(?:\.\d+)?", normalized)
    ignored = {
        "a",
        "an",
        "the",
        "for",
        "in",
        "of",
        "on",
        "was",
        "were",
        "has",
        "had",
        "is",
        "are",
        "during",
        "total",
        "number",
        "recorded",
    }
    aliases = {
        "absence": "absent",
        "attendance": "work_attend",
        "attend": "work_attend",
        "attended": "work_attend",
        "days": "day",
        "hours": "hour",
        "records": "record",
        "scheduled": "schedule",
        "worked": "work_attend",
        "working": "work_attend",
        "work": "work_attend",
    }
    return {aliases.get(word, word) for word in words if word not in ignored}


def _answer_fact_matches(answer: str, fact: str) -> bool:
    normalized_answer = _normalized_answer_text(answer)
    normalized_fact = _normalized_answer_text(fact)
    if normalized_fact in normalized_answer:
        return True
    return _answer_tokens(fact).issubset(_answer_tokens(answer))


def evaluate_outcome(test: TestQuestion, outcome) -> BehaviorEval:
    turn = _last_turn(outcome.state)
    rows = _result_rows(turn)
    expected_unsupported = test.expected_unsupported_capabilities
    clarifiable_unsupported = bool(
        set(expected_unsupported) & _CLARIFIABLE_CAPABILITIES
    )
    expected_clarification = bool(
        test.expected_clarification_ids
        or test.expected_clarification_outcome == "ambiguous"
    )
    expected_safe_stop = bool(
        test.expected_error or test.expected_clarification_outcome == "none"
    )
    # An unsupported schema SELECT is executed internally, then surfaced as an
    # Unsupported outcome without publishing a verified answer turn.
    expected_execution = not (expected_clarification or expected_safe_stop)
    pending = outcome.state.pending_employee_confirmation
    pending_ids = [option.employee_id for option in pending.options] if pending else []
    calculation = _calculation(turn, test.expected_calculation)
    shaped_group_values = _group_values_by_shape(rows, test.expected_group_values)
    group_order_ok = not test.expected_group_order or (
        len(rows) == len(test.expected_group_order)
        and all(
            expected_group in row.values()
            for row, expected_group in zip(rows, test.expected_group_order)
        )
    )
    matched_count = next(
        (
            value
            for row in rows
            for key, value in row.items()
            if str(key).casefold() == "matched_count"
            and isinstance(value, (int, float))
        ),
        (
            calculation["value"]
            if test.expected_matched_count is not None
            and calculation
            and calculation.get("value") is not None
            else len(rows)
        ),
    )
    expected_calculation = None
    if test.expected_calculation is not None:
        expected_calculation = dict(test.expected_calculation)
        for metadata_key in (
            "numerator",
            "denominator",
            "measure",
            "business_predicates",
            "coverage",
        ):
            expected_calculation.pop(metadata_key, None)
        expected_calculation["field"] = _PHYSICAL_FIELD.get(
            expected_calculation.get("field"), expected_calculation.get("field")
        )
        if "group_by" in expected_calculation:
            expected_calculation["group_by"] = [
                _PHYSICAL_FIELD.get(item, item)
                for item in expected_calculation["group_by"]
            ]
    return BehaviorEval(
        outcome_ok=(
            (
                isinstance(outcome, Unsupported)
                or (
                    clarifiable_unsupported
                    and isinstance(outcome, Clarification)
                    and pending is None
                )
            )
            if expected_unsupported
            else isinstance(outcome, (Clarification, Unsupported))
            if expected_clarification
            else isinstance(outcome, (Clarification, Unsupported))
            if expected_safe_stop
            else isinstance(outcome, Answered) == expected_execution
        ),
        plan_ok=_plan_matches(turn.executed_sql if turn else "", test.expected_plan),
        employee_ids_ok=(
            not test.expected_employee_ids
            or (
                turn is not None
                and list(turn.employee_ids) == test.expected_employee_ids
            )
        ),
        matched_count_ok=(
            test.expected_matched_count is None
            or matched_count == test.expected_matched_count
        ),
        calculation_ok=_expected_subset(calculation, expected_calculation),
        clarification_ok=(
            (
                expected_safe_stop
                and isinstance(outcome, (Clarification, Unsupported))
                and pending is None
            )
            or (
                not expected_clarification
                and not expected_safe_stop
                and not isinstance(outcome, Clarification)
            )
            or (
                isinstance(outcome, Clarification)
                and (
                    not test.expected_clarification_ids
                    or pending_ids == test.expected_clarification_ids
                )
            )
            or (expected_clarification and isinstance(outcome, Unsupported))
        ),
        answer_facts_ok=all(
            _answer_fact_matches(outcome.reply, item)
            for item in test.expected_answer_facts
        ),
        record_ids_ok=(
            not test.expected_record_ids
            or _record_ids(turn) == test.expected_record_ids
        ),
        group_values_ok=(
            not test.expected_group_values
            or _expected_subset(
                (
                    _group_values(turn, test.expected_calculation)
                    if all(
                        isinstance(item, dict) and "group" in item and "value" in item
                        for item in test.expected_group_values
                    )
                    else shaped_group_values[0]
                    if shaped_group_values
                    else rows
                ),
                (
                    shaped_group_values[1]
                    if shaped_group_values
                    else test.expected_group_values
                ),
            )
        ),
        group_order_ok=group_order_ok,
        unsupported_capabilities_ok=(
            (
                not expected_unsupported
                and not expected_safe_stop
                and not expected_clarification
                and not isinstance(outcome, Unsupported)
            )
            or (
                expected_safe_stop and isinstance(outcome, (Clarification, Unsupported))
            )
            or (
                expected_clarification
                and isinstance(outcome, (Clarification, Unsupported))
            )
            or (
                clarifiable_unsupported
                and isinstance(outcome, Clarification)
                and pending is None
            )
            or (
                isinstance(outcome, Unsupported)
                and outcome.capability in expected_unsupported
            )
        ),
    )


def evaluate_behavior(test: TestQuestion) -> BehaviorEval:
    state = ConversationState()
    history: list[dict[str, str]] = []
    outcome = run_turn(
        TurnRequest(
            question=test.question,
            history=tuple(history),
            state=state,
            access_context=LOCAL_DEMO_ACCESS,
        )
    )
    result = evaluate_outcome(test, outcome)
    multi_turn_ok = True
    state = outcome.state
    history.extend(
        (
            {"role": "user", "content": test.question},
            {"role": "assistant", "content": outcome.reply},
        )
    )
    for expected in test.turns:
        outcome = run_turn(
            TurnRequest(
                question=expected.user,
                history=tuple(history),
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            )
        )
        state = outcome.state
        pending = state.pending_employee_confirmation
        if expected.expected_employee_ids is not None:
            multi_turn_ok = (
                multi_turn_ok
                and list(state.active_employee_ids) == expected.expected_employee_ids
            )
        if expected.expected_pending_ids is not None:
            pending_ids = (
                [option.employee_id for option in pending.options] if pending else []
            )
            multi_turn_ok = (
                multi_turn_ok and pending_ids == expected.expected_pending_ids
            )
        multi_turn_ok = multi_turn_ok and all(
            _answer_fact_matches(outcome.reply, item)
            for item in expected.expected_answer_facts
        )
        history.extend(
            (
                {"role": "user", "content": expected.user},
                {"role": "assistant", "content": outcome.reply},
            )
        )
    return result.model_copy(update={"multi_turn_ok": multi_turn_ok})


def _fingerprints(case_bytes: bytes) -> dict[str, str]:
    values = {
        "runtime": "attendance-online/v1",
        "evaluator": "attendance-direct-sql-eval/v1",
    }
    fingerprints = {
        key: hashlib.sha256(value.encode()).hexdigest() for key, value in values.items()
    }
    fingerprints["cases"] = hashlib.sha256(case_bytes).hexdigest()
    return fingerprints


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    temporary.write_text(serialized, encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        path.write_text(serialized, encoding="utf-8")
        temporary.unlink(missing_ok=True)


def _resume_report(
    path: Path,
    *,
    selected_indices: list[int],
    fingerprints: dict[str, str],
) -> dict:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("The evaluator checkpoint is not readable JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("The evaluator checkpoint must be a JSON object")
    if report.get("fingerprints") != fingerprints:
        raise ValueError("The evaluator checkpoint fingerprints do not match this run")
    if report.get("selected_indices") != selected_indices:
        raise ValueError("The evaluator checkpoint selected indices do not match")
    if report.get("tests") != len(selected_indices):
        raise ValueError("The evaluator checkpoint test count does not match")
    completed = report.get("completed")
    if (
        not isinstance(completed, int)
        or isinstance(completed, bool)
        or not 0 <= completed <= len(selected_indices)
    ):
        raise ValueError("The evaluator checkpoint completed prefix is invalid")
    if report.get("status") not in {"running", "complete"}:
        raise ValueError("The evaluator checkpoint status is invalid")
    failures = report.get("failures")
    if not isinstance(failures, list):
        raise ValueError("The evaluator checkpoint failures are invalid")
    completed_indices = set(selected_indices[:completed])
    if any(
        not isinstance(item, dict) or item.get("index") not in completed_indices
        for item in failures
    ):
        raise ValueError("The evaluator checkpoint failure indices are invalid")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("test_number", nargs="?", type=int)
    parser.add_argument("--behavior", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--test-file", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--batch-size", type=int)
    args = parser.parse_args(argv)
    if args.resume and args.output is None:
        parser.error("--resume requires --output")
    if args.batch_size is not None and args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    case_path = args.test_file if args.test_file is not None else Path(TEST_FILE)
    case_bytes = case_path.read_bytes()
    cases = load_tests(test_file=args.test_file)
    index = args.test_number if args.test_number is not None else 0
    selected = tuple(enumerate(cases)) if args.all else ((index, cases[index]),)
    selected_indices = [case_index for case_index, _ in selected]
    fingerprints = _fingerprints(case_bytes)
    if args.resume:
        report = _resume_report(
            args.output,
            selected_indices=selected_indices,
            fingerprints=fingerprints,
        )
    else:
        report = {
            "status": "running",
            "tests": len(selected),
            "selected_indices": selected_indices,
            "completed": 0,
            "failures": [],
            "fingerprints": fingerprints,
        }
    if args.output and not args.resume:
        _write(args.output, report)
    start = report["completed"]
    stop = (
        min(len(selected), start + args.batch_size)
        if args.batch_size is not None
        else len(selected)
    )
    for case_index, case in selected[start:stop]:
        result = evaluate_behavior(case)
        if not result.passed:
            report["failures"].append(
                {"index": case_index, "result": result.model_dump()}
            )
        report["completed"] += 1
        if args.output:
            _write(args.output, report)
    report["status"] = (
        "complete" if report["completed"] == report["tests"] else "running"
    )
    if args.output:
        _write(args.output, report)
    print(json.dumps(report, indent=2))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BehaviorEval", "evaluate_behavior", "evaluate_outcome", "main"]
