"""Exercise identity clarification and an attribute follow-up in the private Colab runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


RUNTIME_CONFIG = Path("/content/.attendance_private_runtime.json")
REPORT = Path("/content/attendance-follow-up-repro.json")
QUESTIONS = (
    "who is mukhtar ahmd",
    "1",
    "what is his department and his position",
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
    turns = []
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
                "sql": latest.executed_sql
                if outcome.kind == "answered" and latest
                else None,
            }
        )
        history += (
            {"role": "user", "content": question},
            {"role": "assistant", "content": outcome.reply},
        )
    REPORT.write_text(json.dumps(turns, indent=2), encoding="utf-8")
    for turn in turns:
        print(f"{turn['question']}: {turn['kind']} — {turn['reply']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
