"""Prompt template loader.

All prompt templates live in config/prompts.json.
This module has no model-specific config — users pass models directly.
"""

import json
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


_prompts_cache: dict | None = None


def load_prompts(prompts_path: str | Path | None = None) -> dict:
    """Load prompt templates from config/prompts.json."""
    global _prompts_cache
    if _prompts_cache is not None:
        return _prompts_cache

    if prompts_path is None:
        prompts_path = _project_root() / "config" / "prompts.json"

    path = Path(prompts_path)
    if not path.exists():
        _prompts_cache = {}
        return _prompts_cache

    _prompts_cache = json.loads(path.read_text(encoding="utf-8"))
    return _prompts_cache


def build_prompt(section: str, **kwargs: str) -> str:
    """Build a prompt from the templates and fill placeholders.

    Args:
        section: Key in prompts.json, e.g. "fast_agent" / "deep_agent".
        kwargs: Values to substitute into the template.

    Returns:
        The fully rendered prompt string.
    """
    prompts = load_prompts()
    section_data = prompts.get(section, {})
    template = section_data.get("user_template", "{prompt}")
    context = dict(section_data)
    context.update(kwargs)
    return template.format(**context)