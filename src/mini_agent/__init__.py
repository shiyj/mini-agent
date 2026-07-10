"""mini_agent — local lightweight programming agent.

Uses httpx for model calls and exec() for code execution.

ReAct architecture with two depths:
  - run_fast_agent()   single-pass code generation, no tools
  - run_deep_agent()   full ReAct loop with tools and iteration
"""

from .agent import (
    get_local_model,
    read_file,
    run_python_file,
    execute_bash_command,
    run_fast_agent,
    run_deep_agent,
    run_deep_agent_with_config,
)
from .config import load_agent_config
from .context import ContextCompressor, SummaryCompressor
from .model import ResponsesModel
from .tools import Tool, tool
from .types import ChatMessage, MessageRole, TokenUsage

__all__ = [
    "get_local_model",
    "ResponsesModel",
    "read_file",
    "run_python_file",
    "execute_bash_command",
    "run_fast_agent",
    "run_deep_agent",
    "run_deep_agent_with_config",
    "load_agent_config",
    "ContextCompressor",
    "SummaryCompressor",
    "Tool",
    "tool",
    "ChatMessage",
    "MessageRole",
    "TokenUsage",
]
