import unittest

from pydantic import ValidationError

from week5.new_implementation import ingest


def _document(record, line=1):
    return {
        "type": "attendance",
        "source": "attendance.xlsx",
        "sheet": "September",
        "excel_row": line + 1,
        "jsonl_line": line,
        "record": record,
    }


class SourceInterfaceTests(unittest.TestCase):
    def test_ingestion_exposes_multiformat_source_entrypoints(self):
        self.assertTrue(hasattr(ingest, "_source_files"))
        self.assertTrue(hasattr(ingest, "convert_sources_to_jsonl"))

    def test_prior_attendance_partition_cannot_disappear_or_be_reclassified(self):
        previous = [
            {
                "source_path": "attendance/a.xlsx",
                "name": "Sheet1",
                "domain": "attendance",
                "status": "valid",
            }
        ]

        missing = ingest._unsafe_attendance_source_changes(previous, [])
        reclassified = ingest._unsafe_attendance_source_changes(
            previous,
            [
                {
                    "source_path": "attendance/a.xlsx",
                    "name": "Sheet1",
                    "domain": "unknown",
                    "status": "quarantined",
                }
            ],
        )

        self.assertEqual(len(missing), 1)
        self.assertEqual(len(reclassified), 1)
        self.assertEqual(
            ingest._unsafe_attendance_source_changes(
                previous,
                [],
                allow_removal=True,
            ),
            [],
        )
        self.assertEqual(
            len(
                ingest._unsafe_attendance_source_changes(
                    previous,
                    [
                        {
                            "source_path": "attendance/a.xlsx",
                            "name": "Sheet1",
                            "domain": "unknown",
                            "status": "quarantined",
                        }
                    ],
                    allow_removal=True,
                )
            ),
            1,
        )

    def test_new_unknown_partition_does_not_block_attendance(self):
        current = [
            {
                "source_path": "loans/a.csv",
                "name": "CSV",
                "domain": "unknown",
                "status": "quarantined",
            }
        ]
        self.assertEqual(ingest._unsafe_attendance_source_changes([], current), [])


class NormalizationTests(unittest.TestCase):
    def test_non_finite_numeric_values_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ingest.normalize_attendance_record({"Total_OT": value})

    def test_valid_record_normalizes_dates_times_and_numerics(self):
        normalized = ingest.normalize_attendance_record(
            {
                "Employee_ID": "E-1",
                "Name": "Example",
                "Date": "09/03/2026",
                "Actual_From_Time": "8:05 AM",
                "Total_OT": "1.25",
            }
        )

        self.assertEqual(normalized["Date"], "2026-09-03")
        self.assertEqual(normalized["Actual_From_Time"], "08:05:00")
        self.assertEqual(normalized["Total_OT"], 1.25)

    def test_missing_employee_id_is_rejected(self):
        with self.assertRaises(ValidationError):
            ingest.validate_attendance_record({"Name": "Example", "Date": "2026-09-03"})

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValueError):
            ingest.normalize_attendance_record(
                {
                    "Employee_ID": "E-1",
                    "Name": "Example",
                    "Date": "not-a-date",
                }
            )

    def test_boolean_numeric_value_is_rejected(self):
        with self.assertRaises(ValueError):
            ingest._normalize_numeric_value(True)

    def test_ingestion_stats_can_record_stage_timings(self):
        stats = ingest.IngestionStats(stage_seconds={"jsonl_read": 0.25})

        self.assertEqual(stats.stage_seconds["jsonl_read"], 0.25)


class ChunkingTests(unittest.TestCase):
    def test_duplicate_logical_records_collapse_to_one_chunk(self):
        record = {
            "Employee_ID": "E-1",
            "Name": "Example",
            "Date": "2026-09-03",
            "Status": "Present",
        }
        stats = ingest.IngestionStats()

        chunks = ingest.create_record_chunks(
            [_document(record, 1), _document(dict(record), 2)],
            stats,
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(stats.duplicate_records, 1)

    def test_employee_period_chunk_groups_records_by_month(self):
        first = {
            "Employee_ID": "E-1",
            "Name": "Example",
            "Date": "2026-09-01",
            "Status": "Present",
        }
        second = {**first, "Date": "2026-09-02", "Status": "Absent"}

        chunks = ingest.create_employee_period_chunks(
            [_document(first, 1), _document(second, 2)]
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].metadata["Period"], "2026-09")
        self.assertEqual(chunks[0].metadata["records_count"], 2)
        self.assertEqual(chunks[0].metadata["domain"], "attendance")

    def test_daily_chunk_is_explicitly_attendance_only(self):
        document = _document(
            {
                "Employee_ID": "E-1",
                "Name": "Example",
                "Date": "2026-09-01",
            },
            1,
        )
        document["record"]["_raw_row_key"] = "raw-revision-key"
        chunk = ingest.create_record_chunks([document])[0]

        self.assertEqual(chunk.metadata["domain"], "attendance")
        self.assertEqual(chunk.metadata["raw_row_key"], "raw-revision-key")

    def test_canonical_records_feed_daily_and_period_chunks_consistently(self):
        old = {
            "Employee_ID": "E-1",
            "Name": "Example",
            "Date": "2026-09-01",
            "Status": "Absent",
            "last_Updated_date": "2026-09-01T09:00:00",
        }
        new = {
            **old,
            "Status": "Present",
            "last_Updated_date": "2026-09-02T09:00:00",
        }
        stats = ingest.IngestionStats()

        canonical = ingest.canonicalize_documents(
            [_document(old, 1), _document(new, 2)], stats
        )
        daily = ingest.create_record_chunks(canonical)
        periods = ingest.create_employee_period_chunks(canonical)

        self.assertEqual(stats.duplicate_records, 1)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(len(daily), 1)
        self.assertEqual(periods[0].metadata["records_count"], 1)
        self.assertIn("Status: Present", daily[0].page_content)


if __name__ == "__main__":
    unittest.main()
