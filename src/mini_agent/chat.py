"""Chat session module — lightweight conversation management with history, persistence, streaming, and token tracking."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from .model import ResponsesModel
from .types import ChatMessage, MessageRole


class ChatSession:
    """Lightweight chat session with conversation history, persistence, and token tracking.

    Features:
    - Conversation history management (system + user/assistant turns)
    - Custom system prompt (mutable at runtime)
    - JSONL persistence (append-only, crash-safe)
    - Token usage tracking
    - Streaming support (when model supports it)
    - Token usage statistics
    - Session metadata (id, created_at, model info)
    """

    def __init__(
        self,
        model: Any,  # ResponsesModel or any object with generate()
        system_prompt: str = "You are a helpful assistant.",
        session_id: Optional[str] = None,
        persist_path: Optional[str | Path] = None,
    ):
        """
        Args:
            model: Model instance with generate(messages, stop_sequences) -> ChatMessage
            system_prompt: System prompt (plain string, rendered by caller if needed)
            session_id: Optional session identifier (auto-generated if not provided)
            persist_path: Optional path to JSONL file for crash-safe persistence
        """
        self._model = model
        self._system_prompt = system_prompt
        self._session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        self._persist_path = Path(persist_path) if persist_path else None
        self._history: list[ChatMessage] = []
        self._total_tokens = {"input": 0, "output": 0, "total": 0}
        self._created_at = datetime.now().isoformat()
        self._metadata = {
            "session_id": self._session_id,
            "created_at": self._created_at,
            "model_name": getattr(model, "model_name", "unknown"),
            "model_params": {
                "temperature": getattr(model, "temperature", None),
                "max_output_tokens": getattr(model, "max_output_tokens", None),
            },
        }

        # Load existing history if persist file exists
        if self._persist_path and self._persist_path.exists():
            self._load_history()

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._system_prompt = value

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def history(self) -> list[ChatMessage]:
        return list(self._history)

    @property
    def total_tokens(self) -> dict[str, int]:
        return dict(self._total_tokens)

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    # -------------------------------------------------------------------------
    # Core chat logic
    # -------------------------------------------------------------------------

    def _build_messages(self, user_text: str) -> list[dict]:
        """Build messages for generate(): system + history + current user."""
        msgs: list[dict] = [
            {"role": "system", "content": [{"type": "text", "text": self.system_prompt}]}
        ]
        for msg in self._history:
            msgs.append({"role": msg.role.value, "content": msg.content})
        msgs.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
        return msgs

    def _persist(self, record: dict) -> None:
        """Append a record to the JSONL persistence file (crash-safe append)."""
        if self._persist_path:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load_history(self) -> None:
        """Load conversation history from persistence file."""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("type") == "message" and "role" in rec and "content" in rec:
                        role = MessageRole.USER if rec["role"] == "user" else MessageRole.ASSISTANT
                        self._history.append(ChatMessage(role=role, content=rec["content"]))
        except Exception:
            # Corrupted file - start fresh
            self._history.clear()

    def _update_token_usage(self, response: Any) -> None:
        """Extract and accumulate token usage from response."""
        tu = getattr(response, "token_usage", None)
        if tu:
            inp = getattr(tu, "input_tokens", 0) or tu.get("input_tokens", 0)
            out = getattr(tu, "output_tokens", 0) or tu.get("output_tokens", 0)
            self._total_tokens["input"] += inp
            self._total_tokens["output"] += out
            self._total_tokens["total"] += inp + out

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def chat(
        self,
        user_text: str,
        stop_sequences: Optional[list[str]] = None,
        stream: bool = False,
    ) -> str | Iterator[str]:
        """Send a user message, return assistant reply (or stream chunks).

        Args:
            user_text: User input text
            stop_sequences: Optional stop sequences for generation
            stream: If True, return iterator of chunks (requires model.generate_stream)

        Returns:
            Assistant response text, or iterator of chunks if stream=True
        """
        msgs = self._build_messages(user_text)

        # Record user message to history
        user_msg = ChatMessage(role=MessageRole.USER, content=user_text)
        self._history.append(user_msg)
        self._persist({"type": "message", "role": "user", "content": user_text, "timestamp": datetime.now().isoformat()})

        if stream:
            return self._chat_stream(msgs, stop_sequences)
        else:
            return self._chat_blocking(msgs, stop_sequences)

    def _chat_blocking(self, messages: list[dict], stop_sequences: Optional[list[str]]) -> str:
        """Blocking single-call generation."""
        response = self._model.generate(messages=messages, stop_sequences=stop_sequences)
        self._update_token_usage(response)

        content = response.content or ""
        # Record assistant message to history
        asst_msg = ChatMessage(role=MessageRole.ASSISTANT, content=content)
        self._history.append(asst_msg)
        self._persist({"type": "message", "role": "assistant", "content": content, "timestamp": datetime.now().isoformat()})
        return content

    def _chat_stream(self, messages: list[dict], stop_sequences: Optional[list[str]]) -> Iterator[str]:
        """Streaming generation (requires model.generate_stream).

        Falls back to blocking mode if model doesn't support streaming.
        """
        if not hasattr(self._model, "generate_stream"):
            # Fall back to blocking mode, yielding the full response as a single chunk
            response = self._model.generate(messages=messages, stop_sequences=stop_sequences)
            self._update_token_usage(response)
            content = response.content or ""
            asst_msg = ChatMessage(role=MessageRole.ASSISTANT, content=content)
            self._history.append(asst_msg)
            self._persist({"type": "message", "role": "assistant", "content": content, "timestamp": datetime.now().isoformat()})
            yield content
            return

        full_chunks: list[str] = []
        for chunk in self._model.generate_stream(messages=messages, stop_sequences=stop_sequences):
            full_chunks.append(chunk)
            yield chunk

        # After streaming, record full response
        full_content = "".join(full_chunks)
        asst_msg = ChatMessage(role=MessageRole.ASSISTANT, content=full_content)
        self._history.append(asst_msg)
        self._persist({"type": "message", "role": "assistant", "content": full_content, "timestamp": datetime.now().isoformat()})

    # -------------------------------------------------------------------------
    # Session management
    # -------------------------------------------------------------------------

    def reset(self) -> None:
        """Clear conversation history (keeps system prompt and metadata)."""
        self._history.clear()
        if self._persist_path and self._persist_path.exists():
            self._persist_path.unlink()

    def get_history(self) -> list[ChatMessage]:
        return list(self._history)

    def export_history(self, path: str | Path) -> None:
        """Export conversation history to JSON file."""
        data = {
            "metadata": self.metadata,
            "system_prompt": self.system_prompt,
            "messages": [
                {"role": m.role.value, "content": m.content} for m in self._history
            ],
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def import_history(cls, path: str | Path, model: Any, **kwargs) -> "ChatSession":
        """Create session from exported history file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        session = cls(model, system_prompt=data.get("system_prompt", "You are a helpful assistant."), **kwargs)
        for msg in data.get("messages", []):
            role = MessageRole.USER if msg["role"] == "user" else MessageRole.ASSISTANT
            session._history.append(ChatMessage(role=role, content=msg["content"]))
        return session