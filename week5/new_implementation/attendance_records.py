"""Attendance normalization and chunk-projection façade."""

from .ingest import (  # noqa: F401
    create_record_chunks,
    fetch_jsonl_documents,
    normalize_attendance_record,
    validate_attendance_record,
)

__all__ = [name for name in globals() if not name.startswith("_")]
