import unittest

from week5.new_implementation import plan_compiler
from week5.new_implementation.attendance_schema import (
    AnswerContract,
    PlannerProposal,
    ProposedCalculation,
    ProposedFieldChoice,
    ProposedFilter,
    ProposedMeasureChoice,
    ProposedPredicateChoice,
    ProposedNameHint,
    QueryPlan,
)
from week5.new_implementation.plan_compiler import (
    PLAN_INVARIANTS,
    CompilationContext,
    compile_proposal,
    revalidate_executable_plan,
)
from week5.new_implementation.semantic_resolution import (
    ResolutionContext,
    SemanticFact,
    detect_semantic_facts,
)


class ExecutableChoiceCoverageTests(unittest.TestCase):
    def test_revalidation_rejects_deleted_filter_and_aggregate(self):
        question = "Count Authorized records"
        result = self.compile(
            question,
            measure=dict(name="attendance_records", evidence_text="records"),
            business_predicates=[dict(name="authorized", evidence_text="Authorized")],
            answer_contract=dict(shape="scalar", unit="records"),
        )
        self.assertTrue(result.ready, result.violations)
        resolution = ResolutionContext({})
        context = CompilationContext(
            question, detect_semantic_facts(question, resolution), resolution
        )
        for changes in ({"filters": []}, {"measure": None, "aggregation": "none"}):
            with self.subTest(changes=changes):
                altered = result.executable_plan.model_copy(update=changes)
                checked = revalidate_executable_plan(
                    altered, context, result.provenance
                )
                self.assertFalse(checked.ready)
        without_filter_provenance = tuple(
            p for p in result.provenance if p.target_kind != "filter"
        )
        checked = revalidate_executable_plan(
            result.executable_plan.model_copy(update={"filters": []}),
            context,
            without_filter_provenance,
        )
        self.assertFalse(checked.ready)

    def test_grouped_percentage_is_rejected_before_execution(self):
        result = self.compile(
            "Top 3 Departments by percentage of records with Status equal to Authorized",
            calculation=dict(
                operation="percentage",
                evidence_text="percentage",
                percentage_condition=dict(
                    field="Status",
                    operator="eq",
                    value="Authorized",
                    evidence_text="Status equal to Authorized",
                ),
            ),
            group_by=[dict(field="Department", evidence_text="Departments")],
            order_by=dict(field="value", direction="desc", evidence_text="Top 3"),
            limit=dict(value=3, evidence_text="Top 3"),
            answer_contract=dict(shape="grouped", unit="percentage"),
        )
        self.assertFalse(result.ready)
        self.assertIn("unsupported_capability", {v.code for v in result.violations})

    def test_independent_unbound_sum_is_not_hidden_by_named_count(self):
        result = self.compile(
            "Count records and calculate total score",
            measure=dict(name="attendance_records", evidence_text="records"),
            answer_contract=dict(shape="scalar", unit="records"),
        )
        self.assertFalse(result.ready)
        self.assertIn("unsupported_capability", {v.code for v in result.violations})

    def test_unknown_projection_remainder_without_from_is_rejected(self):
        result = self.compile(
            "Show Date and FavoriteColor",
            answer_contract=dict(shape="rows", unit="value"),
        )
        self.assertFalse(result.ready)
        self.assertIn("unsupported_capability", {v.code for v in result.violations})

    def test_trailing_order_key_is_not_silently_dropped(self):
        result = self.compile(
            "Show records ordered by Date ascending and Name descending",
            order_by=dict(
                field="Date", direction="asc", evidence_text="Date ascending"
            ),
            answer_contract=dict(shape="rows", unit="value"),
        )
        self.assertFalse(result.ready)
        self.assertIn("unsupported_capability", {v.code for v in result.violations})

    def test_first_and_last_without_order_basis_are_rejected(self):
        for position in ("first", "last"):
            with self.subTest(position=position):
                result = self.compile(
                    f"Show {position} 3 records",
                    limit=dict(value=3, evidence_text=f"{position} 3"),
                    answer_contract=dict(shape="rows", unit="value"),
                )
                self.assertFalse(result.ready)
                self.assertIn(
                    "unsupported_capability", {v.code for v in result.violations}
                )

    def test_unbound_calculation_cannot_disappear_from_request(self):
        for question in (
            "Calculate average attendance records",
            "What percentage have Status equal to Authorized?",
        ):
            with self.subTest(question=question):
                result = self.compile(
                    question, answer_contract=dict(shape="rows", unit="value")
                )
                self.assertFalse(result.ready)
                self.assertIn(
                    "unsupported_capability", {v.code for v in result.violations}
                )

    def test_unresolved_executable_clauses_do_not_compile_empty_rows(self):
        for question in (
            "Show records ordered by Date",
            "Show Date and Salary from records",
            "Rank Departments by attendance",
        ):
            with self.subTest(question=question):
                result = self.compile(
                    question, answer_contract=dict(shape="rows", unit="value")
                )
                self.assertFalse(result.ready)
                self.assertIn(
                    "unsupported_capability", {v.code for v in result.violations}
                )

    def test_explicit_order_and_direction_compile(self):
        result = self.compile(
            "Show records ordered by Date ascending",
            order_by=dict(
                field="Date", direction="asc", evidence_text="Date ascending"
            ),
            answer_contract=dict(shape="rows", unit="value"),
        )
        self.assertTrue(result.ready, result.violations)

    def test_revalidation_rejects_changed_executable_order_direction(self):
        question = "Show records ordered by Date ascending"
        resolution = ResolutionContext({})
        context = CompilationContext(
            question,
            (
                SemanticFact(
                    kind="order_by",
                    field="Date",
                    direction="asc",
                    evidence_text="Date ascending",
                    origin="question",
                    strength="strong",
                ),
            ),
            resolution,
        )
        proposal = PlannerProposal.model_validate(
            dict(
                status="ready",
                order_by=dict(
                    field="Date", direction="asc", evidence_text="Date ascending"
                ),
                answer_contract=dict(shape="rows", unit="value"),
            )
        )
        result = compile_proposal(proposal, context)
        self.assertTrue(result.ready, result.violations)
        changed = result.executable_plan.model_copy(update={"order_direction": "desc"})
        checked = revalidate_executable_plan(changed, context, result.provenance)
        self.assertFalse(checked.ready)

    def test_narrative_contract_cannot_execute_structured_subset(self):
        result = self.compile(
            "Count records",
            measure=dict(name="attendance_records", evidence_text="records"),
            answer_contract=dict(shape="narrative", unit="records"),
        )
        self.assertFalse(result.ready)

    def test_arbitrary_field_cannot_become_percentage_denominator(self):
        result = self.compile(
            "What percentage of records have Status equal to Authorized?",
            calculation=dict(
                operation="percentage",
                field="Department",
                evidence_text="percentage",
                percentage_condition=dict(
                    field="Status",
                    operator="eq",
                    value="Authorized",
                    evidence_text="Authorized",
                ),
            ),
            answer_contract=dict(
                shape="scalar", unit="percentage", subject_field="Department"
            ),
        )
        self.assertFalse(result.ready)

    def compile(self, question, **choices):
        resolution = ResolutionContext({})
        proposal = PlannerProposal.model_validate(dict(status="ready", **choices))
        return compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

    def test_field_mention_does_not_authorize_grouping(self):
        result = self.compile(
            "Count records with Department equal to Finance",
            measure=dict(name="attendance_records", evidence_text="records"),
            group_by=[dict(field="Department", evidence_text="Department")],
            answer_contract=dict(shape="grouped", unit="records", grain=["Department"]),
        )
        self.assertIn("ungrounded_constraint", {v.code for v in result.violations})

    def test_order_direction_must_match_request(self):
        result = self.compile(
            "Show records ordered by Date ascending",
            order_by=dict(
                field="Date", direction="desc", evidence_text="Date ascending"
            ),
            answer_contract=dict(shape="rows", unit="value"),
        )
        self.assertFalse(result.ready)

    def test_grouped_ranking_has_grounded_derived_order_and_limit(self):
        result = self.compile(
            "Top 3 Departments by count of records",
            measure=dict(name="attendance_records", evidence_text="records"),
            group_by=[dict(field="Department", evidence_text="Departments")],
            order_by=dict(field="value", direction="desc", evidence_text="Top 3"),
            limit=dict(value=3, evidence_text="Top 3"),
            answer_contract=dict(shape="grouped", unit="records", grain=["Department"]),
        )
        self.assertTrue(result.ready, result.violations)
        self.assertEqual(result.executable_plan.order_by, "value")

    def test_record_projection_is_preserved(self):
        result = self.compile(
            "Show Date and Status from records",
            projection=[
                dict(field="Date", evidence_text="Date"),
                dict(field="Status", evidence_text="Status"),
            ],
            answer_contract=dict(shape="rows", unit="value", grain=["Date", "Status"]),
        )
        self.assertTrue(result.ready, result.violations)
        self.assertEqual(result.executable_plan.projection, ["Date", "Status"])

    def test_omitted_requested_projection_and_limit_are_rejected(self):
        for question in ("Show Date and Status from records", "Show first 3 records"):
            with self.subTest(question=question):
                result = self.compile(
                    question, answer_contract=dict(shape="rows", unit="value")
                )
                self.assertFalse(result.ready)
                self.assertIn("uncovered_fact", {v.code for v in result.violations})

    def test_percentage_numerator_cannot_become_denominator_filter(self):
        condition = dict(
            field="Status",
            operator="eq",
            value="Authorized",
            evidence_text="Status equal to Authorized",
        )
        result = self.compile(
            "What percentage of records have Status equal to Authorized?",
            filters=[condition],
            calculation=dict(
                operation="percentage",
                evidence_text="percentage",
                percentage_condition=condition,
            ),
            answer_contract=dict(shape="scalar", unit="percentage"),
        )
        self.assertFalse(result.ready)

    def test_partial_boolean_calculation_and_narrative_are_rejected(self):
        for question in (
            "Count records where Status is Authorized or Date is 2026-09-03",
            "Count records and calculate median worked hours",
            "Count records and explain why employees were absent",
        ):
            with self.subTest(question=question):
                result = self.compile(
                    question,
                    measure=dict(name="attendance_records", evidence_text="records"),
                    answer_contract=dict(shape="scalar", unit="records"),
                )
                self.assertFalse(result.ready)
                self.assertIn(
                    "unsupported_capability", {v.code for v in result.violations}
                )


class CompleteAnswerContractTests(unittest.TestCase):
    def test_revalidation_rejects_structurally_malformed_executable_plan(self):
        question = "Count attendance records by Department."
        resolution = ResolutionContext({})
        context = CompilationContext(
            question,
            detect_semantic_facts(question, resolution),
            resolution,
        )
        proposal = PlannerProposal(
            status="ready",
            measure=ProposedMeasureChoice(
                name="attendance_records", evidence_text="attendance records"
            ),
            group_by=[
                ProposedFieldChoice(field="Department", evidence_text="Department")
            ],
            answer_contract=AnswerContract(
                shape="grouped",
                unit="records",
                subject_field=None,
                grain=["Department"],
            ),
        )
        compiled = compile_proposal(proposal, context)
        self.assertTrue(compiled.ready, compiled.violations)

        malformed_plans = (
            compiled.executable_plan.model_copy(update={"answer_contract": None}),
            compiled.executable_plan.model_copy(
                update={"group_by": ["Department", "Department"]}
            ),
        )
        for malformed in malformed_plans:
            with self.subTest(malformed=malformed.model_dump()):
                checked = revalidate_executable_plan(
                    malformed, context, compiled.provenance
                )
                self.assertFalse(checked.ready)
                self.assertIn(
                    "invalid_schema",
                    {violation.code for violation in checked.violations},
                )

    def test_contract_grain_uses_effective_employee_grouping(self):
        plan = QueryPlan(
            mode="exact",
            search_query="total overtime by employee name",
            aggregation="sum",
            aggregation_field="Total_OT",
            group_by=["Name"],
        )

        self.assertEqual(
            plan_compiler.derive_expected_answer_contract(plan).model_dump(),
            {
                "shape": "grouped",
                "unit": "hours",
                "subject_field": "Total_OT",
                "grain": ["Employee_ID", "Name", "Total_OT"],
            },
        )

    def test_expected_contract_is_derived_from_the_complete_operation(self):
        cases = (
            (
                QueryPlan(
                    mode="exact",
                    search_query="records",
                    measure="attendance_records",
                    aggregation="count",
                ),
                dict(shape="scalar", unit="records", subject_field=None, grain=[]),
            ),
            (
                QueryPlan(
                    mode="exact",
                    search_query="employee percentage",
                    aggregation="percentage",
                    aggregation_field="Employee_ID",
                    percentage_condition=dict(
                        field="Status", operator="eq", value="Authorized"
                    ),
                ),
                dict(
                    shape="scalar",
                    unit="percentage",
                    subject_field="Employee_ID",
                    grain=["Employee_ID"],
                ),
            ),
            (
                QueryPlan(
                    mode="exact",
                    search_query="average lateness by department",
                    aggregation="average",
                    aggregation_field="Lateness_Hrs",
                    group_by=["Department"],
                ),
                dict(
                    shape="grouped",
                    unit="hours",
                    subject_field="Lateness_Hrs",
                    grain=["Department", "Lateness_Hrs"],
                ),
            ),
            (
                QueryPlan(
                    mode="exact",
                    search_query="selected columns",
                    projection=["Date", "Status"],
                ),
                dict(
                    shape="rows",
                    unit="value",
                    subject_field=None,
                    grain=["Date", "Status"],
                ),
            ),
            (
                QueryPlan(mode="semantic", search_query="attendance anomaly"),
                dict(shape="narrative", unit="value", subject_field=None, grain=[]),
            ),
        )

        for plan, expected in cases:
            with self.subTest(plan=plan.search_query):
                self.assertEqual(
                    plan_compiler.derive_expected_answer_contract(plan).model_dump(),
                    expected,
                )

    def test_revalidation_rejects_changed_contract_grain(self):
        question = "How many attendance records?"
        resolution = ResolutionContext({})
        context = CompilationContext(
            question,
            detect_semantic_facts(question, resolution),
            resolution,
        )
        proposal = PlannerProposal(
            status="ready",
            measure=ProposedMeasureChoice(
                name="attendance_records", evidence_text="attendance records"
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="records", subject_field=None, grain=[]
            ),
        )
        compiled = compile_proposal(proposal, context)
        self.assertTrue(compiled.ready, compiled.violations)

        changed = compiled.executable_plan.model_copy(
            update={
                "answer_contract": AnswerContract(
                    shape="scalar",
                    unit="records",
                    subject_field=None,
                    grain=["Date"],
                )
            }
        )
        checked = revalidate_executable_plan(changed, context, compiled.provenance)

        self.assertFalse(checked.ready)
        self.assertIn("answer_contract_mismatch", {v.code for v in checked.violations})


class TemporalCompositionCompilerTests(unittest.TestCase):
    def test_prior_constraints_and_temporal_bound_compile_together(self):
        for question, predicates, other_filters in (
            (
                "Count Authorized records before 2026-09-03",
                [
                    ProposedPredicateChoice(
                        name="authorized", evidence_text="Authorized"
                    )
                ],
                [],
            ),
            (
                "Count records for Department Human Resources before 2026-09-03",
                [],
                [
                    ProposedFilter(
                        field="Department",
                        operator="eq",
                        value="Human Resources",
                        evidence_text="Human Resources",
                    )
                ],
            ),
        ):
            with self.subTest(question=question):
                resolution = ResolutionContext({"Department": ("Human Resources",)})
                proposal = PlannerProposal(
                    status="ready",
                    filters=other_filters
                    + [
                        ProposedFilter(
                            field="Date",
                            operator="lt",
                            value="2026-09-03",
                            evidence_text="before 2026-09-03",
                        )
                    ],
                    business_predicates=predicates,
                    measure=ProposedMeasureChoice(
                        name="attendance_records", evidence_text="records"
                    ),
                    answer_contract=AnswerContract(shape="scalar", unit="records"),
                )
                result = compile_proposal(
                    proposal,
                    CompilationContext(
                        question,
                        detect_semantic_facts(question, resolution),
                        resolution,
                    ),
                )
                self.assertTrue(result.ready, result.violations)

    def test_name_hint_and_complete_temporal_negation_compile_together(self):
        question = "Count records for morgan river not before 2026-09-03"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            name_hint=ProposedNameHint(
                value="morgan river", evidence_text="morgan river"
            ),
            filters=[
                ProposedFilter(
                    field="Date",
                    operator="gte",
                    value="2026-09-03",
                    evidence_text="not before 2026-09-03",
                )
            ],
            measure=ProposedMeasureChoice(
                name="attendance_records", evidence_text="records"
            ),
            answer_contract=AnswerContract(shape="scalar", unit="records"),
        )
        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )
        self.assertTrue(result.ready, result.violations)


class PlanCompilerTests(unittest.TestCase):
    def test_fieldless_unsupported_predicate_operator_rejects_measure_only_plan(self):
        question = "How many attendance records match Authorized?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            measure=ProposedMeasureChoice(
                name="attendance_records", evidence_text="attendance records"
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="records", subject_field=None, grain=[]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn(
            "unsupported_capability", {item.code for item in result.violations}
        )

    def test_unsupported_or_malformed_constraint_rejects_measure_only_plan(self):
        cases = (
            "How many days have status matches Authorized?",
            "How many days have Date equal to 2026-02-30?",
            "How many days have worked hours above bananas?",
        )

        for question in cases:
            with self.subTest(question=question):
                resolution = ResolutionContext({})
                proposal = PlannerProposal(
                    status="ready",
                    measure=ProposedMeasureChoice(
                        name="distinct_dates", evidence_text="days"
                    ),
                    answer_contract=AnswerContract(
                        shape="scalar",
                        unit="dates",
                        subject_field="Date",
                        grain=["Date"],
                    ),
                )

                result = compile_proposal(
                    proposal,
                    CompilationContext(
                        question,
                        detect_semantic_facts(question, resolution),
                        resolution,
                    ),
                )

                self.assertFalse(result.ready)
                self.assertIn(
                    "unsupported_capability",
                    {violation.code for violation in result.violations},
                )

    def test_numeric_worked_hours_constraint_compiles_without_work_predicate(self):
        question = "How many days had worked hours above 2?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            filters=[
                ProposedFilter(
                    field="Total_Worked_Hrs",
                    operator="gt",
                    value=2,
                    evidence_text="worked hours above 2",
                )
            ],
            measure=ProposedMeasureChoice(name="distinct_dates", evidence_text="days"),
            answer_contract=AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertTrue(result.ready, result.violations)
        self.assertEqual(
            result.executable_plan.filters[0].model_dump(),
            {"field": "Total_Worked_Hrs", "operator": "gt", "value": 2.0},
        )
        self.assertEqual(result.executable_plan.business_predicates, [])

    def test_omitting_independent_positive_work_occurrence_is_rejected(self):
        question = "How many days did not work and work?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            measure=ProposedMeasureChoice(name="distinct_dates", evidence_text="days"),
            business_predicates=[
                ProposedPredicateChoice(name="not_worked", evidence_text="not work")
            ],
            answer_contract=AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn("uncovered_fact", {item.code for item in result.violations})

    def test_percentage_condition_is_canonicalized_grounded_and_preserved(self):
        question = (
            "What percentage of all attendance records have Status equal to Authorized?"
        )
        resolution = ResolutionContext({})
        facts = detect_semantic_facts(question, resolution)
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="percentage",
                field=None,
                percentage_condition=ProposedFilter(
                    field="Status",
                    operator="eq",
                    value="authorized",
                    evidence_text="Status equal to Authorized",
                ),
                evidence_text="percentage",
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="percentage", subject_field=None, grain=[]
            ),
        )

        result = compile_proposal(
            proposal, CompilationContext(question, facts, resolution)
        )

        self.assertTrue(result.ready, result.violations)
        self.assertEqual(
            result.executable_plan.percentage_condition.model_dump(),
            {"field": "Status", "operator": "eq", "value": "Authorized"},
        )

    def test_unresolvable_percentage_condition_fails_during_compilation(self):
        question = "What percentage have Status equal to Not A Real Status?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="percentage",
                field=None,
                percentage_condition=ProposedFilter(
                    field="Status",
                    operator="eq",
                    value="Not A Real Status",
                    evidence_text="Status equal to Not A Real Status",
                ),
                evidence_text="percentage",
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="percentage", subject_field=None, grain=[]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn("invalid_schema", {item.code for item in result.violations})

    def test_wrong_calculation_operation_is_rejected(self):
        question = "What is average Lateness_Hrs?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="max",
                field="Lateness_Hrs",
                evidence_text="average Lateness_Hrs",
            ),
            answer_contract=AnswerContract(
                shape="scalar",
                unit="hours",
                subject_field="Lateness_Hrs",
                grain=["Lateness_Hrs"],
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertEqual(
            {item.code for item in result.violations},
            {"ungrounded_constraint", "uncovered_fact"},
        )

    def test_requested_grouping_cannot_be_omitted(self):
        question = "What is average Lateness_Hrs by Department?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="average",
                field="Lateness_Hrs",
                evidence_text="average Lateness_Hrs",
            ),
            answer_contract=AnswerContract(
                shape="scalar",
                unit="hours",
                subject_field="Lateness_Hrs",
                grain=["Lateness_Hrs"],
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn("uncovered_fact", {item.code for item in result.violations})

    def test_numeric_calculation_rejects_non_numeric_field_before_execution(self):
        question = "What is the sum of Date?"
        resolution = ResolutionContext({})
        proposal = PlannerProposal(
            status="ready",
            calculation=ProposedCalculation(
                operation="sum", field="Date", evidence_text="sum of Date"
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="value", subject_field="Date", grain=["Date"]
            ),
        )

        result = compile_proposal(
            proposal,
            CompilationContext(
                question, detect_semantic_facts(question, resolution), resolution
            ),
        )

        self.assertFalse(result.ready)
        self.assertIn("invalid_schema", {item.code for item in result.violations})

    def setUp(self):
        self.question = "how many off days for A11017"
        self.resolution = ResolutionContext(
            catalog={
                "Day_Type": ("Working Day", "OFF Day", "OFF Day (ZAS)"),
                "Exception": ("Absent", "Lateness", "OK"),
            }
        )
        self.facts = detect_semantic_facts(self.question, self.resolution)

    def test_valid_but_ungrounded_different_column_is_rejected(self):
        proposal = self._proposal(
            ProposedFilter(
                field="Exception",
                operator="eq",
                value="Absent",
                evidence_text="off days",
            )
        )

        result = compile_proposal(proposal, self._context())

        self.assertFalse(result.ready)
        self.assertEqual(
            {item.code for item in result.violations},
            {"ungrounded_constraint", "uncovered_fact"},
        )

    def test_registered_value_family_compiles_without_special_case(self):
        proposal = self._proposal(
            ProposedFilter(
                field="Day_Type",
                operator="in",
                value=["OFF Day", "OFF Day (ZAS)"],
                evidence_text="off days",
            )
        )

        result = compile_proposal(proposal, self._context())

        self.assertTrue(result.ready, result.violations)
        self.assertEqual(
            result.executable_plan.filters[0].model_dump(),
            {
                "field": "Day_Type",
                "operator": "in",
                "value": ["OFF Day", "OFF Day (ZAS)"],
            },
        )
        self.assertEqual(result.executable_plan.mode, "exact")

    def test_false_evidence_is_rejected(self):
        proposal = self._proposal(
            ProposedFilter(
                field="Day_Type",
                operator="eq",
                value="Working Day",
                evidence_text="working day",
            )
        )
        result = compile_proposal(proposal, self._context())
        self.assertIn("ungrounded_constraint", {v.code for v in result.violations})

    def test_answer_contract_must_match_measure(self):
        proposal = PlannerProposal(
            status="ready",
            measure=ProposedMeasureChoice(
                name="attendance_records", evidence_text="days"
            ),
            answer_contract=AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )
        result = compile_proposal(proposal, self._context())
        self.assertIn("answer_contract_mismatch", {v.code for v in result.violations})

    def test_invariant_pipeline_is_unique_and_ordered(self):
        self.assertEqual(
            [type(item).__name__ for item in PLAN_INVARIANTS],
            [
                "SchemaInvariant",
                "GroundingInvariant",
                "CoverageInvariant",
                "ContradictionInvariant",
                "AnswerContractInvariant",
                "CapabilityInvariant",
                "ExecutableChoiceInvariant",
            ],
        )

    def _proposal(self, proposed_filter):
        return PlannerProposal(
            status="ready",
            filters=[
                proposed_filter,
                ProposedFilter(
                    field="Employee_ID",
                    operator="eq",
                    value="A11017",
                    evidence_text="A11017",
                ),
            ],
            measure=ProposedMeasureChoice(name="distinct_dates", evidence_text="days"),
            answer_contract=AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )

    def _context(self):
        return CompilationContext(
            question=self.question,
            facts=self.facts,
            resolution_context=self.resolution,
        )


if __name__ == "__main__":
    unittest.main()
