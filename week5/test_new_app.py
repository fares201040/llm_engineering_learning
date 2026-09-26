from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from week5 import new_app
from week5.new_implementation import answer


class LaunchModeTests(unittest.TestCase):
    def test_new_app_imports_when_executed_as_a_standalone_script(self):
        week5_directory = Path(__file__).resolve().parent
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import runpy; "
                    "runpy.run_path('new_app.py', run_name='new_app_import_test')"
                ),
            ],
            cwd=week5_directory,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


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
        original = answer.ConversationState(active_employee_ids=("A11017",))

        context, state = new_app.reset_conversation(original)

        self.assertIn("Relevant Context", context)
        self.assertNotIn("Employee_ID", context)
        self.assertEqual(state.verified_turns, ())
        self.assertEqual(state.active_employee_ids, ())
        self.assertIsNone(state.pending_employee_confirmation)
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
        with patch.object(
            new_app,
            "answer_question_with_state",
            return_value=(
                "Selected Alex Example North.",
                [],
                answer.ConversationState(active_employee_ids=("A11000",)),
            ),
        ):
            _history, _context, updated = new_app.chat_with_state(
                [{"role": "user", "content": "1"}], first
            )

        self.assertEqual(updated.active_employee_ids, ("A11000",))
        self.assertEqual(first.active_employee_ids, ())
        self.assertEqual(second.active_employee_ids, ())

    def test_unexpected_answer_error_is_logged_and_rendered_safely(self):
        history = [{"role": "user", "content": "Show attendance"}]
        with (
            patch.object(
                new_app,
                "answer_question_with_state",
                side_effect=RuntimeError("database password must not leak"),
            ),
            patch.object(new_app.logger, "error") as log_error,
        ):
            updated_history, context, state = new_app.chat_with_state(
                history, answer.ConversationState()
            )

        log_error.assert_called_once_with("APDC attendance answer failed safely")
        self.assertIn("try again", updated_history[-1]["content"].lower())
        self.assertNotIn("password", updated_history[-1]["content"].lower())
        self.assertIn("Relevant Context", context)
        self.assertEqual(state.verified_turns, ())
        self.assertEqual(state.active_employee_ids, ())

    def test_unexpected_answer_error_preserves_arabic_locale(self):
        history = [{"role": "user", "content": "اعرض سجلات الحضور"}]
        with patch.object(
            new_app,
            "answer_question_with_state",
            side_effect=RuntimeError("private failure detail"),
        ):
            updated_history, _context, _state = new_app.chat_with_state(
                history, answer.ConversationState()
            )

        text = updated_history[-1]["content"]
        self.assertIn("تعذر", text)
        self.assertNotIn("private failure detail", text)


if __name__ == "__main__":
    unittest.main()
