"""mini_agent — local lightweight programming agent built on smolagents.

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

__all__ = [
    "get_local_model",
    "write_file",
    "run_python_file",
    "execute_bash_command",
    "run_fast_agent",
    "run_deep_agent",
    "load_prompts",
    "build_prompt",
]