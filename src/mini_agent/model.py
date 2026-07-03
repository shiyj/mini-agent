"""Custom Model that calls Ollama's /v1/responses endpoint via httpx.

No smolagents dependency — uses local types only.
Uses SSE streaming so the first token arrives in ~0.3 s.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from .types import (
    ChatMessage,
    MessageRole,
    TokenUsage,
    get_clean_message_list,
    remove_content_after_stop_sequences,
)


def _flatten_content(msg: dict[str, Any]) -> str:
    """Extract a plain text string from a message dict.

    Messages carry ``content`` as either a plain string or a list
    of ``{"type": "text", "text": "..."}`` dicts.
    """
    raw = msg.get("content")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts)
    return ""


def _parse_sse(line: str) -> tuple[str, str] | None:
    """Parse a single SSE line into (event_type, data) or None."""
    line = line.strip()
    if not line:
        return None
    if line.startswith("event: "):
        return ("event", line[7:])
    if line.startswith("data: "):
        return ("data", line[6:])
    return None


class OllamaResponsesModel:
    """Model that calls Ollama's ``/v1/responses`` endpoint via httpx.

    No smolagents dependency. Uses SSE streaming internally.

    Parameters:
        model_name: Ollama model tag, e.g. ``"ornith:9b"``.
        api_base: Ollama server URL (default ``http://localhost:11434``).
        temperature: Sampling temperature.
        max_output_tokens: Maximum tokens the model may generate (default 512).
    """

    def __init__(
        self,
        model_name: str,
        api_base: str = "http://localhost:11434",
        temperature: float = 0.0,
        max_output_tokens: int = 512,
        **kwargs,
    ):
        self.model_name = model_name
        self.model_id = model_name
        self.api_base = api_base.rstrip("/")
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self._client = httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list | None = None,
        **kwargs,
    ) -> ChatMessage:
        """Generate a response from the model.

        Args:
            messages: List of messages (ChatMessage or dict).
            stop_sequences: Stop sequences to truncate the output.
            response_format: Optional response format spec.
            tools_to_call_from: Optional list of tools (not used currently).

        Returns:
            A ChatMessage with the model's response.
        """
        # 1. Clean and normalize messages
        cleaned = get_clean_message_list(
            messages, flatten_messages_as_text=True
        )

        # 2. Split system message from the rest
        instructions: str | None = None
        input_items: list[dict[str, str]] = []
        for msg in cleaned:
            role = msg.get("role", "user")
            text = msg.get("content", "")
            if not isinstance(text, str):
                text = _flatten_content(msg)
            if role == "system":
                instructions = text
            else:
                input_items.append({"role": role, "content": text})

        # 3. Build the request body
        body: dict[str, Any] = {
            "model": self.model_name,
            "input": input_items,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "stream": True,
        }
        if instructions:
            body["instructions"] = instructions

        # 4. Response format (JSON mode etc.)
        if response_format:
            body["text"] = {"format": response_format}

        # 5. Merge extra kwargs
        body.update(kwargs)

        # 6. Stream SSE events and collect output
        output_text = ""
        usage_data: dict[str, Any] = {}
        for event_type, data in self._iter_sse(body):
            if event_type == "response.output_text.delta":
                delta = data.get("delta", "")
                if isinstance(delta, str):
                    output_text += delta
            elif event_type == "response.completed":
                resp_data = data.get("response") or data
                usage_data = resp_data.get("usage", {})
                if not output_text:
                    output_text = self._extract_output_text(resp_data)

        # 7. Apply stop sequences
        if stop_sequences and output_text:
            output_text = remove_content_after_stop_sequences(
                output_text, stop_sequences
            )

        # 8. Token usage
        token_usage = TokenUsage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
        )

        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=output_text or "",
            raw=usage_data,
            token_usage=token_usage,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _iter_sse(
        self, body: dict[str, Any]
    ) -> Iterator[tuple[str, Any]]:
        """POST to ``/v1/responses`` with ``stream: true`` and yield
        ``(event_type, parsed_data)`` tuples from the SSE stream."""
        url = f"{self.api_base}/v1/responses"
        with self._client.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            current_event = ""
            for raw_line in resp.iter_lines():
                parsed = _parse_sse(raw_line)
                if parsed is None:
                    continue
                kind, value = parsed
                if kind == "event":
                    current_event = value
                elif kind == "data" and current_event:
                    try:
                        data = json.loads(value)
                    except json.JSONDecodeError:
                        data = value
                    yield (current_event, data)
                    current_event = ""

    @staticmethod
    def _extract_output_text(raw: dict[str, Any]) -> str:
        """Walk ``output`` items and collect ``output_text`` fragments."""
        fragments: list[str] = []
        for item in raw.get("output", []):
            if item.get("type") != "message":
                continue
            for content_item in item.get("content", []):
                if isinstance(content_item, dict) and content_item.get("type") == "output_text":
                    fragments.append(content_item.get("text", ""))
        return "".join(fragments)
