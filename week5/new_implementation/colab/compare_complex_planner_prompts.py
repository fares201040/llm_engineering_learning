"""Compare planner prompts on the same multi-turn acceptance scenario in Colab."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch


RUNTIME_CONFIG = Path("/content/.attendance_phase3_runtime.json")
PRIVATE_RUNTIME_CONFIG = Path("/content/.attendance_private_runtime.json")


def _settings() -> dict[str, str]:
    if not RUNTIME_CONFIG.is_file():
        from week5.new_implementation.colab.prepare_synthetic_runtime import (
            create_synthetic_database,
        )

        private = json.loads(PRIVATE_RUNTIME_CONFIG.read_text(encoding="utf-8"))
        settings = {
            key: value
            for key, value in private.items()
            if not key.startswith("ATTENDANCE_PRIVATE_")
        }
        settings["POSTGRES_READONLY_DSN"] = create_synthetic_database()
        RUNTIME_CONFIG.write_text(json.dumps(settings), encoding="utf-8")
        RUNTIME_CONFIG.chmod(0o600)
    return json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))


def main() -> int:
    bootstrap = RUNTIME_CONFIG if RUNTIME_CONFIG.is_file() else PRIVATE_RUNTIME_CONFIG
    bootstrap_settings = json.loads(bootstrap.read_text(encoding="utf-8"))
    sys.path.insert(0, bootstrap_settings["ATTENDANCE_PHASE2_SOURCE_ROOT"])
    settings = _settings()
    if not settings.get("OPENAI_API_KEY"):
        raise RuntimeError("GPT planner credentials are unavailable")
    source = Path(settings["ATTENDANCE_PHASE2_SOURCE_ROOT"])
    os.environ.update(settings)
    sys.path.insert(0, str(source))

    from week5.new_evaluation.acceptance import SCENARIOS, run_scenario_turn
    from week5.new_implementation.colab.compare_planner_prompts import LEAN_SQL_SYSTEM
    from week5.new_implementation.online import pipeline, planner

    requested = os.environ.get("PROMPT_VARIANT", "").casefold()
    if requested not in {"guided", "lean"}:
        raise ValueError("PROMPT_VARIANT must be guided or lean")
    report_path = Path(f"/content/attendance-prompt-{requested}-complex.json")
    report = {"variant": requested, "scenario": "long", "runs": []}
    original_prompt = planner._SYSTEM
    planner._SYSTEM = original_prompt if requested == "guided" else LEAN_SQL_SYSTEM
    original_log = pipeline.log_layer_output
    trace: list[dict[str, object]] = []

    def observe(layer, output, *, attempt=None):
        if layer in {"sql_planner", "sql_execution_failure"}:
            trace.append({"layer": layer, "attempt": attempt, "output": output})
        original_log(layer, output, attempt=attempt)

    state = None
    history = ()
    try:
        with patch.object(pipeline, "log_layer_output", side_effect=observe):
            for index, step in enumerate(SCENARIOS["long"]):
                trace = []
                started = perf_counter()
                try:
                    record, state, history = run_scenario_turn(
                        "long", index, state=state, history=history
                    )
                    entry = {
                        "turn": index + 1,
                        "question": step.question,
                        "passed": record["passed"],
                        "outcome": record["outcome"],
                        "failure_code": record["failure_code"],
                        "reply": record["reply"],
                        "result": record["result"],
                        "missing_answer_facts": record["missing_answer_facts"],
                        "missing_result_facts": record["missing_result_facts"],
                        "missing_sql_facts": record["missing_sql_facts"],
                        "state_continuity_ok": record["state_continuity_ok"],
                    }
                except Exception as exc:
                    entry = {
                        "turn": index + 1,
                        "question": step.question,
                        "passed": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:1000],
                    }
                entry["trace"] = trace
                entry["duration_seconds"] = round(perf_counter() - started, 2)
                report["runs"].append(entry)
                report_path.write_text(
                    json.dumps(report, indent=2, default=str), encoding="utf-8"
                )
                print(
                    f"{requested} complex turn {index + 1}: "
                    f"{'PASS' if entry['passed'] else 'FAIL'} "
                    f"({entry['duration_seconds']}s)",
                    flush=True,
                )
    finally:
        planner._SYSTEM = original_prompt
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
