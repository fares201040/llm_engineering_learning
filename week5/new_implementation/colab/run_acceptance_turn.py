"""Execute exactly one checkpointed acceptance turn in the prepared Colab VM."""

from __future__ import annotations

import json
import os
from pathlib import Path
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
    sys.path.insert(0, str(source_root))
    os.chdir(source_root)

    scenario = os.environ.get("ACCEPTANCE_SCENARIO", "long")
    turn = os.environ.get("ACCEPTANCE_TURN")
    if turn is None:
        raise ValueError("Set ACCEPTANCE_TURN for one 1-based turn at a time.")

    from week5.new_evaluation.acceptance import main as acceptance_main

    return acceptance_main([scenario, "--turn", turn, "--output", str(CHECKPOINT)])


if __name__ == "__main__":
    raise SystemExit(main())
