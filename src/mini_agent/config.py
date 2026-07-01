"""Configuration loader for Ollama model settings and prompt templates.

All configuration comes from the config/ directory:
- config/config.json   -> Ollama connection settings
- config/prompts.json  -> Prompt templates
"""

import json
from pathlib import Path
from dataclasses import dataclass, field


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Ollama config
# ---------------------------------------------------------------------------

@dataclass
class OllamaModel:
    name: str = ""
    supports_tools: bool = False


@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434/v1"
    provider: str = "ollama"
    default_model: str = "ornith:9b"
    models: dict[str, OllamaModel] = field(default_factory=dict)

    def get_default_model(self) -> str:
        if not self.models:
            return self.default_model
        return next(iter(self.models.keys()))


_config_cache: OllamaConfig | None = None


def load_config(config_path: str | Path | None = None) -> OllamaConfig:
    """Load Ollama configuration from config/config.json."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if config_path is None:
        config_path = _project_root() / "config" / "config.json"

    path = Path(config_path)
    if not path.exists():
        _config_cache = OllamaConfig()
        return _config_cache

    raw = json.loads(path.read_text(encoding="utf-8"))

    models = {}
    for model_key, model_info in raw.get("models", {}).items():
        models[model_key] = OllamaModel(
            name=model_info.get("name", model_key),
            supports_tools=model_info.get("supports_tools", False),
        )

    _config_cache = OllamaConfig(
        base_url=raw.get("base_url", "http://localhost:11434/v1"),
        provider=raw.get("provider", "ollama"),
        default_model=raw.get("default_model", "ornith:9b"),
        models=models,
    )
    return _config_cache


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

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
    """Build a prompt by loading the template and filling placeholders.

    Args:
        section: Key in prompts.json, e.g. "fast_agent".
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