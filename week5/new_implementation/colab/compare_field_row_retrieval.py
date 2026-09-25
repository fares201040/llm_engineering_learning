"""Compare row and field attendance retrieval on synthetic Colab data only."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
import traceback


SOURCE_ROOT = Path("/content/attendance_phase2_source")
REPORT_PATH = Path("/content/attendance-field-row-comparison.json")
ROW_PATH = Path("/content/attendance-comparison-row-chroma")
FIELD_PATH = Path("/content/attendance-comparison-field-chroma")
MODEL = "all-MiniLM-L6-v2"
TOP_K = (1, 3, 5)


def _record(employee_id, name, department, day, shift, hours, **extra):
    return {
        "Employee_ID": employee_id,
        "Name": name,
        "Department": department,
        "Date": day,
        "Shift": shift,
        "Day_Type": "Working Day",
        "Exception": "OK",
        "Total_Worked_Hrs": hours,
        **extra,
    }


def synthetic_documents():
    """Small generated fixture; no repository attendance file is read."""
    rows = (
        _record(
            "S100",
            "Synthetic Ada",
            "Engineering",
            "2026-09-01",
            "Morning",
            8,
            Lateness_Hrs=0,
            OT_Authorized=0,
            Actual_From_Time="08:01",
        ),
        _record(
            "S100",
            "Synthetic Ada",
            "Engineering",
            "2026-09-02",
            "Morning",
            7.5,
            Lateness_Hrs=0.5,
            Employee_Remarks="Late due to road closure",
        ),
        _record(
            "S100",
            "Synthetic Ada",
            "Engineering",
            "2026-09-03",
            "Evening",
            10,
            OT_Authorized=2,
            Actual_From_Time="14:05",
        ),
        _record(
            "S100",
            "Synthetic Ada",
            "Engineering",
            "2026-09-04",
            "Morning",
            0,
            Exception="Absent",
            Leave_Type="Sick Leave",
            Leave_Hrs=8,
        ),
        _record(
            "S100",
            "Synthetic Ada",
            "Engineering",
            "2026-09-05",
            "Morning",
            8,
            Actual_From_Time="07:55",
        ),
        _record(
            "S200",
            "Synthetic Bilal",
            "Finance",
            "2026-09-01",
            "Morning",
            6,
            Lateness_Hrs=0,
            OT_Authorized=0,
        ),
        _record(
            "S200",
            "Synthetic Bilal",
            "Finance",
            "2026-09-02",
            "Evening",
            8,
            OT_Authorized=1,
            Actual_From_Time="15:10",
        ),
        _record(
            "S200",
            "Synthetic Bilal",
            "Finance",
            "2026-09-03",
            "Morning",
            0,
            Exception="Leave",
            Leave_Type="Annual Leave",
            Leave_Hrs=8,
        ),
        _record(
            "S200",
            "Synthetic Bilal",
            "Finance",
            "2026-09-04",
            "Morning",
            6,
            Employee_Remarks="Training day",
        ),
        _record(
            "S200",
            "Synthetic Bilal",
            "Finance",
            "2026-09-05",
            "Evening",
            7,
            Lateness_Hrs=1,
            Actual_From_Time="16:00",
        ),
        _record(
            "S300",
            "Synthetic Cora",
            "Operations",
            "2026-09-01",
            "Night",
            8,
            Actual_From_Time="22:00",
        ),
        _record(
            "S300",
            "Synthetic Cora",
            "Operations",
            "2026-09-02",
            "Night",
            9,
            OT_Authorized=1,
        ),
        _record(
            "S300",
            "Synthetic Cora",
            "Operations",
            "2026-09-03",
            "Night",
            0,
            Exception="Absent",
            Employee_Remarks="Unexcused absence",
        ),
        _record(
            "S300",
            "Synthetic Cora",
            "Operations",
            "2026-09-04",
            "Night",
            8,
            Actual_From_Time="21:48",
        ),
        _record(
            "S300",
            "Synthetic Cora",
            "Operations",
            "2026-09-05",
            "Night",
            8,
            Lateness_Hrs=0,
            OT_Authorized=0,
        ),
    )
    return [
        {
            "type": "attendance",
            "source": "synthetic_colab_fixture",
            "sheet": "generated",
            "excel_row": index,
            "record": record,
        }
        for index, record in enumerate(rows, start=1)
    ]


QUESTIONS = (
    (
        "Which attendance record shows sick leave for Synthetic Ada?",
        "S100",
        "2026-09-04",
        "Leave_Type",
    ),
    (
        "Find Synthetic Ada's authorized overtime of two hours.",
        "S100",
        "2026-09-03",
        "OT_Authorized",
    ),
    (
        "Which record notes Synthetic Ada was late because of a road closure?",
        "S100",
        "2026-09-02",
        "Employee_Remarks",
    ),
    ("When did Synthetic Bilal take annual leave?", "S200", "2026-09-03", "Leave_Type"),
    (
        "Find the training day remark for Synthetic Bilal.",
        "S200",
        "2026-09-04",
        "Employee_Remarks",
    ),
    (
        "Which Synthetic Bilal record has one hour of lateness?",
        "S200",
        "2026-09-05",
        "Lateness_Hrs",
    ),
    (
        "Find Synthetic Cora's unexcused absence.",
        "S300",
        "2026-09-03",
        "Employee_Remarks",
    ),
    (
        "What record shows Synthetic Cora swiped in at 21:48?",
        "S300",
        "2026-09-04",
        "Actual_From_Time",
    ),
    (
        "Which Synthetic Cora night shift had one authorized overtime hour?",
        "S300",
        "2026-09-02",
        "OT_Authorized",
    ),
    (
        "Find Synthetic Ada's 07:55 original swipe time.",
        "S100",
        "2026-09-05",
        "Actual_From_Time",
    ),
    (
        "For employee S100 on 2026-09-03, find authorized overtime.",
        "S100", "2026-09-03", "OT_Authorized",
    ),
    (
        "For employee S100 on 2026-09-04, find the leave type.",
        "S100", "2026-09-04", "Leave_Type",
    ),
    (
        "For employee S200 on 2026-09-05, find lateness hours.",
        "S200", "2026-09-05", "Lateness_Hrs",
    ),
    (
        "For employee S300 on 2026-09-04, find actual swipe time.",
        "S300", "2026-09-04", "Actual_From_Time",
    ),
    (
        "For employee S200 on 2026-09-03, find the leave type.",
        "S200", "2026-09-03", "Leave_Type",
    ),
    (
        "Find the record with two authorized overtime hours.",
        "S100", "2026-09-03", "OT_Authorized",
    ),
    (
        "Find the road closure remark.",
        "S100", "2026-09-02", "Employee_Remarks",
    ),
    (
        "Find the annual leave record.",
        "S200", "2026-09-03", "Leave_Type",
    ),
    (
        "Find the original swipe time of 21:48.",
        "S300", "2026-09-04", "Actual_From_Time",
    ),
    (
        "Find the training day remark.",
        "S200", "2026-09-04", "Employee_Remarks",
    ),
)
QUERY_TYPES = ("name_field",) * 10 + ("id_date_field",) * 5 + ("field_only",) * 5


def _compact_exception(exc, stage):
    return {
        "category": "untriaged_issue",
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc)[:2000],
        "traceback": traceback.format_exception(type(exc), exc, exc.__traceback__)[-8:],
    }


def _query(collection, vector, count):
    started = perf_counter()
    response = collection.query(
        query_embeddings=[vector],
        n_results=min(count, collection.count()),
        include=["metadatas", "distances"],
    )
    return response, perf_counter() - started


def _hits(response, *, field_index):
    metadatas = (response.get("metadatas") or [[]])[0]
    distances = (response.get("distances") or [[]])[0]
    return [
        {
            "parent_record_id": (
                metadata.get("parent_record_id")
                if field_index
                else metadata.get("record_id")
            ),
            "field_name": metadata.get("field_name") if field_index else None,
            "distance": float(distance),
        }
        for metadata, distance in zip(metadatas, distances)
    ]


def _rank(hits, record_id, field_name=None):
    for position, hit in enumerate(hits, start=1):
        if hit["parent_record_id"] == record_id and (
            field_name is None or hit["field_name"] == field_name
        ):
            return position
    return None


def _summary(cases, arm):
    ranks = [case[arm]["parent_rank"] for case in cases]
    return {
        f"parent_hit_at_{k}": sum(rank is not None and rank <= k for rank in ranks)
        / len(ranks)
        for k in TOP_K
    } | {
        "parent_mrr_at_5": sum(1 / rank for rank in ranks if rank and rank <= 5)
        / len(ranks),
        "median_query_seconds": statistics.median(
            case[arm]["query_seconds"] for case in cases
        ),
    }


def worker():
    from week5.new_implementation.chroma_client import create_chroma_client
    from week5.new_implementation.config import settings
    from week5.new_implementation.embedding import (
        collection_name_for_model,
        get_embeddings,
    )
    from week5.new_implementation.field_index import build_field_index
    from week5.new_implementation.ingest import (
        IngestionStats,
        _make_record_id,
        create_record_chunks,
        sync_embeddings_to_chroma,
    )

    report = {
        "status": "incomplete",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "generated_synthetic_only",
        "model": MODEL,
        "provider": "huggingface",
        "row_chroma_path": str(ROW_PATH),
        "field_chroma_path": str(FIELD_PATH),
        "issues": [],
    }
    stage = "fixture"
    try:
        documents = synthetic_documents()
        by_key = {
            (doc["record"]["Employee_ID"], doc["record"]["Date"]): _make_record_id(
                doc["record"]
            )
            for doc in documents
        }
        report["synthetic_record_count"] = len(documents)

        stage = "embedding_model"
        embeddings = get_embeddings(
            settings.embedding_model, settings.embedding_provider
        )
        row_collection_name = collection_name_for_model(
            settings.chroma_collection_name, settings.embedding_model, "chunks"
        )
        field_collection_name = collection_name_for_model(
            settings.chroma_collection_name, settings.embedding_model, "fields"
        )

        stage = "row_index_build"
        row_chunks = create_record_chunks(documents)
        row_stats = IngestionStats()
        started = perf_counter()
        sync_embeddings_to_chroma(
            row_chunks,
            row_stats,
            client=create_chroma_client(ROW_PATH),
            collection_name=row_collection_name,
        )
        report["row_build_seconds"] = perf_counter() - started
        report["row_embedding_inputs"] = row_stats.embedding_inputs

        stage = "field_index_build"
        started = perf_counter()
        field_stats = build_field_index(documents, db_path=FIELD_PATH)
        report["field_build_seconds"] = perf_counter() - started
        report["field_embedding_inputs"] = field_stats.embedding_inputs

        stage = "retrieval"
        row_collection = create_chroma_client(ROW_PATH).get_collection(
            row_collection_name
        )
        field_collection = create_chroma_client(FIELD_PATH).get_collection(
            field_collection_name
        )
        report["row_vector_count"] = row_collection.count()
        report["field_vector_count"] = field_collection.count()
        report["cases"] = []
        if len(QUESTIONS) != len(QUERY_TYPES):
            raise ValueError("query type count does not match synthetic questions")
        for (question, employee_id, day, expected_field), query_type in zip(
            QUESTIONS, QUERY_TYPES
        ):
            vector = embeddings.embed_query(question)
            expected_record = by_key[(employee_id, day)]
            row_response, row_time = _query(row_collection, vector, 20)
            field_response, field_time = _query(field_collection, vector, 20)
            row_hits = _hits(row_response, field_index=False)
            field_hits = _hits(field_response, field_index=True)
            report["cases"].append(
                {
                    "question": question,
                    "query_type": query_type,
                    "expected_record_id": expected_record,
                    "expected_field": expected_field,
                    "row": {
                        "parent_rank": _rank(row_hits, expected_record),
                        "query_seconds": row_time,
                        "top_five": row_hits[:5],
                    },
                    "field": {
                        "parent_rank": _rank(field_hits, expected_record),
                        "exact_field_rank": _rank(
                            field_hits, expected_record, expected_field
                        ),
                        "query_seconds": field_time,
                        "top_five": field_hits[:5],
                    },
                }
            )

        report["row_metrics"] = _summary(report["cases"], "row")
        report["field_metrics"] = _summary(report["cases"], "field")
        report["field_metrics"]["exact_field_hit_at_5"] = sum(
            case["field"]["exact_field_rank"] is not None
            and case["field"]["exact_field_rank"] <= 5
            for case in report["cases"]
        ) / len(report["cases"])
        report["category_metrics"] = {
            query_type: {
                "case_count": len(group),
                "row": _summary(group, "row"),
                "field": _summary(group, "field")
                | {
                    "exact_field_hit_at_5": sum(
                        case["field"]["exact_field_rank"] is not None
                        and case["field"]["exact_field_rank"] <= 5
                        for case in group
                    ) / len(group)
                },
            }
            for query_type in ("name_field", "id_date_field", "field_only")
            if (group := [
                case for case in report["cases"] if case["query_type"] == query_type
            ])
        }
        report["status"] = "complete"
    except Exception as exc:
        report["issues"].append(_compact_exception(exc, stage))
    finally:
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "status",
                    "synthetic_record_count",
                    "row_vector_count",
                    "field_vector_count",
                    "row_metrics",
                    "field_metrics",
                    "category_metrics",
                    "issues",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "complete" else 1


def main():
    if "--worker" in sys.argv:
        return worker()
    if not SOURCE_ROOT.joinpath("week5/new_implementation/field_index.py").is_file():
        raise FileNotFoundError("The sanitized comparison source is not extracted.")
    if importlib.util.find_spec("openpyxl") is None:
        installed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "openpyxl>=3.1.5"],
            capture_output=True,
            text=True,
            check=False,
        )
        if installed.returncode:
            raise RuntimeError(
                f"openpyxl installation failed with exit code {installed.returncode}"
            )
    environment = os.environ.copy()
    for key in ("POSTGRES_DSN", "POSTGRES_READONLY_DSN", "OPENAI_API_KEY"):
        environment.pop(key, None)
    environment.update(
        {
            "EMBEDDING_PROVIDER": "huggingface",
            "EMBEDDING_MODEL": MODEL,
            "ENABLE_POSTGRES": "false",
            "ENABLE_PGVECTOR": "false",
            "CHROMA_DB_PATH": str(ROW_PATH),
            "CHROMA_FIELD_DB_PATH": str(FIELD_PATH),
            "CHROMA_COLLECTION_NAME": "docs",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "week5.new_implementation.colab.compare_field_row_retrieval",
            "--worker",
        ],
        cwd=SOURCE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    print(completed.stdout)
    if completed.returncode:
        print(completed.stderr[-4000:])
    return completed.returncode


if __name__ == "__main__":
    if main():
        raise RuntimeError("Synthetic retrieval comparison failed; inspect its report.")
