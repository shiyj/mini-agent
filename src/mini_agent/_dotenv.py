"""Minimal .env loader - zero dependencies, no surprise behavior.

Reads a ``.env`` file from the project root (next to ``config/``) and
populates ``os.environ`` for any KEY=VALUE lines whose key is NOT already in
the environment. Existing process env wins, so a one-off ``export FOO=...``
before ``python3`` overrides whatever is in ``.env`` - same semantics as
``python-dotenv``.

Format supported (one assignment per line):
    KEY=value
    KEY="value with spaces"
    export KEY=value
    # comment lines
    blank lines

Quoted values have their quotes stripped. Lines that look malformed are
silently skipped - .env is for dev convenience, never for secrets.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .config import _project_root


# Lines like:  KEY=value   /  KEY="value"   /   export KEY=val
# We capture the KEY (letters/digits/underscore, must start with letter/underscore)
# and the VALUE up to end-of-line. Comments are stripped first.
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _strip_comment(line: str) -> str:
    """Strip a trailing ``# comment`` if present, respecting quoted values."""
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> int:
    """Load a .env file and populate ``os.environ``.

    Args:
        path: Path to the .env file. Defaults to <project_root>/.env.
        override: If True, overwrite existing process env vars with .env values.
            Default False (existing env wins, matches python-dotenv behavior).

    Returns:
        The number of variables that were set (whether newly or overridden).
    """
    if path is None:
        path = _project_root() / ".env"
    path = Path(path)
    if not path.is_file():
        return 0

    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        m = _ASSIGN_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), _strip_quotes(m.group(2))
        if override or key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def load_dotenv_if_present() -> int:
    """Convenience wrapper: load .env from project root if it exists.

    Used by load_model() so examples pick up the dev .env automatically.
    No-op if .env is absent (so the library stays side-effect-free when
    imported by tools that don't want any .env magic).
    """
    return load_dotenv()