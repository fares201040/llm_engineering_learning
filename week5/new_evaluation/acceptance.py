"""Named live acceptance scenarios for attendance-online/v1."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
from unittest.mock import patch

from ..new_implementation.online.execution import LOCAL_DEMO_ACCESS
from ..new_implementation.online.pipeline import Answered, Clarification, TurnRequest, Unsupported, run_turn
from ..new_implementation.online.state import ConversationState


SCENARIOS = {
    "wail": (
        "How many days did Wail work during September 2026?",
        "Show the absence dates instead.",
    ),
    "faris": (
        "How many days did Faris work during September 2026?",
        "How many hours did he work?",
        "Show the absence dates instead.",
        "Repeat that result.",
    ),
    "long": (
        "How many days did A11017 work during September 2026?",
        "Show the absence dates instead.",
        "Show dates that are not absent.",
        "Group worked hours by department having total hours over 10.",
        "Compare that with last month.",
        "Join attendance to payroll.",
        "Rank departments by worked hours.",
        "Show a running total over dates.",
    ),
}


def run_scenario(name: str) -> dict:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}")
    state = ConversationState(session_id=f"acceptance-{name}")
    history: list[dict[str, str]] = []
    turns = []
    context = (
        patch(
            "week5.new_implementation.online.pipeline.audit_with_one_repair",
            side_effect=lambda initial, **_kwargs: initial,
        )
        if name == "wail"
        else nullcontext()
    )
    with context:
        for question in SCENARIOS[name]:
            outcome = run_turn(
                TurnRequest(
                    question=question,
                    history=tuple(history),
                    state=state,
                    access_context=LOCAL_DEMO_ACCESS,
                )
            )
            state = outcome.state
            turns.append(
                {
                    "question": question,
                    "outcome": outcome.kind,
                    "reply": outcome.reply,
                    "capability": getattr(outcome, "capability", None),
                    "verified_turns": len(state.verified_turns),
                }
            )
            history.extend(({"role": "user", "content": question}, {"role": "assistant", "content": outcome.reply}))
    expected_nonexecution = {"compare", "join", "ranking", "window"}
    unsupported = {item["capability"] for item in turns if item["outcome"] == "unsupported"}
    passed = any(item["outcome"] in {"answered", "clarification"} for item in turns)
    if name == "long":
        passed = passed and expected_nonexecution <= unsupported
    return {"scenario": name, "passed": passed, "turns": turns}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenarios", nargs="*", choices=tuple(SCENARIOS), default=list(SCENARIOS))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = {"status": "running", "completed": 0, "scenarios": []}
    if args.output:
        _write(args.output, report)
    for name in args.scenarios:
        report["scenarios"].append(run_scenario(name))
        report["completed"] += 1
        if args.output:
            _write(args.output, report)
    report["status"] = "complete"
    report["passed"] = all(item["passed"] for item in report["scenarios"])
    if args.output:
        _write(args.output, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
