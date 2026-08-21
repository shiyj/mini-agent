"""Custom Model that calls a ``/v1/responses`` endpoint via httpx.

Uses local types only. Single non-streaming POST per call - simpler to debug;
retry once (with a temperature bump) if the model returns a blank response.
Also supports streaming via Server-Sent Events (SSE).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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


class ResponsesModel:
    """Model that calls a ``/v1/responses`` compatible endpoint via httpx.

    Provider-agnostic — works with any server
    that implements the OpenAI Responses API schema (Ollama, custom proxies, etc.).

    Parameters:
        model_name: Model tag, e.g. ``"ornith:9b"``.
        api_base: Server base URL (default ``http://localhost:11434``).
        temperature: Sampling temperature.
        max_output_tokens: Maximum tokens the model may generate (default 512).
    """

    def __init__(
        self,
        model_name: str,
        api_base: str = "http://localhost:11434",
        api_key: str | None = None,
        temperature: float = 0.0,
        max_output_tokens: int = 512,
        context_window: int = 65536,
        log_file: str | None = None,
        reasoning: dict[str, Any] | None = None,
        timeout: float = 600.0,
        **kwargs,
    ):
        self.model_name = model_name
        # Normalize: accept baseURL with or without a trailing "/v1"
        base = api_base.rstrip("/")
        if base.lower().endswith("/v1"):
            base = base[:-3]
        self.api_base = base
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.context_window = context_window
        self.log_file = log_file
        self.reasoning = reasoning
        self.timeout = timeout
        self._client = httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=10.0)
        )

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
        }
        if instructions:
            body["instructions"] = instructions
        if self.reasoning is not None:
            body["reasoning"] = self.reasoning

        # 4. Response format (JSON mode etc.)
        if response_format:
            body["text"] = {"format": response_format}

        # 5. Merge extra kwargs
        body.update(kwargs)

        # 6. Send request — retry on empty output (model-side hiccup)
        max_attempts = 2
        output_text = ""
        usage_data: dict[str, Any] = {}
        for attempt in range(max_attempts):
            attempt_body = dict(body)
            if attempt > 0:
                # Bump temperature to break the model out of a blank response
                base_t = attempt_body.get("temperature") or 0.0
                attempt_body["temperature"] = min(1.0, base_t + 0.25 * attempt)
            output_text, usage_data = self._send_bulk(attempt_body)
            if output_text and output_text.strip():
                break

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

    def generate_stream(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        **kwargs,
    ) -> Iterator[str]:
        """Generate a streaming response via /v1/responses with stream=true.

        Uses the SAME endpoint and body format as ``generate()`` so both paths
        produce identical model behaviour (the earlier /v1/chat/completions
        endpoint caused the model to lose system-prompt instructions, leading
        to inconsistent Deep Agent triggering between blocking and streaming).

        Yields text chunks as they arrive from the model.
        """
        # Build request body - identical to generate() but with stream=true
        cleaned = get_clean_message_list(messages, flatten_messages_as_text=True)

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

        body: dict[str, Any] = {
            "model": self.model_name,
            "input": input_items,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "stream": True,
        }
        if instructions:
            body["instructions"] = instructions
        if self.reasoning is not None:
            body["reasoning"] = self.reasoning

        url = f"{self.api_base}/v1/responses"
        headers = {"Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._log("request", body)

        with self._client.stream("POST", url, json=body, headers=headers, timeout=httpx.Timeout(self.timeout, connect=10.0)) as resp:
            resp.raise_for_status()
            self._log("response", body, response_data={"stream": True})

            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type", "")
                if event_type == "response.output_text.delta":
                    delta = data.get("delta", "")
                    if delta:
                        yield delta
                elif event_type == "response.completed":
                    break
                elif event_type == "response.error":
                    err = data.get("error", {})
                    raise RuntimeError(f"Model stream error: {err.get('message', 'unknown')}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(
        self,
        direction: str,
        body: dict[str, Any],
        response_data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Append one JSONL entry to the log file (if configured)."""
        if not self.log_file:
            return
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "model": self.model_name,
        }
        if direction == "request":
            entry["body"] = body
        else:
            if response_data is not None:
                entry["response"] = response_data
            if error is not None:
                entry["error"] = error
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # Silently ignore write failures — logging should never crash

    def _send_bulk(
        self, body: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Send a non-streaming request and extract output from the JSON response."""
        url = f"{self.api_base}/v1/responses"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._log("request", body)
        resp = self._client.post(url, json=body, headers=headers)
        try:
            resp.raise_for_status()
            data = resp.json()
            self._log("response", body, response_data=data)
        except Exception as e:
            self._log("response", body, error=str(e))
            raise
        output_text = self._extract_output_text(data)
        usage_data = data.get("usage", {})
        return output_text, usage_data

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
