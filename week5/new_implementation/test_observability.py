import json
import unittest

from week5.new_implementation.observability import EventLogger, redact


class ObservabilityTests(unittest.TestCase):
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
