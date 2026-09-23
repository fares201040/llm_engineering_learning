"""Deterministic evaluator for the attendance-online/v1 public runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from ..new_implementation.online.execution import LOCAL_DEMO_ACCESS
from ..new_implementation.online.pipeline import (
    Answered,
    Clarification,
    TurnRequest,
    Unsupported,
    run_turn,
)
from ..new_implementation.online.query import All, Any, Condition, Not, Predicate
from ..new_implementation.online.state import ConversationState, VerifiedTurn
from .test import TestQuestion, load_tests


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
    unsupported_capabilities_ok: bool = True
    multi_turn_ok: bool = True

    @property
    def passed(self) -> bool:
        return all(self.model_dump().values())


def canonical_expression(expression):
    """Keep nested Boolean meaning intact for evaluator comparisons."""

    if isinstance(expression, Condition):
        return {
            "kind": "condition",
            "field": expression.field_id,
            "operator": expression.operator,
            "value": list(expression.value) if isinstance(expression.value, tuple) else expression.value,
        }
    if isinstance(expression, Predicate):
        return {"kind": "predicate", "predicate": expression.predicate_id}
    if isinstance(expression, Not):
        return {"kind": "not", "item": canonical_expression(expression.item)}
    if isinstance(expression, (All, Any)):
        return {
            "kind": expression.kind,
            "items": [canonical_expression(item) for item in expression.items],
        }
    raise TypeError(f"unsupported expression {type(expression).__name__}")


def _last_turn(state: ConversationState) -> VerifiedTurn | None:
    return state.verified_turns[-1] if state.verified_turns else None


def _query_summary(turn: VerifiedTurn | None) -> dict[str, object]:
    if turn is None:
        return {}
    filters = [
        canonical_expression(component.expression)
        for component in turn.components
        if component.kind == "filter"
    ]
    outputs = [component.output for component in turn.components if component.kind == "output"]
    if not outputs:
        return {"filters": filters, "route": "narrative"}
    output = outputs[0]
    summary: dict[str, object] = {"filters": filters, "output": output.model_dump(mode="json")}
    if output.kind == "aggregate" and output.measures:
        measure = output.measures[0]
        summary.update(
            {
                "aggregation": measure.function,
                "aggregation_field": measure.field_id,
                "group_by": list(output.group_by),
                "limit": output.limit,
            }
        )
    elif output.kind == "rows":
        summary.update({"projection": list(output.fields), "limit": output.limit})
    return summary


def _result_rows(turn: VerifiedTurn | None) -> list[dict]:
    if turn is None:
        return []
    rows = turn.result.get("rows", [])
    return rows if isinstance(rows, list) else list(rows) if isinstance(rows, tuple) else []


def _expected_subset(actual, expected) -> bool:
    if expected is None:
        return True
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _expected_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(item in actual for item in expected)
    return actual == expected


_LOGICAL_FIELD = {
    "Employee_ID": "employee_id",
    "Name": "employee_name",
    "Date": "date",
    "Day_Type": "day_type",
    "Status": "status",
    "Exception": "exception",
    "Total_Worked_Hrs": "worked_hours",
    "Lateness_Hrs": "lateness_hours",
    "Early_Out_Hrs": "early_out_hours",
    "Overbreak_Hrs": "overbreak_hours",
    "Total_OT": "total_overtime_hours",
    "OT_Authorized": "authorized_overtime_hours",
    "OT_Not_Authorized": "unauthorized_overtime_hours",
    "Leave_Hrs": "leave_hours",
}


def _leaves(expressions: list[dict], *, negated: bool = False):
    for expression in expressions:
        kind = expression.get("kind")
        if kind == "condition":
            yield {**expression, "negated": negated}
        elif kind == "predicate":
            yield {**expression, "negated": negated}
        elif kind == "not":
            yield from _leaves([expression["item"]], negated=not negated)
        else:
            yield from _leaves(expression.get("items", []), negated=negated)


def _plan_matches(actual: dict[str, object], expected: dict | None) -> bool:
    if expected is None:
        return True
    output = actual.get("output") if isinstance(actual.get("output"), dict) else {}
    leaves = list(_leaves(actual.get("filters", [])))
    predicates = {item["predicate"] for item in leaves if item.get("kind") == "predicate" and not item.get("negated")}

    def filter_matches(value: dict) -> bool:
        field = _LOGICAL_FIELD.get(value.get("field"), value.get("field"))
        wanted = {
            "kind": "condition",
            "field": field,
            "operator": value.get("operator"),
            "value": value.get("value"),
            "negated": False,
        }
        return wanted in leaves

    for key, value in expected.items():
        if key == "required_filter" and not filter_matches(value):
            return False
        if key == "required_filters" and not all(filter_matches(item) for item in value):
            return False
        if key == "business_predicates" and set(value) != predicates:
            return False
        if key == "aggregation" and actual.get("aggregation") != value:
            return False
        if key == "aggregation_field" and actual.get("aggregation_field") != _LOGICAL_FIELD.get(value, value):
            return False
        if key == "group_by" and actual.get("group_by") != [_LOGICAL_FIELD.get(item, item) for item in value]:
            return False
        if key == "measure":
            derived = "distinct_dates" if actual.get("aggregation") == "distinct_count" and actual.get("aggregation_field") == "date" else "attendance_records" if actual.get("aggregation") == "count" else None
            if derived != value:
                return False
        if key == "mode":
            route = "semantic" if actual.get("route") == "narrative" else "exact"
            if value not in {route, "hybrid" if route == "semantic" else route}:
                return False
    return True


def _calculation(turn: VerifiedTurn | None, plan: dict[str, object]) -> dict[str, object] | None:
    rows = _result_rows(turn)
    output = plan.get("output")
    if not rows or not isinstance(output, dict) or output.get("kind") != "aggregate":
        return None
    measures = output.get("measures") or []
    if not measures:
        return None
    measure = measures[0]
    output_id = measure["output_id"]
    return {
        "operation": measure["function"],
        "field": measure.get("field_id"),
        "value": rows[0].get(output_id) if not output.get("group_by") else None,
        "rows": rows,
    }


def _calculation_matches(actual: dict[str, object] | None, expected: dict | None) -> bool:
    if expected is None:
        return True
    normalized = dict(expected)
    if normalized.get("field") in _LOGICAL_FIELD:
        normalized["field"] = _LOGICAL_FIELD[normalized["field"]]
    return _expected_subset(actual, normalized)


def _record_ids(turn: VerifiedTurn | None) -> list[str]:
    if turn is None:
        return []
    if turn.result.get("kind") == "narrative":
        return [str(item) for item in turn.result.get("record_ids", []) if item is not None]
    return [str(row["record_id"]) for row in _result_rows(turn) if "record_id" in row]


def evaluate_outcome(test: TestQuestion, outcome) -> BehaviorEval:
    turn = _last_turn(outcome.state)
    plan = _query_summary(turn)
    rows = _result_rows(turn)
    calculation = _calculation(turn, plan)
    expected_unsupported = test.expected_unsupported_capabilities
    expected_clarification = bool(test.expected_clarification_ids or test.expected_clarification_outcome)
    expected_execution = not (expected_unsupported or expected_clarification or test.expected_error)
    pending = outcome.state.pending_employee_confirmation
    pending_ids = [pending.employee_id] if pending is not None else []
    matched_count = len(rows)
    if calculation and calculation.get("operation") == "count" and calculation.get("value") is not None:
        matched_count = calculation["value"]
    actual_group_values = rows if plan.get("group_by") else []
    return BehaviorEval(
        outcome_ok=(isinstance(outcome, Answered) == expected_execution),
        plan_ok=_plan_matches(plan, test.expected_plan),
        employee_ids_ok=(not test.expected_employee_ids or (turn is not None and list(turn.employee_ids) == test.expected_employee_ids)),
        matched_count_ok=(test.expected_matched_count is None or matched_count == test.expected_matched_count),
        calculation_ok=_calculation_matches(calculation, test.expected_calculation),
        clarification_ok=(
            (not expected_clarification and not isinstance(outcome, Clarification))
            or (
                isinstance(outcome, Clarification)
                and (not test.expected_clarification_ids or pending_ids == test.expected_clarification_ids)
            )
        ),
        answer_facts_ok=all(item.casefold() in outcome.reply.casefold() for item in test.expected_answer_facts),
        record_ids_ok=(not test.expected_record_ids or _record_ids(turn) == test.expected_record_ids),
        group_values_ok=(not test.expected_group_values or all(item in actual_group_values for item in test.expected_group_values)),
        unsupported_capabilities_ok=(
            (not expected_unsupported and not isinstance(outcome, Unsupported))
            or (isinstance(outcome, Unsupported) and outcome.capability in expected_unsupported)
        ),
    )


def evaluate_behavior(test: TestQuestion) -> BehaviorEval:
    state = ConversationState()
    history: list[dict[str, str]] = []
    outcome = run_turn(
        TurnRequest(question=test.question, history=tuple(history), state=state, access_context=LOCAL_DEMO_ACCESS)
    )
    result = evaluate_outcome(test, outcome)
    multi_turn_ok = True
    state = outcome.state
    history.extend(({"role": "user", "content": test.question}, {"role": "assistant", "content": outcome.reply}))
    for expected in test.turns:
        outcome = run_turn(
            TurnRequest(question=expected.user, history=tuple(history), state=state, access_context=LOCAL_DEMO_ACCESS)
        )
        state = outcome.state
        pending = state.pending_employee_confirmation
        if expected.expected_employee_ids is not None:
            multi_turn_ok = multi_turn_ok and list(state.active_employee_ids) == expected.expected_employee_ids
        if expected.expected_pending_ids is not None:
            multi_turn_ok = multi_turn_ok and ([pending.employee_id] if pending else []) == expected.expected_pending_ids
        multi_turn_ok = multi_turn_ok and all(item.casefold() in outcome.reply.casefold() for item in expected.expected_answer_facts)
        history.extend(({"role": "user", "content": expected.user}, {"role": "assistant", "content": outcome.reply}))
    return result.model_copy(update={"multi_turn_ok": multi_turn_ok})


def _fingerprints() -> dict[str, str]:
    values = {
        "runtime": "attendance-online/v1",
        "evaluator": "attendance-online-eval/v1",
    }
    return {key: hashlib.sha256(value.encode()).hexdigest() for key, value in values.items()}


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("test_number", nargs="?", type=int)
    parser.add_argument("--behavior", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    cases = load_tests()
    if args.all:
        selected = tuple(enumerate(cases))
    else:
        index = args.test_number if args.test_number is not None else 0
        selected = ((index, cases[index]),)
    report = {"status": "running", "tests": len(selected), "completed": 0, "failures": [], "fingerprints": _fingerprints()}
    if args.output:
        _write(args.output, report)
    for index, case in selected:
        result = evaluate_behavior(case)
        if not result.passed:
            report["failures"].append({"index": index, "result": result.model_dump()})
        report["completed"] += 1
        if args.output:
            _write(args.output, report)
    report["status"] = "complete"
    if args.output:
        _write(args.output, report)
    print(json.dumps(report, indent=2))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BehaviorEval", "canonical_expression", "evaluate_behavior", "evaluate_outcome", "main"]
