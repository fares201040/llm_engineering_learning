"""Private local comparison of row and possibly partial field Chroma indexes.

The source records and case-level output stay in ignored new_evaluation/results.
Only aggregate metrics are printed. Run with the configured local Hugging Face model.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import random
from statistics import median
from time import perf_counter

from .chroma_client import create_chroma_client
from .config import PROJECT_ROOT, settings
from .embedding import collection_name_for_model, get_embeddings
from .field_index import _load_current_documents, create_field_chunks
from .ingest import _make_record_id


REPORT_PATH = (
    PROJECT_ROOT
    / "week5"
    / "new_evaluation"
    / "results"
    / "field_row_local_semantic_partial_comparison.json"
)
MODEL = "all-MiniLM-L6-v2"
RANDOM_SEED = 20260925
FIELD_GROUPS = (
    "Leave_Type",
    "OT_Authorized",
    "Lateness_Hrs",
    "Actual_From_Time",
    "Total_Worked_Hrs",
)
VALUE_GROUPS = (
    ("Employee_Remarks", 3),
    ("OT_Authorized", 4),
    ("Lateness_Hrs", 4),
    ("Total_Worked_Hrs", 4),
    ("Actual_From_Time", 5),
)
QUESTION_PHRASES = {
    "Leave_Type": "what type of leave was recorded",
    "OT_Authorized": "how many authorized overtime hours were recorded",
    "Lateness_Hrs": "how many lateness hours were recorded",
    "Actual_From_Time": "what was the actual start time",
    "Total_Worked_Hrs": "how many total hours were worked",
}
VALUE_PHRASES = {
    "Employee_Remarks": "Find the attendance record with this employee remark: {value}.",
    "OT_Authorized": "Find the attendance record with {value} authorized overtime hours.",
    "Lateness_Hrs": "Find the attendance record with {value} lateness hours.",
    "Total_Worked_Hrs": "Find the attendance record with {value} total worked hours.",
    "Actual_From_Time": "Find the attendance record with actual start time {value}.",
}


def _populated(value: object) -> bool:
    return value is not None and bool(str(value).strip())


def _select_cases(rows: list[dict], all_rows: list[dict]) -> list[dict]:
    """Build 60 grounded questions across three semantic-search patterns."""
    rng = random.Random(RANDOM_SEED)
    ordered = sorted(rows, key=_make_record_id)
    name_counts = Counter(str(row.get("Name") or "") for row in all_rows)
    selected: list[dict] = []

    for group in ("name_date_field", "id_date_field"):
        used_records: set[str] = set()
        used_employees: set[str] = set()
        for field in FIELD_GROUPS:
            candidates = [
                row
                for row in ordered
                if _populated(row.get(field))
                and (
                    group != "name_date_field" or name_counts[str(row.get("Name"))] == 7
                )
            ]
            rng.shuffle(candidates)
            chosen = []
            for row in candidates:
                record_id = _make_record_id(row)
                employee_id = str(row["Employee_ID"])
                if record_id in used_records or employee_id in used_employees:
                    continue
                chosen.append(row)
                used_records.add(record_id)
                used_employees.add(employee_id)
                if len(chosen) == 4:
                    break
            if len(chosen) != 4:
                raise ValueError(f"insufficient distinct records for {group}/{field}")
            for row in chosen:
                person = (
                    f"{row['Name']}"
                    if group == "name_date_field"
                    else f"employee {row['Employee_ID']}"
                )
                question = f"For {person} on {row['Date']}, {QUESTION_PHRASES[field]}?"
                selected.append(
                    {
                        "group": group,
                        "expected_field": field,
                        "question": question,
                        "expected_record_id": _make_record_id(row),
                    }
                )

    for field, quota in VALUE_GROUPS:
        values = Counter(
            str(row[field]) for row in all_rows if _populated(row.get(field))
        )
        candidates = [
            row
            for row in ordered
            if _populated(row.get(field)) and values[str(row[field])] == 1
        ]
        rng.shuffle(candidates)
        if len(candidates) < quota:
            raise ValueError(f"insufficient unique values for value_only/{field}")
        for row in candidates[:quota]:
            selected.append(
                {
                    "group": "value_only",
                    "expected_field": field,
                    "question": VALUE_PHRASES[field].format(value=row[field]),
                    "expected_record_id": _make_record_id(row),
                }
            )

    if len(selected) != 60 or len({case["question"] for case in selected}) != 60:
        raise ValueError("expected 60 distinct grounded semantic questions")
    return selected


def _rank(hits: list[dict], expected: str, *, field: str | None = None) -> int | None:
    for index, hit in enumerate(hits, start=1):
        if hit["parent_record_id"] == expected and (
            field is None or hit["field_name"] == field
        ):
            return index
    return None


def _unique_parent_rank(hits: list[dict], expected: str) -> int | None:
    seen = set()
    for hit in hits:
        parent = hit["parent_record_id"]
        if parent in seen:
            continue
        seen.add(parent)
        if parent == expected:
            return len(seen)
    return None


def _query(
    collection,
    vector: list[float],
    eligible_ids: list[str],
    *,
    field_index: bool,
) -> tuple[list[dict], float]:
    parent_key = "parent_record_id" if field_index else "record_id"
    started = perf_counter()
    result = collection.query(
        query_embeddings=[vector],
        n_results=50,
        where={
            "$and": [
                {
                    "chunk_type": "attendance_field"
                    if field_index
                    else "attendance_record"
                },
                {parent_key: {"$in": eligible_ids}},
            ]
        },
        include=["metadatas", "distances"],
    )
    elapsed = perf_counter() - started
    hits = []
    for metadata, distance in zip(result["metadatas"][0], result["distances"][0]):
        hits.append(
            {
                "parent_record_id": metadata.get("parent_record_id")
                if field_index
                else metadata.get("record_id"),
                "field_name": metadata.get("field_name") if field_index else None,
                "distance": float(distance),
            }
        )
    return hits, elapsed


def _metrics(cases: list[dict], arm: str) -> dict:
    ranks = [case[arm]["unique_parent_rank"] for case in cases]
    result = {
        f"parent_hit_at_{k}": sum(rank is not None and rank <= k for rank in ranks)
        / len(cases)
        for k in (1, 3, 5, 10)
    }
    raw_ranks = [case[arm]["raw_parent_rank"] for case in cases]
    result.update(
        {
            f"raw_chunk_parent_hit_at_{k}": sum(
                rank is not None and rank <= k for rank in raw_ranks
            )
            / len(cases)
            for k in (1, 3, 5, 10)
        }
    )
    result["parent_mrr_at_10"] = sum(
        1 / rank for rank in ranks if rank is not None and rank <= 10
    ) / len(cases)
    result["median_query_seconds"] = median(
        case[arm]["query_seconds"] for case in cases
    )
    if arm == "field":
        result["exact_field_hit_at_5"] = sum(
            case[arm]["exact_field_rank"] is not None
            and case[arm]["exact_field_rank"] <= 5
            for case in cases
        ) / len(cases)
        result["exact_field_hit_at_10"] = sum(
            case[arm]["exact_field_rank"] is not None
            and case[arm]["exact_field_rank"] <= 10
            for case in cases
        ) / len(cases)
    return result


def main() -> None:
    if (
        settings.embedding_provider != "huggingface"
        or settings.embedding_model != MODEL
    ):
        raise RuntimeError(
            "this comparison requires the local MiniLM row and field indexes"
        )
    if settings.chroma_db_path.resolve() == settings.chroma_field_db_path.resolve():
        raise RuntimeError("row and field Chroma databases must be separate")

    documents = _load_current_documents()
    all_rows = [document["record"] for document in documents]
    row_name = collection_name_for_model(
        settings.chroma_collection_name, MODEL, "chunks"
    )
    field_name = collection_name_for_model(
        settings.chroma_collection_name, MODEL, "fields"
    )
    row_collection = create_chroma_client(settings.chroma_db_path).get_collection(
        row_name
    )
    field_collection = create_chroma_client(
        settings.chroma_field_db_path
    ).get_collection(field_name)

    row_metadata = row_collection.get(
        where={"chunk_type": "attendance_record"}, include=["metadatas"]
    )["metadatas"]
    indexed_row_ids = {metadata["record_id"] for metadata in row_metadata}
    expected_row_ids = {_make_record_id(row) for row in all_rows}
    if indexed_row_ids != expected_row_ids:
        raise RuntimeError(
            "row index does not cover exactly the current attendance records"
        )

    expected_fields: dict[str, set[str]] = {}
    for chunk in create_field_chunks(documents):
        expected_fields.setdefault(chunk.metadata["parent_record_id"], set()).add(
            chunk.metadata["field_name"]
        )
    field_metadata = field_collection.get(include=["metadatas"])["metadatas"]
    stored_fields: dict[str, set[str]] = {}
    for metadata in field_metadata:
        stored_fields.setdefault(metadata["parent_record_id"], set()).add(
            metadata["field_name"]
        )
    eligible_ids = sorted(
        record_id
        for record_id, fields in stored_fields.items()
        if fields == expected_fields[record_id]
    )
    eligible_id_set = set(eligible_ids)
    rows = [row for row in all_rows if _make_record_id(row) in eligible_id_set]
    if len(rows) != len(eligible_ids):
        raise RuntimeError("indexed attendance record coverage is inconsistent")
    cases = _select_cases(rows, all_rows)
    field_count = field_collection.count()
    if field_count != len(field_metadata):
        raise RuntimeError("field index changed during comparison setup")

    embeddings = get_embeddings(MODEL, "huggingface")
    for case in cases:
        vector = embeddings.embed_query(case["question"])
        expected = case["expected_record_id"]
        row_hits, row_seconds = _query(
            row_collection, vector, eligible_ids, field_index=False
        )
        field_hits, field_seconds = _query(
            field_collection, vector, eligible_ids, field_index=True
        )
        case["row"] = {
            "raw_parent_rank": _rank(row_hits, expected),
            "unique_parent_rank": _unique_parent_rank(row_hits, expected),
            "query_seconds": row_seconds,
            "top_20": row_hits[:20],
        }
        case["field"] = {
            "raw_parent_rank": _rank(field_hits, expected),
            "unique_parent_rank": _unique_parent_rank(field_hits, expected),
            "exact_field_rank": _rank(
                field_hits, expected, field=case["expected_field"]
            ),
            "query_seconds": field_seconds,
            "top_20": field_hits[:20],
        }

    report = {
        "status": "complete",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "private_local_attendance_projection",
        "model": MODEL,
        "case_count": len(cases),
        "source_record_count": len(all_rows),
        "comparison_record_count": len(rows),
        "excluded_partial_record_count": len(stored_fields) - len(rows),
        "row_vector_count": row_collection.count(),
        "row_attendance_vector_count": len(row_metadata),
        "expected_field_chunk_count": sum(
            len(fields) for fields in expected_fields.values()
        ),
        "field_vector_count": field_count,
        "metrics": {
            group: {
                "case_count": len(group_cases),
                "row": _metrics(group_cases, "row"),
                "field": _metrics(group_cases, "field"),
            }
            for group in ("name_date_field", "id_date_field", "value_only", "all")
            if (
                group_cases := cases
                if group == "all"
                else [case for case in cases if case["group"] == group]
            )
        },
        "field_metrics": {
            field: {
                "case_count": len(field_cases),
                "row": _metrics(field_cases, "row"),
                "field": _metrics(field_cases, "field"),
            }
            for field in sorted({case["expected_field"] for case in cases})
            if (
                field_cases := [
                    case for case in cases if case["expected_field"] == field
                ]
            )
        },
        "cases": cases,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "case_count": report["case_count"],
                "source_record_count": report["source_record_count"],
                "comparison_record_count": report["comparison_record_count"],
                "excluded_partial_record_count": report[
                    "excluded_partial_record_count"
                ],
                "row_vector_count": report["row_vector_count"],
                "row_attendance_vector_count": report["row_attendance_vector_count"],
                "expected_field_chunk_count": report["expected_field_chunk_count"],
                "field_vector_count": report["field_vector_count"],
                "metrics": report["metrics"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
