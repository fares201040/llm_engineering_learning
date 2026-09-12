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
