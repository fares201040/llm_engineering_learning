import unittest
from pathlib import Path

from week5.new_evaluation.benchmark import (
    build_benchmark_report,
    percentile,
    summarize_samples,
)


class BenchmarkTests(unittest.TestCase):
    def test_percentiles_and_failures_are_reported(self):
        self.assertEqual(percentile([1, 2, 3, 4], 95), 4)
        summary = summarize_samples([0.1, 0.2, 0.3], failures=1, skipped=2)
        self.assertEqual(summary["median"], 0.2)
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["skipped"], 2)

    @unittest.skipUnless(
        Path(__file__).with_name("dataset_manifest.json").is_file(),
        "private APDC dataset manifest is not available in this checkout",
    )
    def test_report_measures_real_local_components_and_identifies_dataset(self):
        report = build_benchmark_report(warmups=0, runs=1)

        self.assertEqual(report["dataset_state"], "available")
        self.assertIsNotNone(report["dataset_fingerprint"])
        self.assertTrue(
            {
                "plan_compilation",
                "python_grouped_calculation",
                "context_projection",
                "postgres_exact_snapshot",
            }.issubset(report["stages"])
        )
        self.assertEqual(report["provider_stages"]["state"], "skipped")

    def test_report_is_explicit_when_private_manifest_is_unavailable(self):
        missing = Path(__file__).with_name("missing-private-manifest.json")

        report = build_benchmark_report(warmups=0, runs=1, manifest_path=missing)

        self.assertEqual(report["dataset_state"], "private_fixture_unavailable")
        self.assertIsNone(report["dataset_fingerprint"])
