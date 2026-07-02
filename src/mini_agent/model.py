"""Custom smolagents Model that calls Ollama's /v1/responses endpoint via httpx.

Eliminates the 5.5 s import overhead of litellm by using a bare HTTP client.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from smolagents.models import (
    ChatMessage,
    MessageRole,
    Model,
    TokenUsage,
    get_clean_message_list,
    remove_content_after_stop_sequences,
)
from smolagents.tools import Tool


def _flatten_content(msg: dict[str, Any]) -> str:
    """Extract a plain text string from a smolagents message dict.

    smolagents messages carry ``content`` as either a plain string or a list
    of ``{"type": "text", "text": "..."}`` dicts.  The Responses API expects
    a plain string.
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


class OllamaResponsesModel(Model):
    """smolagents Model that calls Ollama's ``/v1/responses`` endpoint.

    No litellm dependency — uses ``httpx`` directly.  Supports the full
    smolagents ``Model`` interface so it works with both
    ``run_fast_agent`` and ``CodeAgent`` (deep agent).

    Parameters:
        model_name:
            Ollama model tag, e.g. ``"ornith:9b"``.
        api_base:
            Ollama server URL (default ``http://localhost:11434``).
        temperature:
            Sampling temperature.
        max_output_tokens:
            Maximum tokens the model may generate (default 512).
            Prevents runaway thinking.
        **kwargs:
            Additional keyword arguments forwarded to the base ``Model``.
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
        self.api_base = api_base.rstrip("/")
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self._client = httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0))
        super().__init__(model_id=model_name, **kwargs)

    # ------------------------------------------------------------------
    # Public API — called by smolagents agents
    # ------------------------------------------------------------------

    def generate(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Tool] | None = None,
        **kwargs,
    ) -> ChatMessage:
        # 1. Clean messages (smolagents standard)
        cleaned = get_clean_message_list(
            messages,
            flatten_messages_as_text=True,
        )

        # 2. Split system message from the rest
        instructions: str | None = None
        input_items: list[dict[str, str]] = []
        for msg in cleaned:
            role = msg.get("role", "user")
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
        }
        if instructions:
            body["instructions"] = instructions

        # 4. Response format (JSON mode etc.)
        if response_format:
            body["text"] = {"format": response_format}

        # 5. Tools (native tool calling via Responses API)
        if tools_to_call_from:
            from smolagents.models import get_tool_json_schema

            body["tools"] = [get_tool_json_schema(t) for t in tools_to_call_from]

        # 6. Merge extra kwargs (e.g. num_ctx, reasoning effort)
        body.update(kwargs)

        # 7. Send request
        raw = self._post(body)

        # 8. Parse output
        output_text = self._extract_output_text(raw)
        if stop_sequences and output_text:
            output_text = remove_content_after_stop_sequences(
                output_text, stop_sequences
            )

        # 9. Extract token usage
        usage = raw.get("usage") or {}
        token_usage = TokenUsage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=output_text or "",
            raw=raw,
            token_usage=token_usage,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST to ``/v1/responses`` and return the parsed JSON."""
        url = f"{self.api_base}/v1/responses"
        resp = self._client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()

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
