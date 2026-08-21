"""Chat session - conversation management with history, persistence, streaming,
token tracking, and Deep Agent (run_deep_agent) delegation.

Per-turn flow:
    user message -> model.generate() / generate_stream()
        -> if the response contains a ```deep block, delegate the task inside
           to run_deep_agent (the sub-agent) and append its result
        -> the combined assistant reply is recorded to history + JSONL
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from .agent import run_deep_agent
from .prompts import DEEP_AGENT_AWARE_SYSTEM
from .types import ChatMessage, MessageRole


# One regex for the Deep Agent trigger. Handles both a bare
#     ```deep
#     task
#     ```
# and the same block wrapped in an outer code fence (which 9B models often add):
#     ```
#     ```deep
#     task
#     ```
#     ```
_DEEP_AGENT_RE = re.compile(
    r"(?:```\s*\n)?```deep[ \t]*\n(.*?)\n```(?:\s*\n```)?",
    re.DOTALL,
)


class ChatSession:
    """Lightweight chat session with history, persistence, streaming, token
    tracking, and Deep Agent delegation."""

    def __init__(
        self,
        model: Any,  # ResponsesModel or any object with generate()/generate_stream()
        system_prompt: str = "You are a helpful assistant.",
        session_id: Optional[str] = None,
        persist_path: Optional[str | Path] = None,
        enable_deep_agent: bool = True,
        deep_agent_config: Optional[dict] = None,
        max_history_turns: int = 20,
        debug: bool = False,
    ):
        """
        Args:
            model: Model with generate(messages, stop_sequences) -> ChatMessage.
            system_prompt: Plain system prompt (rendered by caller if needed).
            session_id: Auto-generated if not provided.
            persist_path: Optional JSONL file for crash-safe persistence.
            enable_deep_agent: Allow ```deep delegation (default: True).
            deep_agent_config: Optional knobs (max_steps, max_observation_length,
                verbose) forwarded to run_deep_agent.
            max_history_turns: Max user/assistant turn pairs kept in the sent
                context (see _build_messages). None = unbounded.
        """
        self._model = model
        self._system_prompt = system_prompt
        self._session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        self._persist_path = Path(persist_path) if persist_path else None
        self._history: list[ChatMessage] = []
        self._total_tokens = {"input": 0, "output": 0, "total": 0}
        self._created_at = datetime.now().isoformat()
        self._enable_deep_agent = enable_deep_agent
        self._deep_agent_config = deep_agent_config or {}
        self._max_history_turns = max_history_turns
        self._effective_system_cache: Optional[str] = None

        self._metadata = {
            "session_id": self._session_id,
            "created_at": self._created_at,
            "model_name": getattr(model, "model_name", "unknown"),
            "model_params": {
                "temperature": getattr(model, "temperature", None),
                "max_output_tokens": getattr(model, "max_output_tokens", None),
            },
        }
        self._debug = debug

        if self._persist_path and self._persist_path.exists():
            self._load_history()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._system_prompt = value
        self._effective_system_cache = None  # invalidate cache

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

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------

    def _effective_system(self) -> str:
        """System prompt, with the Deep Agent protocol appended when enabled."""
        if self._effective_system_cache is None:
            if self._enable_deep_agent:
                self._effective_system_cache = (
                    f"{self._system_prompt}\n\n{DEEP_AGENT_AWARE_SYSTEM}"
                )
            else:
                self._effective_system_cache = self._system_prompt
        return self._effective_system_cache

    def _recent_history(self) -> list[ChatMessage]:
        """History trimmed to the last max_history_turns pairs."""
        if not self._max_history_turns:
            return self._history
        limit = self._max_history_turns * 2  # user + assistant per turn
        return self._history[-limit:]

    def _build_messages(self, user_text: str) -> list[dict]:
        """Build messages for the model: system + (trimmed) history + current user.

        The current user message is appended ONLY here, so it appears exactly once.
        """
        msgs: list[dict] = [{"role": "system", "content": self._effective_system()}]
        for msg in self._recent_history():
            msgs.append({"role": msg.role.value, "content": msg.content})
        msgs.append({"role": "user", "content": user_text})
        return msgs

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, record: dict) -> None:
        """Append one JSON line to the persistence file (crash-safe)."""
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._persist_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load_history(self) -> None:
        """Restore history from the persistence file, skipping corrupted lines."""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # skip one bad line, keep the rest
                    if rec.get("type") == "message" and "role" in rec and "content" in rec:
                        role = (
                            MessageRole.USER
                            if rec["role"] == "user"
                            else MessageRole.ASSISTANT
                        )
                        if isinstance(rec["content"], str):
                            self._history.append(
                                ChatMessage(role=role, content=rec["content"])
                            )
        except OSError:
            pass  # unreadable file - start fresh

    # ------------------------------------------------------------------
    # Token tracking
    # ------------------------------------------------------------------

    def _update_token_usage(self, response: Any) -> None:
        """Accumulate token usage from a ChatMessage/TokenUsage response."""
        tu = getattr(response, "token_usage", None)
        if tu is None:
            return
        if hasattr(tu, "input_tokens"):
            inp = tu.input_tokens or 0
            out = tu.output_tokens or 0
        elif isinstance(tu, dict):
            inp = tu.get("input_tokens", 0)
            out = tu.get("output_tokens", 0)
        else:
            return
        self._total_tokens["input"] += inp
        self._total_tokens["output"] += out
        self._total_tokens["total"] += inp + out

    # ------------------------------------------------------------------
    # Deep Agent integration
    # ------------------------------------------------------------------

    def _extract_deep_trigger(self, content: str) -> Optional[str]:
        """Return the task inside a ```deep block, or None."""
        if not self._enable_deep_agent:
            return None
        match = _DEEP_AGENT_RE.search(content)
        task = match.group(1).strip() if match else ""
        return task or None

    def _strip_deep_trigger(self, content: str) -> str:
        """Remove the ```deep block (and any wrapping fence leaves)."""
        cleaned = _DEEP_AGENT_RE.sub("", content)
        # Drop a lone ``` line left behind if an outer wrapping fence was used.
        cleaned = re.sub(r"^\s*```\s*$", "", cleaned, flags=re.MULTILINE)
        return cleaned.strip()

    def _summarize_history_for_deep_agent(self) -> str:
        """Summarize recent conversation for the sub-agent (it has no memory).

        The Deep Agent runs independently between calls, so contextual facts the
        model already established must be re-stated in the delegated task.
        """
        if not self._history:
            return "No previous conversation."
        lines = []
        for msg in self._recent_history():
            role = "User" if msg.role == MessageRole.USER else "Assistant"
            content = msg.content[:300] + ("..." if len(msg.content) > 300 else "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines) if lines else "No previous conversation."

    def _delegate_to_deep_agent(self, task: str) -> str:
        """Run a task through the sub-agent (run_deep_agent)."""
        context = self._summarize_history_for_deep_agent()
        prompt = (
            f"## Conversation Context (summarized)\n{context}\n\n"
            f"## Task\n{task}\n\n"
            "## Important Instructions\n"
            "- Write NEW Python scripts to accomplish the task.\n"
            "- For QUERY/INFO tasks (get time, check system, read data): just write and run "
            "a script that prints the result, then call final_answer with the printed output. "
            "You do NOT need to save any file to disk.\n"
            "- For FILE-MODIFICATION tasks: write the file with a ```file:<path>``` block, "
            "then run it with run_python_file to verify.\n"
            "- Do NOT try to read or run files that may not exist.\n"
            "- The final_answer(x) argument MUST BE the actual deliverable (the report text, "
            "the query result, etc). NEVER use a placeholder like 'done', 'fixed', or 'ok'."
        )
        return run_deep_agent(
            prompt=prompt,
            model=self._model,
            max_steps=self._deep_agent_config.get("max_steps", 15),
            max_observation_length=self._deep_agent_config.get("max_observation_length", 2000),
            verbose=self._deep_agent_config.get("verbose", False),
        )

    def _run_deep_agent_safe(self, task: str) -> str:
        """Delegate to the sub-agent, turning any failure into a surfaced result."""
        try:
            return self._delegate_to_deep_agent(task)
        except Exception as e:  # noqa: BLE001 - a failed delegate must not crash chat
            return f"[Deep Agent failed: {type(e).__name__}: {e}]"

    def _handle_deep_trigger(self, content: str) -> str:
        """If content has a ```deep block, delegate and return the combined text."""
        task = self._extract_deep_trigger(content)
        if not task:
            return content
        clean = self._strip_deep_trigger(content)
        deep_result = self._run_deep_agent_safe(task)
        return f"{clean}\n\n---\n{deep_result}" if clean else deep_result

    # ------------------------------------------------------------------
    # Turn recording
    # ------------------------------------------------------------------

    def _record_turn(self, user_text: str, assistant_content: str) -> None:
        """Append one user + assistant turn to history and persistence."""
        now = datetime.now().isoformat()
        self._history.append(ChatMessage(role=MessageRole.USER, content=user_text))
        self._history.append(
            ChatMessage(role=MessageRole.ASSISTANT, content=assistant_content)
        )
        self._persist(
            {"type": "message", "role": "user", "content": user_text, "timestamp": now}
        )
        self._persist(
            {
                "type": "message",
                "role": "assistant",
                "content": assistant_content,
                "timestamp": now,
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        user_text: str,
        stop_sequences: Optional[list[str]] = None,
        stream: bool = False,
    ) -> str | Iterator[str]:
        """Send a user message and return the assistant reply.

        When ``stream=True`` and the model supports ``generate_stream``, returns
        a generator of text chunks (including the Deep Agent result if triggered).
        Otherwise returns the full reply as a string, or a single-chunk generator
        for the streaming call (fallback).

        An explicit ```deep block in the *user's* message is honored directly:
        the task is delegated to the Deep Agent without calling the model (model
        auto-triggering is unreliable on small models, so this gives a reliable
        escape hatch).

        Args:
            user_text: The user's message.
            stop_sequences: Optional stop sequences for generation.
            stream: Request streaming output.

        Returns:
            Assistant reply (str, or Iterator[str] when stream=True).
        """
        if self._debug:
            print(f"[DEBUG] ChatSession.chat() called with user_text={user_text!r}, stream={stream}", flush=True)
        if stream:
            return self._chat_stream(user_text, stop_sequences)

        explicit = self._extract_deep_trigger(user_text)
        if explicit:
            if self._debug:
                print(f"[DEBUG] Explicit deep trigger detected: {explicit!r}", flush=True)
            result = self._run_deep_agent_safe(explicit)
            self._record_turn(user_text, result)
            return result

        return self._chat_blocking(user_text, stop_sequences)

    def _chat_blocking(self, user_text: str, stop_sequences: Optional[list[str]]) -> str:
        if self._debug:
            print(f"[DEBUG] _chat_blocking called with user_text={user_text!r}", flush=True)
        messages = self._build_messages(user_text)
        response = self._model.generate(messages=messages, stop_sequences=stop_sequences)
        self._update_token_usage(response)
        content = self._handle_deep_trigger(response.content or "")
        self._record_turn(user_text, content)
        if self._debug:
            print(f"[DEBUG] _chat_blocking response: {content[:200]!r}...", flush=True)
        return content

    def _chat_stream(
        self, user_text: str, stop_sequences: Optional[list[str]]
    ) -> Iterator[str]:
        """Stream output, handling a trailing Deep Agent trigger if present."""
        if self._debug:
            print(f"[DEBUG] _chat_stream called with user_text={user_text!r}", flush=True)
        # Honor an explicit ```deep block in the user's message directly.
        explicit = self._extract_deep_trigger(user_text)
        if explicit:
            if self._debug:
                print(f"[DEBUG] Explicit deep trigger in user message: {explicit!r}", flush=True)
            result = self._run_deep_agent_safe(explicit)
            self._record_turn(user_text, result)
            yield result
            return

        messages = self._build_messages(user_text)

        if not hasattr(self._model, "generate_stream"):
            # Fall back to a single blocking call, yielded as one chunk.
            if self._debug:
                print("[DEBUG] Model doesn't support generate_stream, falling back to blocking", flush=True)
            yield self._chat_blocking(user_text, stop_sequences)
            return

        chunks: list[str] = []
        for chunk in self._model.generate_stream(
            messages=messages, stop_sequences=stop_sequences
        ):
            chunks.append(chunk)
            if self._debug:
                print(f"[DEBUG] Streaming chunk: {chunk!r}", flush=True)
            yield chunk

        content = "".join(chunks)
        task = self._extract_deep_trigger(content)
        if task:
            if self._debug:
                print(f"[DEBUG] Deep agent trigger detected in stream: {task!r}", flush=True)
            yield "\n\n---\n"
            deep_result = self._run_deep_agent_safe(task)
            yield deep_result
            clean = self._strip_deep_trigger(content)
            final = f"{clean}\n\n---\n{deep_result}" if clean else deep_result
        else:
            final = content
        self._record_turn(user_text, final)

    def deep_chat(self, task: str) -> str:
        """Explicitly run a task through the Deep Agent and record it as a turn."""
        result = self._run_deep_agent_safe(task)
        self._record_turn(task, result)
        return result

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear conversation history (keeps system prompt and metadata)."""
        self._history.clear()
        if self._persist_path and self._persist_path.exists():
            self._persist_path.unlink()

    def get_history(self) -> list[ChatMessage]:
        return list(self._history)

    def export_history(self, path: str | Path) -> None:
        """Export conversation history + metadata to a JSON file."""
        data = {
            "metadata": self.metadata,
            "system_prompt": self.system_prompt,
            "messages": [
                {"role": m.role.value, "content": m.content} for m in self._history
            ],
        }
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def import_history(
        cls, path: str | Path, model: Any, **kwargs
    ) -> "ChatSession":
        """Create a session from an exported history file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        session = cls(
            model,
            system_prompt=data.get("system_prompt", "You are a helpful assistant."),
            **kwargs,
        )
        for msg in data.get("messages", []):
            role = (
                MessageRole.USER if msg["role"] == "user" else MessageRole.ASSISTANT
            )
            if isinstance(msg.get("content"), str):
                session._history.append(
                    ChatMessage(role=role, content=msg["content"])
                )
        return session