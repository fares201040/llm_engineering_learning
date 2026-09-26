"""Run the direct-SQL evaluator against synthetic Colab cases only."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


RUNTIME_CONFIG = Path("/content/.attendance_phase3_runtime.json")
REPORT = Path("/content/attendance-synthetic-eval.json")


def main() -> int:
    settings = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
    source = Path(settings["ATTENDANCE_PHASE2_SOURCE_ROOT"])
    cases = source / "week5/new_implementation/colab/synthetic_evaluation_cases.jsonl"
    if not cases.is_file():
        raise FileNotFoundError("The sanitized synthetic evaluation cases are missing.")
    environment = os.environ.copy()
    environment.update(settings)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "week5.new_evaluation.eval",
            "--all",
            "--test-file",
            str(cases),
            "--output",
            str(REPORT),
        ],
        cwd=source,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    print(completed.stdout)
    if completed.returncode and not completed.stdout:
        print("Synthetic evaluator exited before producing its report.")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
