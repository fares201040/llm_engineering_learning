"""Execute exactly one checkpointed acceptance turn in the prepared Colab VM."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


RUNTIME_CONFIG = Path("/content/.attendance_phase3_runtime.json")
CHECKPOINT = Path(
    os.environ.get("ACCEPTANCE_CHECKPOINT", "/content/attendance-phase3-long.json")
)


def main() -> int:
    if not RUNTIME_CONFIG.is_file():
        raise RuntimeError("Run prepare_synthetic_runtime.py before a live turn.")
    os.environ.update(json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8")))
    source_root = Path(os.environ["ATTENDANCE_PHASE2_SOURCE_ROOT"])
    if not source_root.joinpath("week5/new_evaluation/acceptance.py").is_file():
        raise FileNotFoundError("The sanitized attendance source is not extracted.")
    scenario = os.environ.get("ACCEPTANCE_SCENARIO", "long")
    turn = os.environ.get("ACCEPTANCE_TURN")
    if turn is None:
        raise ValueError("Set ACCEPTANCE_TURN for one 1-based turn at a time.")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "week5.new_evaluation.acceptance",
            scenario,
            *(["--ui"] if os.environ.get("ACCEPTANCE_UI") == "1" else []),
            "--turn",
            turn,
            "--output",
            str(CHECKPOINT),
        ],
        cwd=source_root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    print(completed.stdout)
    if completed.returncode:
        for line in completed.stderr.splitlines():
            if "attendance_layer layer=" in line:
                print(line)
    return completed.returncode


if __name__ == "__main__":
    if main():
        raise RuntimeError("Acceptance turn failed; inspect its synthetic checkpoint.")
