import html
import logging
import os
import re
from math import ceil
from threading import Lock
from time import sleep

import gradio as gr

if __package__:
    from .new_implementation.answer import (
        ConversationState,
        LOCAL_DEMO_ACCESS,
        answer_question_with_state,
    )
    from .new_implementation.config import settings
else:
    from new_implementation.answer import (
        ConversationState,
        LOCAL_DEMO_ACCESS,
        answer_question_with_state,
    )
    from new_implementation.config import settings


logger = logging.getLogger(__name__)
_REVEAL_INTERVAL_SECONDS = 0.06
_MAX_REVEAL_STEPS = 40
_CLIENT_SEQUENCE_BODY_JS = """
    const next = Math.max((globalThis.__apdcTurnSequence || 0) + 1, Date.now());
    globalThis.__apdcTurnSequence = next;
"""
_SUBMIT_WITH_SEQUENCE_JS = (
    """(message, gate, sequence, clearSequence) => {
"""
    + _CLIENT_SEQUENCE_BODY_JS
    + """
    return [message, gate, next, globalThis.__apdcClearSequence || 0];
}"""
)
_CLEAR_WITH_SEQUENCE_JS = (
    """(gate, sequence) => {
"""
    + _CLIENT_SEQUENCE_BODY_JS
    + """
    globalThis.__apdcClearSequence = next;
    return [gate, next];
}"""
)
_DISPLAY_CURRENT_SEQUENCE_JS = """(payload, history, context, message) => {
    if (!payload || payload.sequence !== globalThis.__apdcTurnSequence) {
        return [history, context, message];
    }
    const clearInput = payload.clear_input && message === payload.submitted_message;
    return [payload.history, payload.context, clearInput ? "" : message];
}"""


class _TurnGate:
    """Keep the accepted conversation and invalidate obsolete UI events."""

    def __init__(
        self, generation: int = 0, history=None, state=None, last_clear: int = 0
    ):
        self._generation = generation
        self._history = list(history or [])
        self._state = state or ConversationState()
        self._last_clear = last_clear
        self._lock = Lock()

    def __deepcopy__(self, _memo):
        with self._lock:
            return _TurnGate(
                self._generation, self._history, self._state, self._last_clear
            )

    def claim_submit(self, sequence: int, clear_sequence: int) -> bool:
        with self._lock:
            if sequence <= self._generation:
                return False
            if clear_sequence > self._last_clear:
                self._history = []
                self._state = ConversationState()
                self._last_clear = clear_sequence
            self._generation = sequence
            return True

    def current(self) -> int:
        with self._lock:
            return self._generation

    def snapshot(self):
        with self._lock:
            return list(self._history), self._state

    def commit(self, sequence: int, history, state) -> bool:
        with self._lock:
            if sequence != self._generation:
                return False
            self._history = list(history)
            self._state = state
            return True

    def reset(self, sequence: int) -> bool:
        with self._lock:
            if sequence <= self._generation:
                return False
            self._generation = sequence
            self._history = []
            self._state = ConversationState()
            self._last_clear = sequence
            return True


def _environment_flag(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def launch_options() -> dict[str, object]:
    """Return local defaults or Colab-friendly Gradio launch options."""

    if not _environment_flag("GRADIO_SHARE"):
        return {"inbrowser": True}
    options: dict[str, object] = {
        "inbrowser": False,
        "share": True,
        "prevent_thread_lock": _environment_flag("GRADIO_PREVENT_THREAD_LOCK"),
    }
    auth_user = os.getenv("GRADIO_AUTH_USER", "").strip()
    auth_password = os.getenv("GRADIO_AUTH_PASSWORD", "").strip()
    if auth_user and auth_password:
        options["auth"] = (auth_user, auth_password)
    return options


def format_context(context):
    result = "<h2 style='color: #ff7800;'>Relevant Context</h2>\n\n"
    for doc in context:
        source = (
            doc.metadata.get("source")
            or doc.metadata.get("source_file")
            or doc.metadata.get("chunk_type")
            or "unknown"
        )
        result += (
            "<span style='color: #ff7800;'>Source: "
            f"{html.escape(str(source))}</span>\n\n"
        )
        result += f"<pre><code>{html.escape(doc.page_content)}</code></pre>\n\n"
    return result


def chat(history):
    updated, context, _state = chat_with_state(history, ConversationState())
    return updated, context


def chat_with_state(history, state):
    if not history:
        return history, format_context([]), state or ConversationState()

    history = list(history)
    last_message = history[-1]["content"]
    prior = history[:-1]
    try:
        answer, context, updated_state = answer_question_with_state(
            last_message,
            prior,
            state or ConversationState(),
            access_context=LOCAL_DEMO_ACCESS,
        )
    except Exception:
        logger.error("APDC attendance answer failed safely")
        answer = (
            "تعذر إكمال هذا الطلب بأمان. يرجى المحاولة مرة أخرى."
            if any("\u0600" <= char <= "\u06ff" for char in last_message)
            else "I couldn't complete that request safely. Please try again."
        )
        context = []
        updated_state = state or ConversationState()

    history.append({"role": "assistant", "content": answer})
    return history, format_context(context), updated_state


def _normalize_markdown_spacing(reply: str) -> str:
    """Keep flat lists separate from prose in model-produced Markdown."""

    lines = reply.split("\n")
    output: list[str] = []
    fence: str | None = None
    previous_was_list = False
    for line in lines:
        marker = re.match(r"\s*(`{3,}|~{3,})(.*)", line)
        if marker is not None:
            if fence is None:
                fence = marker.group(1)
            elif (
                marker.group(1)[0] == fence[0]
                and len(marker.group(1)) >= len(fence)
                and not marker.group(2).strip()
            ):
                fence = None
        is_list = fence is None and bool(re.match(r"(?:[-+*]|\d+[.)])\s+", line))
        if output and output[-1].strip() and line.strip():
            if (is_list and not previous_was_list) or (
                previous_was_list and not is_list
            ):
                output.append("")
        output.append(line)
        previous_was_list = is_list
    return "\n".join(output)


def _markdown_reveal_points(reply: str) -> list[int]:
    """Choose complete Markdown units for the reviewed answer's display pace."""

    if not reply:
        return []
    lines = reply.splitlines(keepends=True)
    points: list[int] = []
    offset = 0
    fence: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})(.*)", stripped.rstrip("\r\n"))
        if marker is not None:
            if fence is None:
                fence = marker.group(1)
            elif (
                marker.group(1)[0] == fence[0]
                and len(marker.group(1)) >= len(fence)
                and not marker.group(2).strip()
            ):
                fence = None
            offset += len(line)
            if fence is None:
                points.append(offset)
            index += 1
            continue
        if fence is not None:
            offset += len(line)
            index += 1
            continue
        if (
            index + 1 < len(lines)
            and "|" in line
            and re.fullmatch(
                r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*",
                lines[index + 1],
            )
        ):
            offset += len(line) + len(lines[index + 1])
            points.append(offset)
            index += 2
            continue
        if not any(
            token in line for token in ("`", "*", "_", "[", "]", "|", "<")
        ) and not re.match(r"\s*(?:[-+]|\d+[.)]|#{1,6})\s", line):
            sentences = list(re.finditer(r"[.!?](?=\s|$)", line))
            if sentences:
                points.extend(offset + match.end() for match in sentences)
            elif len(line) > 64:
                next_target = 64
                for match in re.finditer(r"\s+", line):
                    if match.start() >= next_target:
                        points.append(offset + match.start())
                        next_target = match.start() + 64
        offset += len(line)
        points.append(offset)
        index += 1
    if not points or points[-1] != len(reply):
        points.append(len(reply))
    points = sorted(set(points))
    stride = max(1, ceil(len(points) / _MAX_REVEAL_STEPS))
    selected = points[stride - 1 :: stride]
    if selected[-1] != len(reply):
        selected.append(len(reply))
    return selected


def chat_with_state_stream(
    history,
    state,
    gate: _TurnGate | None = None,
    generation: int | None = None,
    on_final=None,
):
    """Show the question, then reveal only the pipeline's reviewed reply."""

    current_state = state or ConversationState()
    gate = gate or _TurnGate()
    generation = gate.current() if generation is None else generation
    if not history:
        yield history, format_context([]), current_state
        return

    history = list(history)
    last_message = history[-1]["content"]
    arabic = any("\u0600" <= char <= "\u06ff" for char in last_message)
    if gate.current() != generation:
        return
    display = history + [{"role": "assistant", "content": "Thinking ..."}]
    yield list(display), format_context([]), current_state
    try:
        reply, context, updated_state = answer_question_with_state(
            last_message,
            history[:-1],
            current_state,
            access_context=LOCAL_DEMO_ACCESS,
        )
        reply = _normalize_markdown_spacing(reply)
        # The pipeline has completed its review. Only this accepted reply is
        # exposed to the browser, in cumulative chunks for progressive display.
        for index, end in enumerate(_markdown_reveal_points(reply)):
            if index:
                sleep(_REVEAL_INTERVAL_SECONDS)
            if gate.current() != generation:
                return
            final = end >= len(reply)
            display[-1] = {"role": "assistant", "content": reply[:end]}
            if final and on_final is not None:
                on_final(display, updated_state)
            yield (
                list(display),
                format_context(context if final else []),
                updated_state if final else current_state,
            )
        if not reply:
            if gate.current() != generation:
                return
            display[-1] = {"role": "assistant", "content": ""}
            if on_final is not None:
                on_final(display, updated_state)
            yield list(display), format_context(context), updated_state
    except Exception:
        if gate.current() != generation:
            return
        logger.error("APDC attendance answer failed safely")
        reply = (
            "تعذر إكمال هذا الطلب بأمان. يرجى المحاولة مرة أخرى."
            if arabic
            else "I couldn't complete that request safely. Please try again."
        )
        display[-1] = {"role": "assistant", "content": reply}
        if on_final is not None:
            on_final(display, current_state)
        yield list(display), format_context([]), current_state


def submit_chat(message, gate: _TurnGate, sequence: int, clear_sequence: int = 0):
    """Publish only a submit whose browser sequence is still current."""

    sequence = int(sequence)
    if not gate.claim_submit(sequence, int(clear_sequence)):
        return
    history, state = gate.snapshot()
    updated_history = list(history or []) + [{"role": "user", "content": message}]
    for index, (chat_history, context, updated_state) in enumerate(
        chat_with_state_stream(
            updated_history,
            state,
            gate,
            sequence,
            on_final=lambda final_history, final_state: gate.commit(
                sequence, final_history, final_state
            ),
        )
    ):
        yield {
            "sequence": sequence,
            "history": chat_history,
            "context": context,
            "clear_input": index == 0,
            "submitted_message": message,
        }


def reset_session(gate: _TurnGate, sequence: int):
    if not gate.reset(int(sequence)):
        return gr.skip(), gr.skip()
    return [], format_context([])


def reset_conversation(_state=None):
    return format_context([]), ConversationState()


def main():
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    theme = gr.themes.Soft(font=["Inter", "system-ui", "sans-serif"])

    with gr.Blocks(title="APDC Attendance Assistant", theme=theme) as ui:
        turn_gate = gr.State(value=_TurnGate())
        client_sequence = gr.Number(value=0, precision=0, visible=False)
        client_clear_sequence = gr.Number(value=0, precision=0, visible=False)
        stream_buffer = gr.JSON(visible=False)
        gr.Markdown(
            "# 🏢 APDC Attendance Assistant\n"
            "Ask about employee attendance, worked days, schedules, leave, or overtime."
        )

        with gr.Row():
            with gr.Column(scale=1):
                chatbot = gr.Chatbot(
                    label="💬 Conversation",
                    height=600,
                    type="messages",
                    show_copy_button=True,
                    render_markdown=True,
                    sanitize_html=True,
                )
                message = gr.Textbox(
                    label="Your Question",
                    placeholder="Ask an APDC attendance question...",
                    show_label=False,
                )
            with gr.Column(scale=1):
                context_markdown = gr.Markdown(
                    label="📚 Retrieved Context",
                    value="*Retrieved context will appear here*",
                    container=True,
                    height=600,
                )

        message.submit(
            submit_chat,
            inputs=[message, turn_gate, client_sequence, client_clear_sequence],
            outputs=[stream_buffer],
            js=_SUBMIT_WITH_SEQUENCE_JS,
            concurrency_limit=2,
            trigger_mode="multiple",
        )
        stream_buffer.change(
            fn=None,
            inputs=[stream_buffer, chatbot, context_markdown, message],
            outputs=[chatbot, context_markdown, message],
            js=_DISPLAY_CURRENT_SEQUENCE_JS,
            queue=False,
            trigger_mode="multiple",
            show_progress="hidden",
        )
        chatbot.clear(
            reset_session,
            inputs=[turn_gate, client_sequence],
            outputs=[chatbot, context_markdown],
            js=_CLEAR_WITH_SEQUENCE_JS,
            queue=False,
            show_progress="hidden",
        )

    ui.launch(**launch_options())


if __name__ == "__main__":
    main()
