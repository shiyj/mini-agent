"""Configuration loader — per-model agent settings.

One config file in config/:
  - agents.json     — per-model settings (providers, context window, etc.)
"""

import json
import os
import re
from pathlib import Path

from .context import SummaryCompressor


_agents_cache: dict | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _resolve_env(value: str) -> str:
    """Resolve ${VAR_NAME} patterns to environment variables."""
    def _replace(m: re.Match) -> str:
        var = m.group(1)
        return os.environ.get(var, "")
    return re.sub(r"\$\{(\w+)\}", _replace, value)


def _load_agent_config(
    model_name: str,
    provider_name: str | None = None,
    agents_path: str | Path | None = None,
) -> dict:
    """Load and merge configuration for a given model (internal use only).

    Resolution order:
      1. defaults  →  2. provider.base  →  3. model-specific settings

    Args:
        model_name: Model identifier to look up (e.g. ``"deepseek-chat"``).
        provider_name: Optional provider name to scope the lookup.
        agents_path: Path to ``agents.json``.

    Returns:
        A flat dict with keys:
          ``baseURL``, ``apiKey``, ``context_window``, ``max_output_tokens``,
          ``max_observation_length``, ``max_steps``, ``model_name``, ``provider_name``.
    """
    global _agents_cache
    if agents_path is None:
        agents_path = _project_root() / "config" / "agents.json"

    path = Path(agents_path)
    if not path.exists():
        return _default_config(model_name)

    if _agents_cache is None:
        _agents_cache = json.loads(path.read_text(encoding="utf-8"))

    data = _agents_cache

    # Start with defaults
    result = dict(data.get("defaults", {}))

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
        result[key] = val

    result["model_name"] = model_name
    result["provider_name"] = p_name
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_model(
    model_name: str,
    provider_name: str | None = None,
    *,
    log_file: str | None = None,
    reasoning: dict | None = None,
    agents_path: str | Path | None = None,
) -> tuple["ResponsesModel", "SummaryCompressor"]:
    """Load a model from agents.json configuration.

    This is the single entry point for creating a model from config.
    All model parameters (baseURL, apiKey, temperature, context_window,
    max_output_tokens, max_steps, max_observation_length) are read from
    agents.json. Only runtime-only options (log_file, reasoning) can be
    overridden here.

    Args:
        model_name: Model identifier (e.g. "ornith:9b", "gpt-4o").
        provider_name: Provider name to disambiguate (e.g. "ollama-local").
        log_file: Optional JSONL log file path for request/response logging.
        reasoning: Optional reasoning config for the model.
        agents_path: Optional custom path to agents.json.

    Returns:
        A tuple of (ResponsesModel, SummaryCompressor with default settings).
        The compressor is a FRESH instance per call - never shared - so
        concurrent agents cannot corrupt each other's state.
    """
    from ._dotenv import load_dotenv_if_present
    from .model import ResponsesModel

    # Auto-load a project-root .env so OPENAI_API_KEY / OPENROUTER_API_KEY
    # etc. work out of the box for examples. Does nothing if .env is absent
    # and never overrides variables already set in the process env.
    load_dotenv_if_present()

    config = _load_agent_config(model_name, provider_name, agents_path)

    model = ResponsesModel(
        model_name=config["model_name"],
        api_base=config["baseURL"],
        api_key=config.get("apiKey") or None,
        temperature=config.get("temperature", 0.0),
        max_output_tokens=config.get("max_output_tokens", 4096),
        context_window=config.get("context_window", 65536),
        timeout=config.get("timeout", 600.0),
        log_file=log_file,
        reasoning=reasoning,
    )

    return model, SummaryCompressor()


def _default_config(model_name: str) -> dict:
    return {
        "baseURL": "",
        "apiKey": "",
        "context_window": 65536,
        "max_output_tokens": 4096,
        "max_observation_length": 2000,
        "max_steps": 8,
        "temperature": 0.0,
        "timeout": 600.0,
        "model_name": model_name,
        "provider_name": "",
    }
