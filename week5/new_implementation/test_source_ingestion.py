from contextlib import closing, contextmanager
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
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
    def test_source_record_ids_cannot_replace_distinct_logical_ids(self):
        first = {"Employee_ID": "E-1", "Date": "2026-09-03", "_record_id": "shared"}
        second = {"Employee_ID": "E-2", "Date": "2026-09-03", "_record_id": "shared"}
        self.assertNotEqual(ingest._make_record_id(first), "shared")
        self.assertNotEqual(ingest._make_record_id(first), ingest._make_record_id(second))
        validated = ingest.validate_attendance_record({
            **first, "Name": "Example", "_verified_record_id": "attacker-controlled",
        })
        self.assertNotIn("_verified_record_id", validated)
        documents = [{"record": first}]
        ingest.apply_existing_record_ids(documents, [{
            "employee_id": "E-1", "attendance_date": "2026-09-03",
            "record_id": "attendance:legacy",
        }])
        self.assertEqual(ingest._make_record_id(first), "attendance:legacy")

    def test_csv_supplied_id_cannot_collide_in_sink_rows(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            (folder / "two.csv").write_text(
                "Employee ID,Name,Date,_record_id\n"
                "E-1,First,2026-09-03,shared-id\n"
                "E-2,Second,2026-09-03,shared-id\n",
                encoding="utf-8",
            )
            with patch.object(ingest, "KNOWLEDGE_BASE_PATH", root):
                snapshot = ingest.convert_sources_to_jsonl(persist=False)
            documents = ingest.canonicalize_documents(list(snapshot.documents))
            ids = [ingest._postgres_attendance_values(doc)["record_id"] for doc in documents]
        self.assertEqual(len(set(ids)), 2)
        self.assertNotIn("shared-id", ids)

    def test_missing_accepted_baseline_blocks_sink_key_deletion(self):
        snapshot = ingest.SourceConversionResult(
            documents=({"record": {"Employee_ID": "E-1", "Date": "2026-09-03"}},),
            raw_rows=(), partition_summaries=(), unsafe_reasons=(), invalid_count=0,
        )
        existing = [{"employee_id": "E-2", "attendance_date": "2026-09-03"}]
        with (patch.object(ingest, "ENABLE_POSTGRES", True),
              patch.object(ingest, "ALLOW_ATTENDANCE_SOURCE_REMOVAL", False)):
            reasons = ingest._candidate_acceptance_reasons(snapshot, existing)
            with_history = ingest._candidate_acceptance_reasons(
                snapshot, existing, accepted_partitions=[{
                    "source_path": "attendance/a.csv", "name": "CSV",
                    "domain": "attendance", "status": "valid", "row_count": 2,
                }]
            )
        self.assertIn("refusing deletion", " ".join(reasons))
        self.assertIn("refusing deletion", " ".join(with_history))
        with patch.object(ingest, "ALLOW_ATTENDANCE_SOURCE_REMOVAL", True):
            self.assertFalse(ingest._candidate_acceptance_reasons(snapshot, existing))

    def test_previous_populated_partition_cannot_become_header_only(self):
        prior = [{"source_path": "a.csv", "name": "CSV", "domain": "attendance",
                  "status": "valid", "row_count": 1}]
        current = [{**prior[0], "row_count": 0}]
        self.assertIn("now empty", " ".join(
            ingest._unsafe_attendance_source_changes(prior, current)
        ))
        self.assertEqual(
            ingest._unsafe_attendance_source_changes(prior, current, allow_removal=True),
            [],
        )

    def test_duplicate_candidate_does_not_advance_manifest(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            first = folder / "first.csv"
            second = folder / "second.csv"
            first.write_text("Employee ID,Name,Date\nE-1,First,2026-09-03\n", encoding="utf-8")
            second.write_text("Employee ID,Name,Date\nE-1,Second,2026-09-03\n", encoding="utf-8")
            output = root / "attendance.jsonl"
            invalid = root / "attendance.invalid.jsonl"
            manifest = root / "attendance.sources.json"
            output.write_text("ACCEPTED\n", encoding="utf-8")
            manifest.write_text('{"partitions": []}', encoding="utf-8")
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "JSONL_OUTPUT_PATH", output),
                patch.object(ingest, "INVALID_JSONL_PATH", invalid),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest),
            ):
                rejected = ingest.convert_sources_to_jsonl()
                self.assertEqual(output.read_text(encoding="utf-8"), "ACCEPTED\n")
                self.assertEqual(manifest.read_text(encoding="utf-8"), '{"partitions": []}')
                second.unlink()
                accepted = ingest.convert_sources_to_jsonl()
            self.assertTrue(rejected.unsafe_reasons)
            self.assertFalse(accepted.unsafe_reasons)
            self.assertEqual(len(accepted.documents), 1)

    def test_preview_shares_postgres_disabled_quarantine_gate(self):
        with workspace_temp_directory() as root:
            attendance = root / "attendance"
            attendance.mkdir()
            (attendance / "valid.csv").write_text(
                "Employee ID,Name,Date\nE-1,Example,2026-09-03\n", encoding="utf-8"
            )
            unknown = root / "unknown"
            unknown.mkdir()
            (unknown / "notes.csv").write_text(
                "Code,Notes\nX,unclassified\n", encoding="utf-8"
            )
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "ENABLE_POSTGRES", False),
                patch.object(ingest, "_preview_chroma_rows", return_value=([], "absent", {})),
            ):
                report = ingest.preview_import()
        self.assertIn("PostgreSQL is disabled", " ".join(report["unsafe_reasons"]))

    def test_preview_missing_history_refuses_partial_source_set(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            (folder / "remaining.csv").write_text(
                "Employee ID,Name,Date\nE-1,Example,2026-09-03\n", encoding="utf-8"
            )
            prior = [{"record_id": "attendance:prior", "employee_id": "E-2",
                      "attendance_date": "2026-09-03", "content_hash": "hash"}]
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "settings", replace(
                    ingest.settings, ingestion_state_path=root / "missing.sqlite3"
                )),
                patch.object(ingest, "ENABLE_POSTGRES", False),
                patch.object(ingest, "_preview_chroma_rows",
                             return_value=(prior, "snapshot_inspected", {})),
                patch.object(ingest, "ALLOW_ATTENDANCE_SOURCE_REMOVAL", False),
            ):
                report = ingest.preview_import()
        self.assertIsNone(report["chroma_counts"])
        self.assertIn("refusing deletion", " ".join(report["unsafe_reasons"]))

    def test_corrupt_candidate_manifest_uses_accepted_ledger_baseline(self):
        with workspace_temp_directory() as root:
            from week5.new_implementation.ingestion_state import IngestionLedger
            folder = root / "attendance"
            folder.mkdir()
            (folder / "remaining.csv").write_text(
                "Employee ID,Name,Date\nE-1,Example,2026-09-03\n", encoding="utf-8"
            )
            ledger = IngestionLedger(root / "ledger.sqlite3")
            with ledger.writer() as writer:
                writer.set_accepted_partitions([{
                    "source_path": "attendance/removed.csv", "name": "CSV",
                    "domain": "attendance", "status": "valid", "row_count": 1,
                }])
            manifest = root / "attendance.sources.json"
            manifest.write_text("{corrupt", encoding="utf-8")
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "settings", replace(
                    ingest.settings, ingestion_state_path=ledger.path
                )),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest),
                patch.object(ingest, "JSONL_OUTPUT_PATH", root / "attendance.jsonl"),
                patch.object(ingest, "INVALID_JSONL_PATH", root / "invalid.jsonl"),
            ):
                self.assertEqual(
                    ingest._accepted_partitions_readonly(), ledger.accepted_partitions()
                )
                snapshot = ingest.load_source_snapshot(ledger)
        self.assertIn("disappeared", " ".join(snapshot.unsafe_reasons))

    def test_preview_detects_divergent_chroma_id_without_opening_live_client(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            (folder / "sample.csv").write_text(
                "Employee ID,Name,Date\nE-1,Example,2026-09-03\n", encoding="utf-8"
            )
            chroma_path = root / "chroma"
            chroma_path.mkdir()
            database = chroma_path / "chroma.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript("""
                    CREATE TABLE collections (id TEXT, name TEXT);
                    CREATE TABLE segments (id TEXT, collection TEXT);
                    CREATE TABLE embeddings (id INTEGER, segment_id TEXT, embedding_id TEXT);
                    CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT);
                    INSERT INTO collections VALUES ('c1', 'test');
                    INSERT INTO segments VALUES ('s1', 'c1');
                    INSERT INTO embeddings VALUES (1, 's1', 'attendance:chroma-legacy');
                    INSERT INTO embedding_metadata VALUES (1, 'record_id', 'attendance:chroma-legacy');
                    INSERT INTO embedding_metadata VALUES (1, 'chunk_type', 'attendance_record');
                    INSERT INTO embedding_metadata VALUES (1, 'Employee_ID', 'E-1');
                    INSERT INTO embedding_metadata VALUES (1, 'Date', '2026-09-03');
                """)
                business_hash = sha256(
                    b'{"Date":"2026-09-03","Employee_ID":"E-1","Name":"Example"}'
                ).hexdigest()
                connection.execute(
                    "INSERT INTO embedding_metadata VALUES (1, 'content_hash', ?)",
                    (business_hash,),
                )
                connection.commit()
            before = sha256(database.read_bytes()).hexdigest()
            existing_row = {
                "record_id": "attendance:postgres-legacy", "employee_id": "E-1",
                "attendance_date": "2026-09-03", "content_hash": business_hash,
            }
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", root / "missing.json"),
                patch.object(ingest, "settings", replace(ingest.settings, chroma_db_path=chroma_path)),
                patch.object(ingest, "COLLECTION_NAME", "test"),
                patch.object(ingest, "ENABLE_POSTGRES", True),
                patch.object(ingest, "_existing_postgres_rows", return_value=[existing_row]),
                patch.object(ingest, "create_chroma_client", side_effect=AssertionError("live client opened")),
            ):
                preview = ingest.preview_import()
                existing_row["record_id"] = "attendance:chroma-legacy"
                safe_preview = ingest.preview_import()
            self.assertEqual(before, sha256(database.read_bytes()).hexdigest())
            self.assertIsNone(preview["counts"])
            self.assertIn("multiple IDs", " ".join(preview["unsafe_reasons"]))
            self.assertEqual(preview["chroma_comparison"], "snapshot_inspected")
            self.assertEqual(safe_preview["counts"]["unchanged"], 1)
            self.assertEqual(safe_preview["chroma_counts"]["unchanged"], 1)
            self.assertGreater(safe_preview["chroma_snapshot"]["copied_bytes"], 0)

    def test_preview_reads_sources_without_writing_derived_files(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            (folder / "september.csv").write_text(
                "Employee ID,Name,Date,Shift\nE-1,Example,2026-09-03,Night\n",
                encoding="utf-8",
            )
            output = root / "derived" / "attendance.jsonl"
            invalid = root / "derived" / "attendance.invalid.jsonl"
            manifest = root / "derived" / "attendance.sources.json"
            existing_row = {
                "record_id": "attendance:legacy", "employee_id": "E-1",
                "attendance_date": "2026-09-03", "content_hash": "old-hash",
            }
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "JSONL_OUTPUT_PATH", output),
                patch.object(ingest, "INVALID_JSONL_PATH", invalid),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest),
                patch.object(ingest, "ENABLE_POSTGRES", True),
                patch.object(ingest, "settings", replace(
                    ingest.settings, chroma_db_path=root / "absent-chroma"
                )),
                patch.object(ingest, "_existing_postgres_rows", return_value=[existing_row]),
            ):
                preview = ingest.preview_import()
                existing_row["content_hash"] = sha256(
                    b'{"Date":"2026-09-03","Employee_ID":"E-1","Name":"Example","Shift":"Night"}'
                ).hexdigest()
                unchanged_preview = ingest.preview_import()
            self.assertFalse(output.exists())
            self.assertFalse(invalid.exists())
            self.assertFalse(manifest.exists())
            self.assertEqual(preview["counts"]["changed"], 1)
            self.assertEqual(preview["counts"]["new"], 0)
            self.assertEqual(unchanged_preview["counts"]["unchanged"], 1)
            self.assertEqual(unchanged_preview["counts"]["changed"], 0)
            self.assertEqual(preview["date_coverage"]["min"], "2026-09-03")
            self.assertEqual(
                preview["source_date_coverage"]["attendance/september.csv"]["months"],
                ["2026-09"],
            )

    def test_preview_refuses_removed_counts_when_no_valid_records(self):
        with workspace_temp_directory() as root:
            folder = root / "attendance"
            folder.mkdir()
            (folder / "empty.csv").write_text("Employee ID,Name,Date\n", encoding="utf-8")
            with (
                patch.object(ingest, "KNOWLEDGE_BASE_PATH", root),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", root / "missing.json"),
                patch.object(ingest, "ENABLE_POSTGRES", True),
                patch.object(ingest, "_existing_postgres_rows", return_value=[{
                    "record_id": "legacy", "employee_id": "E-1",
                    "attendance_date": "2026-09-03", "content_hash": "hash",
                }]),
            ):
                preview = ingest.preview_import()
        self.assertIsNone(preview["counts"])
        self.assertIn("zero valid", " ".join(preview["unsafe_reasons"]))

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
