"""One bounded conversation-history view shared by both model stages."""

from __future__ import annotations


MAX_HISTORY_CHARS = 24000
RECENT_MESSAGES = 12
MAX_MESSAGES = 200


def model_history(history: tuple[dict[str, str], ...]) -> tuple[dict[str, str], ...]:
    """Keep full history when it fits; compact older text when it does not."""

    messages = tuple(
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    )
    if (
        len(messages) <= MAX_MESSAGES
        and sum(len(item["content"]) for item in messages) <= MAX_HISTORY_CHARS
    ):
        return messages

    recent = messages[-RECENT_MESSAGES:]
    older = messages[:-RECENT_MESSAGES]
    if sum(len(item["content"]) for item in recent) > MAX_HISTORY_CHARS // 2:
        per_message = max(1, (MAX_HISTORY_CHARS // 2) // max(1, len(recent)))
        recent = tuple(
            {
                "role": item["role"],
                "content": (
                    item["content"]
                    if len(item["content"]) <= per_message
                    else item["content"][: per_message - 1] + "…"
                ),
            }
            for item in recent
        )
    recent_chars = sum(len(item["content"]) for item in recent)
    budget = max(0, MAX_HISTORY_CHARS - recent_chars)
    excerpts: list[str] = []
    if older and budget:
        per_message = max(1, min(240, (budget - 120) // len(older)))
        for index, item in enumerate(older, start=1):
            content = " ".join(item["content"].split())
            if len(content) > per_message:
                content = content[: per_message - 1] + "…"
            excerpts.append(f"{index}. {item['role']}: {content}")
    summary = {
        "role": "history_compaction",
        "content": (
            f"Earlier conversation ({len(older)} messages; text shortened to fit "
            "the context budget):\n" + "\n".join(excerpts)
        ),
    }
    summary["content"] = summary["content"][:budget]
    return (summary, *recent)


__all__ = ["model_history"]
