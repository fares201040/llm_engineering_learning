"""Create a synthetic PostgreSQL and Qwen runtime inside a Colab VM."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


SOURCE_ROOT = Path("/content/attendance_phase2_source")
RUNTIME_CONFIG = Path("/content/.attendance_phase3_runtime.json")
OPENAI_KEY_FILE = Path("/content/.attendance_openai_api_key")
OLLAMA_LOG = Path("/content/ollama-phase3.log")


def model_stage_timeout_seconds() -> str:
    """Allow slower CPU inference without relaxing GPU session timeouts."""
    if shutil.which("nvidia-smi") is not None:
        probe = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, check=False
        )
        if probe.returncode == 0 and probe.stdout.strip():
            return "240"
    return "900"


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


def install_ollama() -> None:
    if shutil.which("ollama") is None:
        installer = run(["curl", "-fsSL", "https://ollama.com/install.sh"]).stdout
        try:
            run(["bash"], input_text=installer)
        except subprocess.CalledProcessError as exc:
            detail = "\n".join(item for item in (exc.stdout, exc.stderr) if item)
            detail = detail.strip()[-4000:] or "no installer output"
            raise RuntimeError(f"Official Ollama installer failed: {detail}") from exc
    server_env = os.environ.copy()
    server_env["OLLAMA_HOST"] = "127.0.0.1:11434"
    server_env["OLLAMA_NUM_PARALLEL"] = "1"
    log_handle = OLLAMA_LOG.open("ab")
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=2):
            server = None
    except (OSError, URLError):
        try:
            server = subprocess.Popen(
                ["ollama", "serve"],
                env=server_env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            log_handle.close()
            raise

    for _ in range(60):
        if server is not None and server.poll() is not None:
            log_handle.close()
            raise RuntimeError(
                "Ollama server exited during startup; inspect its Colab log."
            )
        try:
            with urlopen("http://127.0.0.1:11434/api/tags", timeout=2):
                break
        except (OSError, URLError):
            time.sleep(1)
    else:
        raise RuntimeError("Ollama server did not become ready within 60 seconds.")

    run(["ollama", "pull", "qwen3.5:2b"])
    smoke_payload = json.dumps(
        {"model": "qwen3.5:2b", "prompt": "Reply with the word ready.", "stream": False}
    ).encode("utf-8")
    request = Request(
        "http://127.0.0.1:11434/api/generate",
        data=smoke_payload,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=180) as response:
        if not json.loads(response.read()).get("response", "").strip():
            raise RuntimeError("Qwen returned an empty Colab smoke-test response.")
    log_handle.close()


def main() -> None:
    if not SOURCE_ROOT.joinpath("week5/new_evaluation/acceptance.py").is_file():
        raise FileNotFoundError(
            "Upload and extract attendance_phase2_source.zip before preparing the runtime."
        )
    if os.geteuid() != 0:
        raise RuntimeError("Colab setup needs its standard root notebook kernel.")

    run(["apt-get", "update", "-qq"])
    run(["apt-get", "install", "-y", "postgresql", "postgresql-client", "zstd"])
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

    install_ollama()
    if RUNTIME_CONFIG.is_file():
        settings = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
        dsn = settings["POSTGRES_READONLY_DSN"]
    else:
        dsn = create_synthetic_database()
        settings = {
            "ATTENDANCE_PHASE2_SOURCE_ROOT": str(SOURCE_ROOT),
            "POSTGRES_READONLY_DSN": dsn,
            "POSTGRES_ATTENDANCE_TABLE": "attendance_records",
            "LLM_REFERENCE_MODEL": "ollama_chat/qwen3.5:2b",
            "LLM_PLANNER_MODEL": "openai/gpt-5-nano",
            "LLM_PLANNER_MAX_OUTPUT_TOKENS": "6000",
            "OLLAMA_API_BASE": "http://127.0.0.1:11434",
            "OLLAMA_HOST": "127.0.0.1:11434",
        }
    settings["LLM_PLANNER_MODEL"] = "openai/gpt-5-nano"
    settings["LLM_PLANNER_MAX_OUTPUT_TOKENS"] = "6000"
    settings["LLM_REFERENCE_MODEL"] = "ollama_chat/qwen3.5:2b"
    if OPENAI_KEY_FILE.is_file():
        settings["OPENAI_API_KEY"] = OPENAI_KEY_FILE.read_text(encoding="utf-8").strip()
        OPENAI_KEY_FILE.unlink()
    timeout = model_stage_timeout_seconds()
    for stage in ("REFERENCE", "PLANNER"):
        settings[f"LLM_{stage}_TIMEOUT_SECONDS"] = timeout
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

    print(
        "Colab runtime ready: Qwen smoke test passed; synthetic DB has 16 rows and 3 employees."
    )
    print(
        "The generated read-only DSN is stored only in this Colab VM's private runtime config."
    )
    print(
        "Run exactly one acceptance turn, inspect the report, then decide whether to continue."
    )


if __name__ == "__main__":
    main()
