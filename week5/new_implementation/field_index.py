"""Build an experimental one-business-field-per-chunk attendance index."""

from __future__ import annotations

from hashlib import sha1
import logging
from pathlib import Path
from typing import Sequence

from .chroma_client import create_chroma_client
from .config import settings
from .embedding import collection_name_for_model
from .ingest import (
    JSONL_OUTPUT_PATH,
    IngestionStats,
    Result,
    _business_record,
    _content_hash,
    _count_invalid_records,
    _documents_from_jsonl,
    _jsonl_needs_refresh,
    _make_record_id,
    canonicalize_documents,
    sync_embeddings_to_chroma,
)


logger = logging.getLogger(__name__)
IDENTIFIER_FIELDS = frozenset({"Employee_ID", "Shift", "Date"})


def _populated(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _plain_value(value: object) -> str:
    """Render values deterministically without making a JSON document."""
    if isinstance(value, dict):
        return "; ".join(
            f"{str(key).replace('_', ' ')}: {_plain_value(item)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, (list, tuple)):
        return ", ".join(_plain_value(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def create_field_chunks(documents: Sequence[dict]) -> list[Result]:
    """Create one logical chunk for each populated non-identifier source field."""
    chunks: list[Result] = []
    for document in canonicalize_documents(documents):
        record = document["record"]
        business = _business_record(record)
        parent_id = _make_record_id(record)
        employee_id = str(record["Employee_ID"])
        attendance_date = str(record["Date"])
        shift = (
            _plain_value(record.get("Shift")) if _populated(record.get("Shift")) else ""
        )
        shift_sentence = f"had shift {shift}" if shift else "had no recorded shift"

        for field_name in sorted(business):
            if field_name in IDENTIFIER_FIELDS:
                continue
            value = business[field_name]
            if not _populated(value):
                continue
            value_text = _plain_value(value)
            if not value_text:
                continue

            label = field_name.replace("_", " ").strip().lower()
            page_content = (
                f"On {attendance_date}, employee {employee_id} {shift_sentence}. "
                f"{label.capitalize()} was {value_text}."
            )
            field_digest = sha1(field_name.encode("utf-8")).hexdigest()
            field_id = f"{parent_id}:field:{field_digest}"
            content_hash = _content_hash(
                {
                    "employee_id": employee_id,
                    "date": attendance_date,
                    "shift": shift,
                    "field_name": field_name,
                    "field_value": value,
                }
            )
            metadata = {
                "domain": "attendance",
                "chunk_type": "attendance_field",
                "record_id": field_id,
                "parent_record_id": parent_id,
                "content_hash": content_hash,
                "field_name": field_name,
                "field_value": value_text,
                "Employee_ID": employee_id,
                "Date": attendance_date,
                "Shift": shift or "not recorded",
            }
            for source_key in ("source", "sheet", "excel_row", "jsonl_line"):
                source_value = document.get(source_key)
                if source_value is not None:
                    metadata[source_key] = source_value
            chunks.append(
                Result(
                    page_content=page_content,
                    metadata=metadata,
                    record_id=field_id,
                    content_hash=content_hash,
                    chunk_type="attendance_field",
                )
            )
    return chunks


def build_field_index(
    documents: Sequence[dict], *, db_path: Path | None = None
) -> IngestionStats:
    """Incrementally publish field chunks in their own Chroma database."""
    chunks = create_field_chunks(documents)
    if not chunks:
        raise ValueError("no populated attendance fields are available to index")
    target = (db_path or settings.chroma_field_db_path).resolve()
    row_target = settings.chroma_db_path.resolve()
    if (
        target == row_target
        or target.is_relative_to(row_target)
        or row_target.is_relative_to(target)
    ):
        raise ValueError("field and row Chroma paths must be different")
    collection_name = collection_name_for_model(
        settings.chroma_collection_name, settings.embedding_model, "fields"
    )
    record_count = len({chunk.metadata["parent_record_id"] for chunk in chunks})
    stats = IngestionStats(jsonl_records=record_count, attendance_chunks=len(chunks))
    sync_embeddings_to_chroma(
        chunks,
        stats,
        client=create_chroma_client(target),
        collection_name=collection_name,
    )
    logger.info(
        "Field index synced records=%s fields=%s new=%s changed=%s "
        "unchanged=%s deleted=%s model=%s collection=%s",
        record_count,
        len(chunks),
        stats.new_records,
        stats.changed_records,
        stats.unchanged_records,
        stats.deleted_records,
        settings.embedding_model,
        collection_name,
    )
    return stats


def _load_current_documents() -> list[dict]:
    """Refuse unsafe snapshots before a field Chroma client is created."""
    if not JSONL_OUTPUT_PATH.is_file() or _jsonl_needs_refresh():
        raise RuntimeError(
            "canonical attendance JSONL is missing or stale; run ingestion first"
        )
    if _count_invalid_records():
        raise RuntimeError("canonical attendance projection has invalid records")
    with JSONL_OUTPUT_PATH.open("r", encoding="utf-8") as handle:
        source_lines = sum(1 for line in handle if line.strip())
    if source_lines == 0:
        raise RuntimeError("canonical attendance projection is empty")
    documents = _documents_from_jsonl(strict=True)
    if len(documents) != source_lines:
        raise RuntimeError("canonical attendance projection contains invalid rows")
    return documents


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    documents = _load_current_documents()
    stats = build_field_index(documents)
    collection_name = collection_name_for_model(
        settings.chroma_collection_name, settings.embedding_model, "fields"
    )
    print(
        "Field Chroma index ready: "
        f"records={stats.jsonl_records} fields={stats.attendance_chunks} "
        f"embedded={stats.embedding_inputs} upserts={stats.chroma_upserts} "
        f"model={settings.embedding_model} collection={collection_name}"
    )


if __name__ == "__main__":
    main()
