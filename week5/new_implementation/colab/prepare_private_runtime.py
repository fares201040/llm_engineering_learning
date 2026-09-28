"""Import the validated private payload into an ephemeral Colab PostgreSQL DB."""

from __future__ import annotations

import base64
from importlib import import_module
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from zipfile import ZipFile

SOURCE_ROOT = Path(
    os.environ.get("ATTENDANCE_PHASE2_SOURCE_ROOT", "/content/attendance_phase2_source")
)
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

_private_payload = import_module("week5.new_implementation.colab.private_payload")
EXPECTED_FACTS = _private_payload.EXPECTED_FACTS
validate_payload_archive = _private_payload.validate_payload_archive


PRIVATE_PAYLOAD = Path("/content/attendance_private_payload.zip")
PRIVATE_DIRECTORY = Path("/content/attendance_private_payload")
RUNTIME_CONFIG = Path("/content/.attendance_private_runtime.json")
SYNTHETIC_RUNTIME_CONFIG = Path("/content/.attendance_phase3_runtime.json")
OPENAI_KEY_FILE = Path("/content/.attendance_openai_api_key")

TABLE_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE public.attendance_records (
    record_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    name TEXT NOT NULL,
    organization_unit TEXT,
    country TEXT,
    work_location TEXT,
    department TEXT,
    position TEXT,
    job TEXT,
    gradeset TEXT,
    grade TEXT,
    attendance_date DATE NOT NULL,
    day TEXT,
    day_type TEXT,
    holiday_type TEXT,
    shift TEXT,
    status TEXT,
    exception TEXT,
    total_worked_hrs DOUBLE PRECISION,
    lateness_hrs DOUBLE PRECISION,
    early_out_hrs DOUBLE PRECISION,
    overbreak_hrs DOUBLE PRECISION,
    regular_units DOUBLE PRECISION,
    pre_ot_hrs DOUBLE PRECISION,
    post_ot_hrs DOUBLE PRECISION,
    total_ot DOUBLE PRECISION,
    ot_authorized DOUBLE PRECISION,
    ot_not_authorized DOUBLE PRECISION,
    leave_type TEXT,
    leave_hrs DOUBLE PRECISION,
    last_updated_at TIMESTAMP,
    source_file TEXT,
    source_sheet TEXT,
    source_excel_row INTEGER,
    source_jsonl_line INTEGER,
    search_text TEXT NOT NULL,
    record_json JSONB NOT NULL,
    raw_row_key TEXT,
    synced_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_attendance_employee_id ON public.attendance_records(employee_id);
CREATE INDEX idx_attendance_date ON public.attendance_records(attendance_date);
CREATE INDEX idx_attendance_department ON public.attendance_records(department);
CREATE INDEX idx_attendance_exception ON public.attendance_records(exception);
CREATE INDEX idx_attendance_shift ON public.attendance_records(shift);
COMMENT ON COLUMN public.attendance_records.work_location IS
    'Work-location group within the employee department. A department can contain multiple work-location groups; this field is distinct from department.';
"""


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


def _psql(database: str, sql: str) -> subprocess.CompletedProcess:
    return run(
        [
            "runuser",
            "-u",
            "postgres",
            "--",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-A",
            "-t",
            "-F",
            "|",
            "-d",
            database,
        ],
        input_text=sql,
    )


def private_database_sql(*, database: str, role: str, password: str) -> str:
    """Return the fixed least-privilege role policy for the private database."""
    return f"""
CREATE ROLE {role} LOGIN PASSWORD '{password}';
REVOKE ALL ON DATABASE {database} FROM {role};
REVOKE TEMPORARY ON DATABASE {database} FROM PUBLIC;
GRANT CONNECT ON DATABASE {database} TO {role};
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM {role};
GRANT USAGE ON SCHEMA public TO {role};
REVOKE CREATE ON SCHEMA public FROM {role};
GRANT SELECT ON TABLE public.attendance_records TO {role};
ALTER ROLE {role} IN DATABASE {database} SET default_transaction_read_only = on;
"""


def _drop_private_database(database: str, role: str) -> None:
    _psql(
        "postgres",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{database}' AND pid <> pg_backend_pid();\n"
        f"DROP DATABASE IF EXISTS {database};\n"
        f"DROP ROLE IF EXISTS {role};\n",
    )


def validate_import_facts(output: str) -> None:
    facts = output.strip().split("|")
    expected = [
        str(EXPECTED_FACTS["record_count"]),
        str(EXPECTED_FACTS["employee_count"]),
        str(EXPECTED_FACTS["date_min"]),
        str(EXPECTED_FACTS["date_max"]),
    ]
    if facts != expected:
        raise RuntimeError("Imported private attendance facts do not match manifest")


def create_private_database(attendance_bytes: bytes) -> dict[str, str]:
    """Create, populate, verify, and lock down a fresh ephemeral database."""
    suffix = secrets.token_hex(4)
    database = f"attendance_private_{suffix}"
    role = f"attendance_private_reader_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    run(["runuser", "-u", "postgres", "--", "createdb", database])
    try:
        _psql(database, TABLE_SQL)
        rows = [json.loads(line) for line in attendance_bytes.splitlines()]
        for offset in range(0, len(rows), 250):
            encoded = base64.b64encode(
                json.dumps(rows[offset : offset + 250], separators=(",", ":")).encode(
                    "utf-8"
                )
            ).decode("ascii")
            _psql(
                database,
                "INSERT INTO public.attendance_records "
                "SELECT * FROM jsonb_populate_recordset("
                "NULL::public.attendance_records, "
                f"convert_from(decode('{encoded}', 'base64'), 'UTF8')::jsonb);",
            )
        verification = _psql(
            database,
            "SELECT COUNT(*), COUNT(DISTINCT employee_id), "
            "MIN(attendance_date), MAX(attendance_date) "
            "FROM public.attendance_records;",
        ).stdout
        validate_import_facts(verification)
        _psql(
            database,
            private_database_sql(database=database, role=role, password=password),
        )
    except Exception:
        _drop_private_database(database, role)
        raise
    return {
        "database": database,
        "role": role,
        "dsn": f"postgresql://{role}:{password}@127.0.0.1:5432/{database}",
    }


def write_runtime_config(path: Path, settings: dict[str, str]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(settings, indent=2))
    path.chmod(0o600)


def _model_settings() -> dict[str, str]:
    defaults = {
        "LLM_REFERENCE_MODEL": "ollama_chat/qwen3.5:2b",
        "LLM_REFERENCE_TIMEOUT_SECONDS": "180",
        "LLM_PLANNER_MODEL": "openai/gpt-5-nano",
        "LLM_PLANNER_TIMEOUT_SECONDS": "180",
        "LLM_PLANNER_MAX_OUTPUT_TOKENS": "6000",
        "OLLAMA_API_BASE": "http://127.0.0.1:11434",
        "OLLAMA_HOST": "127.0.0.1:11434",
    }
    if SYNTHETIC_RUNTIME_CONFIG.is_file():
        prior = json.loads(SYNTHETIC_RUNTIME_CONFIG.read_text(encoding="utf-8"))
        if isinstance(prior.get("OPENAI_API_KEY"), str):
            defaults["OPENAI_API_KEY"] = prior["OPENAI_API_KEY"]
        for key in ("OLLAMA_API_BASE", "OLLAMA_HOST"):
            if isinstance(prior.get(key), str):
                defaults[key] = prior[key]
        for stage in ("REFERENCE", "PLANNER", "ANSWER"):
            key = f"LLM_{stage}_TIMEOUT_SECONDS"
            if isinstance(prior.get(key), str):
                defaults[key] = prior[key]
    if OPENAI_KEY_FILE.is_file():
        defaults["OPENAI_API_KEY"] = OPENAI_KEY_FILE.read_text(encoding="utf-8").strip()
        OPENAI_KEY_FILE.unlink()
    return defaults


def main() -> None:
    expected_sha256 = os.environ.get("ATTENDANCE_PRIVATE_PAYLOAD_SHA256", "")
    if len(expected_sha256) != 64:
        raise ValueError("ATTENDANCE_PRIVATE_PAYLOAD_SHA256 must be provided")
    validate_payload_archive(PRIVATE_PAYLOAD, expected_sha256=expected_sha256)
    if not SOURCE_ROOT.joinpath("week5/new_evaluation/eval.py").is_file():
        raise FileNotFoundError("The sanitized source snapshot is not prepared")
    if RUNTIME_CONFIG.exists():
        raise RuntimeError("Private runtime config already exists; clean it up first")
    model_settings = _model_settings()
    if not model_settings.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "The private runtime requires OPENAI_API_KEY for the GPT SQL planner"
        )

    run(["service", "postgresql", "start"])
    with ZipFile(PRIVATE_PAYLOAD) as archive:
        attendance_bytes = archive.read("attendance_records.jsonl")
        case_bytes = archive.read("tests.jsonl")
    if PRIVATE_DIRECTORY.exists():
        shutil.rmtree(PRIVATE_DIRECTORY)
    PRIVATE_DIRECTORY.mkdir(mode=0o700)
    attendance_path = PRIVATE_DIRECTORY / "attendance_records.jsonl"
    case_path = PRIVATE_DIRECTORY / "tests.jsonl"
    attendance_path.write_bytes(attendance_bytes)
    case_path.write_bytes(case_bytes)
    attendance_path.chmod(0o600)
    case_path.chmod(0o600)

    database = create_private_database(attendance_bytes)
    settings = {
        **model_settings,
        "ATTENDANCE_PHASE2_SOURCE_ROOT": str(SOURCE_ROOT),
        "ATTENDANCE_PRIVATE_CASE_FILE": str(case_path),
        "ATTENDANCE_PRIVATE_DATABASE": database["database"],
        "ATTENDANCE_PRIVATE_ROLE": database["role"],
        "POSTGRES_READONLY_DSN": database["dsn"],
        "POSTGRES_ATTENDANCE_TABLE": "attendance_records",
    }
    try:
        write_runtime_config(RUNTIME_CONFIG, settings)
    except Exception:
        _drop_private_database(database["database"], database["role"])
        raise
    print(
        "Private Colab runtime ready: payload hash and manifest validated; "
        "3964 rows, 568 employees, 311 cases."
    )
    print("The generated read-only DSN remains only in the mode-0600 runtime config.")


if __name__ == "__main__":
    main()
