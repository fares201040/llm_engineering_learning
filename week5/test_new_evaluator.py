import gc
import unittest
import warnings
from types import SimpleNamespace
from unittest.mock import patch

from week5.new_evaluation.test import TestQuestion


def dashboard_case(question, category="count", token="case-1"):
    return SimpleNamespace(
        question=question,
        category=category,
        model_dump=lambda mode="python": {
            "question": question,
            "category": category,
            "token": token,
        },
    )


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
        self.assertEqual(details["Question"].tolist(), ["First", "Second"])

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
        self.assertEqual(details["Question"].tolist(), ["First", "Second"])

    def test_answer_evaluation_reports_errors_without_exception_details(self):
        dashboard = self.dashboard()
        cases = [SimpleNamespace(question="Private question", category="identity")]

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_answer_with_diagnostic",
                side_effect=RuntimeError("database password must not leak"),
            ),
        ):
            summary, category_rows, details = dashboard.run_answer_evaluation(
                0, progress=None
            )

        self.assertIn("0 completed, 1 failed", summary)
        self.assertNotIn("password", summary.lower())
        self.assertTrue(category_rows.empty)
        self.assertEqual(details.loc[0, "Status"], "Failed (evaluation_error)")
        self.assertNotIn("password", details.to_string().lower())
        self.assertEqual(details.loc[0, "Question"], "Private question")
        self.assertEqual(details.loc[0, "Cause"], "evaluation_runtime_failure")

    def test_behavior_evaluation_does_not_misclassify_runtime_errors_as_provider(self):
        dashboard = self.dashboard()
        cases = [SimpleNamespace(question="Synthetic question", category="identity")]

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_behavior",
                side_effect=RuntimeError("database failure detail"),
            ),
        ):
            _summary, _categories, details = dashboard.run_behavior_evaluation(
                0, progress=None
            )

        self.assertEqual(details.loc[0, "Cause"], "evaluation_runtime_failure")
        self.assertNotIn("database failure detail", details.to_string())

    def test_answer_evaluation_does_not_guess_timeout_stage(self):
        dashboard = self.dashboard()
        cases = [SimpleNamespace(question="Synthetic question", category="identity")]

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_answer_with_diagnostic",
                side_effect=TimeoutError("ambiguous timeout detail"),
            ),
        ):
            _summary, _categories, details = dashboard.run_answer_evaluation(
                0, progress=None
            )

        self.assertEqual(details.loc[0, "Cause"], "evaluation_runtime_failure")
        self.assertNotIn("ambiguous timeout detail", details.to_string())

    def test_answer_evaluation_reports_failure_causes_and_questions(self):
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
        self.assertEqual(details.loc[0, "Question"], "Private question")

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

    def test_bird_evaluation_aggregates_exact_and_component_scores(self):
        dashboard = self.dashboard()
        cases = [dashboard_case("First"), dashboard_case("Second", token="case-2")]
        results = [
            SimpleNamespace(
                status="evaluated",
                execution_accuracy=1.0,
                components={"matched_count": True, "calculation": True},
                reason=None,
            ),
            SimpleNamespace(
                status="evaluated",
                execution_accuracy=0.0,
                components={"matched_count": True, "calculation": False},
                reason=None,
            ),
        ]

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "verify_dataset",
                return_value={"fingerprint": "dataset-a"},
            ),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_bird_case",
                side_effect=results,
            ),
        ):
            summary, components, details = dashboard.run_bird_evaluation(
                0, False, progress=None
            )

        self.assertIn("BIRD-style Contract EX: 50.0%", summary)
        self.assertIn("evaluated 2 · skipped 0 · failed 0", summary)
        self.assertEqual(
            components.to_dict("records"),
            [
                {
                    "Component": "Matched count",
                    "Passed": 2,
                    "Evaluated": 2,
                    "Score %": 100.0,
                },
                {
                    "Component": "Calculation",
                    "Passed": 1,
                    "Evaluated": 2,
                    "Score %": 50.0,
                },
            ],
        )
        self.assertEqual(details["Question"].tolist(), ["First", "Second"])
        self.assertEqual(details["Status"].tolist(), ["Passed", "Failed"])
        self.assertIn("Contract EX", details.columns)
        self.assertNotIn("BIRD EX", details.columns)

    def test_bird_unavailable_does_not_report_a_zero_score(self):
        dashboard = self.dashboard()
        case = dashboard_case("Unsupported")
        skipped = SimpleNamespace(
            status="skipped",
            execution_accuracy=None,
            components={},
            reason="no_verified_output_contract",
        )

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=[case]),
            patch.object(
                dashboard.apdc_evaluation,
                "verify_dataset",
                return_value={"fingerprint": "dataset-a"},
            ),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_bird_case",
                return_value=skipped,
            ),
        ):
            summary, components, details = dashboard.run_bird_evaluation(
                0, False, progress=None
            )

        self.assertIn("BIRD-style Contract EX: unavailable", summary)
        self.assertNotIn("0.0%", summary)
        self.assertIn("evaluated 0 · skipped 1 · failed 0", summary)
        self.assertTrue(components.empty)
        self.assertEqual(details.loc[0, "Status"], "Skipped")

    def test_bird_dataset_verification_failure_is_explicit_and_scores_nothing(self):
        dashboard = self.dashboard()
        case = dashboard_case("Must not execute")

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=[case]),
            patch.object(
                dashboard.apdc_evaluation,
                "verify_dataset",
                side_effect=RuntimeError("postgresql://employee:secret@private"),
            ),
            patch.object(dashboard.apdc_evaluation, "evaluate_bird_case") as evaluate,
        ):
            summary, components, details = dashboard.run_bird_evaluation(
                0, False, progress=None
            )

        self.assertIn("BIRD-style Contract EX: unavailable", summary)
        self.assertIn("Dataset verification failed", summary)
        self.assertNotIn("secret", summary)
        self.assertTrue(components.empty)
        self.assertTrue(details.empty)
        evaluate.assert_not_called()

    def test_bird_missing_private_corpus_is_explicit_and_scores_nothing(self):
        dashboard = self.dashboard()

        with (
            patch.object(
                dashboard.apdc_evaluation,
                "load_tests",
                side_effect=FileNotFoundError("private/path/tests.jsonl"),
            ),
            patch.object(dashboard.apdc_evaluation, "verify_dataset") as verify_dataset,
        ):
            summary, components, details = dashboard.run_bird_evaluation(
                0, False, progress=None
            )

        self.assertIn("BIRD-style Contract EX: unavailable", summary)
        self.assertIn("Evaluation corpus is unavailable", summary)
        self.assertNotIn("private/path", summary)
        self.assertIn("evaluated 0 · skipped 0 · failed 0", summary)
        self.assertTrue(components.empty)
        self.assertTrue(details.empty)
        verify_dataset.assert_not_called()

    def test_bird_runtime_failure_is_sanitized_and_not_scored(self):
        dashboard = self.dashboard()
        case = dashboard_case("Analyst-visible question")

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=[case]),
            patch.object(
                dashboard.apdc_evaluation,
                "verify_dataset",
                return_value={"fingerprint": "dataset-a"},
            ),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_bird_case",
                side_effect=RuntimeError(
                    "employee Alice prompt SQL SELECT secret params history"
                ),
            ),
        ):
            summary, _components, details = dashboard.run_bird_evaluation(
                0, False, progress=None
            )

        self.assertIn("evaluated 0 · skipped 0 · failed 1", summary)
        rendered = details.to_string().lower()
        self.assertIn("analyst-visible question", rendered)
        for private_value in ("alice", "select", "secret", "params", "history"):
            self.assertNotIn(private_value, rendered)
        self.assertEqual(details.loc[0, "Status"], "Failed (evaluation_error)")

    def test_bird_controlled_nonexecution_is_scored_and_reports_only_safe_cause(self):
        dashboard = self.dashboard()
        dashboard.clear_bird_cache()
        case = dashboard_case("Analyst-visible question", token="controlled-failure")
        result = SimpleNamespace(
            status="evaluated",
            execution_accuracy=0.0,
            components={"matched_count": False, "record_ids": False},
            reason="controlled_nonexecution",
        )

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=[case]),
            patch.object(
                dashboard.apdc_evaluation,
                "verify_dataset",
                return_value={"fingerprint": "dataset-a"},
            ),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_bird_case",
                return_value=result,
            ),
        ):
            summary, components, details = dashboard.run_bird_evaluation(
                0, False, progress=None
            )

        self.assertIn("BIRD-style Contract EX: 0.0%", summary)
        self.assertIn("evaluated 1 · skipped 0 · failed 0", summary)
        self.assertEqual(details.loc[0, "Question"], "Analyst-visible question")
        self.assertEqual(details.loc[0, "Status"], "Failed")
        self.assertEqual(details.loc[0, "Cause"], "controlled_nonexecution")
        self.assertEqual(components["Passed"].tolist(), [0, 0])

    def test_bird_cache_reuses_verified_result_and_force_recompute_bypasses_it(self):
        dashboard = self.dashboard()
        dashboard.clear_bird_cache()
        case = dashboard_case("Cached", token="stable")
        result = SimpleNamespace(
            status="evaluated",
            execution_accuracy=1.0,
            components={"matched_count": True},
            reason=None,
        )

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=[case]),
            patch.object(
                dashboard.apdc_evaluation,
                "verify_dataset",
                return_value={"fingerprint": "dataset-a"},
            ),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_bird_case",
                return_value=result,
            ) as evaluate,
        ):
            dashboard.run_bird_evaluation(0, False, progress=None)
            _, _, cached_details = dashboard.run_bird_evaluation(
                0, False, progress=None
            )
            dashboard.run_bird_evaluation(0, True, progress=None)

        self.assertEqual(evaluate.call_count, 2)
        self.assertTrue(cached_details.loc[0, "Cached"])

    def test_bird_cache_key_changes_with_dataset_fingerprint(self):
        dashboard = self.dashboard()
        dashboard.clear_bird_cache()
        case = dashboard_case("Dataset drift", token="stable")
        result = SimpleNamespace(
            status="evaluated",
            execution_accuracy=1.0,
            components={"matched_count": True},
            reason=None,
        )

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=[case]),
            patch.object(
                dashboard.apdc_evaluation,
                "verify_dataset",
                side_effect=[
                    {"fingerprint": "dataset-a"},
                    {"fingerprint": "dataset-b"},
                ],
            ),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_bird_case",
                return_value=result,
            ) as evaluate,
        ):
            dashboard.run_bird_evaluation(0, False, progress=None)
            dashboard.run_bird_evaluation(0, False, progress=None)

        self.assertEqual(evaluate.call_count, 2)

    def test_bird_cache_key_distinguishes_omitted_and_explicit_default_contracts(self):
        dashboard = self.dashboard()
        required = {
            "question": "How many verified records are there?",
            "keywords": ["records"],
            "reference_answer": "There are no records.",
            "category": "count",
        }
        omitted = TestQuestion(**required)
        explicit = TestQuestion(**required, expected_record_ids=[])

        self.assertEqual(
            omitted.model_dump(mode="json"),
            explicit.model_dump(mode="json"),
        )
        self.assertNotEqual(
            dashboard._bird_cache_key("dataset-a", omitted),
            dashboard._bird_cache_key("dataset-a", explicit),
        )

    def test_bird_cache_key_changes_with_safe_runtime_fingerprint(self):
        dashboard = self.dashboard()
        case = dashboard_case("Runtime drift", token="stable")

        with patch.object(
            dashboard,
            "_bird_runtime_fingerprint",
            side_effect=["runtime-a", "runtime-b"],
        ):
            first = dashboard._bird_cache_key("dataset-a", case)
            second = dashboard._bird_cache_key("dataset-a", case)

        self.assertNotEqual(first, second)

    def test_bird_runtime_fingerprint_uses_only_safe_execution_flags(self):
        dashboard = self.dashboard()
        first_settings = SimpleNamespace(
            rag_model="public-model-a",
            conversation_model="public-conversation-a",
            constraint_candidate_limit=4,
            enable_postgres=True,
            enable_pgvector=False,
        )
        second_settings = SimpleNamespace(
            rag_model="public-model-b",
            conversation_model="public-conversation-a",
            constraint_candidate_limit=4,
            enable_postgres=True,
            enable_pgvector=False,
        )
        private_dsn = "postgresql://private-user:private-pass@private-host/private-db"

        with (
            patch.object(dashboard.apdc_evaluation, "settings", first_settings),
            patch.object(dashboard.apdc_evaluation, "POSTGRES_DSN", private_dsn),
        ):
            first = dashboard._bird_runtime_fingerprint()
        with (
            patch.object(dashboard.apdc_evaluation, "settings", second_settings),
            patch.object(dashboard.apdc_evaluation, "POSTGRES_DSN", private_dsn),
        ):
            second = dashboard._bird_runtime_fingerprint()

        self.assertNotEqual(first, second)
        self.assertRegex(first, r"\A[0-9a-f]{64}\Z")
        for private in ("private-user", "private-pass", "private-host", "private-db"):
            self.assertNotIn(private, first)

    def test_bird_mixed_pass_skip_and_runtime_failure_denominator(self):
        dashboard = self.dashboard()
        dashboard.clear_bird_cache()
        cases = [
            dashboard_case("Pass", token="pass"),
            dashboard_case("Skip", token="skip"),
            dashboard_case("Runtime", token="runtime"),
        ]
        outcomes = [
            SimpleNamespace(
                status="evaluated",
                execution_accuracy=1.0,
                components={"matched_count": True},
                reason=None,
            ),
            SimpleNamespace(
                status="skipped",
                execution_accuracy=None,
                components={},
                reason="no_verified_output_contract",
            ),
            RuntimeError("private prompt SQL parameters"),
        ]

        with (
            patch.object(dashboard.apdc_evaluation, "load_tests", return_value=cases),
            patch.object(
                dashboard.apdc_evaluation,
                "verify_dataset",
                return_value={"fingerprint": "dataset-a"},
            ),
            patch.object(
                dashboard.apdc_evaluation,
                "evaluate_bird_case",
                side_effect=outcomes,
            ),
        ):
            summary, components, details = dashboard.run_bird_evaluation(
                0, False, progress=None
            )

        self.assertIn("BIRD-style Contract EX: 100.0%", summary)
        self.assertIn("evaluated 1 · skipped 1 · failed 1", summary)
        self.assertEqual(components["Evaluated"].tolist(), [1])
        self.assertEqual(details["Question"].tolist(), ["Pass", "Skip", "Runtime"])
        self.assertEqual(
            details["Status"].tolist(),
            ["Passed", "Skipped", "Failed (evaluation_error)"],
        )
        self.assertNotIn("private", details.to_string().lower())

    def test_app_adds_bird_as_fifth_tab_without_changing_existing_tab_order(self):
        dashboard = self.dashboard()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            app = dashboard.build_app()
            try:
                config = app.get_config_file()
            finally:
                app.close()
                del app
                gc.collect()
        tabs = [
            component["props"]["label"]
            for component in config["components"]
            if component["type"] == "tabitem"
        ]

        self.assertEqual(
            tabs, ["Dataset", "Behavior", "Retrieval", "Answer Quality", "BIRD"]
        )


if __name__ == "__main__":
    unittest.main()
