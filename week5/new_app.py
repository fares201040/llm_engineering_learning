import html
import logging
import os

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


def reset_conversation(_state=None):
    return format_context([]), ConversationState()


def main():
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    def put_message_in_chatbot(message, history):
        return "", history + [{"role": "user", "content": message}]

    theme = gr.themes.Soft(font=["Inter", "system-ui", "sans-serif"])

    with gr.Blocks(title="APDC Attendance Assistant", theme=theme) as ui:
        conversation_state = gr.State(value=ConversationState())
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
            put_message_in_chatbot,
            inputs=[message, chatbot],
            outputs=[message, chatbot],
        ).then(
            chat_with_state,
            inputs=[chatbot, conversation_state],
            outputs=[chatbot, context_markdown, conversation_state],
        )
        chatbot.clear(
            reset_conversation,
            inputs=[conversation_state],
            outputs=[context_markdown, conversation_state],
            show_progress="hidden",
        )

    ui.launch(**launch_options())


if __name__ == "__main__":
    main()
