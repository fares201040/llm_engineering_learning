import unittest
from unittest.mock import patch

from week5.new_implementation import config


class ConfigParsingTests(unittest.TestCase):
    def test_integer_parser_uses_default_for_missing_value(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(config.env_int("MISSING_TEST_INT", 17), 17)

    def test_integer_parser_rejects_invalid_values_with_a_clear_error(self):
        with patch.dict("os.environ", {"BAD_TEST_INT": "not-an-int"}, clear=True):
            with self.assertRaisesRegex(ValueError, "BAD_TEST_INT"):
                config.env_int("BAD_TEST_INT", 17)

    def test_settings_read_operational_values_from_environment(self):
        with patch.dict(
            "os.environ",
            {
                "RAG_MODEL": "openai/test-model",
                "RETRIEVAL_K": "9",
                "ENABLE_EMPLOYEE_PERIOD_CHUNKS": "false",
            },
            clear=True,
        ):
            settings = config.Settings.from_environment()

        self.assertEqual(settings.rag_model, "openai/test-model")
        self.assertEqual(settings.semantic_k, 9)
        self.assertFalse(settings.enable_employee_period_chunks)

    def test_chroma_telemetry_defaults_to_disabled(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = config.Settings.from_environment()

        self.assertIs(getattr(settings, "chroma_anonymized_telemetry", None), False)

    def test_chroma_telemetry_can_be_explicitly_enabled(self):
        with patch.dict(
            "os.environ",
            {"CHROMA_ANONYMIZED_TELEMETRY": "true"},
            clear=True,
        ):
            settings = config.Settings.from_environment()

        self.assertIs(getattr(settings, "chroma_anonymized_telemetry", None), True)

    def test_csv_source_glob_defaults_to_csv_files(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = config.Settings.from_environment()

        self.assertEqual(getattr(settings, "source_csv_glob", None), "*.csv")

    def test_multidomain_ingestion_safety_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = config.Settings.from_environment()

        self.assertEqual(getattr(settings, "ingestion_format_version", None), 3)
        self.assertIs(getattr(settings, "allow_attendance_source_removal", None), False)

    def test_boolean_parser_rejects_ambiguous_values(self):
        with patch.dict("os.environ", {"BAD_TEST_BOOL": "maybe"}, clear=True):
            with self.assertRaisesRegex(ValueError, "BAD_TEST_BOOL"):
                config.env_bool("BAD_TEST_BOOL")

    def test_sql_identifier_parser_rejects_executable_fragments(self):
        with patch.dict(
            "os.environ",
            {"BAD_TABLE": "attendance_records; DROP TABLE employees"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "BAD_TABLE"):
                config.env_identifier("BAD_TABLE", "attendance_records")

        with patch.dict(
            "os.environ", {"GOOD_TABLE": "attendance.records_2026"}, clear=True
        ):
            self.assertEqual(
                config.env_identifier("GOOD_TABLE", "attendance_records"),
                "attendance.records_2026",
            )
