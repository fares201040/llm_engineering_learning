"""Environment-backed settings shared by offline ingestion and online querying."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(Path(__file__).with_name(".env.postgres"), override=False)


def _text(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _integer(name: str, default: int, minimum: int = 0) -> int:
    raw = _text(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _number(name: str, default: float, minimum: float = 0.0) -> float:
    raw = _text(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    value = _text(name, str(default)).casefold()
    if value in {"true", "yes", "1", "on"}:
        return True
    if value in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _path(name: str, default: Path) -> Path:
    value = Path(_text(name, str(default)))
    return value if value.is_absolute() else PROJECT_ROOT / value


def _identifier(name: str, default: str) -> str:
    value = _text(name, default)
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    if not re.fullmatch(rf"{identifier}(?:\.{identifier})?", value):
        raise ValueError(f"{name} must be a plain or schema-qualified SQL identifier")
    return value


@dataclass(frozen=True)
class Settings:
    # Online runtime.
    postgres_readonly_dsn: str
    postgres_connect_timeout_seconds: int
    postgres_attendance_table: str
    postgres_chunks_table: str
    app_timezone: str
    max_exact_results: int
    final_k: int
    embedding_provider: str
    embedding_model: str
    llm_reference_model: str
    llm_reference_timeout_seconds: float
    llm_reference_max_output_tokens: int
    llm_planner_model: str
    llm_planner_timeout_seconds: float
    llm_planner_max_output_tokens: int
    postgres_statement_timeout_ms: int
    postgres_lock_timeout_ms: int
    postgres_idle_transaction_timeout_ms: int
    max_sql_result_bytes: int
    log_level: str

    # Offline ingestion. These retain their previous behavior and names.
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
    enable_pgvector: bool
    pgvector_dimensions: int
    enable_employee_period_chunks: bool
    allow_empty_snapshot: bool
    allow_invalid_snapshot: bool
    allow_attendance_source_removal: bool
    embedding_batch_max_tokens: int
    embedding_batch_max_items: int
    chroma_batch_size: int

    @property
    def llm_turn_provider_call_limit(self) -> int:
        return 8

    @classmethod
    def from_environment(cls) -> "Settings":
        knowledge = _path(
            "KNOWLEDGE_BASE_PATH", PROJECT_ROOT / "week5" / "new-knowledge-base"
        )
        global_model = os.getenv("LLM_MODEL")
        global_model = (
            global_model.strip() if global_model and global_model.strip() else None
        )
        reference_default_model = global_model or "openai/gpt-4.1-mini"
        planner_default_model = global_model or "openai/gpt-4.1-mini"
        postgres_dsn = _text("POSTGRES_DSN", "")
        embedding_provider = _text("EMBEDDING_PROVIDER", "huggingface").lower()
        if embedding_provider not in {"huggingface", "openai"}:
            raise ValueError("EMBEDDING_PROVIDER must be huggingface or openai")
        return cls(
            postgres_readonly_dsn=_text("POSTGRES_READONLY_DSN", postgres_dsn),
            postgres_connect_timeout_seconds=_integer(
                "POSTGRES_CONNECT_TIMEOUT_SECONDS", 5, 1
            ),
            postgres_attendance_table=_identifier(
                "POSTGRES_ATTENDANCE_TABLE", "attendance_records"
            ),
            postgres_chunks_table=_identifier(
                "POSTGRES_CHUNKS_TABLE", "knowledge_chunks"
            ),
            app_timezone=_text("APP_TIMEZONE", "Asia/Aden"),
            max_exact_results=_integer("MAX_EXACT_RESULTS", 100, 1),
            final_k=_integer("FINAL_K", 6, 1),
            embedding_provider=embedding_provider,
            embedding_model=_text(
                "EMBEDDING_MODEL",
                "text-embedding-3-large"
                if embedding_provider == "openai"
                else "all-MiniLM-L6-v2",
            ),
            llm_reference_model=_text("LLM_REFERENCE_MODEL", reference_default_model),
            llm_reference_timeout_seconds=_number(
                "LLM_REFERENCE_TIMEOUT_SECONDS", 30.0, 0.1
            ),
            llm_reference_max_output_tokens=_integer(
                "LLM_REFERENCE_MAX_OUTPUT_TOKENS", 3000, 128
            ),
            llm_planner_model=_text("LLM_PLANNER_MODEL", planner_default_model),
            llm_planner_timeout_seconds=_number(
                "LLM_PLANNER_TIMEOUT_SECONDS", 30.0, 0.1
            ),
            llm_planner_max_output_tokens=_integer(
                "LLM_PLANNER_MAX_OUTPUT_TOKENS", 6000, 256
            ),
            postgres_statement_timeout_ms=_integer(
                "POSTGRES_STATEMENT_TIMEOUT_MS", 30000, 1
            ),
            postgres_lock_timeout_ms=_integer("POSTGRES_LOCK_TIMEOUT_MS", 3000, 1),
            postgres_idle_transaction_timeout_ms=_integer(
                "POSTGRES_IDLE_TRANSACTION_TIMEOUT_MS", 30000, 1
            ),
            max_sql_result_bytes=_integer("MAX_SQL_RESULT_BYTES", 1000000, 1024),
            log_level=_text("LOG_LEVEL", "INFO").upper(),
            chroma_db_path=_path(
                "CHROMA_DB_PATH", PROJECT_ROOT / "week5" / "new_preprocessed_db"
            ),
            chroma_collection_name=_text("CHROMA_COLLECTION_NAME", "docs"),
            chroma_anonymized_telemetry=_boolean("CHROMA_ANONYMIZED_TELEMETRY", False),
            knowledge_base_path=knowledge,
            jsonl_output_path=_path(
                "JSONL_OUTPUT_PATH", knowledge / "attendance" / "attendance.jsonl"
            ),
            invalid_jsonl_path=_path(
                "INVALID_JSONL_PATH",
                knowledge / "attendance" / "attendance.invalid.jsonl",
            ),
            ingestion_state_path=_path(
                "INGESTION_STATE_PATH",
                knowledge / "attendance" / "ingestion_state.sqlite3",
            ),
            index_schema_version=_integer("INDEX_SCHEMA_VERSION", 2, 1),
            ingestion_format_version=_integer("INGESTION_FORMAT_VERSION", 3, 1),
            source_xlsx_glob=_text("SOURCE_XLSX_GLOB", "*.xlsx"),
            source_csv_glob=_text("SOURCE_CSV_GLOB", "*.csv"),
            enable_postgres=_boolean("ENABLE_POSTGRES", False),
            postgres_dsn=postgres_dsn,
            enable_pgvector=_boolean("ENABLE_PGVECTOR", False),
            pgvector_dimensions=_integer("PGVECTOR_DIMENSIONS", 384, 1),
            enable_employee_period_chunks=_boolean(
                "ENABLE_EMPLOYEE_PERIOD_CHUNKS", True
            ),
            allow_empty_snapshot=_boolean("ALLOW_EMPTY_SNAPSHOT", False),
            allow_invalid_snapshot=_boolean("ALLOW_INVALID_SNAPSHOT", False),
            allow_attendance_source_removal=_boolean(
                "ALLOW_ATTENDANCE_SOURCE_REMOVAL", False
            ),
            embedding_batch_max_tokens=_integer(
                "EMBEDDING_BATCH_MAX_TOKENS", 250000, 1
            ),
            embedding_batch_max_items=_integer("EMBEDDING_BATCH_MAX_ITEMS", 256, 1),
            chroma_batch_size=_integer("CHROMA_BATCH_SIZE", 500, 1),
        )


settings = Settings.from_environment()


__all__ = ["PROJECT_ROOT", "Settings", "settings"]
