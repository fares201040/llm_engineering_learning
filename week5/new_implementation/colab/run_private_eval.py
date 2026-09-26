"""Run one bounded, resumable private-evaluator batch in Colab."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


RUNTIME_CONFIG = Path("/content/.attendance_private_runtime.json")
PRIVATE_REPORT = Path("/content/attendance-private-eval.json")


def consume_restart_flag(environment: dict[str, str]) -> bool:
    """Read and remove the one-shot flag from a persistent Colab kernel."""

    return environment.pop("PRIVATE_EVAL_RESTART", "").casefold() in {
        "1",
        "true",
        "yes",
    }


def prepare_report(report: Path, *, restart: bool) -> bool:
    """Reset an obsolete checkpoint only when the caller explicitly requests it."""

    if restart:
        report.unlink(missing_ok=True)
    return report.is_file()


def evaluator_command(
    *,
    source: Path,
    cases: Path,
    report: Path,
    batch_size: int,
    resume: bool,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "week5.new_evaluation.eval",
        "--all",
        "--test-file",
        cases.as_posix(),
        "--output",
        report.as_posix(),
        "--batch-size",
        str(batch_size),
    ]
    if resume:
        command.append("--resume")
    return command


def main() -> int:
    settings = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
    source = Path(settings["ATTENDANCE_PHASE2_SOURCE_ROOT"])
    cases = Path(settings["ATTENDANCE_PRIVATE_CASE_FILE"])
    batch_size = int(os.environ.get("PRIVATE_EVAL_BATCH_SIZE", "10"))
    if not 1 <= batch_size <= 50:
        raise ValueError("PRIVATE_EVAL_BATCH_SIZE must be between 1 and 50")
    environment = os.environ.copy()
    environment.update(settings)
    restart = consume_restart_flag(os.environ)
    resume = prepare_report(PRIVATE_REPORT, restart=restart)
    completed = subprocess.run(
        evaluator_command(
            source=source,
            cases=cases,
            report=PRIVATE_REPORT,
            batch_size=batch_size,
            resume=resume,
        ),
        cwd=source,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
