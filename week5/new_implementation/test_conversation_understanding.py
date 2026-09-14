import unittest

from pydantic import TypeAdapter, ValidationError


class ConversationContractTests(unittest.TestCase):
    def _context(self, **replacements):
        from week5.new_implementation.conversation_understanding import (
            ConversationDecisionContext,
        )

        values = {
            "message": "worked days for Morgan and the same for Sam",
            "employee_choice_ids": ("employee:morgan", "employee:sam"),
            "prior_unit_choice_ids": ("prior:latest",),
            "strong_fact_ids": ("fact:worked",),
            "max_units": 8,
        }
        values.update(replacements)
        return ConversationDecisionContext(**values)

    def _resolved_payload(self):
        return {
            "status": "resolved",
            "units": [
                {
                    "route": "attendance",
                    "source_span": (0, 22),
                    "relation": "new",
                    "fact_ids": ("fact:worked",),
                    "employee_mentions": (
                        {"kind": "resolve", "source_span": (16, 22)},
                    ),
                },
                {
                    "route": "attendance",
                    "source_span": (27, 43),
                    "relation": "modify_scope",
                    "base_unit_choice_id": "prior:latest",
                    "employee_mentions": (
                        {
                            "kind": "reference_choice",
                            "source_span": (40, 43),
                            "choice_id": "employee:sam",
                        },
                    ),
                },
            ],
        }

    def test_provider_decision_is_a_strict_discriminated_union(self):
        from week5.new_implementation.conversation_understanding import (
            ConversationDecision,
        )

        adapter = TypeAdapter(ConversationDecision)
        resolved = ConversationDecision.model_validate(self._resolved_payload())
        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(len(resolved.units), 2)

        ambiguous = adapter.validate_python(
            {"status": "ambiguous", "reason": "ambiguous_reference"}
        )
        self.assertEqual(ambiguous.reason, "ambiguous_reference")
        self.assertEqual(
            ConversationDecision.model_json_schema()["title"],
            "ConversationDecision",
        )

        invalid = (
            {
                "status": "resolved",
                "units": self._resolved_payload()["units"],
                "reason": "ambiguous_reference",
            },
            {"status": "resolved", "units": []},
            {"status": "ambiguous", "reason": "ambiguous_reference", "units": []},
            {"status": "ambiguous", "reason": "model_will_explain"},
            {"status": "resolved", "units": [], "sql": "SELECT 1"},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                adapter.validate_python(payload)

    def test_route_specific_units_reject_fields_owned_by_other_routes(self):
        from week5.new_implementation.conversation_understanding import (
            ConversationDecision,
        )

        adapter = TypeAdapter(ConversationDecision)
        invalid_units = (
            {
                "route": "social",
                "source_span": (0, 5),
                "relation": "new",
            },
            {
                "route": "unrelated",
                "source_span": (0, 5),
                "employee_mentions": (),
            },
            {
                "route": "attendance",
                "source_span": (0, 5),
                "relation": "new",
                "response": "invented answer",
            },
        )
        for unit in invalid_units:
            with self.subTest(unit=unit), self.assertRaises(ValidationError):
                adapter.validate_python({"status": "resolved", "units": [unit]})

    def test_source_contracts_reject_blank_context_invalid_spans_and_unknown_keys(self):
        from week5.new_implementation.conversation_understanding import (
            ConversationDecision,
            ConversationDecisionContext,
        )

        adapter = TypeAdapter(ConversationDecision)
        invalid_units = (
            {"route": "social", "source_span": (2, 2)},
            {"route": "social", "source_span": (-1, 2)},
            {
                "route": "attendance",
                "source_span": (0, 5),
                "relation": "new",
                "employee_mentions": (
                    {
                        "kind": "resolve",
                        "source_span": (0, 5),
                        "employee_id": "invented",
                    },
                ),
            },
        )
        for unit in invalid_units:
            with self.subTest(unit=unit), self.assertRaises(ValidationError):
                adapter.validate_python({"status": "resolved", "units": [unit]})

        with self.assertRaises(ValidationError):
            ConversationDecisionContext(message="   ", max_units=1)

    def test_attendance_relation_requires_the_correct_base_and_view_shape(self):
        from week5.new_implementation.conversation_understanding import (
            AttendanceUnitDecision,
        )

        invalid = (
            {
                "source_span": (0, 5),
                "relation": "new",
                "base_unit_choice_id": "prior:latest",
            },
            {"source_span": (0, 5), "relation": "repeat"},
            {
                "source_span": (0, 5),
                "relation": "change_view",
                "base_unit_choice_id": "prior:latest",
            },
            {
                "source_span": (0, 5),
                "relation": "repeat",
                "base_unit_choice_id": "prior:latest",
                "view_choice_id": "view:union",
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AttendanceUnitDecision(**values)

        changed = AttendanceUnitDecision(
            source_span=(0, 5),
            relation="change_view",
            base_unit_choice_id="prior:latest",
            view_choice_id="view:intersection",
        )
        self.assertEqual(changed.view_choice_id, "view:intersection")

    def test_provider_selects_only_request_local_view_choice_ids(self):
        from week5.new_implementation.conversation_understanding import (
            AttendanceUnitDecision,
            ConversationDecision,
            ConversationDecisionValidationError,
            validate_conversation_decision,
        )

        with self.assertRaises(ValidationError):
            AttendanceUnitDecision(
                source_span=(0, 5),
                relation="change_view",
                base_unit_choice_id="prior:latest",
                view="intersection_dates",
            )

        decision = ConversationDecision.model_validate(
            {
                "status": "resolved",
                "units": (
                    {
                        "route": "attendance",
                        "source_span": (0, 5),
                        "relation": "change_view",
                        "base_unit_choice_id": "prior:latest",
                        "view_choice_id": "view:intersection",
                    },
                ),
            }
        )
        validate_conversation_decision(
            decision,
            self._context(
                message="those",
                strong_fact_ids=(),
                view_choice_ids=("view:intersection",),
            ),
        )

        with self.assertRaisesRegex(
            ConversationDecisionValidationError, "unknown view choice"
        ):
            validate_conversation_decision(
                decision,
                self._context(message="those", strong_fact_ids=()),
            )

    def test_modifying_relations_require_relation_specific_changes(self):
        from week5.new_implementation.conversation_understanding import (
            AttendanceUnitDecision,
        )

        for relation in ("modify_scope", "replace_result", "add_constraints"):
            with self.subTest(relation=relation), self.assertRaises(ValidationError):
                AttendanceUnitDecision(
                    source_span=(0, 5),
                    relation=relation,
                    base_unit_choice_id="prior:latest",
                )

        scope = AttendanceUnitDecision(
            source_span=(0, 5),
            relation="modify_scope",
            base_unit_choice_id="prior:latest",
            employee_mentions=({"kind": "resolve", "source_span": (0, 5)},),
        )
        replacement = AttendanceUnitDecision(
            source_span=(0, 5),
            relation="replace_result",
            base_unit_choice_id="prior:latest",
            fact_ids=("fact:new-result",),
        )
        constrained = AttendanceUnitDecision(
            source_span=(0, 5),
            relation="add_constraints",
            base_unit_choice_id="prior:latest",
            fact_ids=("fact:new-constraint",),
        )

        self.assertTrue(scope.employee_mentions)
        self.assertTrue(replacement.fact_ids)
        self.assertTrue(constrained.fact_ids)

    def test_context_validation_rejects_unknown_choices_and_uncovered_facts(self):
        from week5.new_implementation.conversation_understanding import (
            ConversationDecision,
            ConversationDecisionValidationError,
            validate_conversation_decision,
        )

        decision = TypeAdapter(ConversationDecision).validate_python(
            self._resolved_payload()
        )
        self.assertIs(
            validate_conversation_decision(decision, self._context()),
            decision,
        )

        invalid_cases = (
            (
                self._resolved_payload()
                | {
                    "units": [
                        self._resolved_payload()["units"][0],
                        self._resolved_payload()["units"][1]
                        | {"base_unit_choice_id": "prior:invented"},
                    ]
                },
                "unknown base-unit choice",
            ),
            (
                self._resolved_payload()
                | {
                    "units": [
                        self._resolved_payload()["units"][0],
                        self._resolved_payload()["units"][1]
                        | {
                            "employee_mentions": (
                                {
                                    "kind": "reference_choice",
                                    "source_span": (40, 43),
                                    "choice_id": "employee:invented",
                                },
                            )
                        },
                    ]
                },
                "unknown employee choice",
            ),
            (
                self._resolved_payload()
                | {
                    "units": [
                        self._resolved_payload()["units"][0]
                        | {"fact_ids": ("fact:invented",)},
                        self._resolved_payload()["units"][1],
                    ]
                },
                "unknown fact choice",
            ),
        )
        adapter = TypeAdapter(ConversationDecision)
        for payload, message in invalid_cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ConversationDecisionValidationError, message),
            ):
                validate_conversation_decision(
                    adapter.validate_python(payload), self._context()
                )

        with self.assertRaisesRegex(
            ConversationDecisionValidationError, "strong facts are not covered"
        ):
            validate_conversation_decision(
                decision,
                self._context(strong_fact_ids=("fact:worked", "fact:date")),
            )

    def test_context_validation_enforces_source_order_overlap_and_references(self):
        from week5.new_implementation.conversation_understanding import (
            ConversationDecision,
            ConversationDecisionValidationError,
            validate_conversation_decision,
        )

        adapter = TypeAdapter(ConversationDecision)
        bad_payloads = (
            (
                self._resolved_payload()
                | {
                    "units": [
                        self._resolved_payload()["units"][0],
                        self._resolved_payload()["units"][1]
                        | {"source_span": (20, 43)},
                    ]
                },
                "partially overlap",
            ),
            (
                self._resolved_payload()
                | {
                    "units": list(reversed(self._resolved_payload()["units"])),
                },
                "source order",
            ),
            (
                self._resolved_payload()
                | {
                    "units": [
                        self._resolved_payload()["units"][0],
                        self._resolved_payload()["units"][1]
                        | {
                            "employee_mentions": (
                                {
                                    "kind": "previous_unit",
                                    "source_span": (40, 43),
                                    "unit_index": 1,
                                },
                            )
                        },
                    ]
                },
                "earlier unit",
            ),
        )
        for payload, message in bad_payloads:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ConversationDecisionValidationError, message),
            ):
                validate_conversation_decision(
                    adapter.validate_python(payload), self._context()
                )

        shared_span = self._resolved_payload()
        shared_span["units"][1]["source_span"] = (0, 22)
        shared_span["units"][1]["employee_mentions"] = (
            {
                "kind": "reference_choice",
                "source_span": (16, 22),
                "choice_id": "employee:sam",
            },
        )
        validate_conversation_decision(
            adapter.validate_python(shared_span),
            self._context(),
        )

    def test_context_validation_enforces_message_bounds_unit_budget_and_containment(
        self,
    ):
        from week5.new_implementation.conversation_understanding import (
            ConversationDecision,
            ConversationDecisionValidationError,
            validate_conversation_decision,
        )

        adapter = TypeAdapter(ConversationDecision)
        decision = adapter.validate_python(self._resolved_payload())
        for context, message in (
            (self._context(message="short"), "message bounds"),
            (self._context(max_units=1), "unit budget"),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ConversationDecisionValidationError, message),
            ):
                validate_conversation_decision(decision, context)

        outside = self._resolved_payload()
        outside["units"][0]["employee_mentions"] = (
            {"kind": "resolve", "source_span": (23, 26)},
        )
        with self.assertRaisesRegex(
            ConversationDecisionValidationError, "inside its unit"
        ):
            validate_conversation_decision(
                adapter.validate_python(outside), self._context()
            )


if __name__ == "__main__":
    unittest.main()
