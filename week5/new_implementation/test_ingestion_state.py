import unittest
from pathlib import Path
import uuid

from week5.new_implementation.ingestion_state import IngestionLedger


class IngestionLedgerTests(unittest.TestCase):
    def test_source_snapshot_records_parser_partition_and_raw_row_metadata(self):
        path = Path(__file__).with_name(f"source-meta-{uuid.uuid4().hex}.sqlite3")
        ledger = IngestionLedger(path)
        try:
            with ledger.writer() as writer:
                writer.replace_source_snapshots(
                    [
                        (
                            "attendance/a.csv",
                            "source-hash",
                            [{"Employee_ID": "A10029"}],
                            [],
                            3,
                            [{"name": "CSV", "domain": "attendance"}],
                            1,
                        )
                    ],
                    ["attendance/a.csv"],
                )

            snapshot = ledger.source_snapshot(
                "attendance/a.csv",
                "source-hash",
                parser_version=3,
            )

            self.assertEqual(snapshot["parser_version"], 3)
            self.assertEqual(snapshot["partitions"][0]["domain"], "attendance")
            self.assertEqual(snapshot["raw_row_count"], 1)
            self.assertIsNone(
                ledger.source_snapshot(
                    "attendance/a.csv",
                    "source-hash",
                    parser_version=4,
                )
            )
        finally:
            for suffix in ("", "-wal", "-shm", ".lock"):
                candidate = Path(f"{path}{suffix}")
                if candidate.exists():
                    candidate.unlink()

    TEMP_ROOT = Path(__file__).parent

    def setUp(self):
        self.path = self.TEMP_ROOT / f"test-ledger-{uuid.uuid4().hex}.sqlite3"

    def tearDown(self):
        for suffix in ("", "-wal", "-shm", ".lock"):
            path = Path(f"{self.path}{suffix}")
            if path.exists():
                path.unlink()

    def test_generation_records_and_stage_checkpoints_resume(self):
        from week5.new_implementation.ingestion_state import IngestionLedger

        ledger = IngestionLedger(self.path)
        with ledger.writer() as writer:
            generation = writer.begin_generation("source-hash")
            writer.upsert_record(
                generation,
                "r1",
                {"Employee_ID": "A10029", "Date": "2026-09-01"},
                business_hash="business",
                embedding_hash="embedding",
                metadata_hash="metadata",
                source_locator="attendance.xlsx#Sheet1!2",
            )
            writer.checkpoint("chroma", "batch-1", generation)
            writer.activate_generation(generation)

        reopened = IngestionLedger(self.path)
        self.assertEqual(reopened.active_generation(), generation)
        self.assertTrue(
            reopened.is_checkpoint_complete("chroma", "batch-1", generation)
        )
        self.assertEqual(reopened.active_records()[0]["record_id"], "r1")

    def test_writer_is_single_process_and_rolls_back_failed_generation(self):
        from week5.new_implementation.ingestion_state import IngestionLedger

        ledger = IngestionLedger(self.path)
        with self.assertRaises(RuntimeError):
            with ledger.writer() as writer:
                writer.begin_generation("source-hash")
                raise RuntimeError("conversion failed")
        self.assertIsNone(ledger.active_generation())

    def test_same_source_resumes_pending_generation_and_its_checkpoint(self):
        from week5.new_implementation.ingestion_state import IngestionLedger

        ledger = IngestionLedger(self.path)
        with ledger.writer() as writer:
            first = writer.begin_or_resume_generation("same-source")
            writer.checkpoint("chroma", "batch-1", first, payload_hash="items-v1")

        with ledger.writer() as writer:
            resumed = writer.begin_or_resume_generation("same-source")

        self.assertEqual(resumed, first)
        self.assertTrue(
            ledger.is_checkpoint_complete(
                "chroma", "batch-1", resumed, payload_hash="items-v1"
            )
        )
        self.assertFalse(
            ledger.is_checkpoint_complete(
                "chroma", "batch-1", resumed, payload_hash="items-v2"
            )
        )

    def test_different_source_does_not_reuse_pending_generation(self):
        from week5.new_implementation.ingestion_state import IngestionLedger

        ledger = IngestionLedger(self.path)
        with ledger.writer() as writer:
            first = writer.begin_or_resume_generation("source-one")
        with ledger.writer() as writer:
            second = writer.begin_or_resume_generation("source-two")
        self.assertNotEqual(second, first)

    def test_run_lock_rejects_a_second_ingestion_and_releases_cleanly(self):
        from week5.new_implementation.ingestion_state import IngestionLedger

        first = IngestionLedger(self.path)
        second = IngestionLedger(self.path)
        with first.run_lock():
            with self.assertRaisesRegex(RuntimeError, "already active"):
                with second.run_lock():
                    self.fail("a second ingestion acquired the same run lease")

        with second.run_lock():
            pass


if __name__ == "__main__":
    unittest.main()
