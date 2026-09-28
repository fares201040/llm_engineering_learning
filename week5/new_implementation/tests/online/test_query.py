from __future__ import annotations

import json
import unittest

from pydantic import BaseModel, ValidationError

from week5.new_implementation.online import context
from week5.new_implementation.online.context import (
    DatabaseColumn,
    DatabaseContext,
    DatabaseDateCoverage,
    DatabaseTable,
    SharedModelContext,
)
from week5.new_implementation.online.provider import (
    CallBudget,
    call_structured,
    call_text,
    log_layer_output,
)


def database_context():
    return DatabaseContext(
        server_version="17.2",
        tables=(
            DatabaseTable(
                schema_name="public",
                table_name="attendance_records",
                object_type="BASE TABLE",
                description="Authoritative attendance records.",
                date_coverage=DatabaseDateCoverage(
                    field="attendance_date",
                    available_start="2026-09-01",
                    available_end="2026-09-07",
                ),
                columns=(
                    DatabaseColumn(
                        name="employee_id",
                        data_type="text",
                        nullable=False,
                        description="Authoritative employee identifier.",
                    ),
                    DatabaseColumn(
                        name="total_worked_hrs",
                        data_type="double precision",
                        nullable=True,
                        description="Actual total worked hours.",
                    ),
                ),
            ),
        ),
    )


class StructuredProbe(BaseModel):
    value: str


class DatabaseContextTests(unittest.TestCase):
    def test_database_categories_are_discovered_as_exact_standard_values(self):
        class Result:
            def fetchall(self):
                return [
                    {"value": "Finance"},
                    {"value": "Human Resource"},
                    {"value": "Operations"},
                ]

        class Connection:
            def execute(self, _query):
                return Result()

        values = context._discover_standard_values(
            Connection(),
            schema_name="public",
            table_name="attendance_records",
            column_name="department",
        )

        self.assertEqual(values, ("Finance", "Human Resource", "Operations"))

    def test_high_cardinality_text_is_not_sent_as_standard_values(self):
        class Result:
            def fetchall(self):
                return [{"value": f"value-{index}"} for index in range(101)]

        class Connection:
            def execute(self, _query):
                return Result()

        values = context._discover_standard_values(
            Connection(),
            schema_name="public",
            table_name="attendance_records",
            column_name="department",
        )

        self.assertEqual(values, ())

    def test_json_field_standard_values_follow_database_observations(self):
        fields = {
            field.name: field
            for field in context._record_json_fields(
                {
                    "day_type": ("Observed Schedule",),
                    "exception": ("Observed Exception",),
                }
            )
        }
        self.assertEqual(fields["Day_Type"].standard_values, ("Observed Schedule",))
        self.assertEqual(fields["Exception"].standard_values, ("Observed Exception",))
        self.assertEqual(fields["Status"].standard_values, ())

    def test_all_record_json_source_fields_have_queryable_semantic_descriptions(self):
        expected_fields = {
            "Actual_From_Date",
            "Actual_From_Time",
            "Actual_To_Date",
            "Actual_To_Time",
            "Country",
            "Date",
            "Day",
            "Day_Type",
            "Department",
            "Early_Out_Hrs",
            "Employee_ID",
            "Employee_Remarks",
            "Exception",
            "From_Date",
            "From_Time",
            "Grade",
            "Gradeset",
            "Job",
            "Lateness_Hrs",
            "Leave_Hrs",
            "Leave_Type",
            "Holiday_Type",
            "Name",
            "OT_Authorized",
            "OT_Not_Authorized",
            "OT_Type_1",
            "OT_Type_2",
            "OT_Type_3",
            "OT_Type_4",
            "OT_Type_5",
            "OT_Value_1",
            "OT_Value_2",
            "Organization_Unit",
            "Overbreak_Hrs",
            "OT_Value_3",
            "OT_Value_4",
            "OT_Value_5",
            "Pending_with",
            "Position",
            "Post_OT_End_Time",
            "Post_OT_Start_Time",
            "Post_OT_hrs",
            "Pre_OT_End_Time",
            "Pre_OT_Start_Time",
            "Regular_Units",
            "Schedule_From_Date",
            "Schedule_From_Time",
            "Schedule_To_Date",
            "Schedule_To_Time",
            "Shift",
            "Status",
            "To_Date",
            "To_Time",
            "Total_OT",
            "Total_Worked_Hrs",
            "Work_Location",
            "last_Updated_date",
            "pre_ot_hrs",
        }
        fields = {item.name: item for item in context._record_json_fields()}

        self.assertEqual(set(fields), expected_fields)
        self.assertTrue(all(item.description for item in fields.values()))
        self.assertEqual(
            fields["Actual_From_Time"].sql_text_expression,
            "record_json ->> 'Actual_From_Time'",
        )
        self.assertIn(
            "Admin clerks cannot modify", fields["Actual_From_Time"].description
        )
        self.assertIn(
            "admin clerk may modify it manually", fields["From_Time"].description
        )
        self.assertIn("same swipe", fields["From_Time"].description)
        self.assertIn("worked-time calculations", fields["From_Time"].description)
        self.assertIn("positive number proves", fields["Total_Worked_Hrs"].description)
        self.assertIn("leave, absent", fields["Total_Worked_Hrs"].description)
        self.assertEqual(
            fields["Actual_From_Time"].json_type,
            "ISO time string (HH:MM:SS)",
        )
        self.assertEqual(fields["Total_Worked_Hrs"].json_type, "number")

    def test_every_physical_attendance_column_has_a_specific_description(self):
        expected_columns = {
            "record_id",
            "content_hash",
            "employee_id",
            "name",
            "organization_unit",
            "country",
            "work_location",
            "department",
            "position",
            "job",
            "gradeset",
            "grade",
            "attendance_date",
            "day",
            "day_type",
            "holiday_type",
            "shift",
            "status",
            "exception",
            "total_worked_hrs",
            "lateness_hrs",
            "early_out_hrs",
            "overbreak_hrs",
            "regular_units",
            "pre_ot_hrs",
            "post_ot_hrs",
            "total_ot",
            "ot_authorized",
            "ot_not_authorized",
            "leave_type",
            "leave_hrs",
            "last_updated_at",
            "source_file",
            "source_sheet",
            "source_excel_row",
            "source_jsonl_line",
            "search_text",
            "record_json",
            "raw_row_key",
            "synced_at",
        }

        self.assertEqual(set(context._COLUMN_DESCRIPTIONS), expected_columns)
        self.assertTrue(all(context._COLUMN_DESCRIPTIONS.values()))
        self.assertNotIn(
            "Application attendance field.", context._COLUMN_DESCRIPTIONS.values()
        )
        with self.assertRaisesRegex(RuntimeError, "invented_column"):
            context._require_column_descriptions(("employee_id", "invented_column"))
        context._require_column_descriptions(
            ("employee_id", "invented_column"),
            {"invented_column": "Meaning defined by the database schema."},
        )
        self.assertEqual(
            context._column_description(
                "day_type", "Updated meaning supplied by PostgreSQL COMMENT."
            ),
            "Updated meaning supplied by PostgreSQL COMMENT.",
        )
        self.assertIn(
            "total_worked_hrs > 0",
            context._COLUMN_DESCRIPTIONS["total_worked_hrs"],
        )
        self.assertIn(
            "not proof that work occurred",
            context._COLUMN_DESCRIPTIONS["day_type"],
        )
        self.assertIn("'OFF Day (ZAS)'", context._COLUMN_DESCRIPTIONS["day_type"])
        self.assertIn("include both", context._COLUMN_DESCRIPTIONS["day_type"])
        self.assertIn(
            "not proof that work occurred",
            context._COLUMN_DESCRIPTIONS["status"],
        )
        self.assertIn(
            "explicitly marked absent, filter exception = 'Absent'",
            context._COLUMN_DESCRIPTIONS["exception"],
        )
        self.assertIn(
            "IS DISTINCT FROM 'Absent'",
            context._COLUMN_DESCRIPTIONS["exception"],
        )
        self.assertIn("all four pairs", context._COLUMN_DESCRIPTIONS["record_json"])
        self.assertIn(
            "One department can contain multiple work-location groups",
            context._COLUMN_DESCRIPTIONS["work_location"],
        )
        json_descriptions = {
            field.name: field.description for field in context._record_json_fields()
        }
        self.assertIn("IS DISTINCT FROM", json_descriptions["From_Date"])
        self.assertIn("Actual_To_Time", json_descriptions["To_Time"])
        self.assertIn(
            "One department can contain multiple work-location groups",
            json_descriptions["Work_Location"],
        )

    def test_context_uses_exact_physical_metadata_and_is_immutable(self):
        context = database_context()

        self.assertEqual(context.engine, "PostgreSQL")
        self.assertEqual(context.tables[0].columns[1].name, "total_worked_hrs")
        self.assertFalse(context.tables[0].columns[0].nullable)
        self.assertEqual(context.tables[0].date_coverage.available_end, "2026-09-07")
        with self.assertRaises(ValidationError):
            context.tables[0].columns[0].name = "invented"  # type: ignore[misc]

    def test_shared_payload_contains_question_history_and_reused_context(self):
        shared = SharedModelContext(
            current_question="Show total hours.",
            updated_request="Request:\nShow total hours.",
            conversation_history=({"role": "user", "content": "hours"},),
            trusted_context={"verified_turns": []},
            database_context=database_context(),
        )

        self.assertEqual(
            set(shared.model_payload()),
            {
                "current_question",
                "as_of_date",
                "last_calendar_month",
                "updated_request",
                "previous_verified_turn",
                "request_relationship",
                "subject_relationship",
                "resolved_employee_ids",
                "required_date_scope",
                "requested_period_vs_observed_rows",
                "request_has_date_period",
                "conversation_history",
                "trusted_context",
                "scope_provenance",
                "database_type",
                "database_context",
                "observed_date_ranges",
                "calendar_month_date_extent",
                "resolution_statement",
            },
        )
        serialized = json.dumps(shared.model_payload())
        self.assertNotIn("semantic_contracts", serialized)
        self.assertEqual(
            list(shared.model_payload())[-9:],
            [
                "current_question",
                "updated_request",
                "previous_verified_turn",
                "request_relationship",
                "subject_relationship",
                "resolved_employee_ids",
                "required_date_scope",
                "requested_period_vs_observed_rows",
                "request_has_date_period",
            ],
        )
        self.assertEqual(
            shared.model_payload()["current_question"], "Show total hours."
        )
        self.assertEqual(shared.model_payload()["calendar_month_date_extent"], [])
        self.assertIsNone(shared.model_payload()["last_calendar_month"])
        september = shared.model_copy(
            update={"as_of_date": "2026-09-25", "request_has_date_period": True}
        )
        self.assertEqual(
            september.model_payload()["last_calendar_month"],
            {"start": "2026-08-01", "end": "2026-08-31"},
        )
        month_extents = september.model_payload()["calendar_month_date_extent"]
        self.assertEqual(
            [item["period"] for item in month_extents],
            ["previous_calendar_month", "current_calendar_month_to_as_of_date"],
        )
        self.assertTrue(month_extents[0]["observed_extent_starts_after_period_start"])
        self.assertTrue(month_extents[1]["observed_extent_ends_before_period_end"])
        self.assertEqual(
            shared.model_payload()["conversation_history"],
            [{"role": "user", "content": "hours"}],
        )
        self.assertIn("total_worked_hrs", serialized)
        self.assertIn('"date_coverage"', serialized)
        self.assertIn('"available_start": "2026-09-01"', serialized)
        self.assertNotIn("password", serialized.casefold())

    def test_plain_text_provider_preserves_sql_and_disables_transport_retries(self):
        captured = {}

        class Message:
            content = "WITH x AS (SELECT 1) SELECT * FROM x"

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        def complete(**kwargs):
            captured.update(kwargs)
            return Response()

        sql = call_text(
            stage="sql_planner",
            model="test",
            system="system",
            payload={"request": "test"},
            budget=CallBudget(limit=1),
            timeout=1,
            max_output_tokens=100,
            completion_fn=complete,
        )

        self.assertEqual(sql, Message.content)
        self.assertEqual(captured["num_retries"], 0)
        self.assertNotIn("reasoning_effort", captured)

    def test_ollama_text_provider_disables_reasoning(self):
        captured = {}

        class Message:
            content = "SELECT 1"

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        def complete(**kwargs):
            captured.update(kwargs)
            return Response()

        call_text(
            stage="sql_planner",
            model="ollama_chat/qwen3.5:2b",
            system="system",
            payload={"current_question": "test", "conversation_history": []},
            budget=CallBudget(limit=1),
            timeout=1,
            max_output_tokens=100,
            completion_fn=complete,
        )

        self.assertEqual(captured["reasoning_effort"], "none")
        self.assertEqual(captured["temperature"], 0)
        self.assertEqual(captured["num_ctx"], 32768)

    def test_ollama_gpt_oss_uses_supported_reasoning_effort(self):
        captured = {}

        class Message:
            content = "SELECT 1"

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        def complete(**kwargs):
            captured.update(kwargs)
            return Response()

        call_text(
            stage="sql_planner",
            model="ollama_chat/gpt-oss:20b",
            system="system",
            payload={"current_question": "test", "conversation_history": []},
            budget=CallBudget(limit=1),
            timeout=1,
            max_output_tokens=100,
            completion_fn=complete,
        )

        self.assertEqual(captured["reasoning_effort"], "medium")
        self.assertEqual(captured["temperature"], 0)
        self.assertEqual(captured["num_ctx"], 32768)

    def test_ollama_structured_provider_disables_reasoning(self):
        captured = {}

        class Message:
            content = '{"value":"ok"}'

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        def complete(**kwargs):
            captured.update(kwargs)
            return Response()

        result = call_structured(
            stage="reference",
            model="ollama_chat/qwen3.5:2b",
            system="system",
            payload={"current_question": "test", "conversation_history": []},
            response_model=StructuredProbe,
            budget=CallBudget(limit=1),
            timeout=1,
            max_output_tokens=100,
            completion_fn=complete,
        )

        self.assertEqual(result.value, "ok")
        self.assertEqual(captured["reasoning_effort"], "none")
        self.assertEqual(captured["temperature"], 0)
        self.assertEqual(captured["num_ctx"], 32768)

    def test_layer_logger_emits_summary_and_exact_debug_output(self):
        with self.assertLogs(
            "week5.new_implementation.online.layers", level="DEBUG"
        ) as captured:
            log_layer_output(
                "employee_resolution",
                {
                    "employees": [{"employee_id": "A1", "name": "Wail Ali"}],
                    "dsn": "postgresql://reader:secret@localhost/db",
                    "password": "secret-two",
                    "api_key": "secret-three",
                },
                attempt=1,
            )

        combined = "\n".join(captured.output)
        self.assertIn("layer=employee_resolution status=completed attempt=1", combined)
        self.assertIn('"employee_id":"A1"', combined)
        self.assertNotIn("reader:secret@", combined)
        self.assertNotIn("secret-two", combined)
        self.assertNotIn("secret-three", combined)
        debug_line = next(
            line for line in captured.output if "attendance_layer_output" in line
        )
        payload = json.loads(debug_line.split(" output=", 1)[1])
        self.assertEqual(payload["password"], "***")
        self.assertEqual(payload["api_key"], "***")


if __name__ == "__main__":
    unittest.main()
