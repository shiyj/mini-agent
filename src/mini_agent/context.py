"""Context compression — pluggable strategies to keep conversation history bounded.

Usage:
    from mini_agent import SummaryCompressor

    compressor = SummaryCompressor(max_messages=20, compress_batch=5, keep_recent=6)
    result = run_deep_agent(prompt, model, compressor=compressor)

Or implement your own by subclassing ContextCompressor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import ResponsesModel


class ContextCompressor:
    """Base class — override compress() for a custom strategy.

    compress() is called before each model call in react_loop.
    It receives the full message list and may return a shortened version.
    """

    def compress(
        self,
        messages: list[dict],
        model: "ResponsesModel",
    ) -> list[dict]:
        return messages


# Rough estimate: ~4 chars per token for English text
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _truncate_by_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to roughly fit within max_tokens."""
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n... [truncated] ...\n" + text[-half:]


class SummaryCompressor(ContextCompressor):
    """Batched context compression via LLM summarization.

    When ``len(messages) > max_messages``, the oldest messages (before the
    last ``keep_recent``) are compressed in batches of ``compress_batch``.
    Each batch is summarized into a single user message, reducing the total
    message count by ``(compress_batch - 1)`` per batch.

    Parameters:
        max_messages: Trigger compression when total messages exceed this.
        compress_batch: Number of old messages to compress into one summary.
        keep_recent: Number of most recent messages to always preserve verbatim.
        max_summary_tokens: Max output tokens for each summary generation call.
    """

    SUMMARY_INSTRUCTION = (
        "Summarize the following conversation between a programmer agent and "
        "its execution environment. Focus on:\n"
        "- What code was written and why\n"
        "- What errors occurred and how they were addressed\n"
        "- Key findings, decisions, and current state\n"
        "Be concise — at most 5 sentences."
    )

    def __init__(
        self,
        max_messages: int = 20,
        compress_batch: int = 5,
        keep_recent: int = 6,
        max_summary_tokens: int = 512,
    ):
        self.max_messages = max_messages
        self.compress_batch = compress_batch
        self.keep_recent = keep_recent
        self.max_summary_tokens = max_summary_tokens

    def compress(
        self,
        messages: list[dict],
        model: "ResponsesModel",
    ) -> list[dict]:
        if len(messages) <= self.max_messages:
            return messages

        system_msg = messages[0]
        task_msg = messages[1] if len(messages) > 1 and messages[1].get("role") == "user" else None

        old_start = 2 if task_msg else 1
        old_messages = messages[old_start : -self.keep_recent]
        recent_messages = messages[-self.keep_recent :]

        if not old_messages:
            return messages

        # Compress old messages in batches
        compressed_content: list[str] = []
        remaining = list(old_messages)
        batch_size = min(self.compress_batch, len(remaining))
        while remaining:
            batch = remaining[:batch_size]
            remaining = remaining[batch_size:]
            summary = self._summarize(batch, model)
            compressed_content.append(summary)

        result = [system_msg]
        if task_msg:
            result.append(task_msg)
        for summary in compressed_content:
            result.append({"role": "user", "content": f"[Previous context summary]\n{summary}"})
        result.extend(recent_messages)
        return result

    def _summarize(
        self,
        messages: list[dict],
        model: "ResponsesModel",
    ) -> str:
        transcript = "\n\n".join(
            f"[{m['role']}]\n{m['content'] if isinstance(m['content'], str) else m['content']}"
            for m in messages
        )

        # Guard: truncate transcript if it would exceed the model's input budget
        max_input_tokens = model.context_window - model.max_output_tokens
        available_for_transcript = max_input_tokens - 200  # room for system + overhead
        if _estimate_tokens(transcript) > available_for_transcript:
            transcript = _truncate_by_tokens(transcript, available_for_transcript)

        summarization_messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": self.SUMMARY_INSTRUCTION}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": transcript}],
            },
        ]

        max_tokens = min(self.max_summary_tokens, max_input_tokens // 2)

        original_max = model.max_output_tokens
        model.max_output_tokens = max_tokens
        try:
            response = model.generate(messages=summarization_messages)
        finally:
            model.max_output_tokens = original_max

        return (response.content or "").strip() or "(no summary)"
