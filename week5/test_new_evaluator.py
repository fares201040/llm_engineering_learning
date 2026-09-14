import unittest
from types import SimpleNamespace
from unittest.mock import patch


class ApdcEvaluationDashboardTests(unittest.TestCase):
    def dashboard(self):
        try:
            from week5 import new_evaluator
        except ImportError as exc:
            self.fail(f"The APDC evaluation dashboard is missing: {exc}")
        return new_evaluator

    def test_case_limit_zero_means_all_cases(self):
        dashboard = self.dashboard()

        self.assertEqual(dashboard.limit_cases([1, 2, 3], 0), [1, 2, 3])

    def test_case_limit_selects_only_the_requested_prefix(self):
        dashboard = self.dashboard()

        self.assertEqual(dashboard.limit_cases([1, 2, 3], 2), [1, 2])

    def test_behavior_evaluation_reports_failed_checks(self):
        dashboard = self.dashboard()
        cases = [
            SimpleNamespace(question="First", category="identity"),
            SimpleNamespace(question="Second", category="worked_days"),
        ]
        results = [
            SimpleNamespace(
                model_dump=lambda: {
                    "plan_ok": True,
                    "employee_ids_ok": True,
                    "matched_count_ok": True,
                }
            ),
            SimpleNamespace(
                model_dump=lambda: {
                    "plan_ok": True,
                    "employee_ids_ok": False,
                    "matched_count_ok": True,
                }
            ),
        ]

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_behavior",
                side_effect=results,
            ),
        ):
            summary, category_rows, details = dashboard.run_behavior_evaluation(
                0, progress=None
            )

        self.assertIn("1 of 2 passed", summary)
        self.assertEqual(
            category_rows.to_dict("records"),
            [
                {"Category": "identity", "Pass Rate": 100.0},
                {"Category": "worked_days", "Pass Rate": 0.0},
            ],
        )
        self.assertEqual(details.loc[1, "Failed checks"], "employee_ids_ok")
        self.assertNotIn("Question", details.columns)

    def test_retrieval_evaluation_aggregates_apdc_metrics(self):
        dashboard = self.dashboard()
        cases = [
            SimpleNamespace(question="First", category="identity"),
            SimpleNamespace(question="Second", category="identity"),
        ]
        results = [
            SimpleNamespace(mrr=1.0, ndcg=0.8, keyword_coverage=100.0),
            SimpleNamespace(mrr=0.5, ndcg=0.6, keyword_coverage=50.0),
        ]

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_retrieval",
                side_effect=results,
            ),
        ):
            summary, category_rows, details = dashboard.run_retrieval_evaluation(
                0, progress=None
            )

        self.assertIn("MRR: 0.7500", summary)
        self.assertIn("nDCG: 0.7000", summary)
        self.assertIn("Keyword coverage: 75.0%", summary)
        self.assertEqual(
            category_rows.to_dict("records"),
            [{"Category": "identity", "Average MRR": 0.75}],
        )
        self.assertEqual(len(details), 2)
        self.assertNotIn("Question", details.columns)

    def test_answer_evaluation_reports_errors_without_exception_details(self):
        dashboard = self.dashboard()
        cases = [SimpleNamespace(question="Private question", category="identity")]

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_answer",
                side_effect=RuntimeError("database password must not leak"),
            ),
        ):
            summary, category_rows, details = dashboard.run_answer_evaluation(
                0, progress=None
            )

        self.assertIn("0 completed, 1 failed", summary)
        self.assertNotIn("password", summary.lower())
        self.assertTrue(category_rows.empty)
        self.assertEqual(details.loc[0, "Status"], "Failed (RuntimeError)")
        self.assertNotIn("password", details.to_string().lower())
        self.assertNotIn("Question", details.columns)

    def test_answer_evaluation_reports_privacy_safe_failure_causes(self):
        dashboard = self.dashboard()
        cases = [SimpleNamespace(question="Private question", category="identity")]
        result = SimpleNamespace(accuracy=4.0, completeness=5.0, relevance=5.0)
        diagnostic = SimpleNamespace(cause="evaluator_expectation_drift")

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_answer_with_diagnostic",
                return_value=(result, diagnostic),
            ),
        ):
            _summary, _category_rows, details = dashboard.run_answer_evaluation(
                0, progress=None
            )

        self.assertEqual(details.loc[0, "Cause"], "evaluator_expectation_drift")
        self.assertNotIn("Question", details.columns)
        self.assertNotIn("Private question", details.to_string())

    def test_dataset_verification_renders_manifest_summary(self):
        dashboard = self.dashboard()
        manifest = {
            "record_count": 3964,
            "employee_count": 568,
            "date_min": "2026-09-01",
            "date_max": "2026-09-07",
            "fingerprint": "abc123",
        }

        with patch.object(
            dashboard.apdc_evaluation, "verify_dataset", return_value=manifest
        ):
            rendered = dashboard.run_dataset_verification()

        self.assertIn("Dataset verified", rendered)
        self.assertIn("3,964 records", rendered)
        self.assertIn("568 employees", rendered)
        self.assertIn("2026-09-01 through 2026-09-07", rendered)


if __name__ == "__main__":
    unittest.main()
