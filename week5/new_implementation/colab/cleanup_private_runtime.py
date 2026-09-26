"""Delete private evaluation artifacts and database state from the Colab VM."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess


PRIVATE_PAYLOAD = Path("/content/attendance_private_payload.zip")
PRIVATE_DIRECTORY = Path("/content/attendance_private_payload")
PRIVATE_REPORT = Path("/content/attendance-private-eval.json")
RUNTIME_CONFIG = Path("/content/.attendance_private_runtime.json")


def run(command: list[str], *, input_text: str | None = None):
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        check=True,
        capture_output=True,
    )


def _drop_database_and_role(database: str, role: str) -> None:
    if not re.fullmatch(r"attendance_private_[0-9a-f]{8}", database):
        raise ValueError("Refusing to drop an unexpected database name")
    if not re.fullmatch(r"attendance_private_reader_[0-9a-f]{8}", role):
        raise ValueError("Refusing to drop an unexpected role name")
    sql = (
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{database}' AND pid <> pg_backend_pid();\n"
        f"DROP DATABASE IF EXISTS {database};\n"
        f"DROP ROLE IF EXISTS {role};\n"
    )
    run(
        [
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            "postgres",
        ],
        input_text=sql,
    )


def main() -> None:
    if RUNTIME_CONFIG.is_file():
        settings = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
        _drop_database_and_role(
            settings["ATTENDANCE_PRIVATE_DATABASE"],
            settings["ATTENDANCE_PRIVATE_ROLE"],
        )
    if PRIVATE_DIRECTORY.exists():
        shutil.rmtree(PRIVATE_DIRECTORY)
    for path in (PRIVATE_PAYLOAD, PRIVATE_REPORT, RUNTIME_CONFIG):
        path.unlink(missing_ok=True)
    print("Private Colab evaluation database, config, payload, and report removed.")


if __name__ == "__main__":
    main()
