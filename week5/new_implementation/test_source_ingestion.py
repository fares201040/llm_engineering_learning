from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from openpyxl import Workbook

from week5.new_implementation import ingest, source_ingestion


@contextmanager
def workspace_temp_directory():
    path = Path.cwd() / "week5" / "new_implementation" / f".test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


class SourceIngestionModuleTests(unittest.TestCase):
    def test_source_ingestion_module_exists(self):
        spec = importlib.util.find_spec("week5.new_implementation.source_ingestion")
        self.assertIsNotNone(spec)

    def test_module_exposes_minimal_source_contract(self):
        expected = {
            "SourceFile",
            "SourceRow",
            "SourcePartition",
            "PartitionClassification",
            "RawSourceRow",
            "discover_sources",
            "read_partitions",
            "classify_partition",
            "build_raw_rows",
        }
        missing = sorted(
            name for name in expected if not hasattr(source_ingestion, name)
        )
        self.assertEqual(missing, [])

    def test_discovery_finds_xlsx_and_csv_in_stable_order(self):
        with workspace_temp_directory() as root:
            (root / "attendance").mkdir()
            (root / "attendance" / "b.csv").write_text("a\n1\n", encoding="utf-8")
            (root / "attendance" / "a.xlsx").write_bytes(b"xlsx")
            (root / "attendance" / "~$locked.xlsx").write_bytes(b"locked")

            sources = source_ingestion.discover_sources(root, "*.xlsx", "*.csv")

        self.assertEqual(
            [source.relative_path for source in sources],
            ["attendance/a.xlsx", "attendance/b.csv"],
        )
        self.assertEqual([source.source_format for source in sources], ["xlsx", "csv"])
        self.assertTrue(all(len(source.sha256) == 64 for source in sources))

    def test_csv_attendance_partition_is_read_and_classified(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            path = folder / "attendance.csv"
            path.write_text(
                "Employee ID,Name,Date\nA10029,Example Name,2026-09-01\n",
                encoding="utf-8-sig",
            )
            source = source_ingestion.discover_sources(root, "*.xlsx", "*.csv")[0]
            partition = source_ingestion.read_partitions(source)[0]
            classification = source_ingestion.classify_partition(partition)

        self.assertEqual(partition.name, "CSV")
        self.assertEqual(partition.header_row, 1)
        self.assertEqual(partition.rows[0].row_number, 2)
        self.assertEqual(partition.rows[0].normalized_record["Employee_ID"], "A10029")
        self.assertEqual(classification.domain, "attendance")
        self.assertEqual(classification.status, "valid")

    def test_malformed_csv_creates_row_zero_read_error_sentinel(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            path = folder / "broken.csv"
            path.write_bytes(b"\xff\xfe\x00")
            source = source_ingestion.discover_sources(root, "*.xlsx", "*.csv")[0]
            partition = source_ingestion.read_partitions(source)[0]
            classification = source_ingestion.classify_partition(partition)
            raw_rows = source_ingestion.build_raw_rows(partition, classification)

        self.assertEqual(classification.reason_code, "read_error")
        self.assertEqual(classification.status, "quarantined")
        self.assertEqual(raw_rows[0].source_row, 0)

    def test_unregistered_folder_is_quarantined_even_with_attendance_headers(self):
        source = source_ingestion.SourceFile(
            path=Path("loans/data.csv"),
            relative_path="loans/data.csv",
            sha256="a" * 64,
            source_format="csv",
        )
        partition = source_ingestion.SourcePartition(
            source=source,
            name="CSV",
            header_row=1,
            raw_headers=("Employee ID", "Name", "Date"),
            normalized_headers=("Employee_ID", "Name", "Date"),
            rows=(),
        )

        classification = source_ingestion.classify_partition(partition)

        self.assertEqual(classification.domain, "unknown")
        self.assertEqual(classification.reason_code, "unrecognized_domain")

    def test_duplicate_normalized_headers_are_quarantined(self):
        source = source_ingestion.SourceFile(
            path=Path("attendance/data.csv"),
            relative_path="attendance/data.csv",
            sha256="b" * 64,
            source_format="csv",
        )
        partition = source_ingestion.SourcePartition(
            source=source,
            name="CSV",
            header_row=1,
            raw_headers=("Employee ID", "Employee_ID", "Name", "Date"),
            normalized_headers=("Employee_ID", "Employee_ID", "Name", "Date"),
            rows=(),
        )

        classification = source_ingestion.classify_partition(partition)

        self.assertEqual(classification.domain, "unknown")
        self.assertEqual(classification.reason_code, "duplicate_headers")

    def test_workbook_sheets_are_independent_partitions(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            path = folder / "mixed.xlsx"
            workbook = Workbook()
            attendance = workbook.active
            attendance.title = "Attendance"
            attendance.append(["Employee ID", "Name", "Date"])
            attendance.append(["A10029", "Example Name", "2026-09-01"])
            notes = workbook.create_sheet("Notes")
            notes.append(["Topic", "Text"])
            notes.append(["Reminder", "Confidential"])
            workbook.save(path)

            source = source_ingestion.discover_sources(root, "*.xlsx", "*.csv")[0]
            partitions = source_ingestion.read_partitions(source)
            classifications = [
                source_ingestion.classify_partition(partition)
                for partition in partitions
            ]

        self.assertEqual(
            [partition.name for partition in partitions], ["Attendance", "Notes"]
        )
        self.assertEqual(classifications[0].domain, "attendance")
        self.assertEqual(classifications[1].domain, "unknown")
        self.assertEqual(classifications[1].reason_code, "schema_mismatch")

    def test_raw_row_identity_is_stable_and_preserves_header_value_arrays(self):
        source = source_ingestion.SourceFile(
            path=Path("attendance/data.csv"),
            relative_path="attendance/data.csv",
            sha256="c" * 64,
            source_format="csv",
        )
        row = source_ingestion.SourceRow(
            row_number=2,
            raw_values=("A10029", "Example Name", "2026-09-01"),
            normalized_record={
                "Employee_ID": "A10029",
                "Name": "Example Name",
                "Date": "2026-09-01",
            },
        )
        partition = source_ingestion.SourcePartition(
            source=source,
            name="CSV",
            header_row=1,
            raw_headers=("Employee ID", "Name", "Date"),
            normalized_headers=("Employee_ID", "Name", "Date"),
            rows=(row,),
        )
        classification = source_ingestion.PartitionClassification(
            domain="attendance",
            status="valid",
            reason_code=None,
        )

        first_rows = source_ingestion.build_raw_rows(partition, classification)
        second_rows = source_ingestion.build_raw_rows(partition, classification)

        self.assertEqual(len(first_rows), 1)
        self.assertEqual(len(second_rows), 1)
        if not first_rows or not second_rows:
            return
        first = first_rows[0]
        second = second_rows[0]

        self.assertEqual(first.raw_row_key, second.raw_row_key)
        self.assertEqual(
            first.payload_json,
            {
                "headers": ["Employee ID", "Name", "Date"],
                "values": ["A10029", "Example Name", "2026-09-01"],
            },
        )

    def test_converter_routes_attendance_csv_and_preserves_raw_row(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            (folder / "attendance.csv").write_text(
                "Employee ID,Name,Date\nA10029,Example Name,2026-09-01\n",
                encoding="utf-8-sig",
            )
            output = root / "attendance.jsonl"
            invalid = root / "attendance.invalid.jsonl"
            manifest = root / "attendance.sources.json"
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "JSONL_OUTPUT_PATH", output),
                patch.object(ingest, "INVALID_JSONL_PATH", invalid),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest),
            ):
                snapshot = ingest.convert_sources_to_jsonl()

            records = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(len(snapshot.documents), 1)
        self.assertEqual(len(snapshot.raw_rows), 1)
        self.assertEqual(snapshot.raw_rows[0].status, "valid")
        self.assertEqual(records[0]["Employee_ID"], "A10029")

    def test_converter_quarantines_unknown_csv_without_attendance_output(self):
        with workspace_temp_directory() as root:
            folder = root / "loans"
            folder.mkdir()
            (folder / "loans.csv").write_text(
                "Employee ID,Amount\nA10029,100\n",
                encoding="utf-8",
            )
            output = root / "attendance.jsonl"
            invalid = root / "attendance.invalid.jsonl"
            manifest = root / "attendance.sources.json"
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "JSONL_OUTPUT_PATH", output),
                patch.object(ingest, "INVALID_JSONL_PATH", invalid),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest),
            ):
                snapshot = ingest.convert_sources_to_jsonl()

        self.assertEqual(snapshot.documents, ())
        self.assertEqual(len(snapshot.raw_rows), 1)
        self.assertEqual(snapshot.raw_rows[0].domain, "unknown")
        self.assertEqual(snapshot.raw_rows[0].status, "quarantined")

    def test_rejected_reclassification_keeps_last_safe_artifacts(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            (folder / "attendance.csv").write_text(
                "Employee ID,Unexpected\nA10029,value\n",
                encoding="utf-8",
            )
            output = root / "attendance.jsonl"
            invalid = root / "attendance.invalid.jsonl"
            manifest = root / "attendance.sources.json"
            output.write_text("LAST_SAFE_JSONL\n", encoding="utf-8")
            invalid.write_text("LAST_SAFE_INVALID\n", encoding="utf-8")
            prior = {
                "version": 3,
                "parser_version": 1,
                "sources": [],
                "partitions": [
                    {
                        "source_path": "attendance/attendance.csv",
                        "name": "CSV",
                        "domain": "attendance",
                        "status": "valid",
                        "row_count": 1,
                    }
                ],
            }
            manifest.write_text(json.dumps(prior), encoding="utf-8")
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "JSONL_OUTPUT_PATH", output),
                patch.object(ingest, "INVALID_JSONL_PATH", invalid),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest),
            ):
                snapshot = ingest.convert_sources_to_jsonl()

            stored = json.loads(manifest.read_text(encoding="utf-8"))
            stored_output = output.read_text(encoding="utf-8")
            stored_invalid = invalid.read_text(encoding="utf-8")

        self.assertTrue(snapshot.unsafe_reasons)
        self.assertEqual(stored, prior)
        self.assertEqual(stored_output, "LAST_SAFE_JSONL\n")
        self.assertEqual(stored_invalid, "LAST_SAFE_INVALID\n")

    def test_removing_all_sources_returns_unsafe_and_keeps_last_safe_artifacts(self):
        with workspace_temp_directory() as root:
            output = root / "attendance.jsonl"
            invalid = root / "attendance.invalid.jsonl"
            manifest = root / "attendance.sources.json"
            output.write_text("LAST_SAFE_JSONL\n", encoding="utf-8")
            invalid.write_text("LAST_SAFE_INVALID\n", encoding="utf-8")
            prior = {
                "version": 3,
                "parser_version": 1,
                "sources": [],
                "partitions": [
                    {
                        "source_path": "attendance/attendance.csv",
                        "name": "CSV",
                        "domain": "attendance",
                        "status": "valid",
                        "row_count": 1,
                    }
                ],
            }
            manifest.write_text(json.dumps(prior), encoding="utf-8")
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "JSONL_OUTPUT_PATH", output),
                patch.object(ingest, "INVALID_JSONL_PATH", invalid),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest),
            ):
                try:
                    snapshot = ingest.convert_sources_to_jsonl()
                except FileNotFoundError as exc:
                    self.fail(
                        "Removing a previously valid source must return an unsafe "
                        f"snapshot, not FileNotFoundError: {exc}"
                    )

            stored = json.loads(manifest.read_text(encoding="utf-8"))
            stored_output = output.read_text(encoding="utf-8")
            stored_invalid = invalid.read_text(encoding="utf-8")

        self.assertIn("disappeared", " ".join(snapshot.unsafe_reasons))
        self.assertEqual(snapshot.documents, ())
        self.assertEqual(snapshot.raw_rows, ())
        self.assertEqual(stored, prior)
        self.assertEqual(stored_output, "LAST_SAFE_JSONL\n")
        self.assertEqual(stored_invalid, "LAST_SAFE_INVALID\n")
