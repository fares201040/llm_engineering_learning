import importlib
import unittest

from pydantic import ValidationError


class SemanticSchemaContractTests(unittest.TestCase):
    def test_projection_is_rows_only_and_has_unique_fields(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        for shape, projection in (("scalar", ["Date"]), ("rows", ["Date", "Date"])):
            with self.subTest(shape=shape), self.assertRaises(ValidationError):
                schema.PlannerProposal(
                    status="ready",
                    projection=[dict(field=f, evidence_text=f) for f in projection],
                    answer_contract=dict(shape=shape, unit="value"),
                )

    def test_filter_operator_controls_value_shape_at_model_boundary(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        invalid = (
            {"field": "Status", "operator": "in", "value": "Authorized"},
            {"field": "Status", "operator": "eq", "value": ["Authorized"]},
            {"field": "Status", "operator": "in", "value": []},
        )

        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                schema.ProposedFilter(evidence_text="Authorized", **values)

    def test_percentage_of_rows_does_not_require_an_identity_field(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        calculation = schema.ProposedCalculation(
            operation="percentage",
            field=None,
            percentage_condition=schema.ProposedFilter(
                field="Status",
                operator="eq",
                value="Authorized",
                evidence_text="Status equal to Authorized",
            ),
            evidence_text="percentage",
        )

        self.assertIsNone(calculation.field)

        result = schema.PercentageCalculationResult(
            field=None,
            numerator=2,
            denominator=4,
            value=50.0,
        )
        self.assertIsNone(result.field)

    def test_grouping_shape_is_bounded_and_has_no_duplicates(self):
        schema = importlib.import_module("week5.new_implementation.attendance_schema")
        contract = schema.AnswerContract(
            shape="grouped", unit="records", subject_field=None, grain=[]
        )
        for fields in (
            ["Department", "Department"],
            ["Department", "Shift", "Work_Location"],
        ):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                schema.PlannerProposal(
                    status="ready",
                    group_by=[
                        schema.ProposedFieldChoice(field=field, evidence_text=field)
                        for field in fields
                    ],
                    answer_contract=contract,
                )

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
                    self.assertIn(
                        alias.canonical_value, definition.closed_values, field
                    )
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
            for required in (
                definition.required_filters + definition.incompatible_filters
            ):
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
