"""Tests for model_picker: parse inputs, honor default env, validation."""

import io
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from mini_agent import model_picker
from mini_agent.model_picker import pick_model


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agents_path(tmp_path, monkeypatch):
    """Write a temp agents.json with two providers and a couple of models."""
    p = tmp_path / "agents.json"
    p.write_text(json.dumps({
        "providers": {
            "ollama-local": {
                "baseURL": "http://localhost:11434/v1",
                "models": {
                    "ornith:9b": {"context_window": 65536},
                    "qwen3-coder": {"context_window": 131072},
                },
            },
            "openai": {
                "baseURL": "https://api.openai.com/v1",
                "apiKey": "${OPENAI_API_KEY}",
                "models": {
                    "gpt-4o": {"context_window": 128000},
                    "gpt-4o-mini": {"context_window": 128000},
                },
            },
        },
        "defaults": {"context_window": 65536},
    }))
    return p


def _stdin(line: str | None) -> io.StringIO:
    s = io.StringIO()
    if line is not None:
        s.write(line + "\n")
    s.seek(0)
    return s


# ---------------------------------------------------------------------------
# Default via parameter
# ---------------------------------------------------------------------------

def test_default_param_unique_model(agents_path):
    out = io.StringIO()
    m, p = pick_model(agents_path=agents_path, default="gpt-4o", stdin=_stdin(""), stdout=out)
    assert m == "gpt-4o"
    assert p == "openai"
    # No prompt printed when default is honored
    assert out.getvalue() == ""


def test_default_param_provider_model(agents_path):
    m, p = pick_model(agents_path=agents_path, default="ollama-local/qwen3-coder", stdin=_stdin(""), stdout=io.StringIO())
    assert (m, p) == ("qwen3-coder", "ollama-local")


def test_default_param_unknown_raises(agents_path):
    with pytest.raises(ValueError, match="not found"):
        pick_model(agents_path=agents_path, default="gpt-99", stdin=_stdin(""), stdout=io.StringIO())


def test_default_param_provider_model_unknown_raises(agents_path):
    with pytest.raises(ValueError, match="not found"):
        pick_model(agents_path=agents_path, default="ollama-local/missing", stdin=_stdin(""), stdout=io.StringIO())


# ---------------------------------------------------------------------------
# Default via environment variable
# ---------------------------------------------------------------------------

def test_env_var_default(agents_path, monkeypatch):
    monkeypatch.setenv("MINI_AGENT_DEFAULT_MODEL", "openai/gpt-4o-mini")
    m, p = pick_model(agents_path=agents_path, stdin=_stdin(""), stdout=io.StringIO())
    assert (m, p) == ("gpt-4o-mini", "openai")


# ---------------------------------------------------------------------------
# Interactive selection
# ---------------------------------------------------------------------------

def test_interactive_default_bare_enter_picks_first(agents_path):
    """Bare Enter (empty line) picks the first entry - usually the cheap default."""
    out = io.StringIO()
    m, p = pick_model(agents_path=agents_path, stdin=_stdin(""), stdout=out)
    # agents.json insertion order: ollama-local first, then openai
    assert (m, p) == ("ornith:9b", "ollama-local")
    # Prompt was printed
    assert "Available models" in out.getvalue()
    assert "ornith:9b" in out.getvalue()
    assert "gpt-4o" in out.getvalue()


def test_interactive_numeric_selection(agents_path):
    out = io.StringIO()
    # Selection 3 -> gpt-4o (openai's 1st: 1=ornith:9b, 2=qwen3-coder, 3=gpt-4o)
    m, p = pick_model(agents_path=agents_path, stdin=_stdin("3"), stdout=out)
    assert (m, p) == ("gpt-4o", "openai")


def test_interactive_numeric_selection_second(agents_path):
    # Selection 2 -> qwen3-coder (ollama's 2nd)
    m, p = pick_model(agents_path=agents_path, stdin=_stdin("2"), stdout=io.StringIO())
    assert (m, p) == ("qwen3-coder", "ollama-local")


def test_interactive_string_selection(agents_path):
    out = io.StringIO()
    m, p = pick_model(agents_path=agents_path, stdin=_stdin("openai/gpt-4o-mini"), stdout=out)
    assert (m, p) == ("gpt-4o-mini", "openai")


def test_interactive_bare_unique_name(agents_path):
    """Bare model name that's unique across providers works without provider."""
    out = io.StringIO()
    m, p = pick_model(agents_path=agents_path, stdin=_stdin("gpt-4o"), stdout=out)
    assert (m, p) == ("gpt-4o", "openai")


def test_interactive_out_of_range_raises(agents_path):
    with pytest.raises(ValueError, match="out of range"):
        pick_model(agents_path=agents_path, stdin=_stdin("99"), stdout=io.StringIO())


def test_interactive_unknown_raises(agents_path):
    with pytest.raises(ValueError, match="Unknown model"):
        pick_model(agents_path=agents_path, stdin=_stdin("foobar"), stdout=io.StringIO())


def test_interactive_eof_raises(agents_path):
    with pytest.raises(EOFError):
        pick_model(agents_path=agents_path, stdin=_stdin(None), stdout=io.StringIO())


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_missing_agents_file_raises(tmp_path):
    with pytest.raises(RuntimeError, match="No models found"):
        pick_model(agents_path=tmp_path / "nope.json", stdin=_stdin(""), stdout=io.StringIO())


def test_empty_providers_raises(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text('{"providers": {}}')
    with pytest.raises(RuntimeError, match="No models found"):
        pick_model(agents_path=p, stdin=_stdin(""), stdout=io.StringIO())


def test_malformed_json_silently_returns_empty_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not valid json")
    with pytest.raises(RuntimeError, match="No models found"):
        pick_model(agents_path=p, stdin=_stdin(""), stdout=io.StringIO())