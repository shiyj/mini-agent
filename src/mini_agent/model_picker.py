"""Interactive model picker - scan agents.json, list available (provider, model)
pairs, and let the caller choose. One helper, used by every example that needs
to pick a model at startup.

Public API:
    pick_model(agents_path=None, default=None, stdin=None, stdout=None)
        -> (model_name: str, provider_name: str | None)

Selection protocol (in order):
    1. ``default`` (or ``MINI_AGENT_DEFAULT_MODEL`` env var) if set and present
       in agents.json - return without prompting.
    2. Otherwise, list all (provider, model) pairs and read from stdin.
       - Empty input returns the first entry (so a bare Enter selects a
         sensible default - usually the cheapest local model).
       - A 1-based integer selects that numbered entry.
       - A string in the form "provider/model" selects that pair (provider may
         be omitted if the model name is unique across providers).

All input/output goes through the ``stdin``/``stdout`` parameters so tests can
inject a StringIO without monkeypatching builtins.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TextIO
from typing import Optional

from .config import _project_root


def _load_providers(agents_path: str | Path | None) -> dict:
    """Read agents.json and return {provider_name: {models: {model_name: cfg}}}.

    Robust to missing file: returns {} so the caller can produce a friendly
    error rather than crash on import.
    """
    import json
    path = Path(agents_path) if agents_path else _project_root() / "config" / "agents.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("providers", {}) or {}


def pick_model(
    agents_path: str | Path | None = None,
    default: Optional[str] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> tuple[str, Optional[str]]:
    """Interactively pick a (model_name, provider_name) pair from agents.json.

    Args:
        agents_path: Optional path to agents.json. Defaults to config/agents.json.
        default: Optional model identifier to skip the prompt. Recognized forms:
            - "model_name" (resolved across providers, errors if ambiguous)
            - "provider/model_name" (explicit)
            - "provider:model_name" (also accepted)
            If not given, ``MINI_AGENT_DEFAULT_MODEL`` env var is consulted.
        stdin: Input stream (defaults to sys.stdin). Tests inject a StringIO.
        stdout: Output stream (defaults to sys.stdout). Tests inject a StringIO.

    Returns:
        (model_name, provider_name). provider_name is None when the default
        is given as a bare model_name that needs further disambiguation (then
        config.load_agent_config does the disambiguation at use time).

    Raises:
        RuntimeError: agents.json is empty or missing.
        ValueError: Input is invalid (unparseable or refers to a missing model).
        EOFError: stdin closed before a valid selection was made.
    """
    providers = _load_providers(agents_path)
    if not providers:
        raise RuntimeError(
            f"No models found in {agents_path or 'config/agents.json'}. "
            "Add at least one provider/model entry to agents.json."
        )

    # Flatten into a stable list of (provider, model) tuples
    entries: list[tuple[str, str]] = []
    for p_name, p_cfg in providers.items():
        for m_name in p_cfg.get("models", {}).keys():
            entries.append((p_name, m_name))

    # 1. Honor explicit default
    chosen = default or os.environ.get("MINI_AGENT_DEFAULT_MODEL")
    if chosen:
        return _resolve_default(chosen, entries)

    # 2. Interactive prompt
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout

    _print_list(entries, providers, stdout)
    raw = stdin.readline()
    if not raw:
        raise EOFError("No model selected (stdin closed).")
    return _parse_choice(raw.strip(), entries)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _resolve_default(chosen: str, entries: list[tuple[str, str]]) -> tuple[str, Optional[str]]:
    """Resolve a default spec like "gpt-4o" or "openai/gpt-4o" to a model.

    For bare names that exist in multiple providers, returns the bare name
    with provider=None so config.load_agent_config can do the disambiguation
    later (consistent with the rest of the project's "ambiguous -> raise
    with a clear message" behavior).
    """
    for sep in ("/", ":"):
        if sep in chosen:
            prov, mdl = chosen.split(sep, 1)
            for p, m in entries:
                if p == prov and m == mdl:
                    return m, p
            raise ValueError(
                f"Model '{chosen}' not found in agents.json. "
                f"Available: {', '.join(f'{p}/{m}' for p, m in entries)}"
            )
    # Bare name - check for ambiguous vs unique
    matches = [m for p, m in entries if m == chosen]
    if not matches:
        raise ValueError(
            f"Model '{chosen}' not found in agents.json. "
            f"Available: {', '.join(f'{p}/{m}' for p, m in entries)}"
        )
    if len(matches) == 1:
        # Find the unique (provider, model) entry
        for p, m in entries:
            if m == chosen:
                return m, p
    # Ambiguous: return bare name, provider=None (load_agent_config disambiguates)
    return chosen, None


def _print_list(
    entries: list[tuple[str, str]],
    providers: dict,
    out: TextIO,
) -> None:
    print("Available models (agents.json):", file=out)
    print("-" * 60, file=out)
    # Group by provider for readability
    by_provider: dict[str, list[str]] = {}
    for p, m in entries:
        by_provider.setdefault(p, []).append(m)
    index = 0
    for p_name in by_provider:
        print(f"[{p_name}]", file=out)
        for m_name in by_provider[p_name]:
            index += 1
            print(f"  {index:2d}. {m_name}", file=out)
    print("-" * 60, file=out)
    print("Select a model (number, or 'provider/model'): ", end="", file=out, flush=True)


def _parse_choice(
    raw: str,
    entries: list[tuple[str, str]],
) -> tuple[str, Optional[str]]:
    """Parse the user's selection into (model_name, provider_name)."""
    if not raw:
        # Bare Enter -> first entry
        p, m = entries[0]
        return m, p

    # Numeric selection
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(entries):
            p, m = entries[idx]
            return m, p
        raise ValueError(
            f"Selection {raw} out of range 1..{len(entries)}"
        )

    # "provider/model" or "provider:model"
    for sep in ("/", ":"):
        if sep in raw:
            prov, mdl = raw.split(sep, 1)
            for p, m in entries:
                if p == prov and m == mdl:
                    return m, p
            raise ValueError(
                f"Model '{raw}' not found. Available: "
                f"{', '.join(f'{p}/{m}' for p, m in entries)}"
            )

    # Bare model name (only valid if unique across providers)
    matches = [m for p, m in entries if m == raw]
    if not matches:
        raise ValueError(
            f"Unknown model '{raw}'. Enter a number, or 'provider/model'."
        )
    if len(matches) == 1:
        for p, m in entries:
            if m == raw:
                return m, p
    # Ambiguous - same as default path: return bare name, provider=None
    return raw, None