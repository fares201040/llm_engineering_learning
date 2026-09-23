from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from week5.new_implementation.online.catalog import (
    ATTENDANCE_CATALOG,
    render_provider_catalog,
)
from week5.new_implementation.online.query import (
    AggregateMeasure,
    AggregateOutput,
    All,
    AttendanceQuery,
    Condition,
    EvidenceSpan,
    FilterComponent,
    MaterializedQuery,
    Ordering,
    OutputComponent,
    QueryLimits,
    QueryMaterializationError,
    RowsOutput,
    TrustedQueryComponent,
    materialize_query,
    validate_query,
)


class FlatQueryContractTests(unittest.TestCase):
    def test_models_are_strict_and_reject_unknown_fields(self):
        with self.assertRaises(ValidationError):
            Condition(
                field_id="date",
                operator="eq",
                value="2026-09-01",
                sql="DROP TABLE attendance_records",
            )

    def test_provider_catalog_contains_no_physical_identifiers(self):
        payload = render_provider_catalog()
        self.assertNotIn("attendance_date", payload)
        self.assertNotIn("total_worked_hrs", payload)
        parsed = json.loads(payload)
        self.assertIn("date", {field["id"] for field in parsed["fields"]})

    def test_rows_require_ordering_and_a_bounded_limit(self):
        query = AttendanceQuery(
            filters=(),
            output=RowsOutput(fields=("date",), ordering=(), limit=10),
        )
        issues = validate_query(query, ATTENDANCE_CATALOG, QueryLimits())
        self.assertIn("rows_ordering", {issue.code for issue in issues})

    def test_nested_boolean_limit_is_enforced_by_the_single_limits_object(self):
        expression = Condition(field_id="date", operator="eq", value="2026-09-01")
        expression = All(items=(expression,))
        expression = All(items=(expression,))
        query = AttendanceQuery(
            filters=(expression,),
            output=AggregateOutput(
                measures=(
                    AggregateMeasure(
                        output_id="days",
                        function="distinct_count",
                        field_id="date",
                    ),
                )
            ),
        )
        issues = validate_query(
            query,
            ATTENDANCE_CATALOG,
            QueryLimits(max_boolean_depth=2),
        )
        self.assertIn("boolean_depth", {issue.code for issue in issues})

    def test_removed_output_kinds_are_rejected_by_the_wire_model(self):
        with self.assertRaises(ValidationError):
            AttendanceQuery.model_validate(
                {"filters": [], "output": {"kind": "derive", "items": []}}
            )


class QueryMaterializationTests(unittest.TestCase):
    def test_new_query_requires_exact_evidence_and_one_complete_output(self):
        message = "show absence dates"
        current_filter = FilterComponent(
            component_key="absence",
            evidence=EvidenceSpan(start=5, end=12, text="absence"),
            expression=Condition(
                field_id="exception",
                operator="eq",
                value="Absent",
            ),
        )
        output = OutputComponent(
            component_key="dates",
            evidence=EvidenceSpan(start=13, end=18, text="dates"),
            output=RowsOutput(
                fields=("date",),
                ordering=(Ordering(output_id="date", direction="asc"),),
                limit=100,
            ),
        )

        materialized = materialize_query(
            message=message,
            relationship="new",
            base_turn_id=None,
            retained_component_ids=(),
            current_filters=(current_filter,),
            current_output=output,
            trusted_components=(),
            catalog=ATTENDANCE_CATALOG,
            limits=QueryLimits(),
        )

        self.assertIsInstance(materialized, MaterializedQuery)
        self.assertEqual(materialized.query.output.kind, "rows")
        self.assertEqual(len(materialized.query.filters), 1)

    def test_invalid_current_span_fails_before_query_validation(self):
        with self.assertRaises(QueryMaterializationError) as raised:
            materialize_query(
                message="show absence dates",
                relationship="new",
                base_turn_id=None,
                retained_component_ids=(),
                current_filters=(
                    FilterComponent(
                        component_key="absence",
                        evidence=EvidenceSpan(start=0, end=4, text="absent"),
                        expression=Condition(
                            field_id="exception",
                            operator="eq",
                            value="Absent",
                        ),
                    ),
                ),
                current_output=OutputComponent(
                    component_key="dates",
                    evidence=EvidenceSpan(start=13, end=18, text="dates"),
                    output=RowsOutput(
                        fields=("date",),
                        ordering=(Ordering(output_id="date", direction="asc"),),
                        limit=100,
                    ),
                ),
                trusted_components=(),
                catalog=ATTENDANCE_CATALOG,
                limits=QueryLimits(),
            )
        self.assertIn("current_span", {issue.code for issue in raised.exception.issues})

    def test_modify_retains_owned_filter_and_replaces_output(self):
        prior = TrustedQueryComponent(
            component_id="period-1",
            owner_turn_id="turn-1",
            component=FilterComponent(
                component_key="period",
                evidence=EvidenceSpan(start=0, end=9, text="September"),
                expression=All(
                    items=(
                        Condition(field_id="date", operator="gte", value="2026-09-01"),
                        Condition(field_id="date", operator="lt", value="2026-10-01"),
                    )
                ),
            ),
        )
        output = OutputComponent(
            component_key="hours",
            evidence=EvidenceSpan(start=0, end=5, text="hours"),
            output=AggregateOutput(
                measures=(
                    AggregateMeasure(
                        output_id="worked_hours",
                        function="sum",
                        field_id="worked_hours",
                    ),
                )
            ),
        )

        materialized = materialize_query(
            message="hours",
            relationship="modify",
            base_turn_id="turn-1",
            retained_component_ids=("period-1",),
            current_filters=(),
            current_output=output,
            trusted_components=(prior,),
            catalog=ATTENDANCE_CATALOG,
            limits=QueryLimits(),
        )

        self.assertEqual(len(materialized.query.filters), 1)
        self.assertEqual(materialized.query.output.kind, "aggregate")


if __name__ == "__main__":
    unittest.main()
