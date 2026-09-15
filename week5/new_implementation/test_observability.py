import json
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from pydantic import BaseModel

from week5.new_implementation import answer
from week5.new_implementation.observability import EventLogger, redact


class ObservabilityTests(unittest.TestCase):
    def test_input_clarification_events_contain_counts_not_identity_or_question(self):
        events = []
        logger = EventLogger(sink=events.append, json_format=True)
        employees = [
            answer.EmployeeCandidate(employee_id="A10018", name="Faris Ahmed"),
            answer.EmployeeCandidate(employee_id="A10019", name="Faris North"),
        ]
        with (
            patch.object(answer, "event_logger", logger),
            patch.object(answer, "load_employee_directory", return_value=employees),
            self.assertRaises(answer.EmployeeClarificationRequired),
        ):
            answer._fetch_context_result("who is Faris", request_id="request-1")

        rendered = "\n".join(events)
        payloads = [json.loads(event) for event in events]
        clarification = next(
            payload
            for payload in payloads
            if payload["event"] == "input_clarification_required"
        )
        self.assertEqual(clarification["candidate_count"], 2)
        self.assertEqual(clarification["clarification_kind"], "employee_selection")
        for private_value in ("who is Faris", "Faris Ahmed", "A10018", "score"):
            self.assertNotIn(private_value, rendered)

    def test_grounded_planning_events_expose_counts_but_not_request_content(self):
        events = []
        logger = EventLogger(sink=lambda value: events.append(value), json_format=True)
        question = "How many days were worked?"
        facts = answer.detect_semantic_facts(question, answer.ResolutionContext({}))

        with patch.object(answer, "completion") as completion:
            answer.propose_query(
                question,
                semantic_facts=facts,
                event_logger=logger,
                request_id="request-1",
            )

        completion.assert_not_called()
        payload = json.loads(events[0])
        self.assertEqual(payload["event"], "planning_draft_built")
        self.assertEqual(payload["need_count"], 0)
        self.assertNotIn(question, events[0])

    def test_default_redaction_hides_query_identity_and_dsn(self):
        payload = redact(
            {
                "query": "Show Sample A10029",
                "employee_id": "A10029",
                "dsn": "postgres://secret",
            }
        )
        self.assertNotIn("Sample", str(payload))
        self.assertNotIn("A10029", str(payload))
        self.assertNotIn("secret", str(payload))

    def test_stage_emits_named_duration_and_state_as_json(self):
        events = []
        logger = EventLogger(sink=events.append, json_format=True)
        with logger.stage("planning", request_id="request-1"):
            pass
        payload = json.loads(events[-1])
        self.assertEqual(payload["stage"], "planning")
        self.assertEqual(payload["state"], "success")
        self.assertGreaterEqual(payload["duration_seconds"], 0)

    def test_allowlisted_match_method_counts_remain_structural(self):
        payload = redact(
            {
                "event": "input_surface_analyzed",
                "match_method_counts": {
                    "exact": 2,
                    "fuzzy": 1,
                    "private_method": 99,
                },
            }
        )

        self.assertEqual(payload["match_method_counts"], {"exact": 2, "fuzzy": 1})

    def test_redaction_cannot_be_disabled_for_event_emission(self):
        events = []
        logger = EventLogger(sink=events.append, json_format=True, redact_pii=False)

        logger.emit(
            "input_surface_analyzed",
            question="private question with employee token",
            evidence_text="private evidence",
        )

        self.assertNotIn("private question", events[-1])
        self.assertNotIn("employee token", events[-1])

    def test_request_ids_must_use_an_opaque_telemetry_format(self):
        payload = redact({"event": "stage_complete", "request_id": "EMPLOYEE_SECRET"})

        self.assertNotIn("request_id", payload)

    def test_request_ids_reject_content_shaped_prefixed_values(self):
        payload = redact(
            {"event": "stage_complete", "request_id": "request-private-employee"}
        )

        self.assertNotIn("request_id", payload)

    def test_query_fingerprints_require_the_native_sha256_shape(self):
        payload = redact(
            {"event": "postgres_query_compiled", "fingerprint": "EMPLOYEE_SECRET"}
        )

        self.assertNotIn("fingerprint", payload)

    def test_scalar_telemetry_fields_reject_booleans_and_nested_mappings(self):
        payload = redact(
            {
                "event": "stage_complete",
                "request_id": {"count": 7},
                "fingerprint": False,
                "candidate_count": True,
            }
        )

        self.assertEqual(payload, {"event": "stage_complete"})

    def test_semantic_and_sql_sensitive_payloads_are_redacted(self):
        safe_fingerprint = "a" * 64
        payload = redact(
            {
                "event": "proposal_rejected",
                "violation_codes": ["ungrounded_constraint"],
                "evidence_text": "off days for a private employee token",
                "catalog_value": "Secret Department",
                "sql": "SELECT * FROM attendance_records",
                "params": ["SENSITIVE_EMPLOYEE_TOKEN"],
                "fingerprint": safe_fingerprint,
            }
        )
        rendered = str(payload)
        self.assertIn("ungrounded_constraint", rendered)
        self.assertIn(safe_fingerprint, rendered)
        self.assertNotIn("off days", rendered)
        self.assertNotIn("Secret Department", rendered)
        self.assertNotIn("SELECT", rendered)
        self.assertNotIn("SENSITIVE_EMPLOYEE_TOKEN", rendered)

    def test_redaction_recurses_through_structured_provider_and_plan_payloads(self):
        @dataclass(frozen=True)
        class PlanPayload:
            query: str
            employee_id: str

        class ProviderPayload(BaseModel):
            question: str
            choices: list[str]

        private_text = "Private employee token and database password"
        payload = redact(
            {
                "event": "stage_complete",
                "state": "failure",
                "request_id": ["private-employee-token"],
                "nested": {"question": private_text, "unexpected": private_text},
                "tuple_payload": (private_text,),
                "set_payload": {private_text},
                "provider": ProviderPayload(
                    question=private_text, choices=[private_text]
                ),
                "plan": PlanPayload(query=private_text, employee_id="EMPLOYEE_SECRET"),
                "exception": RuntimeError(private_text),
            }
        )

        rendered = str(payload)
        self.assertEqual(payload["event"], "stage_complete")
        self.assertEqual(payload["state"], "failure")
        self.assertNotIn("Private employee", rendered)
        self.assertNotIn("EMPLOYEE_SECRET", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("unexpected", rendered)

    def test_redaction_drops_untrusted_set_members_without_calling_repr(self):
        class UnsafeRepresentation:
            def __repr__(self):
                raise RuntimeError("private employee token")

        payload = redact(
            {
                "event": "proposal_rejected",
                "violation_codes": {
                    "ungrounded_constraint",
                    UnsafeRepresentation(),
                },
            }
        )

        self.assertEqual(payload["violation_codes"], ["ungrounded_constraint"])

    def test_stage_failure_emits_only_a_controlled_failure_code(self):
        events = []
        logger = EventLogger(sink=events.append, json_format=True)

        with self.assertRaisesRegex(RuntimeError, "private provider payload"):
            with logger.stage(
                "provider", provider_response={"content": "private provider payload"}
            ):
                raise RuntimeError("private provider payload")

        payload = json.loads(events[-1])
        self.assertEqual(payload["failure_code"], "internal_error")
        self.assertNotIn("error_type", payload)
        self.assertNotIn("private provider payload", events[-1])

    def test_answer_diagnostics_do_not_log_aggregate_values(self):
        plan = answer.QueryPlan(
            mode="exact",
            search_query="count attendance",
            aggregation="count",
            answer_contract=answer.AnswerContract(
                shape="scalar",
                unit="records",
                subject_field=None,
                grain=[],
            ),
        )
        with patch.object(answer.logger, "info") as log_info:
            answer._answer_from_context(
                "count attendance",
                [],
                [],
                plan,
                {"operation": "count", "value": 42},
                42,
            )

        rendered_calls = repr(log_info.call_args_list)
        self.assertNotIn("42", rendered_calls)
