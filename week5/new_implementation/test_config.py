import unittest
from unittest.mock import patch

from week5.new_implementation.config import Settings, settings


class ConfigTests(unittest.TestCase):
    def test_online_runtime_has_one_fixed_call_ceiling(self):
        self.assertEqual(settings.llm_turn_provider_call_limit, 8)

    def test_default_models_use_shared_gpt_reference_and_planner(self):
        with patch.dict(
            "os.environ",
            {
                "LLM_MODEL": "",
                "LLM_REFERENCE_MODEL": "",
                "LLM_PLANNER_MODEL": "",
            },
        ):
            configured = Settings.from_environment()

        self.assertEqual(configured.llm_reference_model, "openai/gpt-4.1-mini")
        self.assertEqual(configured.llm_planner_model, "openai/gpt-4.1-mini")

    def test_global_model_remains_an_explicit_all_role_override(self):
        with patch.dict(
            "os.environ",
            {
                "LLM_MODEL": "openai/custom-model",
                "LLM_REFERENCE_MODEL": "",
                "LLM_PLANNER_MODEL": "",
            },
        ):
            configured = Settings.from_environment()

        self.assertEqual(configured.llm_reference_model, "openai/custom-model")
        self.assertEqual(configured.llm_planner_model, "openai/custom-model")

    def test_sql_table_names_are_validated_at_load_time(self):
        self.assertRegex(
            settings.postgres_attendance_table,
            r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$",
        )


if __name__ == "__main__":
    unittest.main()
