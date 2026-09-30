from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import unittest
from unittest.mock import patch

from week5 import new_app
from week5.new_implementation import answer


class LaunchModeTests(unittest.TestCase):
    def test_conversation_is_full_width_with_collapsed_context_below_input(self):
        with patch.object(new_app.gr.Blocks, "launch", autospec=True) as launch:
            new_app.main()
        config = launch.call_args.args[0].config
        components = {item["id"]: item for item in config["components"]}
        parents = {}

        def walk(node, parent=None):
            parents[node["id"]] = parent
            for child in node.get("children", []):
                walk(child, node["id"])

        walk(config["layout"])
        chatbot = next(
            id for id, item in components.items() if item["type"] == "chatbot"
        )
        textbox = next(
            id for id, item in components.items() if item["type"] == "textbox"
        )
        context = next(
            id
            for id, item in components.items()
            if item["type"] == "markdown"
            and item["props"].get("label") == "📚 Retrieved Context"
        )
        accordion = parents[context]
        root = config["layout"]["id"]

        self.assertEqual(parents[chatbot], root)
        self.assertEqual(parents[parents[textbox]], root)
        self.assertEqual(parents[accordion], root)
        self.assertEqual(components[accordion]["type"], "accordion")
        self.assertFalse(components[accordion]["props"]["open"])
        self.assertEqual(components[chatbot]["props"]["height"], 420)
        self.assertEqual(components[textbox]["props"]["lines"], 1)

    def test_clear_event_resets_visible_chatbot_history(self):
        with patch.object(new_app.gr.Blocks, "launch", autospec=True) as launch:
            new_app.main()
        config = launch.call_args.args[0].config
        components = {item["id"]: item for item in config["components"]}
        dependencies = config["dependencies"]
        clear = next(
            item
            for item in dependencies
            if item["targets"][0][1] == "click"
            and components[item["targets"][0][0]]["props"].get("value")
            == "Clear conversation"
        )
        display = next(
            item for item in dependencies if item["targets"][0][1] == "change"
        )

        self.assertEqual(clear["outputs"][0], display["outputs"][0])
        self.assertEqual(new_app.reset_session(new_app._TurnGate(), 1)[0], [])

    def test_submit_and_clear_capture_client_sequence_before_backend_work(self):
        with patch.object(new_app.gr.Blocks, "launch", autospec=True) as launch:
            new_app.main()
        ui = launch.call_args.args[0]
        dependencies = ui.config["dependencies"]
        submitted = next(
            item for item in dependencies if item["targets"][0][1] == "submit"
        )
        clear_handlers = [
            item for item in dependencies if item["targets"][0][1] == "click"
        ]

        self.assertEqual(len(dependencies), 3)
        self.assertEqual(len(clear_handlers), 1)
        self.assertTrue(submitted["queue"])
        self.assertEqual(submitted["trigger_mode"], "multiple")
        self.assertFalse(clear_handlers[0]["queue"])
        self.assertEqual(clear_handlers[0]["cancels"], [])
        self.assertIn(
            "return [message, gate, next, globalThis.__apdcClearSequence || 0]",
            submitted["js"],
        )
        self.assertIn("return [gate, next]", clear_handlers[0]["js"])
        self.assertIn("globalThis.__apdcClearSequence = next", clear_handlers[0]["js"])
        self.assertEqual(len(submitted["inputs"]), 4)
        self.assertEqual(len(clear_handlers[0]["inputs"]), 2)
        display = next(
            item for item in dependencies if item["targets"][0][1] == "change"
        )
        self.assertIn(
            "payload.sequence !== globalThis.__apdcTurnSequence", display["js"]
        )
        self.assertIn(
            "message === payload.submitted_message",
            display["js"],
        )
        self.assertNotIn("payload.clear_input &&", display["js"])
        self.assertNotIn("payload.status", display["js"])
        self.assertFalse(display["queue"])
        self.assertEqual(display["inputs"][0], submitted["outputs"][0])
        self.assertNotIn(display["outputs"][0], submitted["outputs"])
        self.assertNotIn(display["outputs"][2], submitted["outputs"])
        self.assertEqual(len(display["outputs"]), 3)
        self.assertEqual(len(clear_handlers[0]["outputs"]), 2)
        self.assertTrue(any(fn.concurrency_limit == 2 for fn in ui.fns.values()))

    def test_default_launch_mode_opens_the_local_browser(self):
        with patch.dict("os.environ", {}, clear=True):
            options = new_app.launch_options()

        self.assertEqual(options, {"inbrowser": True})

    def test_colab_launch_mode_creates_a_nonblocking_share_link(self):
        with patch.dict(
            "os.environ",
            {
                "GRADIO_SHARE": "true",
                "GRADIO_PREVENT_THREAD_LOCK": "true",
                "GRADIO_AUTH_USER": "attendance",
                "GRADIO_AUTH_PASSWORD": "generated-password",
            },
            clear=True,
        ):
            options = new_app.launch_options()

        self.assertEqual(
            options,
            {
                "inbrowser": False,
                "share": True,
                "prevent_thread_lock": True,
                "auth": ("attendance", "generated-password"),
            },
        )

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
    def test_clear_before_old_submit_starts_rejects_stale_message(self):
        gate = new_app._TurnGate()
        state = answer.ConversationState()

        with patch.object(
            new_app,
            "answer_question_with_state",
            return_value=("New answer", [], state),
        ) as complete:
            cleared_history, cleared_context = new_app.reset_session(gate, 2)
            old = list(new_app.submit_chat("old question", gate, 1))
            new = list(new_app.submit_chat("new question", gate, 3))

        self.assertEqual(old, [])
        self.assertEqual(cleared_history, [])
        self.assertIn("Relevant Context", cleared_context)
        complete.assert_called_once()
        self.assertEqual(new[-1]["history"][-1]["content"], "New answer")

    def test_delayed_clear_does_not_invalidate_newer_submit(self):
        gate = new_app._TurnGate()
        state = answer.ConversationState()
        with patch.object(
            new_app,
            "answer_question_with_state",
            return_value=("New answer", [], state),
        ):
            new = list(new_app.submit_chat("new question", gate, 3))
            stale_clear = new_app.reset_session(gate, 2)

        self.assertEqual(gate.current(), 3)
        self.assertEqual(stale_clear, (new_app.gr.skip(), new_app.gr.skip()))
        self.assertEqual(new[-1]["history"][-1]["content"], "New answer")

    def test_submit_after_clear_resets_canonical_history_before_clear_reaches_backend(
        self,
    ):
        old_state = answer.ConversationState(active_employee_ids=("A11026",))
        gate = new_app._TurnGate(
            generation=1,
            history=[
                {"role": "user", "content": "Old question"},
                {"role": "assistant", "content": "Old verified answer"},
            ],
            state=old_state,
        )
        with patch.object(
            new_app,
            "answer_question_with_state",
            return_value=("New answer", [], answer.ConversationState()),
        ) as complete:
            updates = list(new_app.submit_chat("New question", gate, 3, 2))
            delayed_clear = new_app.reset_session(gate, 2)

        self.assertEqual(delayed_clear, (new_app.gr.skip(), new_app.gr.skip()))
        self.assertEqual(updates[0]["history"][0]["content"], "New question")
        self.assertEqual(len(updates[-1]["history"]), 2)
        self.assertEqual(complete.call_args.args[1], [])
        self.assertEqual(complete.call_args.args[2].active_employee_ids, ())

    def test_next_turn_uses_only_canonical_verified_history_and_state(self):
        gate = new_app._TurnGate()
        verified_state = answer.ConversationState(active_employee_ids=("A11026",))
        with patch.object(
            new_app,
            "answer_question_with_state",
            side_effect=[
                ("First verified answer", [], verified_state),
                ("Second verified answer", [], verified_state),
            ],
        ) as complete:
            list(new_app.submit_chat("First question", gate, 1))
            second = list(new_app.submit_chat("Follow-up", gate, 2))

        self.assertEqual(
            complete.call_args.args[1],
            [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First verified answer"},
            ],
        )
        self.assertIs(complete.call_args.args[2], verified_state)
        self.assertEqual(second[-1]["history"][-1]["content"], "Second verified answer")

    def test_submit_clears_textbox_once_and_preserves_next_draft(self):
        gate = new_app._TurnGate()
        state = answer.ConversationState()
        with patch.object(
            new_app,
            "answer_question_with_state",
            return_value=("Verified " * 30, [], state),
        ):
            updates = list(new_app.submit_chat("question", gate, 1))

        self.assertGreater(len(updates), 2)
        self.assertTrue(updates[0]["clear_input"])
        self.assertEqual(updates[0]["submitted_message"], "question")
        self.assertEqual(
            updates[0]["history"][-1],
            {"role": "assistant", "content": "Generating Answer..."},
        )
        self.assertTrue(all("status" not in item for item in updates))
        self.assertTrue(all(not item["clear_input"] for item in updates[1:]))

    def test_clear_then_immediate_submit_keeps_new_reply_and_suppresses_old(self):
        gate = new_app._TurnGate(generation=1)
        old_started = Event()
        old_release = Event()
        old_outputs = []
        state = answer.ConversationState()

        def complete(question, _history, current_state, **_kwargs):
            if question == "old question":
                old_started.set()
                old_release.wait(timeout=5)
                return "Old answer", [], current_state
            return "New answer", [], current_state

        with patch.object(new_app, "answer_question_with_state", side_effect=complete):
            old = new_app.chat_with_state_stream(
                [{"role": "user", "content": "old question"}], state, gate, 1
            )
            self.assertEqual(next(old)[0][-1]["content"], "Generating Answer...")
            thread = Thread(target=lambda: old_outputs.extend(old))
            thread.start()
            self.assertTrue(old_started.wait(timeout=5))

            new_app.reset_session(gate, 2)
            new = list(new_app.submit_chat("new question", gate, 3))
            old_release.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(old_outputs, [])
        self.assertEqual(new[-1]["history"][-1]["content"], "New answer")

    def test_streamed_chat_waits_for_review_then_reveals_only_final_markdown(self):
        original_history = [{"role": "user", "content": "Summarize attendance"}]
        updated_state = answer.ConversationState(active_employee_ids=("A1",))
        document = answer.Result(
            page_content="Rows: 2", metadata={"source": "attendance"}
        )
        final_reply = "## Verified summary\n" + "- Three days of attendance.\n" * 12
        displayed_reply = (
            "## Verified summary\n\n" + "- Three days of attendance.\n" * 12
        )
        with (
            patch.object(
                new_app,
                "answer_question_with_state",
                return_value=(final_reply, [document], updated_state),
            ) as complete,
            patch.object(new_app, "sleep") as paced_sleep,
        ):
            updates = new_app.chat_with_state_stream(
                original_history, answer.ConversationState()
            )
            progress = next(updates)
            complete.assert_not_called()
            snapshots = list(updates)

        self.assertEqual(
            original_history, [{"role": "user", "content": "Summarize attendance"}]
        )
        self.assertEqual(
            progress[0][-1], {"role": "assistant", "content": "Generating Answer..."}
        )
        self.assertTrue(
            all(item[0][-1]["content"] != "Generating Answer..." for item in snapshots)
        )
        self.assertGreater(len(snapshots), 1)
        self.assertGreaterEqual(new_app._REVEAL_INTERVAL_SECONDS, 0.12)
        self.assertLessEqual(new_app._REVEAL_INTERVAL_SECONDS, 0.25)
        self.assertLessEqual(len(snapshots), new_app._MAX_REVEAL_STEPS)
        self.assertEqual(paced_sleep.call_count, len(snapshots) - 1)
        paced_sleep.assert_called_with(new_app._REVEAL_INTERVAL_SECONDS)
        self.assertTrue(
            all(
                displayed_reply.startswith(item[0][-1]["content"]) for item in snapshots
            )
        )
        self.assertEqual(snapshots[-1][0][-1]["content"], displayed_reply)
        self.assertTrue(
            all("Draft answer" not in item[0][-1]["content"] for item in snapshots)
        )
        self.assertTrue(
            all(item[2].active_employee_ids == () for item in snapshots[:-1])
        )
        self.assertIn("Rows: 2", snapshots[-1][1])
        self.assertIs(snapshots[-1][2], updated_state)

    def test_progressive_markdown_keeps_tables_and_code_fences_well_formed(self):
        reply = (
            "## 1. Summary\n\nOne complete sentence. Another sentence.\n\n"
            "| Day | Count |\n| --- | --- |\n| Sep 1 | 4 |\n| Sep 2 | 5 |\n\n"
            "1. First item.\n2. Second item.\n\n"
            "```sql\nSELECT 1;\n```not-a-close\nSELECT 2;\n```\n"
        )
        with (
            patch.object(
                new_app,
                "answer_question_with_state",
                return_value=(reply, [], answer.ConversationState()),
            ),
            patch.object(new_app, "sleep"),
        ):
            snapshots = list(
                new_app.chat_with_state_stream(
                    [{"role": "user", "content": "Show the summary"}],
                    answer.ConversationState(),
                )
            )
        partials = [item[0][-1]["content"] for item in snapshots[1:-1]]
        self.assertGreater(len(partials), 1)
        self.assertTrue(all(part.endswith(("\n", ".", "!", "?")) for part in partials))
        self.assertTrue(all(part.count("```") % 2 == 0 for part in partials))
        self.assertTrue(
            all(
                "| --- | --- |" in part
                for part in partials
                if "| Day | Count |" in part
            )
        )
        self.assertTrue(all(not part.endswith(("1.", "2.")) for part in partials))
        self.assertTrue(all(not part.endswith("## 1.") for part in partials))
        self.assertTrue(
            all(
                "SELECT 2;\n```\n" in part
                for part in partials
                if "```not-a-close" in part
            )
        )
        self.assertEqual(snapshots[-1][0][-1]["content"], reply)

    def test_streamed_markdown_separates_list_from_surrounding_prose(self):
        reply = "Counts:\n- Draft: 2\n- Authorized: 3\nThese are all records."
        with (
            patch.object(
                new_app,
                "answer_question_with_state",
                return_value=(reply, [], answer.ConversationState()),
            ),
            patch.object(new_app, "sleep"),
        ):
            snapshots = list(
                new_app.chat_with_state_stream(
                    [{"role": "user", "content": "Show counts"}],
                    answer.ConversationState(),
                )
            )
        self.assertEqual(
            snapshots[-1][0][-1]["content"],
            "Counts:\n\n- Draft: 2\n- Authorized: 3\n\nThese are all records.",
        )

    def test_streamed_chat_replaces_progress_after_unexpected_failure(self):
        state = answer.ConversationState()
        with (
            patch.object(
                new_app,
                "answer_question_with_state",
                side_effect=RuntimeError("database password must not leak"),
            ),
            patch.object(new_app.logger, "error") as log_error,
        ):
            snapshots = list(
                new_app.chat_with_state_stream(
                    [{"role": "user", "content": "Show attendance"}], state
                )
            )

        log_error.assert_called_once_with("APDC attendance answer failed safely")
        self.assertEqual(snapshots[0][0][-1]["content"], "Generating Answer...")
        self.assertIn("try again", snapshots[-1][0][-1]["content"].lower())
        self.assertNotIn("password", snapshots[-1][0][-1]["content"])
        self.assertIs(snapshots[-1][2], state)

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
