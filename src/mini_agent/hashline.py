"""Hashline — context-based line hashing and hash-anchored file editing.

Every line gets a 4-hex-char hash derived from its immediate neighbors
(prev + curr + next), so identical lines in different contexts get
different hashes, and editing line N only invalidates anchors for N-1, N, N+1.

The `````edit:path`` Markdown block DSL::

    ```edit:path/to/file.py
    replace 12#a3f4 print(y)
    insert_before 15#9abc new_line()
    insert_after 20#def0 another()
    delete 25#1234
    ```
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile

HASH_LEN = 4
ALPHABET = "0123456789abcdef"


def _line_hash(prev: str, curr: str, nxt: str) -> str:
    """Context-based hash: sha1(prev \\x00 curr \\x00 nxt) first 4 hex chars."""
    raw = f"{prev}\x00{curr}\x00{nxt}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:HASH_LEN]


def tag_lines(text: str) -> str:
    """Prefix every line with ``N#HHHH `` (1-indexed line number + 4-char hash).

    Round-trips exactly: ``strip_tags(tag_lines(t)) == t``.
    """
    lines = text.split("\n")
    n = len(lines)
    out: list[str] = []
    for i, line in enumerate(lines):
        prev = lines[i - 1] if i > 0 else ""
        curr = line
        nxt = lines[i + 1] if i < n - 1 else ""
        h = _line_hash(prev, curr, nxt)
        out.append(f"{i + 1}#{h} {curr}")
    return "\n".join(out)


def strip_tags(text: str) -> str:
    """Remove ``N#HHHH `` prefixes from every line.

    Lines without a matching prefix are passed through unchanged.
    """
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\d+#[0-9a-f]{4} (.*)", line)
        if m:
            out.append(m.group(1))
        else:
            out.append(line)
    return "\n".join(out)


def _tag_lines_for_range(lines: list[str], start: int, end: int) -> str:
    """Return tagged lines for ``lines[start-1:end]`` (1-indexed)."""
    n = len(lines)
    lo = max(0, start - 1)
    hi = min(n, end)
    out: list[str] = []
    for i in range(lo, hi):
        prev = lines[i - 1] if i > 0 else ""
        curr = lines[i]
        nxt = lines[i + 1] if i < n - 1 else ""
        h = _line_hash(prev, curr, nxt)
        out.append(f"{i + 1}#{h} {curr}")
    return "\n".join(out)


def _atomic_write(path: str, content: str) -> None:
    """Write ``content`` to ``path`` atomically via temp-file + rename."""
    dirname = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirname, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_ANCHOR_RE = re.compile(r"^(\d+)#([0-9a-f]{4})$")
_EDIT_LINE_RE = re.compile(r"^(\S+)\s+(\d+#[0-9a-f]{4})\s?(.*)$")


def _parse_dsl(text: str) -> list[dict]:
    """Parse `````edit:`` block content into a list of edit dicts.

    Each edit dict: ``{"op": str, "anchor": str, "content": str}``.
    """
    VALID_OPS = {"replace", "insert_before", "insert_after", "delete"}
    edits: list[dict] = []
    for line in text.split("\n"):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        m = _EDIT_LINE_RE.match(line)
        if not m:
            continue
        op = m.group(1)
        anchor = m.group(2)
        content = m.group(3)
        if op not in VALID_OPS:
            continue
        edits.append({"op": op, "anchor": anchor, "content": content})
    return edits


def apply_edit_block(path: str, dsl_text: str) -> str:
    """Apply edits from a `````edit:path`` block to the file on disk.

    Args:
        path: Path to the file to edit.
        dsl_text: Raw content of the `````edit:`` block (one edit per line).

    Returns:
        A result string suitable for the Observation: either a success report
        with fresh anchors, or a ``[E_STALE_ANCHOR]`` error with guidance.
    """
    edits = _parse_dsl(dsl_text)
    if not edits:
        return "No valid edits found in edit block."

    # Read current file
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return f"[E_STALE_ANCHOR] File not found: {path}"
    except Exception as e:
        return f"Error reading {path}: {e}"

    lines = text.split("\n")
    n = len(lines)

    # Build snapshot with context hashes
    snapshot: list[tuple[int, str, str]] = []
    for i in range(n):
        prev = lines[i - 1] if i > 0 else ""
        curr = lines[i]
        nxt = lines[i + 1] if i < n - 1 else ""
        h = _line_hash(prev, curr, nxt)
        snapshot.append((i + 1, h, curr))

    # Validate all anchors against snapshot
    for edit in edits:
        m = _ANCHOR_RE.match(edit["anchor"])
        if not m:
            return f"[E_STALE_ANCHOR] Invalid anchor format: {edit['anchor']!r}"
        line_no = int(m.group(1))
        expected_hash = m.group(2)
        if line_no < 1 or line_no > n:
            return (
                f"[E_STALE_ANCHOR] Line {line_no} out of range "
                f"(file has {n} lines)"
            )
        actual_hash = snapshot[line_no - 1][1]
        if actual_hash != expected_hash:
            region_start = max(1, line_no - 2)
            region_end = min(n, line_no + 2)
            fresh = _tag_lines_for_range(lines, region_start, region_end)
            return (
                f"[E_STALE_ANCHOR] Line {line_no}#{expected_hash} has changed "
                f"(now {line_no}#{actual_hash}). "
                f"Re-read with read_file() and retry.\n"
                f"Fresh anchors around line {line_no}:\n{fresh}"
            )

    # Apply edits bottom-up (descending line number) so earlier anchors
    # are not invalidated by insertions/deletions at higher positions.
    def _line_no(edit: dict) -> int:
        m = _ANCHOR_RE.match(edit["anchor"])
        return int(m.group(1)) if m else 0

    edits_sorted = sorted(edits, key=_line_no, reverse=True)

    min_affected = n + 1
    max_affected = 0

    for edit in edits_sorted:
        line_no = _line_no(edit)
        op = edit["op"]
        content = edit["content"]

        if op == "replace":
            lines[line_no - 1] = content
            min_affected = min(min_affected, line_no)
            max_affected = max(max_affected, line_no)
        elif op == "delete":
            del lines[line_no - 1]
            min_affected = min(min_affected, line_no)
        elif op == "insert_before":
            lines.insert(line_no - 1, content)
            min_affected = min(min_affected, line_no)
            max_affected = max(max_affected, line_no)
        elif op == "insert_after":
            lines.insert(line_no, content)
            min_affected = min(min_affected, line_no + 1)
            max_affected = max(max_affected, line_no + 1)

    # Write back atomically
    new_text = "\n".join(lines)
    try:
        _atomic_write(path, new_text)
    except Exception as e:
        return f"Error writing {path}: {e}"

    # Return fresh anchors for the affected region
    n_new = len(lines)
    region_start = max(1, min_affected - 1)
    region_end = min(n_new, max_affected + 1)
    fresh = _tag_lines_for_range(lines, region_start, region_end)

    return f"Edited {len(edits)} region(s) in {path}.\nFresh anchors:\n{fresh}"
