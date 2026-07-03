"""mini_agent — local lightweight programming agent.

No smolagents dependency. Uses httpx for model calls and exec() for code execution.

ReAct architecture with two depths:
  - run_fast_agent()   single-pass code generation, no tools
  - run_deep_agent()   full ReAct loop with tools and iteration
"""

from .agent import (
    get_local_model,
    write_file,
    run_python_file,
    execute_bash_command,
    run_fast_agent,
    run_deep_agent,
)
from .config import load_prompts, build_prompt
from .model import ResponsesModel
from .tools import Tool, tool
from .types import ChatMessage, MessageRole, TokenUsage

__all__ = [
    "get_local_model",
    "ResponsesModel",
    "write_file",
    "run_python_file",
    "execute_bash_command",
    "run_fast_agent",
    "run_deep_agent",
    "load_prompts",
    "build_prompt",
    "Tool",
    "tool",
    "ChatMessage",
    "MessageRole",
    "TokenUsage",
]
