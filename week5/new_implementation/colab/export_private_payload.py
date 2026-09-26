"""Export the authorized private attendance evaluation payload locally."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .private_payload import DATASET_FINGERPRINT, build_payload_archive


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PRIVATE_ENV = Path(__file__).resolve().parents[1] / ".env.postgres"
CASE_FILE = REPOSITORY_ROOT / "week5" / "new_evaluation" / "tests.jsonl"
DATASET_MANIFEST = (
    REPOSITORY_ROOT / "week5" / "new_evaluation" / "dataset_manifest.json"
)
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "week5"
    / "new_evaluation"
    / "results"
    / "attendance_private_payload.zip"
)


def export_attendance_jsonl(dsn: str) -> bytes:
    """Read only the authorized public attendance table in stable record order."""
    import psycopg

    query = (
        "SELECT row_to_json(attendance_row)::text "
        "FROM (SELECT * FROM public.attendance_records ORDER BY record_id) "
        "AS attendance_row"
    )
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            rows = connection.execute(query).fetchall()
    return "".join(f"{row[0]}\n" for row in rows).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    load_dotenv(PRIVATE_ENV, override=False)
    dsn = os.getenv("POSTGRES_READONLY_DSN") or os.getenv("POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("The authorized local PostgreSQL DSN is not configured")
    dataset_manifest = json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))
    fingerprint = dataset_manifest.get("fingerprint")
    if fingerprint != DATASET_FINGERPRINT:
        raise ValueError("The local dataset manifest fingerprint does not match")
    digest = build_payload_archive(
        args.output,
        attendance_bytes=export_attendance_jsonl(dsn),
        case_bytes=CASE_FILE.read_bytes(),
        dataset_fingerprint=fingerprint,
    )
    print(f"Created private payload at {args.output}")
    print(f"SHA-256 {digest}")
    print("Payload members: attendance_records.jsonl, tests.jsonl, manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
