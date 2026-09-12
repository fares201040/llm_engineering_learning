import unittest
from unittest.mock import patch

try:
    from week5.new_implementation import answer
    from week5 import new_app
except ModuleNotFoundError:
    from new_implementation import answer
    import new_app


class ContextRenderingTests(unittest.TestCase):
    def test_format_context_handles_generated_chunks_without_source(self):
        document = answer.Result(
            page_content="Employee_ID: E-1\nPeriod: 2026-09",
            metadata={
                "chunk_type": "employee_period",
                "Employee_ID": "E-1",
            },
        )

        try:
            rendered = new_app.format_context([document])
        except KeyError as exc:
            self.fail(f"Context rendering requires an optional metadata key: {exc}")

        self.assertIn("Source:", rendered)
        self.assertIn("employee_period", rendered)

    def test_chat_handles_empty_history_without_index_error(self):
        history, context = new_app.chat([])

        self.assertEqual(history, [])
        self.assertIn("Relevant Context", context)

    def test_format_context_escapes_untrusted_source_and_content(self):
        document = answer.Result(
            page_content="<script>alert('content')</script>",
            metadata={"source": "<script>alert('source')</script>"},
        )

        rendered = new_app.format_context([document])

        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("<pre><code>", rendered)


class SessionStateTests(unittest.TestCase):
    def test_reset_conversation_clears_context_and_trusted_state(self):
        selected = answer.EmployeeCandidate(
            employee_id="A11017", name="Example Employee Alpha"
        )
        original = answer.ConversationState(
            selected_employees=[selected],
            pending_question="Which employee?",
            pending_plan=answer.QueryPlan(mode="exact", search_query="employee"),
            pending_candidates=[selected],
        )

        context, state = new_app.reset_conversation(original)

        self.assertIn("Relevant Context", context)
        self.assertNotIn("Employee_ID", context)
        self.assertEqual(state, answer.ConversationState())
        self.assertIsNot(state, original)

    def test_chat_with_state_renders_the_answer_evidence(self):
        document = answer.Result(
            page_content="Employee_ID: A11017\nName: <Example Employee>",
            metadata={"source": "<attendance.xlsx>"},
        )

        with patch.object(
            new_app,
            "answer_question_with_state",
            return_value=(
                "The employee is Example Employee.",
                [document],
                answer.ConversationState(),
            ),
        ):
            history, context, _state = new_app.chat_with_state(
                [{"role": "user", "content": "Who is this employee?"}],
                answer.ConversationState(),
            )

        self.assertEqual(history[-1]["content"], "The employee is Example Employee.")
        self.assertIn("&lt;attendance.xlsx&gt;", context)
        self.assertIn("Name: &lt;Example Employee&gt;", context)
        self.assertIn("<pre><code>", context)

    def test_chat_with_state_returns_an_independent_updated_session(self):
        first = answer.ConversationState()
        second = answer.ConversationState()
        selected = answer.EmployeeCandidate(
            employee_id="A11000", name="Alex Example North"
        )

        with patch.object(
            new_app,
            "answer_question_with_state",
            return_value=(
                "Selected Alex Example North.",
                [],
                answer.ConversationState(selected_employees=[selected]),
            ),
        ):
            _history, _context, updated = new_app.chat_with_state(
                [{"role": "user", "content": "1"}], first
            )

        self.assertEqual(updated.selected_employees, [selected])
        self.assertEqual(first.selected_employees, [])
        self.assertEqual(second.selected_employees, [])

    def test_unexpected_answer_error_is_logged_and_rendered_safely(self):
        history = [{"role": "user", "content": "Show attendance"}]
        with (
            patch.object(
                new_app,
                "answer_question_with_state",
                side_effect=RuntimeError("database password must not leak"),
            ),
            patch.object(new_app.logger, "exception") as log_exception,
        ):
            updated_history, context, state = new_app.chat_with_state(
                history, answer.ConversationState()
            )

        log_exception.assert_called_once()
        self.assertIn("try again", updated_history[-1]["content"].lower())
        self.assertNotIn("password", updated_history[-1]["content"].lower())
        self.assertIn("Relevant Context", context)
        self.assertEqual(state, answer.ConversationState())


if __name__ == "__main__":
    unittest.main()
