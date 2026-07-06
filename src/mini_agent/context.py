"""Context compression — pluggable strategies to keep conversation history bounded.

Usage:
    from mini_agent import SummaryCompressor

    compressor = SummaryCompressor(keep_recent=6, trigger_threshold=10)
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


class SummaryCompressor(ContextCompressor):
    """Keep last N messages; replace older ones with an LLM-generated summary.

    When len(messages) > trigger_threshold:
      [system] [task] [old_1..old_k] [recent_1..recent_N]
      becomes:
      [system] [task] [summary] [recent_1..recent_N]

    The summary is produced by calling *model* on the old messages.
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
        keep_recent: int = 6,
        trigger_threshold: int = 10,
    ):
        self.keep_recent = keep_recent
        self.trigger_threshold = trigger_threshold

    def compress(
        self,
        messages: list[dict],
        model: "ResponsesModel",
    ) -> list[dict]:
        if len(messages) <= self.trigger_threshold:
            return messages

        system_msg = messages[0]
        task_msg = messages[1] if len(messages) > 1 else None
        old_messages = messages[2 : -self.keep_recent]
        recent_messages = messages[-self.keep_recent :]

        if not old_messages:
            return messages

        summary = self._summarize(old_messages, model)

        result = [system_msg]
        if task_msg:
            result.append(task_msg)
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

        original_max = model.max_output_tokens
        model.max_output_tokens = 512
        try:
            response = model.generate(messages=summarization_messages)
        finally:
            model.max_output_tokens = original_max

        return (response.content or "").strip() or "(no summary)"
