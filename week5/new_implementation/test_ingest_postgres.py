import unittest
import json
from pathlib import Path
from unittest.mock import patch, mock_open
import uuid

from week5.new_implementation import ingest
from week5.new_implementation import answer
from week5.new_implementation.source_ingestion import RawSourceRow


class PostgresIngestionTests(unittest.TestCase):
    def test_ingestion_exposes_raw_and_snapshot_publish_interfaces(self):
        self.assertTrue(hasattr(ingest, "store_raw_rows_to_postgres"))
        self.assertTrue(hasattr(ingest, "publish_postgres_snapshot"))

    def test_ingestion_stats_track_raw_and_quarantined_rows(self):
        stats = ingest.IngestionStats()
        self.assertEqual(getattr(stats, "raw_rows_seen", None), 0)
        self.assertEqual(getattr(stats, "raw_rows_inserted", None), 0)
        self.assertEqual(getattr(stats, "unknown_partitions", None), 0)
        self.assertEqual(getattr(stats, "quarantined_rows", None), 0)

    def test_raw_rows_are_inserted_into_private_jsonb_envelope(self):
        executions = []

        class Cursor:
            rowcount = 1

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params=None):
                executions.append((" ".join(sql.split()), params))

        class Connection:
            def cursor(self):
                return Cursor()

        raw = RawSourceRow(
            raw_row_key="raw-key",
            source_path="loans/loans.csv",
            source_sha256="a" * 64,
            source_format="csv",
            partition_name="CSV",
            source_row=2,
            domain="unknown",
            status="quarantined",
            reason_code="unrecognized_domain",
            payload_hash="b" * 64,
            payload_json={"headers": ["Amount"], "values": [100]},
        )

        inserted = ingest.store_raw_rows_to_postgres([raw], connection=Connection())

        rendered = "\n".join(sql for sql, _params in executions)
        self.assertIn("CREATE SCHEMA IF NOT EXISTS private_ingestion", rendered)
        self.assertIn("private_ingestion.raw_source_rows", rendered)
        self.assertIn("ON CONFLICT (raw_row_key) DO NOTHING", rendered)
        self.assertEqual(inserted, 1)
        insert_params = next(
            params for sql, params in executions if sql.startswith("INSERT INTO")
        )
        self.assertEqual(insert_params[0], "raw-key")
        self.assertEqual(json.loads(insert_params[-1])["values"], [100])

    def test_attendance_projection_keeps_raw_row_key(self):
        values = ingest._postgres_attendance_values(
            {
                "record": {
                    "Employee_ID": "A10029",
                    "Name": "Example Name",
                    "Date": "2026-09-01",
                    "_raw_row_key": "raw-key",
                },
                "source": "attendance/source.csv",
                "sheet": "CSV",
                "excel_row": 2,
                "jsonl_line": 1,
            }
        )
        self.assertEqual(values.get("raw_row_key"), "raw-key")

    def test_publish_uses_one_connection_for_raw_and_attendance_writes(self):
        calls = []
        connection = object()
        stats = ingest.IngestionStats()
        with (
            patch.object(
                ingest,
                "store_raw_rows_to_postgres",
                side_effect=lambda rows, connection=None: calls.append(
                    ("raw", rows, connection)
                )
                or 1,
            ),
            patch.object(
                ingest,
                "sync_attendance_records_to_postgres",
                side_effect=lambda docs, stats=None, connection=None: calls.append(
                    ("attendance", docs, connection)
                )
                or stats,
            ),
        ):
            result = ingest.publish_postgres_snapshot(
                [{"record": {"Employee_ID": "A10029"}}],
                ["raw-row"],
                "generation-1",
                stats,
                connection=connection,
            )

        self.assertIs(result, stats)
        self.assertEqual([call[0] for call in calls], ["raw", "attendance"])
        self.assertTrue(all(call[2] is connection for call in calls))
        self.assertEqual(stats.raw_rows_seen, 1)
        self.assertEqual(stats.raw_rows_inserted, 1)

    def test_attendance_sync_accepts_shared_transaction_and_updates_provenance(self):
        executions = []

        class Cursor:
            rowcount = 1

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params=None):
                executions.append((" ".join(sql.split()), params))

        class Connection:
            def cursor(self):
                return Cursor()

        document = {
            "type": "attendance",
            "source": "attendance/source.csv",
            "sheet": "CSV",
            "excel_row": 2,
            "jsonl_line": 1,
            "record": {
                "Employee_ID": "A10029",
                "Name": "Example Name",
                "Date": "2026-09-01",
                "_raw_row_key": "raw-key",
            },
        }

        with patch.object(ingest, "ENABLE_POSTGRES", True):
            ingest.sync_attendance_records_to_postgres(
                [document],
                connection=Connection(),
            )

        rendered = "\n".join(sql for sql, _params in executions)
        self.assertIn("ADD COLUMN IF NOT EXISTS raw_row_key TEXT", rendered)
        self.assertIn("raw_row_key = EXCLUDED.raw_row_key", rendered)
        self.assertIn(
            "OR attendance_records.raw_row_key IS DISTINCT FROM EXCLUDED.raw_row_key",
            rendered,
        )

    def test_chunk_mirror_is_skipped_when_pgvector_is_disabled(self):
        stats = ingest.IngestionStats()

        with (
            patch.object(ingest, "ENABLE_POSTGRES", True),
            patch.object(ingest, "POSTGRES_DSN", "postgresql://local/test"),
            patch.object(ingest, "ENABLE_PGVECTOR", False),
        ):
            result = ingest.sync_chunks_to_postgres([], stats)

        self.assertIs(result, stats)
        self.assertEqual(stats.postgres_upserts, 0)

    def test_semantic_backend_requires_pgvector_flag(self):
        with (
            patch.object(answer, "ENABLE_POSTGRES", True),
            patch.object(answer, "POSTGRES_DSN", "postgresql://local/test"),
            patch.object(answer, "ENABLE_PGVECTOR", False),
        ):
            self.assertFalse(answer._postgres_vector_enabled())

        with (
            patch.object(answer, "ENABLE_POSTGRES", True),
            patch.object(answer, "POSTGRES_DSN", "postgresql://local/test"),
            patch.object(answer, "ENABLE_PGVECTOR", True),
        ):
            self.assertTrue(answer._postgres_vector_enabled())


class _FakeCollection:
    def __init__(self):
        self.deleted_ids = []

    def get(self, include):
        return {
            "ids": ["legacy-row"],
            "metadatas": [{}],
        }

    def delete(self, ids):
        self.deleted_ids.extend(ids)


class _FakeChromaClient:
    def __init__(self, collection):
        self.collection = collection

    def get_or_create_collection(self, _name):
        return self.collection


class ChromaIngestionSafetyTests(unittest.TestCase):
    def test_metadata_only_change_does_not_call_embedding_api(self):
        item = {
            "id": "attendance:e-1",
            "record_id": "attendance:e-1",
            "content_hash": "same-content",
            "chunk_type": "attendance_record",
            "text": "same text",
            "metadata": {
                "record_id": "attendance:e-1",
                "content_hash": "same-content",
                "embedding_input_hash": "same-embedding",
                "metadata_hash": "new-metadata",
                "domain": "attendance",
            },
            "token_count": 2,
            "embedding_input_hash": "same-embedding",
            "metadata_hash": "new-metadata",
        }

        class Collection:
            def __init__(self):
                self.updated = []

            def get(self, include, ids=None):
                return {
                    "ids": ["attendance:e-1"],
                    "metadatas": [
                        {
                            "record_id": "attendance:e-1",
                            "content_hash": "same-content",
                            "embedding_input_hash": "same-embedding",
                            "metadata_hash": "old-metadata",
                        }
                    ],
                }

            def update(self, ids, metadatas):
                self.updated.extend(zip(ids, metadatas))

            def delete(self, ids):
                pass

            def count(self):
                return 1

        collection = Collection()
        client = _FakeChromaClient(collection)
        with (
            patch.object(ingest, "_prepare_embedding_items", return_value=[item]),
            patch.object(ingest, "create_chroma_client", return_value=client),
            patch.object(ingest, "_embed_items") as embed,
        ):
            stats = ingest.sync_embeddings_to_chroma([])

        embed.assert_not_called()
        self.assertEqual(len(collection.updated), 1)
        self.assertEqual(stats.metadata_updates, 1)

    def test_partially_invalid_snapshot_stops_before_destructive_sinks(self):
        valid = {"record": {"Employee_ID": "A10029", "Date": "2026-09-01"}}
        snapshot = ingest.SourceConversionResult((valid,), (), (), (), 1)
        with (
            patch.object(ingest, "load_source_snapshot", return_value=snapshot),
            patch.object(ingest, "sync_embeddings_to_chroma") as chroma_sync,
            patch.object(ingest, "sync_attendance_records_to_postgres") as pg_sync,
            patch.object(ingest, "sync_chunks_to_postgres") as vector_sync,
            patch.object(ingest, "ALLOW_INVALID_SNAPSHOT", True),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid attendance"):
                ingest.main()
        chroma_sync.assert_not_called()
        pg_sync.assert_not_called()
        vector_sync.assert_not_called()

    def test_empty_valid_snapshot_stops_before_destructive_sinks(self):
        snapshot = ingest.SourceConversionResult((), (), (), (), 12)
        with (
            patch.object(ingest, "load_source_snapshot", return_value=snapshot),
            patch.object(ingest, "sync_embeddings_to_chroma") as chroma_sync,
            patch.object(ingest, "sync_attendance_records_to_postgres") as pg_sync,
            patch.object(ingest, "sync_chunks_to_postgres") as vector_sync,
            patch.object(ingest, "ALLOW_EMPTY_SNAPSHOT", True),
        ):
            with self.assertRaisesRegex(RuntimeError, "zero valid"):
                ingest.main()
        chroma_sync.assert_not_called()
        pg_sync.assert_not_called()
        vector_sync.assert_not_called()

    def test_embedding_failure_does_not_delete_existing_rows(self):
        collection = _FakeCollection()
        client = _FakeChromaClient(collection)
        chunk = ingest.Result(
            page_content="Employee_ID: E-1",
            metadata={"Employee_ID": "E-1"},
            record_id="attendance:e-1:2026-09-01:abc123",
            content_hash="new-content-hash",
        )

        with (
            patch.object(ingest, "create_chroma_client", return_value=client),
            patch.object(
                ingest,
                "_embed_items",
                side_effect=RuntimeError("embedding service unavailable"),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "embedding service unavailable",
            ):
                ingest.sync_embeddings_to_chroma([chunk])

        self.assertEqual(collection.deleted_ids, [])

    def test_checkpoint_is_ignored_when_stored_hashes_do_not_match(self):
        class Collection(_FakeCollection):
            def __init__(self):
                super().__init__()
                self.upserts = []

            def get(self, include, ids=None):
                if ids is None:
                    return {"ids": [], "metadatas": []}
                return {
                    "ids": ids,
                    "metadatas": [
                        {"embedding_input_hash": "wrong", "metadata_hash": "wrong"}
                        for _ in ids
                    ],
                }

            def upsert(self, **kwargs):
                self.upserts.append(kwargs)

            def count(self):
                return len(self.upserts)

        collection = Collection()
        client = _FakeChromaClient(collection)
        ledger = unittest.mock.MagicMock()
        ledger.is_checkpoint_complete.return_value = True
        ledger.writer.return_value.__enter__.return_value = unittest.mock.Mock()
        chunk = ingest.Result(
            page_content="Employee_ID: A10029",
            metadata={"Employee_ID": "A10029"},
            record_id="attendance:a10029:2026-09-01:abc",
            content_hash="business-hash",
        )
        with (
            patch.object(ingest, "create_chroma_client", return_value=client),
            patch.object(
                ingest,
                "_embed_items",
                return_value={chunk.record_id: [0.1, 0.2]},
            ) as embed,
        ):
            ingest.sync_embeddings_to_chroma(
                [chunk], ledger=ledger, generation="generation-1"
            )

        embed.assert_called_once()
        self.assertEqual(len(collection.upserts), 1)


class SourceChangeDetectionTests(unittest.TestCase):
    def test_unchanged_excel_source_reuses_ledger_rows_without_reopening_workbook(self):
        from week5.new_implementation.ingestion_state import IngestionLedger

        token = uuid.uuid4().hex
        root = Path(__file__).parent
        jsonl_path = root / f"cached-{token}.jsonl"
        invalid_path = root / f"cached-{token}.invalid.jsonl"
        manifest_path = root / f"cached-{token}.sources.json"
        ledger_path = root / f"cached-{token}.sqlite3"
        ledger = IngestionLedger(ledger_path)
        record = {
            "Employee_ID": "A10029",
            "Date": "2026-09-01",
            "_source_file": "unchanged.xlsx",
        }
        with ledger.writer() as writer:
            writer.replace_source_snapshots(
                [("unchanged.xlsx", "same-hash", [record], [])],
                ["unchanged.xlsx"],
            )

        try:
            with (
                patch.object(ingest, "JSONL_OUTPUT_PATH", jsonl_path),
                patch.object(ingest, "INVALID_JSONL_PATH", invalid_path),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest_path),
                patch.object(
                    ingest, "_source_excel_files", return_value=[Path("unchanged.xlsx")]
                ),
                patch.object(
                    ingest,
                    "_source_signature",
                    return_value=[{"path": "unchanged.xlsx", "sha256": "same-hash"}],
                ),
                patch.object(ingest, "load_workbook") as load_workbook,
            ):
                ingest.convert_excel_to_jsonl(ledger=ledger)

            load_workbook.assert_not_called()
            self.assertEqual(json.loads(jsonl_path.read_text()), record)
        finally:
            for path in (jsonl_path, invalid_path, manifest_path):
                if path.exists():
                    path.unlink()
            for suffix in ("", "-wal", "-shm", ".lock"):
                path = Path(f"{ledger_path}{suffix}")
                if path.exists():
                    path.unlink()

    def test_conversion_failure_preserves_previous_snapshot_and_manifest(self):
        root = Path(__file__).parent
        token = uuid.uuid4().hex
        jsonl = root / f"atomic-{token}.jsonl"
        invalid = root / f"atomic-{token}.invalid.jsonl"
        manifest = root / f"atomic-{token}.sources.json"
        ledger_path = root / f"atomic-{token}.sqlite3"
        from week5.new_implementation.ingestion_state import IngestionLedger

        ledger = IngestionLedger(ledger_path)
        jsonl.write_text("old-jsonl\n", encoding="utf-8")
        invalid.write_text("old-invalid\n", encoding="utf-8")
        manifest.write_text("old-manifest\n", encoding="utf-8")
        try:
            with (
                patch.object(ingest, "JSONL_OUTPUT_PATH", jsonl),
                patch.object(ingest, "INVALID_JSONL_PATH", invalid),
                patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest),
                patch.object(
                    ingest, "_source_excel_files", return_value=[Path("source.xlsx")]
                ),
                patch.object(
                    ingest,
                    "_source_signature",
                    return_value=[{"path": "source.xlsx", "sha256": "new-hash"}],
                ),
                patch.object(
                    ingest, "load_workbook", side_effect=RuntimeError("read failed")
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "read failed"):
                    ingest.convert_excel_to_jsonl(ledger=ledger)
            self.assertEqual(jsonl.read_text(encoding="utf-8"), "old-jsonl\n")
            self.assertEqual(invalid.read_text(encoding="utf-8"), "old-invalid\n")
            self.assertEqual(manifest.read_text(encoding="utf-8"), "old-manifest\n")
        finally:
            for path in (jsonl, invalid, manifest):
                if path.exists():
                    path.unlink()
            for suffix in ("", "-wal", "-shm", ".lock"):
                path = Path(f"{ledger_path}{suffix}")
                if path.exists():
                    path.unlink()

    def test_source_signature_changes_when_an_excel_source_changes(self):
        source = Path("attendance.xlsx")
        with patch(
            "builtins.open",
            side_effect=[
                __import__("io").BytesIO(b"version-one"),
                __import__("io").BytesIO(b"version-two"),
            ],
        ):
            first = ingest._source_signature([source])
            second = ingest._source_signature([source])

        self.assertNotEqual(first, second)

    def test_jsonl_refresh_is_required_without_a_valid_manifest(self):
        jsonl_path = Path("week5/new-knowledge-base/attendance/attendance.jsonl")
        manifest_path = Path("missing-attendance.sources.json")

        with (
            patch.object(ingest, "JSONL_OUTPUT_PATH", jsonl_path),
            patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest_path),
        ):
            self.assertTrue(ingest._jsonl_needs_refresh([jsonl_path]))

    def test_legacy_v1_manifest_requires_format_v3_conversion(self):
        manifest_path = Path("attendance.sources.json")
        source_path = Path("attendance.xlsx")
        expected_signature = [{"path": "attendance.xlsx", "sha256": "abc"}]
        manifest = json.dumps({"version": 1, "sources": expected_signature})

        with (
            patch.object(ingest, "JSONL_OUTPUT_PATH", Path("attendance.jsonl")),
            patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest_path),
            patch.object(ingest, "_source_signature", return_value=expected_signature),
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=manifest)),
        ):
            self.assertTrue(ingest._jsonl_needs_refresh([source_path]))

    def test_parser_version_change_requires_reclassification(self):
        manifest_path = Path("attendance.sources.json")
        source_path = Path("attendance.xlsx")
        expected_signature = [{"path": "attendance.xlsx", "sha256": "abc"}]
        manifest = json.dumps(
            {
                "version": ingest.settings.ingestion_format_version,
                "parser_version": ingest.SOURCE_PARSER_VERSION - 1,
                "sources": expected_signature,
            }
        )

        with (
            patch.object(ingest, "JSONL_OUTPUT_PATH", Path("attendance.jsonl")),
            patch.object(ingest, "SOURCE_MANIFEST_PATH", manifest_path),
            patch.object(ingest, "_source_signature", return_value=expected_signature),
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=manifest)),
        ):
            self.assertTrue(ingest._jsonl_needs_refresh([source_path]))

    def test_ingestion_summary_is_emitted_through_the_structured_logger(self):
        with self.assertLogs(ingest.logger, level="INFO") as captured:
            ingest._print_stats(ingest.IngestionStats(jsonl_records=3))

        self.assertTrue(
            any("jsonl_records=3" in message for message in captured.output)
        )

    def test_main_records_each_ingestion_stage_without_external_services(self):
        from week5.new_implementation.ingestion_state import IngestionLedger

        captured_stats = []
        ledger_path = Path(__file__).with_name(
            f"test-main-ledger-{uuid.uuid4().hex}.sqlite3"
        )
        ledger = IngestionLedger(ledger_path)

        try:
            document = {
                "type": "attendance",
                "source": "attendance/test.csv",
                "sheet": "CSV",
                "excel_row": 2,
                "jsonl_line": 1,
                "record": {
                    "Employee_ID": "E-1",
                    "Name": "Example",
                    "Date": "2026-09-01",
                },
            }
            snapshot = ingest.SourceConversionResult((document,), (), (), (), 0)
            with (
                patch.object(ingest, "IngestionLedger", return_value=ledger),
                patch.object(ingest, "load_source_snapshot", return_value=snapshot),
                patch.object(ingest, "create_record_chunks", return_value=[]),
                patch.object(ingest, "create_employee_period_chunks", return_value=[]),
                patch.object(ingest, "sync_embeddings_to_chroma"),
                patch.object(ingest, "publish_postgres_snapshot"),
                patch.object(ingest, "sync_chunks_to_postgres"),
                patch.object(ingest, "_print_stats", side_effect=captured_stats.append),
            ):
                ingest.main()
        finally:
            for suffix in ("", "-wal", "-shm", ".lock"):
                candidate = Path(f"{ledger_path}{suffix}")
                if candidate.exists():
                    candidate.unlink()

        self.assertEqual(len(captured_stats), 1)
        self.assertEqual(
            set(captured_stats[0].stage_seconds),
            {
                "jsonl_read_validation",
                "attendance_chunking",
                "period_chunking",
                "embedding_and_chroma_sync",
                "postgres_attendance_sync",
                "postgres_vector_sync",
            },
        )


if __name__ == "__main__":
    unittest.main()
