import importlib
import unittest

from pydantic import ValidationError


class SemanticSchemaContractTests(unittest.TestCase):
    def test_planner_proposal_is_not_an_executable_plan(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        proposal = schema.PlannerProposal(
            status="ready",
            measure=schema.ProposedMeasureChoice(
                name="distinct_dates", evidence_text="days"
            ),
            answer_contract=schema.AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )

        self.assertNotIsInstance(proposal, schema.QueryPlan)
        self.assertNotIsInstance(proposal, schema.ExecutableQueryPlan)

    def test_planner_models_reject_unknown_keys_and_blank_evidence(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")

        with self.assertRaises(ValidationError):
            schema.PlannerProposal.model_validate(
                {
                    "status": "unsupported",
                    "unsupported_capabilities": ["nested_boolean_filters"],
                    "expected_sql": "SELECT 1",
                }
            )
        with self.assertRaises(ValidationError):
            schema.ProposedFilter(
                field="Status", operator="eq", value="Authorized", evidence_text=" "
            )

    def test_registry_references_are_valid(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")

        for field, definition in schema.FIELD_DEFINITIONS.items():
            self.assertTrue(definition.description, field)
            self.assertTrue(definition.natural_names, field)
            self.assertEqual(bool(definition.operators), definition.filterable, field)
            self.assertTrue(set(definition.operators) <= schema.FILTER_OPERATORS, field)
            for alias in definition.value_aliases:
                if definition.resolution_kind == "closed_value":
                    self.assertIn(alias.canonical_value, definition.closed_values, field)
                else:
                    self.assertEqual(definition.resolution_kind, "catalog", field)

        for concept in schema.VALUE_CONCEPT_DEFINITIONS.values():
            self.assertIn(concept.field, schema.FIELD_DEFINITIONS)
            definition = schema.FIELD_DEFINITIONS[concept.field]
            if definition.resolution_kind == "closed_value":
                self.assertTrue(set(concept.members) <= set(definition.closed_values))
            else:
                self.assertEqual(definition.resolution_kind, "catalog")

        for definition in schema.MEASURE_DEFINITIONS.values():
            if definition.aggregation_field is not None:
                self.assertIn(definition.aggregation_field, schema.FIELD_DEFINITIONS)
                self.assertTrue(
                    schema.FIELD_DEFINITIONS[definition.aggregation_field].aggregatable
                )

        for name, definition in schema.BUSINESS_PREDICATE_DEFINITIONS.items():
            self.assertNotIn(name, definition.incompatible_with)
            for incompatible in definition.incompatible_with:
                self.assertIn(incompatible, schema.BUSINESS_PREDICATE_DEFINITIONS)
            for required in definition.required_filters + definition.incompatible_filters:
                self.assertIn(required.field, schema.FIELD_DEFINITIONS)
                self.assertIn(
                    required.operator,
                    schema.FIELD_DEFINITIONS[required.field].operators,
                )

        for definition in schema.INTERPRETATION_PRESETS.values():
            self.assertIn(definition.measure, schema.MEASURE_DEFINITIONS)
            self.assertTrue(
                set(definition.business_predicates)
                <= set(schema.BUSINESS_PREDICATE_DEFINITIONS)
            )

    def test_planner_schema_exposes_only_planner_metadata(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        rendered = schema.render_planner_schema()

        self.assertIn('"name":"Day_Type"', rendered)
        self.assertIn('"name":"Schedule_From_Date"', rendered)
        self.assertNotIn('"name":"chunk_type"', rendered)
        self.assertNotIn("record_json ->>", rendered)


class ComposableIntentTests(unittest.TestCase):
    def test_scheduled_non_attendance_compiles_compositionally(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        plan = schema.QueryPlan(
            mode="exact",
            search_query="not attended",
            measure="distinct_dates",
            business_predicates=["scheduled_working_day", "not_worked"],
        )

        compiled = schema.compile_business_intent(plan)

        self.assertEqual(compiled.aggregation, "distinct_count")
        self.assertEqual(compiled.aggregation_field, "Date")
        self.assertIn(
            {"field": "Day_Type", "operator": "eq", "value": "Working Day"},
            [condition.model_dump() for condition in compiled.filters],
        )
        self.assertIn(
            {"field": "Total_Worked_Hrs", "operator": "lte", "value": 0.0},
            [condition.model_dump() for condition in compiled.filters],
        )

    def test_measure_and_predicate_definitions_cover_supported_business_intents(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")

        self.assertEqual(
            set(schema.MEASURE_DEFINITIONS),
            {"distinct_dates", "attendance_records", "employees"},
        )
        self.assertEqual(
            set(schema.BUSINESS_PREDICATE_DEFINITIONS),
            {
                "scheduled_working_day",
                "worked",
                "not_worked",
                "absent",
                "authorized",
            },
        )

    def test_business_predicates_compile_to_their_independent_filters(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        expected = {
            "scheduled_working_day": {
                "field": "Day_Type",
                "operator": "eq",
                "value": "Working Day",
            },
            "worked": {
                "field": "Total_Worked_Hrs",
                "operator": "gt",
                "value": 0.0,
            },
            "not_worked": {
                "field": "Total_Worked_Hrs",
                "operator": "lte",
                "value": 0.0,
            },
            "absent": {
                "field": "Exception",
                "operator": "eq",
                "value": "Absent",
            },
            "authorized": {
                "field": "Status",
                "operator": "eq",
                "value": "Authorized",
            },
        }

        for predicate, required_filter in expected.items():
            with self.subTest(predicate=predicate):
                plan = schema.QueryPlan(
                    mode="exact",
                    search_query=predicate,
                    measure="distinct_dates",
                    business_predicates=[predicate],
                )
                compiled = schema.compile_business_intent(plan)
                self.assertIn(
                    required_filter,
                    [condition.model_dump() for condition in compiled.filters],
                )

    def test_row_and_employee_measures_compile_authoritatively(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")

        records = schema.compile_business_intent(
            schema.QueryPlan(
                mode="semantic",
                search_query="records",
                measure="attendance_records",
                aggregation="distinct_count",
                aggregation_field="Employee_ID",
            )
        )
        employees = schema.compile_business_intent(
            schema.QueryPlan(
                mode="semantic",
                search_query="employees",
                measure="employees",
                aggregation="count",
            )
        )

        self.assertEqual((records.mode, records.aggregation), ("exact", "count"))
        self.assertIsNone(records.aggregation_field)
        self.assertEqual(employees.aggregation, "distinct_count")
        self.assertEqual(employees.aggregation_field, "Employee_ID")

    def test_incompatible_work_predicates_are_rejected(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        plan = schema.QueryPlan(
            mode="exact",
            search_query="contradictory",
            measure="distinct_dates",
            business_predicates=["worked", "not_worked"],
        )

        with self.assertRaisesRegex(ValueError, "worked.*not_worked"):
            schema.compile_business_intent(plan)

    def test_worked_and_absent_predicates_are_rejected(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        plan = schema.QueryPlan(
            mode="exact",
            search_query="contradictory",
            measure="distinct_dates",
            business_predicates=["worked", "absent"],
        )

        with self.assertRaisesRegex(ValueError, "worked.*absent"):
            schema.compile_business_intent(plan)

    def test_worked_predicate_rejects_explicit_absent_filter(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        plan = schema.QueryPlan(
            mode="exact",
            search_query="contradictory",
            filters=[
                schema.FilterCondition(field="Exception", operator="eq", value="Absent")
            ],
            measure="distinct_dates",
            business_predicates=["worked"],
        )

        with self.assertRaisesRegex(ValueError, "worked.*Absent"):
            schema.compile_business_intent(plan)

    def test_business_compiler_preserves_unrelated_filters(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        plan = schema.QueryPlan(
            mode="exact",
            search_query="authorized absence",
            filters=[
                schema.FilterCondition(
                    field="Employee_ID", operator="eq", value="A11017"
                )
            ],
            measure="distinct_dates",
            business_predicates=["absent", "authorized"],
        )

        compiled = schema.compile_business_intent(plan)

        self.assertIn(
            {"field": "Employee_ID", "operator": "eq", "value": "A11017"},
            [condition.model_dump() for condition in compiled.filters],
        )

    def test_semantic_attendance_record_question_is_not_forced_to_count(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        plan = schema.QueryPlan(
            mode="semantic",
            search_query="abnormal attendance records",
            aggregation="none",
        )

        compiled = schema.compile_business_intent(plan)

        self.assertEqual(compiled.mode, "semantic")
        self.assertEqual(compiled.aggregation, "none")

    def test_percentage_of_attendance_records_keeps_percentage_semantics(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        percentage_condition = schema.FilterCondition(
            field="Status", operator="eq", value="Authorized"
        )
        plan = schema.QueryPlan(
            mode="exact",
            search_query="attendance percentage",
            aggregation="percentage",
            aggregation_field="Employee_ID",
            percentage_condition=percentage_condition,
        )

        compiled = schema.compile_business_intent(plan)

        self.assertEqual(compiled.aggregation, "percentage")
        self.assertEqual(compiled.percentage_condition, percentage_condition)

    def test_listing_attendance_records_does_not_become_a_count(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        plan = schema.QueryPlan(
            mode="exact",
            search_query="attendance records",
            aggregation="none",
        )

        compiled = schema.compile_business_intent(plan)

        self.assertEqual(compiled.aggregation, "none")

    def test_listing_authorized_records_does_not_become_a_count(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        plan = schema.QueryPlan(
            mode="exact",
            search_query="authorized attendance records",
            aggregation="none",
        )

        compiled = schema.compile_business_intent(plan)

        self.assertEqual(compiled.aggregation, "none")


class RelevantDefinitionTests(unittest.TestCase):
    def test_question_selects_only_relevant_field_definitions(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        selector = getattr(schema, "relevant_field_definitions", None)

        self.assertIsNotNone(selector, "relevant definition selector is missing")
        definitions = selector("How late was the employee?")

        self.assertEqual(set(definitions), {"Lateness_Hrs"})
        self.assertIn("Hours of lateness", definitions["Lateness_Hrs"].description)


class FieldRegistryContractTests(unittest.TestCase):
    LIVE_FIELDS = {
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
        "Name",
        "OT_Authorized",
        "OT_Not_Authorized",
        "OT_Type_1",
        "OT_Type_2",
        "OT_Value_1",
        "OT_Value_2",
        "Organization_Unit",
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

    def test_every_live_field_is_declared(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        self.assertEqual(self.LIVE_FIELDS - set(schema.FIELD_DEFINITIONS), set())

    def test_storage_types_have_declarative_operator_contracts(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        expected = {
            "text": {"eq", "ne", "in", "contains", "starts_with"},
            "date": {"eq", "ne", "gt", "gte", "lt", "lte", "in"},
            "time": {"eq", "ne", "gt", "gte", "lt", "lte", "in"},
            "datetime": {"eq", "ne", "gt", "gte", "lt", "lte", "in"},
            "number": {"eq", "ne", "gt", "gte", "lt", "lte", "in"},
        }
        for definition in schema.FIELD_DEFINITIONS.values():
            self.assertEqual(
                set(definition.operators), expected[definition.storage_type]
            )

    def test_registry_exposes_projection_and_safe_sql_expression(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        country = schema.FIELD_DEFINITIONS["Country"]
        self.assertTrue(country.metadata)
        self.assertTrue(country.context)
        self.assertEqual(country.sql_expression, "country")
        self.assertIn("Authorized", schema.FIELD_DEFINITIONS["Status"].closed_values)


if __name__ == "__main__":
    unittest.main()
