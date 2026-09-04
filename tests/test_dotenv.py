"""Tests for the .env loader (mini_agent._dotenv)."""

import os
from pathlib import Path

import pytest

from mini_agent import _dotenv
from mini_agent._dotenv import load_dotenv, load_dotenv_if_present


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip a known set of env vars so tests don't leak between cases."""
    for k in ("FOO", "BAR", "QUOTED", "EXPORTED", "EMPTY", "WEIRD", "DUP"):
        monkeypatch.delenv(k, raising=False)


def test_loads_basic_assignment(tmp_path):
    p = tmp_path / ".env"
    p.write_text("FOO=bar\nBAR=baz\n")
    n = load_dotenv(p)
    assert n == 2
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAR"] == "baz"


def test_skips_comments_and_blanks(tmp_path):
    p = tmp_path / ".env"
    p.write_text("\n# a comment\n\nFOO=bar\n   # indented comment\n\n")
    n = load_dotenv(p)
    assert n == 1
    assert os.environ["FOO"] == "bar"


def test_strips_quotes_and_trailing_space(tmp_path):
    p = tmp_path / ".env"
    p.write_text('QUOTED="hello world"\nFOO=bar   \n')
    load_dotenv(p)
    assert os.environ["QUOTED"] == "hello world"
    assert os.environ["FOO"] == "bar"


def test_handles_export_prefix(tmp_path):
    p = tmp_path / ".env"
    p.write_text("export FOO=bar\n")
    load_dotenv(p)
    assert os.environ["FOO"] == "bar"


def test_existing_env_wins_by_default(tmp_path, monkeypatch):
    """The shell env overrides the .env value, matching python-dotenv."""
    monkeypatch.setenv("FOO", "from-shell")
    p = tmp_path / ".env"
    p.write_text("FOO=from-file\n")
    n = load_dotenv(p)
    # .env's FOO is parsed but skipped (shell wins), so n=0 not 1.
    # The relevant invariant is: os.environ keeps the shell value.
    assert n == 0
    assert os.environ["FOO"] == "from-shell"


def test_override_true_overwrites_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOO", "from-shell")
    p = tmp_path / ".env"
    p.write_text("FOO=from-file\n")
    load_dotenv(p, override=True)
    assert os.environ["FOO"] == "from-file"


def test_malformed_lines_silently_skipped(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "FOO=bar\n"
        "=missing-key\n"               # no key -> skipped
        "EMPTY=\n"                     # key with empty value
        "no-equals-sign\n"             # no = at all -> skipped
        "123NUM=bad\n"                 # key can't start with digit -> skipped
        "GOOD=ok\n"
    )
    n = load_dotenv(p)
    assert n == 3  # FOO + EMPTY + GOOD
    assert os.environ["FOO"] == "bar"
    assert os.environ["EMPTY"] == ""
    assert os.environ["GOOD"] == "ok"
    assert "no-equals-sign" not in os.environ
    assert "123NUM" not in os.environ
    assert "=missing-key" not in os.environ


def test_pound_inside_quotes_is_kept(tmp_path):
    """# inside a quoted value must NOT be treated as a comment."""
    p = tmp_path / ".env"
    p.write_text('QUOTED="a # b"\n')
    load_dotenv(p)
    assert os.environ["QUOTED"] == "a # b"


def test_returns_zero_for_missing_file(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == 0


def test_project_root_default(tmp_path, monkeypatch):
    """load_dotenv_if_present() looks at <project_root>/.env."""
    # Patch the _project_root helper to point at tmp_path
    monkeypatch.setattr(_dotenv, "_project_root", lambda: tmp_path)
    (tmp_path / ".env").write_text("FOO=bar\n")
    n = load_dotenv_if_present()
    assert n == 1
    assert os.environ["FOO"] == "bar"


def test_no_env_file_is_silent_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(_dotenv, "_project_root", lambda: tmp_path)
    n = load_dotenv_if_present()
    assert n == 0


def test_pound_inside_quotes_is_kept(tmp_path):
    """# inside a quoted value must NOT be treated as a comment."""
    p = tmp_path / ".env"
    p.write_text('QUOTED="a # b"\n')
    load_dotenv(p)
    assert os.environ["QUOTED"] == "a # b"