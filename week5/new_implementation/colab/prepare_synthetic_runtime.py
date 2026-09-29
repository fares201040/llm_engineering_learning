"""Create a synthetic PostgreSQL attendance runtime inside a Colab VM."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import time


SOURCE_ROOT = Path("/content/attendance_phase2_source")
RUNTIME_CONFIG = Path("/content/.attendance_phase3_runtime.json")
OPENAI_KEY_FILE = Path("/content/.attendance_openai_api_key")


def run(
    command: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        check=True,
        capture_output=True,
    )


def create_synthetic_database() -> str:
    role = f"phase3_reader_{secrets.token_hex(4)}"
    database = f"attendance_synthetic_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    bootstrap_sql = (
        f"CREATE ROLE {role} LOGIN PASSWORD '{password}';\n"
        f"CREATE DATABASE {database} OWNER postgres;\n"
    )
    run(
        ["runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1"],
        input_text=bootstrap_sql,
    )

    setup_sql = """
    CREATE TABLE public.attendance_records (
        record_id BIGSERIAL PRIMARY KEY,
        employee_id TEXT NOT NULL,
        name TEXT NOT NULL,
        department TEXT NOT NULL,
        attendance_date DATE NOT NULL,
        day_type TEXT NOT NULL,
        exception TEXT,
        total_worked_hrs NUMERIC NOT NULL
    );

    INSERT INTO public.attendance_records
        (employee_id, name, department, attendance_date, day_type,
         exception, total_worked_hrs)
    VALUES
        ('A11017', 'Synthetic Employee One', 'Engineering', '2026-08-03', 'Working Day', 'OK', 8),
        ('A11017', 'Synthetic Employee One', 'Engineering', '2026-08-04', 'Working Day', 'OK', 8),
        ('A11017', 'Synthetic Employee One', 'Engineering', '2026-09-01', 'Working Day', 'OK', 8),
        ('A11017', 'Synthetic Employee One', 'Engineering', '2026-09-02', 'Working Day', 'OK', 8),
        ('A11017', 'Synthetic Employee One', 'Engineering', '2026-09-03', 'Working Day', 'OK', 8),
        ('A11017', 'Synthetic Employee One', 'Engineering', '2026-09-04', 'Working Day', 'OK', 8),
        ('A11017', 'Synthetic Employee One', 'Engineering', '2026-09-05', 'Working Day', 'OK', 8),
        ('A11017', 'Synthetic Employee One', 'Engineering', '2026-09-06', 'Working Day', 'Absent', 0),
        ('A22022', 'Synthetic Employee Two', 'Finance', '2026-08-03', 'Working Day', 'OK', 6),
        ('A22022', 'Synthetic Employee Two', 'Finance', '2026-08-04', 'Working Day', 'OK', 6),
        ('A22022', 'Synthetic Employee Two', 'Finance', '2026-09-01', 'Working Day', 'OK', 6),
        ('A22022', 'Synthetic Employee Two', 'Finance', '2026-09-02', 'Working Day', 'OK', 6),
        ('A22022', 'Synthetic Employee Two', 'Finance', '2026-09-03', 'Working Day', 'OK', 6),
        ('A22022', 'Synthetic Employee Two', 'Finance', '2026-09-04', 'Working Day', 'OK', 6),
        ('A33033', 'Synthetic Employee Three', 'Operations', '2026-08-04', 'Working Day', 'OK', 8),
        ('A33033', 'Synthetic Employee Three', 'Operations', '2026-09-05', 'Working Day', 'OK', 8);

    GRANT CONNECT ON DATABASE {database} TO {role};
    GRANT USAGE ON SCHEMA public TO {role};
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role};
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
    ALTER ROLE {role} IN DATABASE {database}
        SET default_transaction_read_only = on;
    """.format(database=database, role=role)
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
            database,
        ],
        input_text=setup_sql,
    )
    return f"postgresql://{role}:{password}@127.0.0.1:5432/{database}"


def main() -> None:
    if not SOURCE_ROOT.joinpath("week5/new_evaluation/acceptance.py").is_file():
        raise FileNotFoundError(
            "Upload and extract attendance_phase2_source.zip before preparing the runtime."
        )
    if os.geteuid() != 0:
        raise RuntimeError("Colab setup needs its standard root notebook kernel.")

    run(["apt-get", "update", "-qq"])
    run(["apt-get", "install", "-y", "postgresql", "postgresql-client"])
    run(["service", "postgresql", "start"])
    for _ in range(30):
        probe = subprocess.run(
            ["pg_isready"], capture_output=True, text=True, check=False
        )
        if probe.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Synthetic PostgreSQL did not start in Colab.")

    if RUNTIME_CONFIG.is_file():
        settings = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
        dsn = settings["POSTGRES_READONLY_DSN"]
    else:
        dsn = create_synthetic_database()
        settings = {
            "ATTENDANCE_PHASE2_SOURCE_ROOT": str(SOURCE_ROOT),
            "POSTGRES_READONLY_DSN": dsn,
            "POSTGRES_ATTENDANCE_TABLE": "attendance_records",
            "LLM_REFERENCE_MODEL": "openai/gpt-4.1-mini",
            "LLM_PLANNER_MODEL": "openai/gpt-4.1-mini",
            "LLM_PLANNER_MAX_OUTPUT_TOKENS": "6000",
        }
    settings["LLM_PLANNER_MODEL"] = "openai/gpt-4.1-mini"
    settings["LLM_PLANNER_MAX_OUTPUT_TOKENS"] = "6000"
    settings["LLM_REFERENCE_MODEL"] = "openai/gpt-4.1-mini"
    if OPENAI_KEY_FILE.is_file():
        settings["OPENAI_API_KEY"] = OPENAI_KEY_FILE.read_text(encoding="utf-8").strip()
        OPENAI_KEY_FILE.unlink()
    for stage in ("REFERENCE", "PLANNER"):
        settings[f"LLM_{stage}_TIMEOUT_SECONDS"] = "180"
    settings.pop("OLLAMA_API_BASE", None)
    settings.pop("OLLAMA_HOST", None)
    RUNTIME_CONFIG.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    RUNTIME_CONFIG.chmod(0o600)
    os.environ.update(settings)
    import psycopg

    with psycopg.connect(dsn) as connection:
        row_count, employee_count = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT employee_id) "
            "FROM public.attendance_records"
        ).fetchone()
    if (row_count, employee_count) != (16, 3):
        raise RuntimeError(
            "Synthetic fixture row/employee count did not match its oracle."
        )

    print("Colab runtime ready: synthetic DB has 16 rows and 3 employees.")
    print(
        "The generated read-only DSN is stored only in this Colab VM's private runtime config."
    )
    print(
        "Run exactly one acceptance turn, inspect the report, then decide whether to continue."
    )


if __name__ == "__main__":
    main()
