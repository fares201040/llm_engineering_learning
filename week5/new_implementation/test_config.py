import unittest

from week5.new_implementation.config import settings


class ConfigTests(unittest.TestCase):
    def test_online_runtime_has_one_fixed_call_ceiling(self):
        self.assertEqual(settings.llm_turn_provider_call_limit, 11)

    def test_sql_table_names_are_validated_at_load_time(self):
        self.assertRegex(
            settings.postgres_attendance_table,
            r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$",
        )


if __name__ == "__main__":
    unittest.main()
