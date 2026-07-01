"""mini_agent — local lightweight programming agent based on smolagents."""

from .agent import get_local_model, execute_bash_command, run_fast_agent, run_deep_agent
from .config import load_config, load_prompts, build_prompt, OllamaConfig, OllamaModel

__all__ = [
    "get_local_model",
    "execute_bash_command",
    "run_fast_agent",
    "run_deep_agent",
    "load_config",
    "load_prompts",
    "build_prompt",
    "OllamaConfig",
    "OllamaModel",
]