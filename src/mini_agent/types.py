"""Core data types — ChatMessage, MessageRole, TokenUsage.

No external dependencies. These are plain dataclasses and enums.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

    @classmethod
    def roles(cls) -> list[str]:
        return [r.value for r in cls]


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class ChatMessage:
    role: MessageRole
    content: str | list[dict[str, Any]] | None = None
    raw: Any | None = None
    token_usage: TokenUsage | None = None

    def dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value if isinstance(self.role, MessageRole) else self.role,
            "content": self.content,
        }


def remove_content_after_stop_sequences(
    content: str | None, stop_sequences: list[str] | None
) -> str | None:
    """Truncate content at the first occurrence of any stop sequence."""
    if content is None or not stop_sequences:
        return content
    for seq in stop_sequences:
        content = content.split(seq)[0]
    return content


def get_clean_message_list(
    messages: list[ChatMessage | dict],
    flatten_messages_as_text: bool = False,
) -> list[dict[str, Any]]:
    """Normalize a mixed list of ChatMessage/dict into plain dicts.

    Consecutive messages with the same role are merged.
    Content lists of {"type": "text", "text": "..."} are flattened to strings.
    """
    output: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            raw_content = msg.get("content")
        else:
            role = msg.role.value if isinstance(msg.role, MessageRole) else msg.role
            raw_content = msg.content

        # Flatten content
        if isinstance(raw_content, list):
            parts: list[str] = []
            for item in raw_content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            text = "".join(parts)
        elif isinstance(raw_content, str):
            text = raw_content
        else:
            text = str(raw_content) if raw_content else ""

        # Merge consecutive same-role messages
        if output and output[-1]["role"] == role:
            output[-1]["content"] += "\n" + text
        else:
            output.append({"role": role, "content": text})
    return output
