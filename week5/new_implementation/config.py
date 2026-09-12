"""Shared, environment-backed configuration for the attendance application.

The module is deliberately dependency-light so it can be imported both when
the implementation is used as a package and when ``ingest.py`` is executed as
an individual script.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import re

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
load_dotenv(
    dotenv_path=Path(__file__).with_name(".env.postgres"),
    override=False,
)


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or not value.strip() else value.strip()


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer; got {raw!r}.") from exc

    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}; got {value}.")
    return value


def env_float(
    name: str,
    default: float,
    minimum: float | None = None,
) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be a number; got {raw!r}.") from exc

    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}; got {value}.")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} must be one of true/false, yes/no, or 1/0; got {raw!r}.")


def env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw.strip()) if raw and raw.strip() else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def env_identifier(name: str, default: str) -> str:
    """Read a plain or schema-qualified SQL identifier safely."""
    value = env_str(name, default)
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    if not re.fullmatch(rf"{identifier}(?:\.{identifier})?", value):
        raise ValueError(
            f"{name} must be a plain or schema-qualified SQL identifier; got {value!r}."
        )
    return value


@dataclass(frozen=True)
class Settings:
    rag_model: str
    embedding_model: str
    embedding_encoding: str
    embedding_max_tokens: int
    embedding_batch_max_tokens: int
    embedding_batch_max_items: int
    chroma_batch_size: int
    chroma_db_path: Path
    chroma_collection_name: str
    chroma_anonymized_telemetry: bool
    knowledge_base_path: Path
    jsonl_output_path: Path
    invalid_jsonl_path: Path
    ingestion_state_path: Path
    index_schema_version: int
    ingestion_format_version: int
    source_xlsx_glob: str
    source_csv_glob: str
    enable_postgres: bool
    postgres_dsn: str
    postgres_attendance_table: str
    postgres_chunks_table: str
    enable_pgvector: bool
    pgvector_dimensions: int
    enable_employee_period_chunks: bool
    allow_empty_snapshot: bool
    allow_invalid_snapshot: bool
    allow_attendance_source_removal: bool
    app_timezone: str
    semantic_k: int
    final_k: int
    max_exact_results: int
    max_exact_context_records: int
    rerank_preview_chars: int
    final_record_max_chars: int
    fuzzy_name_threshold: float
    auto_match_threshold: float
    constraint_candidate_limit: int
    evidence_sample_size: int
    max_groups: int
    planner_timeout_seconds: float
    final_answer_timeout_seconds: float
    redact_pii: bool
    log_format: str
    benchmark_warmups: int
    benchmark_runs: int
    log_level: str

    @classmethod
    def from_environment(cls) -> "Settings":
        knowledge_base_path = env_path(
            "KNOWLEDGE_BASE_PATH",
            PROJECT_ROOT / "week5" / "new-knowledge-base",
        )
        return cls(
            rag_model=env_str("RAG_MODEL", "openai/gpt-4.1-nano"),
            embedding_model=env_str(
                "EMBEDDING_MODEL",
                "text-embedding-3-large",
            ),
            embedding_encoding=env_str(
                "EMBEDDING_ENCODING",
                "cl100k_base",
            ),
            embedding_max_tokens=env_int(
                "EMBEDDING_MAX_TOKENS",
                7500,
                minimum=1,
            ),
            embedding_batch_max_tokens=env_int(
                "EMBEDDING_BATCH_MAX_TOKENS",
                250000,
                minimum=1,
            ),
            embedding_batch_max_items=env_int(
                "EMBEDDING_BATCH_MAX_ITEMS",
                256,
                minimum=1,
            ),
            chroma_batch_size=env_int(
                "CHROMA_BATCH_SIZE",
                500,
                minimum=1,
            ),
            chroma_db_path=env_path(
                "CHROMA_DB_PATH",
                PROJECT_ROOT / "week5" / "new_preprocessed_db",
            ),
            chroma_collection_name=env_str(
                "CHROMA_COLLECTION_NAME",
                "docs",
            ),
            chroma_anonymized_telemetry=env_bool(
                "CHROMA_ANONYMIZED_TELEMETRY",
                False,
            ),
            knowledge_base_path=knowledge_base_path,
            jsonl_output_path=env_path(
                "JSONL_OUTPUT_PATH",
                knowledge_base_path / "attendance" / "attendance.jsonl",
            ),
            invalid_jsonl_path=env_path(
                "INVALID_JSONL_PATH",
                knowledge_base_path / "attendance" / "attendance.invalid.jsonl",
            ),
            ingestion_state_path=env_path(
                "INGESTION_STATE_PATH",
                knowledge_base_path / "attendance" / "ingestion_state.sqlite3",
            ),
            index_schema_version=env_int("INDEX_SCHEMA_VERSION", 2, minimum=1),
            ingestion_format_version=env_int(
                "INGESTION_FORMAT_VERSION",
                3,
                minimum=1,
            ),
            source_xlsx_glob=env_str("SOURCE_XLSX_GLOB", "*.xlsx"),
            source_csv_glob=env_str("SOURCE_CSV_GLOB", "*.csv"),
            enable_postgres=env_bool("ENABLE_POSTGRES", False),
            postgres_dsn=env_str("POSTGRES_DSN", ""),
            postgres_attendance_table=env_identifier(
                "POSTGRES_ATTENDANCE_TABLE", "attendance_records"
            ),
            postgres_chunks_table=env_identifier(
                "POSTGRES_CHUNKS_TABLE", "knowledge_chunks"
            ),
            enable_pgvector=env_bool("ENABLE_PGVECTOR", False),
            pgvector_dimensions=env_int(
                "PGVECTOR_DIMENSIONS",
                3072,
                minimum=1,
            ),
            enable_employee_period_chunks=env_bool(
                "ENABLE_EMPLOYEE_PERIOD_CHUNKS",
                True,
            ),
            allow_empty_snapshot=env_bool("ALLOW_EMPTY_SNAPSHOT", False),
            allow_invalid_snapshot=env_bool("ALLOW_INVALID_SNAPSHOT", False),
            allow_attendance_source_removal=env_bool(
                "ALLOW_ATTENDANCE_SOURCE_REMOVAL",
                False,
            ),
            app_timezone=env_str("APP_TIMEZONE", "Asia/Aden"),
            semantic_k=env_int("RETRIEVAL_K", 12, minimum=1),
            final_k=env_int("FINAL_K", 6, minimum=1),
            max_exact_results=env_int(
                "MAX_EXACT_RESULTS",
                500,
                minimum=1,
            ),
            max_exact_context_records=env_int(
                "MAX_EXACT_CONTEXT_RECORDS",
                100,
                minimum=1,
            ),
            rerank_preview_chars=env_int(
                "RERANK_PREVIEW_CHARS",
                2500,
                minimum=1,
            ),
            final_record_max_chars=env_int(
                "FINAL_RECORD_MAX_CHARS",
                10000,
                minimum=1,
            ),
            fuzzy_name_threshold=env_float(
                "FUZZY_NAME_THRESHOLD",
                0.62,
                minimum=0.0,
            ),
            auto_match_threshold=env_float("AUTO_MATCH_THRESHOLD", 0.9, minimum=0.0),
            constraint_candidate_limit=env_int(
                "CONSTRAINT_CANDIDATE_LIMIT", 10, minimum=1
            ),
            evidence_sample_size=env_int("EVIDENCE_SAMPLE_SIZE", 20, minimum=1),
            max_groups=env_int("MAX_GROUPS", 50, minimum=1),
            planner_timeout_seconds=env_float(
                "PLANNER_TIMEOUT_SECONDS", 30.0, minimum=0.1
            ),
            final_answer_timeout_seconds=env_float(
                "FINAL_ANSWER_TIMEOUT_SECONDS", 60.0, minimum=0.1
            ),
            redact_pii=env_bool("REDACT_PII", True),
            log_format=env_str("LOG_FORMAT", "text").lower(),
            benchmark_warmups=env_int("BENCHMARK_WARMUPS", 2, minimum=0),
            benchmark_runs=env_int("BENCHMARK_RUNS", 10, minimum=1),
            log_level=env_str("LOG_LEVEL", "INFO").upper(),
        )


settings = Settings.from_environment()
