import inspect
import unittest
from unittest.mock import patch

from week5.new_implementation import config


class ConfigParsingTests(unittest.TestCase):
    def test_legacy_settings_positional_constructor_remains_compatible(self):
        with patch.dict("os.environ", {}, clear=True):
            configured = config.Settings.from_environment()

        conversation_fields = {
            "conversation_model",
            "conversation_timeout_seconds",
            "conversation_max_input_chars",
            "conversation_max_output_tokens",
            "conversation_recent_frame_limit",
            "conversation_referent_limit",
            "conversation_unit_limit",
            "conversation_compiled_plan_limit",
            "conversation_employee_binding_limit",
            "max_derived_group_rows",
        }
        parameter_names = tuple(inspect.signature(config.Settings).parameters)
        legacy_names = tuple(
            name for name in parameter_names if name not in conversation_fields
        )
        reconstructed = config.Settings(
            *(getattr(configured, name) for name in legacy_names)
        )

        self.assertEqual(reconstructed.embedding_model, configured.embedding_model)
        self.assertEqual(reconstructed.log_level, configured.log_level)
        self.assertEqual(reconstructed.conversation_model, reconstructed.rag_model)

    def test_integer_parser_uses_default_for_missing_value(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(config.env_int("MISSING_TEST_INT", 17), 17)

    def test_integer_parser_rejects_invalid_values_with_a_clear_error(self):
        with patch.dict("os.environ", {"BAD_TEST_INT": "not-an-int"}, clear=True):
            with self.assertRaisesRegex(ValueError, "BAD_TEST_INT"):
                config.env_int("BAD_TEST_INT", 17)

    def test_float_parser_rejects_non_finite_values(self):
        for raw in ("nan", "inf", "-inf"):
            with (
                self.subTest(raw=raw),
                patch.dict("os.environ", {"BAD_TEST_FLOAT": raw}, clear=True),
                self.assertRaisesRegex(ValueError, "BAD_TEST_FLOAT must be finite"),
            ):
                config.env_float("BAD_TEST_FLOAT", 8.0, minimum=0.1)

        with patch.dict(
            "os.environ", {"CONVERSATION_TIMEOUT_SECONDS": "nan"}, clear=True
        ):
            with self.assertRaisesRegex(
                ValueError, "CONVERSATION_TIMEOUT_SECONDS must be finite"
            ):
                config.Settings.from_environment()

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

    def test_conversation_settings_have_safe_bounded_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = config.Settings.from_environment()

        self.assertEqual(settings.conversation_model, settings.rag_model)
        self.assertEqual(settings.conversation_timeout_seconds, 8.0)
        self.assertEqual(settings.conversation_max_input_chars, 16000)
        self.assertEqual(settings.conversation_max_output_tokens, 1200)
        self.assertEqual(settings.conversation_recent_frame_limit, 8)
        self.assertEqual(settings.conversation_referent_limit, 24)
        self.assertEqual(settings.conversation_unit_limit, 8)
        self.assertEqual(settings.conversation_compiled_plan_limit, 12)
        self.assertEqual(settings.conversation_employee_binding_limit, 20)
        self.assertEqual(settings.max_derived_group_rows, 400)

    def test_conversation_settings_use_existing_parsers_and_environment_overrides(self):
        environment = {
            "RAG_MODEL": "openai/rag-model",
            "CONVERSATION_MODEL": "openai/conversation-model",
            "CONVERSATION_TIMEOUT_SECONDS": "2.5",
            "CONVERSATION_MAX_INPUT_CHARS": "9000",
            "CONVERSATION_MAX_OUTPUT_TOKENS": "700",
            "CONVERSATION_RECENT_FRAME_LIMIT": "5",
            "CONVERSATION_REFERENT_LIMIT": "11",
            "CONVERSATION_UNIT_LIMIT": "4",
            "CONVERSATION_COMPILED_PLAN_LIMIT": "6",
            "CONVERSATION_EMPLOYEE_BINDING_LIMIT": "9",
            "MAX_DERIVED_GROUP_ROWS": "250",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = config.Settings.from_environment()

        self.assertEqual(settings.conversation_model, "openai/conversation-model")
        self.assertEqual(settings.conversation_timeout_seconds, 2.5)
        self.assertEqual(settings.conversation_max_input_chars, 9000)
        self.assertEqual(settings.conversation_max_output_tokens, 700)
        self.assertEqual(settings.conversation_recent_frame_limit, 5)
        self.assertEqual(settings.conversation_referent_limit, 11)
        self.assertEqual(settings.conversation_unit_limit, 4)
        self.assertEqual(settings.conversation_compiled_plan_limit, 6)
        self.assertEqual(settings.conversation_employee_binding_limit, 9)
        self.assertEqual(settings.max_derived_group_rows, 250)

        with patch.dict("os.environ", {"CONVERSATION_UNIT_LIMIT": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "CONVERSATION_UNIT_LIMIT"):
                config.Settings.from_environment()

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
