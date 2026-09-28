"""Trace the reported name search, adjusted swipe, and department comparison."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


RUNTIME_CONFIG = Path("/content/.attendance_private_runtime.json")
REPORT = Path("/content/attendance-raed-scope-repro.json")
QUESTIONS = (
    "who is raed ans",
    "search for close matches",
    "it is A10101",
    "GOOD, pls tell me if he has manual swipe during september 2026",
    "now give me overtime comparison between the departments during september 2026 and give your analyze",
)


def main() -> int:
    settings = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
    os.environ.update(settings)
    sys.path.insert(0, settings["ATTENDANCE_PHASE2_SOURCE_ROOT"])

    from week5.new_implementation.online.execution import LOCAL_DEMO_ACCESS
    from week5.new_implementation.online.pipeline import TurnRequest, run_turn
    from week5.new_implementation.online.state import ConversationState

    state = ConversationState()
    history: tuple[dict[str, str], ...] = ()
    turns: list[dict[str, object]] = []
    for question in QUESTIONS:
        outcome = run_turn(
            TurnRequest(
                question=question,
                history=history,
                state=state,
                access_context=LOCAL_DEMO_ACCESS,
            )
        )
        state = outcome.state
        latest = state.verified_turns[-1] if state.verified_turns else None
        turns.append(
            {
                "question": question,
                "kind": outcome.kind,
                "reply": outcome.reply,
                "failure_code": getattr(outcome, "code", None),
                "active_employee_ids": state.active_employee_ids,
                "sql": latest.executed_sql
                if outcome.kind == "answered" and latest
                else None,
                "result": latest.result
                if outcome.kind == "answered" and latest
                else None,
            }
        )
        REPORT.write_text(json.dumps(turns, indent=2, default=str), encoding="utf-8")
        history += (
            {"role": "user", "content": question},
            {"role": "assistant", "content": outcome.reply},
        )
        print(f"Raed reproduction turn {len(turns)}: {outcome.kind}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
