import json
import unittest
from unittest.mock import patch

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

    def test_semantic_and_sql_sensitive_payloads_are_redacted(self):
        payload = redact(
            {
                "event": "proposal_rejected",
                "violation_codes": ["ungrounded_constraint"],
                "evidence_text": "off days for A11017",
                "catalog_value": "Secret Department",
                "sql": "SELECT * FROM attendance_records",
                "params": ["A11017"],
                "fingerprint": "safe-fingerprint",
            }
        )
        rendered = str(payload)
        self.assertIn("ungrounded_constraint", rendered)
        self.assertIn("safe-fingerprint", rendered)
        self.assertNotIn("off days", rendered)
        self.assertNotIn("Secret Department", rendered)
        self.assertNotIn("SELECT", rendered)
        self.assertNotIn("A11017", rendered)
