"""mini_agent — local lightweight programming agent.

Uses httpx for model calls and exec() for code execution.

ReAct architecture with two depths:
  - run_fast_agent()   single-pass code generation, no tools
  - run_deep_agent()   full ReAct loop with tools and iteration
"""

from .agent import (
    read_file,
    run_python_file,
    run_fast_agent,
    run_deep_agent,
)
from .chat import ChatSession
from .config import load_model
from .context import ContextCompressor, SummaryCompressor
from .model import ResponsesModel
from .tools import Tool, tool
from .types import ChatMessage, MessageRole, TokenUsage

__all__ = [
    "load_model",
    "ResponsesModel",
    "read_file",
    "run_python_file",
    "run_fast_agent",
    "run_deep_agent",
    "ChatSession",
    "ContextCompressor",
    "SummaryCompressor",
    "Tool",
    "tool",
    "ChatMessage",
    "MessageRole",
    "TokenUsage",
]
