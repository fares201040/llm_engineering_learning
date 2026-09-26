"""Named live acceptance scenarios for attendance-online/v1."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from ..new_implementation.online.execution import LOCAL_DEMO_ACCESS
from ..new_implementation.online.pipeline import TurnRequest, _locale, run_turn
from ..new_implementation.online.state import ConversationState


@dataclass(frozen=True)
class AcceptanceStep:
    question: str
    expected_outcome: Literal["answered", "clarification", "unsupported"]
    expected_capability: str | None = None
    expected_answer_groups: tuple[tuple[str, ...], ...] = ()
    expected_answer_rows: tuple[tuple[str, ...], ...] = ()
    expected_result_groups: tuple[tuple[str, ...], ...] = ()
    expected_result_rows: tuple[tuple[str, ...], ...] = ()
    expected_sql_groups: tuple[tuple[str, ...], ...] = ()
    forbidden_sql_terms: tuple[str, ...] = ()
    forbidden_answer_terms: tuple[str, ...] = ()
    expected_row_count: int | None = None
    expected_answer_order: tuple[str, ...] = ()
    expected_result_order: tuple[str, ...] = ()


SCENARIOS = {
    "wail": (
        AcceptanceStep(
            "How many days did Wail work during September 2026?", "answered"
        ),
        AcceptanceStep("Show the absence dates instead.", "answered"),
    ),
    "faris": (
        AcceptanceStep(
            "How many days did Faris work during September 2026?", "answered"
        ),
        AcceptanceStep("How many hours did he work?", "answered"),
        AcceptanceStep("Show the absence dates instead.", "answered"),
        AcceptanceStep("Repeat that result.", "answered"),
    ),
    "generic-subject": (
        AcceptanceStep("Show attendance for Wail Ali and Faris Hassan.", "answered"),
        AcceptanceStep("Show attendance for Engineering employees.", "answered"),
        AcceptanceStep(
            "Which employees were absent during September 2026?", "answered"
        ),
        AcceptanceStep("Show employees who worked more than 8 hours.", "answered"),
        AcceptanceStep(
            "Show Faris Hassan and employees who worked more than 8 hours.", "answered"
        ),
        AcceptanceStep("Show Faris Hassan in Engineering.", "answered"),
        AcceptanceStep("Show attendance by department for all employees.", "answered"),
        AcceptanceStep("What about October 2026?", "answered"),
        AcceptanceStep(
            "Find employees whose September total exceeded 160 hours, then show their October absences.",
            "answered",
        ),
    ),
    "long": (
        AcceptanceStep(
            "How many days did A11017 work during September 2026?",
            "answered",
            expected_answer_groups=(("5",), ("September 2026",)),
            expected_result_groups=(("5",),),
            expected_sql_groups=(("2026-09-01",), ("2026-09-30",)),
        ),
        AcceptanceStep(
            "Show the absence dates instead.",
            "answered",
            expected_answer_groups=(
                (
                    "2026-09-06",
                    "September 6, 2026",
                    "Sep 6, 2026",
                    "Sep 06, 2026",
                ),
            ),
            expected_result_groups=(
                ("2026-09-06", "September 6, 2026", "Sep 6, 2026", "Sep 06, 2026"),
            ),
            expected_sql_groups=(("2026-09-01",), ("2026-09-30",)),
            forbidden_sql_terms=("total_worked_hrs", "day_type"),
        ),
        AcceptanceStep(
            "Show dates that are not absent.",
            "answered",
            expected_answer_groups=(
                ("2026-09-01", "September 1", "Sep 1"),
                ("2026-09-02", "September 2", "Sep 2"),
                ("2026-09-03", "September 3", "Sep 3"),
                ("2026-09-04", "September 4", "Sep 4"),
                ("2026-09-05", "September 5", "Sep 5"),
            ),
            expected_result_groups=(
                ("2026-09-01", "September 1", "Sep 1"),
                ("2026-09-02", "September 2", "Sep 2"),
                ("2026-09-03", "September 3", "Sep 3"),
                ("2026-09-04", "September 4", "Sep 4"),
                ("2026-09-05", "September 5", "Sep 5"),
            ),
            expected_row_count=5,
            expected_sql_groups=(("2026-09-01",), ("2026-09-30",)),
        ),
        AcceptanceStep(
            "Group worked hours by department having total hours over 10.",
            "answered",
            expected_answer_groups=(
                ("Engineering",),
                ("56",),
                ("Finance",),
                ("36",),
                ("Operations",),
                ("16",),
                ("2026-08-03",),
                ("2026-09-06",),
            ),
            expected_answer_rows=(
                ("Engineering", "56"),
                ("Finance", "36"),
                ("Operations", "16"),
            ),
            expected_result_groups=(
                ("Engineering",),
                ("56",),
                ("Finance",),
                ("36",),
                ("Operations",),
                ("16",),
            ),
            expected_result_rows=(
                ("Engineering", "56"),
                ("Finance", "36"),
                ("Operations", "16"),
            ),
            forbidden_sql_terms=("employee_id", "attendance_date"),
            forbidden_answer_terms=("requested period",),
        ),
        AcceptanceStep(
            "Compare that with last month.",
            "answered",
            expected_answer_groups=(
                ("Engineering",),
                ("Finance",),
                ("Operations",),
                ("August 2026", "August"),
                ("September 2026", "September"),
                ("2026-08-31", "August 31"),
                ("16",),
                ("12",),
                ("8",),
                ("40",),
                ("24",),
            ),
            expected_answer_rows=(
                ("Engineering", "16", "40"),
                ("Finance", "12", "24"),
                ("Operations", "8"),
            ),
            expected_result_groups=(
                ("Engineering",),
                ("Finance",),
                ("Operations",),
                ("16",),
                ("12",),
                ("8",),
                ("40",),
                ("24",),
            ),
            expected_result_rows=(
                ("Engineering", "40", "16"),
                ("Finance", "24", "12"),
                ("Operations", "8"),
            ),
            expected_sql_groups=(
                ("2026-08-01",),
                ("2026-08-31",),
                ("2026-09-01",),
            ),
            forbidden_answer_terms=("Difference: -", "is fully covered", "2026-08-06"),
        ),
        AcceptanceStep("Join attendance to payroll.", "unsupported", "schema"),
        AcceptanceStep(
            "Rank departments by worked hours.",
            "answered",
            expected_answer_groups=(
                ("Engineering",),
                ("56",),
                ("Finance",),
                ("36",),
                ("Operations",),
                ("16",),
            ),
            expected_result_groups=(
                ("Engineering",),
                ("56",),
                ("Finance",),
                ("36",),
                ("Operations",),
                ("16",),
            ),
            expected_result_rows=(
                ("Engineering", "56"),
                ("Finance", "36"),
                ("Operations", "16"),
            ),
            expected_answer_order=("Engineering", "Finance", "Operations"),
            expected_result_order=("Engineering", "Finance", "Operations"),
        ),
        AcceptanceStep(
            "Show a running total over dates.",
            "answered",
            expected_answer_groups=(("running total", "cumulative"), ("108",)),
            expected_result_groups=(("108",),),
        ),
    ),
}


def _normalize_answer_fact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _semantic_answer_check(
    step: AcceptanceStep,
    actual_kind: str,
    reply: str,
    result: object,
    capability: str | None,
) -> tuple[bool, list[str], list[str]]:
    if step.expected_outcome != "answered":
        matches = actual_kind == step.expected_outcome and (
            step.expected_capability is None or capability == step.expected_capability
        )
        failure = [] if matches else ["expected outcome/capability"]
        return matches, failure, []
    if not step.expected_answer_groups or not step.expected_result_groups:
        return (
            False,
            ["semantic answer expectations are not defined"],
            ["semantic result expectations are not defined"],
        )

    normalized_reply = f" {_normalize_answer_fact(reply)} "
    missing = []
    for alternatives in step.expected_answer_groups:
        if not alternatives or not any(
            f" {_normalize_answer_fact(item)} " in normalized_reply
            for item in alternatives
        ):
            missing.append(alternatives[0] if alternatives else "answer fact")

    normalized_answer_rows = [
        f" {_normalize_answer_fact(line)} "
        for line in reply.splitlines()
        if line.strip()
    ]
    if len(step.expected_answer_rows) > 1:
        labels = {
            _normalize_answer_fact(required_row[0])
            for required_row in step.expected_answer_rows
            if required_row
        }
        mentions = sorted(
            (match.start(), label)
            for label in labels
            for match in re.finditer(
                rf"(?<!\w){re.escape(label)}(?!\w)",
                _normalize_answer_fact(reply),
            )
        )
        normalized = _normalize_answer_fact(reply)
        normalized_answer_rows = [
            f" {normalized[start : mentions[index + 1][0] if index + 1 < len(mentions) else len(normalized)]} "
            for index, (start, _label) in enumerate(mentions)
        ]
    for required_row in step.expected_answer_rows:
        if not any(
            all(f" {_normalize_answer_fact(fact)} " in row for fact in required_row)
            for row in normalized_answer_rows
        ):
            missing.append(f"same answer row: {', '.join(required_row)}")

    normalized_result = f" {_normalize_answer_fact(json.dumps(result, default=str))} "
    missing_result = []
    for alternatives in step.expected_result_groups:
        if not alternatives or not any(
            f" {_normalize_answer_fact(item)} " in normalized_result
            for item in alternatives
        ):
            missing_result.append(alternatives[0] if alternatives else "result fact")

    rows = result.get("rows", []) if isinstance(result, dict) else []
    if step.expected_row_count is not None and len(rows) != step.expected_row_count:
        missing_result.append(f"result row count: {step.expected_row_count}")
    normalized_rows = [
        f" {_normalize_answer_fact(json.dumps(row, default=str))} " for row in rows
    ]
    for required_row in step.expected_result_rows:
        if not any(
            all(f" {_normalize_answer_fact(fact)} " in row for fact in required_row)
            for row in normalized_rows
        ):
            missing_result.append(f"same result row: {', '.join(required_row)}")

    last_row = -1
    for fact in step.expected_result_order:
        position = next(
            (
                index
                for index in range(last_row + 1, len(normalized_rows))
                if f" {_normalize_answer_fact(fact)} " in normalized_rows[index]
            ),
            -1,
        )
        if position < 0:
            missing_result.append(f"result order: {fact}")
            break
        last_row = position

    last_position = -1
    for item in step.expected_answer_order:
        normalized_item = _normalize_answer_fact(item)
        position = normalized_reply.find(f" {normalized_item} ", last_position + 1)
        if position < 0:
            missing.append(f"in order: {item}")
            break
        last_position = position
    return not missing and not missing_result, missing, missing_result


def run_scenario_turn(
    name: str,
    turn_index: int,
    *,
    state: ConversationState | None = None,
    history: tuple[dict[str, str], ...] = (),
    surface: Literal["pipeline", "ui"] = "pipeline",
) -> tuple[dict, ConversationState, tuple[dict[str, str], ...]]:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}")
    steps = SCENARIOS[name]
    if not 0 <= turn_index < len(steps):
        raise ValueError(f"turn index must be between 0 and {len(steps) - 1}")
    previous = (
        state
        if state is not None
        else ConversationState(session_id=f"acceptance-{name}")
    )
    step = steps[turn_index]
    if step.expected_outcome == "answered" and (
        not step.expected_answer_groups or not step.expected_result_groups
    ):
        raise ValueError(
            f"scenario {name!r} turn {turn_index + 1} has no semantic answer expectations"
        )
    if surface == "ui":
        from .. import new_app
        from ..new_implementation import answer
        from ..new_implementation.online import pipeline, provider

        outcomes = []
        reference_trace = {}
        provider_log = provider.log_layer_output
        pipeline_log = pipeline.log_layer_output

        def trace_provider(layer, output, *, attempt=None):
            if layer in {"reference", "answer_writer", "answer_verifier"}:
                reference_trace[f"{layer}_{attempt}"] = output.model_dump(mode="json")
            provider_log(layer, output, attempt=attempt)

        def trace_pipeline(layer, output, *, attempt=None):
            if layer in {"employee_resolution", "sql_execution"}:
                reference_trace[layer] = output.model_dump(mode="json")
            elif layer == "sql_planner":
                reference_trace[f"{layer}_{attempt}"] = output
            pipeline_log(layer, output, attempt=attempt)

        def observe(request):
            outcome = run_turn(request)
            outcomes.append(outcome)
            return outcome

        with (
            patch.object(answer, "run_turn", side_effect=observe),
            patch.object(provider, "log_layer_output", side_effect=trace_provider),
            patch.object(pipeline, "log_layer_output", side_effect=trace_pipeline),
        ):
            ui_history, _context, ui_state = new_app.chat_with_state(
                [*history, {"role": "user", "content": step.question}], previous
            )
        if len(outcomes) != 1:
            raise RuntimeError("Gradio callback did not execute exactly one turn")
        outcome = outcomes[0]
        if ui_history[-1] != {"role": "assistant", "content": outcome.reply}:
            raise RuntimeError("Gradio callback changed the answer")
        if ui_state != outcome.state:
            raise RuntimeError("Gradio callback changed conversation state")
    else:
        reference_trace = {}
        outcome = run_turn(
            TurnRequest(
                question=step.question,
                history=history,
                state=previous,
                access_context=LOCAL_DEMO_ACCESS,
            )
        )
    actual_kind = getattr(outcome, "kind", "failed")
    outcome_ok = actual_kind == step.expected_outcome
    capability = getattr(outcome, "capability", None)
    if step.expected_capability is not None:
        outcome_ok = outcome_ok and capability == step.expected_capability
    prior_turns = previous.verified_turns
    next_turns = outcome.state.verified_turns
    state_continuity_ok = outcome.state.session_id == previous.session_id
    if actual_kind == "answered":
        state_continuity_ok = (
            state_continuity_ok
            and len(next_turns) == len(prior_turns) + 1
            and next_turns[: len(prior_turns)] == prior_turns
            and next_turns[-1].original_question == step.question
            and next_turns[-1].answer == outcome.reply
        )
    else:
        state_continuity_ok = state_continuity_ok and next_turns == prior_turns
    latest_result = (
        next_turns[-1].result
        if actual_kind == "answered"
        and len(next_turns) > len(prior_turns)
        and next_turns[-1].original_question == step.question
        else {}
    )
    semantic_ok, missing_answer_facts, missing_result_facts = _semantic_answer_check(
        step,
        actual_kind,
        str(getattr(outcome, "reply", "")),
        latest_result,
        capability,
    )
    missing_answer_facts.extend(
        f"forbidden answer term: {term}"
        for term in step.forbidden_answer_terms
        if term.casefold() in outcome.reply.casefold()
    )
    if missing_answer_facts:
        semantic_ok = False
    latest_sql = (
        next_turns[-1].executed_sql
        if actual_kind == "answered" and len(next_turns) > len(prior_turns)
        else ""
    )
    normalized_sql = f" {_normalize_answer_fact(latest_sql)} "
    missing_sql_facts = [
        alternatives[0]
        for alternatives in step.expected_sql_groups
        if not any(
            f" {_normalize_answer_fact(item)} " in normalized_sql
            for item in alternatives
        )
    ]
    missing_sql_facts.extend(
        f"forbidden SQL term: {term}"
        for term in step.forbidden_sql_terms
        if re.search(rf"\b{re.escape(term)}\b", latest_sql, re.IGNORECASE)
    )
    if missing_sql_facts:
        semantic_ok = False
    locale_ok = actual_kind != "answered" or (
        bool(next_turns)
        and next_turns[-1].locale == _locale(step.question)
        and (
            _locale(step.question) != "ar"
            or any("\u0600" <= char <= "\u06ff" for char in outcome.reply)
        )
    )
    if not locale_ok:
        missing_answer_facts.append("requested answer language")
        semantic_ok = False
    record = {
        "reference_trace": (
            reference_trace
            if not (outcome_ok and semantic_ok and state_continuity_ok)
            else {}
        ),
        "surface": surface,
        "turn_number": turn_index + 1,
        "question": step.question,
        "expected_outcome": step.expected_outcome,
        "expected_capability": step.expected_capability,
        "expected_answer_groups": [
            list(group) for group in step.expected_answer_groups
        ],
        "expected_answer_rows": [list(row) for row in step.expected_answer_rows],
        "expected_result_groups": [
            list(group) for group in step.expected_result_groups
        ],
        "expected_result_rows": [list(row) for row in step.expected_result_rows],
        "expected_sql_groups": [list(group) for group in step.expected_sql_groups],
        "forbidden_sql_terms": list(step.forbidden_sql_terms),
        "forbidden_answer_terms": list(step.forbidden_answer_terms),
        "expected_row_count": step.expected_row_count,
        "expected_answer_order": list(step.expected_answer_order),
        "expected_result_order": list(step.expected_result_order),
        "outcome": actual_kind,
        "failure_code": getattr(outcome, "code", None),
        "capability": capability,
        "reply": outcome.reply,
        "result": latest_result,
        "verified_turns": len(next_turns),
        "outcome_ok": outcome_ok,
        "semantic_ok": semantic_ok,
        "locale_ok": locale_ok,
        "missing_answer_facts": missing_answer_facts,
        "missing_result_facts": missing_result_facts,
        "missing_sql_facts": missing_sql_facts,
        "state_continuity_ok": state_continuity_ok,
        "passed": outcome_ok and semantic_ok and state_continuity_ok,
    }
    updated_history = history + (
        {"role": "user", "content": step.question},
        {"role": "assistant", "content": outcome.reply},
    )
    return record, outcome.state, updated_history


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    temporary.write_text(serialized, encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        path.write_text(serialized, encoding="utf-8")
        temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=tuple(SCENARIOS))
    parser.add_argument(
        "--ui", action="store_true", help="Exercise the Gradio chat callback"
    )
    parser.add_argument(
        "--turn", type=int, required=True, help="1-based scenario turn to run"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="checkpoint JSON path"
    )
    args = parser.parse_args(argv)
    surface = "ui" if args.ui else "pipeline"
    steps = SCENARIOS[args.scenario]
    if not 1 <= args.turn <= len(steps):
        parser.error(f"--turn must be between 1 and {len(steps)}")
    if steps[args.turn - 1].expected_outcome == "answered" and (
        not steps[args.turn - 1].expected_answer_groups
        or not steps[args.turn - 1].expected_result_groups
    ):
        parser.error(
            "this turn has no semantic answer expectations; add an explicit oracle before live execution"
        )

    if args.output.exists():
        try:
            report = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read checkpoint: {exc}")
        if not isinstance(report, dict):
            parser.error("checkpoint must contain a JSON object")
        if (
            report.get("scenario") != args.scenario
            or report.get("surface", "pipeline") != surface
            or report.get("status") != "running"
            or report.get("completed_turns") != args.turn - 1
        ):
            parser.error("checkpoint does not match the next sequential scenario turn")
        if args.turn == 1:
            state = ConversationState(session_id=f"acceptance-{args.scenario}")
            history = ()
            turns = []
            report["turns"] = turns
        else:
            try:
                state = ConversationState.model_validate_json(
                    json.dumps(report["state"]), strict=True
                )
                history = tuple(report["history"])
                turns = list(report["turns"])
            except (KeyError, TypeError, ValueError) as exc:
                parser.error(f"checkpoint is malformed: {exc}")
    else:
        if args.turn != 1:
            parser.error("the first checkpoint must run scenario turn 1")
        state = ConversationState(session_id=f"acceptance-{args.scenario}")
        history = ()
        turns = []
        report = {
            "status": "running",
            "scenario": args.scenario,
            "surface": surface,
            "total_turns": len(steps),
            "completed_turns": 0,
            "turns": turns,
        }
        _write(args.output, report)

    record, state, history = run_scenario_turn(
        args.scenario,
        args.turn - 1,
        state=state,
        history=history,
        surface=surface,
    )
    turns.append(record)
    report.update(
        {
            "completed_turns": args.turn,
            "turns": turns,
            "state": state.model_dump(mode="json"),
            "history": list(history),
        }
    )
    if not record["passed"]:
        report.update({"status": "failed", "passed": False})
    elif args.turn == len(steps):
        report.update(
            {"status": "complete", "passed": all(item["passed"] for item in turns)}
        )
    else:
        report["status"] = "running"
    _write(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "completed_turns": args.turn,
                "turn": record,
                "rewritten_request": (
                    state.verified_turns[-1].rewritten_request
                    if record["outcome"] == "answered"
                    else None
                ),
                "executed_sql": (
                    state.verified_turns[-1].executed_sql
                    if record["outcome"] == "answered"
                    else None
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
