"""Configuration loader — prompts and per-model agent settings.

Two config files in config/:
  - prompts.json    — prompt templates
  - agents.json     — per-model settings (providers, context window, compressor, etc.)
"""

import json
import os
import re
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Prompts
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


# ---------------------------------------------------------------------------
# Agent config (per-model settings)
# ---------------------------------------------------------------------------

_agents_cache: dict | None = None


def _resolve_env(value: str) -> str:
    """Resolve ${VAR_NAME} patterns to environment variables."""
    def _replace(m: re.Match) -> str:
        var = m.group(1)
        return os.environ.get(var, "")
    return re.sub(r"\$\{(\w+)\}", _replace, value)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base (modifies base in place)."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def load_agent_config(
    model_name: str,
    provider_name: str | None = None,
    agents_path: str | Path | None = None,
) -> dict:
    """Load and merge configuration for a given model.

    Resolution order:
      1. defaults  →  2. provider.base  →  3. model-specific settings

    Args:
        model_name: Model identifier to look up (e.g. ``"deepseek-chat"``).
        provider_name: Optional provider name to scope the lookup.
        agents_path: Path to ``agents.json``.

    Returns:
        A flat dict with keys:
          ``baseURL``, ``apiKey``, ``context_window``, ``max_output_tokens``,
          ``max_observation_length``, ``max_steps``, ``compressor`` (dict),
          ``model_name``, ``provider_name``.
    """
    global _agents_cache
    if agents_path is None:
        agents_path = _project_root() / "config" / "agents.json"

    path = Path(agents_path)
    if not path.exists():
        return _default_config(model_name)

    if _agents_cache is None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _deep_merge(raw, {})  # ensure mutable
        _agents_cache = raw

    data = _agents_cache

    # Start with defaults
    result = dict(data.get("defaults", {}))
    result.setdefault("compressor", {})

    # Find matching provider
    providers: dict = data.get("providers", {})
    matched_providers: list[tuple[str, dict]] = []

    for p_name, p_cfg in providers.items():
        if provider_name is not None and p_name != provider_name:
            continue
        models: dict = p_cfg.get("models", {})
        if model_name in models:
            matched_providers.append((p_name, p_cfg))

    if not matched_providers:
        result["baseURL"] = ""
        result["apiKey"] = ""
        result["model_name"] = model_name
        result["provider_name"] = provider_name or ""
        return result

    # Refuse to silently pick the wrong provider when the model name is
    # ambiguous and no provider_name was supplied to disambiguate.
    if len(matched_providers) > 1 and provider_name is None:
        names = ", ".join(p for p, _ in matched_providers)
        raise ValueError(
            f"Model '{model_name}' is defined under multiple providers "
            f"({names}); pass provider_name= to disambiguate."
        )

    # Use the first match (or the only one scoped by provider_name)
    p_name, p_cfg = matched_providers[0]

    # Apply provider-level base
    base_url = p_cfg.get("baseURL", "").rstrip("/")
    result["baseURL"] = base_url

    raw_api_key = p_cfg.get("apiKey")
    if raw_api_key:
        result["apiKey"] = _resolve_env(str(raw_api_key))
    else:
        result["apiKey"] = ""

    # Apply model-specific overrides
    model_cfg: dict = p_cfg["models"][model_name]
    for key, val in model_cfg.items():
        if key == "compressor":
            result.setdefault("compressor", {}).update(val)
        else:
            result[key] = val

    result["model_name"] = model_name
    result["provider_name"] = p_name
    return result


def _default_config(model_name: str) -> dict:
    return {
        "baseURL": "",
        "apiKey": "",
        "context_window": 65536,
        "max_output_tokens": 4096,
        "max_observation_length": 2000,
        "max_steps": 8,
        "compressor": {
            "max_messages": 20,
            "compress_batch": 5,
            "keep_recent": 6,
            "max_summary_tokens": 512,
        },
        "model_name": model_name,
        "provider_name": "",
    }
