from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from week5.new_implementation.colab import cleanup_private_runtime
from week5.new_implementation.colab.prepare_private_runtime import (
    private_database_sql,
    validate_import_facts,
    write_runtime_config,
)
from week5.new_implementation.colab.run_private_eval import (
    consume_restart_flag,
    evaluator_command,
    prepare_report,
)


class PrivateRuntimeTests(unittest.TestCase):
    def test_private_preparer_imports_as_a_standalone_colab_script(self):
        script = Path(__file__).with_name("prepare_private_runtime.py")
        repository_root = Path(__file__).resolve().parents[3]
        environment = os.environ.copy()
        environment["ATTENDANCE_PHASE2_SOURCE_ROOT"] = str(repository_root)
        environment["PYTHONPATH"] = ""
        with TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"import runpy; runpy.run_path({str(script)!r}, run_name='colab_private_prepare')",
                ],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_prepare_validates_payload_before_database_mutation(self):
        from week5.new_implementation.colab import prepare_private_runtime

        with (
            patch.dict(
                os.environ,
                {"ATTENDANCE_PRIVATE_PAYLOAD_SHA256": "a" * 64},
                clear=False,
            ),
            patch.object(
                prepare_private_runtime,
                "validate_payload_archive",
                side_effect=ValueError("invalid payload"),
            ),
            patch.object(prepare_private_runtime, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "invalid payload"):
                prepare_private_runtime.main()
        run.assert_not_called()

    def test_private_runtime_uses_gpt_oss_for_sql_planning(self):
        from week5.new_implementation.colab import prepare_private_runtime

        with TemporaryDirectory() as directory:
            missing_config = Path(directory) / "missing-runtime.json"
            with patch.object(
                prepare_private_runtime,
                "SYNTHETIC_RUNTIME_CONFIG",
                missing_config,
            ):
                settings = prepare_private_runtime._model_settings()

        self.assertEqual(settings["LLM_PLANNER_MODEL"], "ollama_chat/gpt-oss:20b")

    def test_private_runtime_gives_gpt_oss_enough_time_for_full_schema_planning(self):
        from week5.new_implementation.colab import prepare_private_runtime

        with TemporaryDirectory() as directory:
            missing_config = Path(directory) / "missing-runtime.json"
            with patch.object(
                prepare_private_runtime,
                "SYNTHETIC_RUNTIME_CONFIG",
                missing_config,
            ):
                settings = prepare_private_runtime._model_settings()

        self.assertGreaterEqual(
            float(settings["LLM_PLANNER_TIMEOUT_SECONDS"]),
            180.0,
        )

    def test_private_runtime_does_not_truncate_gpt_oss_sql_after_reasoning(self):
        from week5.new_implementation.colab import prepare_private_runtime

        with TemporaryDirectory() as directory:
            missing_config = Path(directory) / "missing-runtime.json"
            with patch.object(
                prepare_private_runtime,
                "SYNTHETIC_RUNTIME_CONFIG",
                missing_config,
            ):
                settings = prepare_private_runtime._model_settings()

        self.assertGreaterEqual(
            int(settings["LLM_PLANNER_MAX_OUTPUT_TOKENS"]),
            6000,
        )

    def test_database_sql_grants_only_select_and_defaults_role_to_read_only(self):
        sql = private_database_sql(
            database="attendance_private_ab12",
            role="attendance_private_reader_cd34",
            password="generated-secret",
        )

        self.assertIn(
            "GRANT SELECT ON TABLE public.attendance_records TO attendance_private_reader_cd34",
            sql,
        )
        self.assertIn(
            "ALTER ROLE attendance_private_reader_cd34 IN DATABASE attendance_private_ab12 SET default_transaction_read_only = on",
            sql,
        )
        self.assertIn(
            "REVOKE CREATE ON SCHEMA public FROM attendance_private_reader_cd34",
            sql,
        )
        self.assertIn("REVOKE CREATE ON SCHEMA public FROM PUBLIC", sql)
        self.assertIn(
            "REVOKE TEMPORARY ON DATABASE attendance_private_ab12 FROM PUBLIC", sql
        )
        self.assertNotIn("GRANT ALL", sql)
        self.assertNotIn("CREATE ON SCHEMA public TO", sql)

    def test_runtime_config_is_written_with_owner_only_permissions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            with patch.object(Path, "chmod") as chmod:
                write_runtime_config(path, {"POSTGRES_READONLY_DSN": "secret"})

            chmod.assert_called_once_with(0o600)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["POSTGRES_READONLY_DSN"],
                "secret",
            )

    def test_runtime_config_never_overwrites_an_existing_secret_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text("existing", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                write_runtime_config(path, {"POSTGRES_READONLY_DSN": "replacement"})

            self.assertEqual(path.read_text(encoding="utf-8"), "existing")

    def test_import_facts_require_the_exact_dataset_oracle(self):
        validate_import_facts("3964|568|2026-09-01|2026-09-07\n")
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            validate_import_facts("3964|567|2026-09-01|2026-09-07\n")

    def test_runner_builds_bounded_resume_command(self):
        command = evaluator_command(
            source=Path("/content/attendance_phase2_source"),
            cases=Path("/content/attendance_private_payload/tests.jsonl"),
            report=Path("/content/attendance-private-eval.json"),
            batch_size=12,
            resume=True,
        )

        self.assertIn("--all", command)
        self.assertEqual(command[-3:], ["--batch-size", "12", "--resume"])
        self.assertIn("/content/attendance_private_payload/tests.jsonl", command)

    def test_runner_can_explicitly_restart_after_runtime_logic_changes(self):
        with TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text('{"completed": 50}', encoding="utf-8")

            self.assertFalse(prepare_report(report, restart=True))
            self.assertFalse(report.exists())
            report.write_text('{"completed": 50}', encoding="utf-8")
            self.assertTrue(prepare_report(report, restart=False))
            self.assertTrue(report.exists())

    def test_restart_flag_is_consumed_from_persistent_colab_environment(self):
        environment = {"PRIVATE_EVAL_RESTART": "true", "KEEP": "yes"}

        self.assertTrue(consume_restart_flag(environment))
        self.assertNotIn("PRIVATE_EVAL_RESTART", environment)
        self.assertEqual(environment["KEEP"], "yes")

    def test_cleanup_is_idempotent_and_removes_only_fixed_private_artifacts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            private_dir = root / "attendance_private_payload"
            private_dir.mkdir()
            (private_dir / "tests.jsonl").write_text("private", encoding="utf-8")
            archive = root / "attendance_private_payload.zip"
            archive.write_bytes(b"private")
            report = root / "attendance-private-eval.json"
            report.write_text("{}", encoding="utf-8")
            config = root / ".attendance_private_runtime.json"
            untouched = root / "keep.txt"
            untouched.write_text("keep", encoding="utf-8")
            with (
                patch.object(cleanup_private_runtime, "PRIVATE_DIRECTORY", private_dir),
                patch.object(cleanup_private_runtime, "PRIVATE_PAYLOAD", archive),
                patch.object(cleanup_private_runtime, "PRIVATE_REPORT", report),
                patch.object(cleanup_private_runtime, "RUNTIME_CONFIG", config),
                patch.object(cleanup_private_runtime, "run") as run,
            ):
                cleanup_private_runtime.main()
                cleanup_private_runtime.main()

            run.assert_not_called()
            self.assertFalse(private_dir.exists())
            self.assertFalse(archive.exists())
            self.assertFalse(report.exists())
            self.assertTrue(untouched.is_file())


if __name__ == "__main__":
    unittest.main()
