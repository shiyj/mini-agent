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

To change several consecutive lines at once, give ``replace`` a second
``end`` anchor — it replaces the inclusive span ``start..end`` with the
following lines (a multi-line block edit, no full-file rewrite needed)::

    ```edit:path/to/file.py
    replace 12#a3f4 15#9abc
    def greet(name):
        return f"Hello, {name}"
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
# op <start-anchor> [<end-anchor>]  (inline content, if any, is the rest of
# the line AFTER the last anchor; exactly one leading space is the separator
# and is dropped, so indented replacements should use the multi-line form).
_EDIT_LINE_RE = re.compile(
    r"^(replace|insert_before|insert_after|delete|append|prepend)\s+"
    r"(\d+#[0-9a-f]{4})"
    r"(?:\s+(\d+#[0-9a-f]{4}))?"
)
# ops that carry a (possibly multi-line) `lines` payload
_CONTENT_OPS = {"replace", "insert_before", "insert_after", "append", "prepend"}


def _parse_dsl(text: str) -> list[dict]:
    """Parse `````edit:`` block content into a list of edit dicts.

    Each edit dict: ``{"op", "anchor", "end", "content"}``.

    - ``replace`` with an optional second ``end`` anchor replaces the inclusive
      span ``start..end`` with the ``content`` lines (pi-hashline "range replace").
      With no ``end`` it replaces a single line (or expands it into many lines).
    - ``insert_before`` / ``insert_after`` / ``append`` / ``prepend`` insert the
      ``content`` lines at the anchor (``append`` == insert after, ``prepend`` ==
      insert before).
    - ``delete`` removes the single anchored line.

    Multi-line payloads: if an op line has no inline content after the anchor(s),
    every following line up to the next op line is treated as ``content``. This
    lets one edit rewrite a whole block of consecutive lines without falling back
    to a full `````file:`` overwrite.
    """
    edits: list[dict] = []
    lines = text.split("\n")
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        m = _EDIT_LINE_RE.match(line)
        if not m:
            i += 1
            continue
        op = m.group(1)
        anchor = m.group(2)
        end = m.group(3)
        rest = line[m.end():]
        inline = rest[1:] if rest.startswith(" ") else rest

        if op == "delete":
            edits.append({"op": op, "anchor": anchor, "end": None, "content": ""})
            i += 1
            continue

        # content ops: replace / insert_before / insert_after / append / prepend
        if inline:
            content = inline
            i += 1
        else:
            j = i + 1
            collected: list[str] = []
            while j < n and not _EDIT_LINE_RE.match(lines[j]):
                collected.append(lines[j])
                j += 1
            while collected and collected[-1].strip() == "":
                collected.pop()
            content = "\n".join(collected)
            i = j
        edits.append({"op": op, "anchor": anchor, "end": end, "content": content})
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
    # Visible line count excludes the trailing empty element produced by a
    # newline-terminated file (mirrors pi-hashline's visibleLineCount).
    visible_n = n - 1 if text.endswith("\n") else n

    # Build snapshot with context hashes
    snapshot: list[tuple[int, str, str]] = []
    for i in range(n):
        prev = lines[i - 1] if i > 0 else ""
        curr = lines[i]
        nxt = lines[i + 1] if i < n - 1 else ""
        h = _line_hash(prev, curr, nxt)
        snapshot.append((i + 1, h, curr))

    def _validate(anchor: str, label: str) -> int:
        m = _ANCHOR_RE.match(anchor)
        if not m:
            raise ValueError(
                f"[E_STALE_ANCHOR] Invalid anchor format: {anchor!r}"
            )
        line_no = int(m.group(1))
        expected_hash = m.group(2)
        if line_no < 1 or line_no > n:
            raise ValueError(
                f"[E_STALE_ANCHOR] Line {line_no} out of range "
                f"(file has {n} lines)"
            )
        actual_hash = snapshot[line_no - 1][1]
        if actual_hash != expected_hash:
            region_start = max(1, line_no - 2)
            region_end = min(n, line_no + 2)
            fresh = _tag_lines_for_range(lines, region_start, region_end)
            raise ValueError(
                f"[E_STALE_ANCHOR] Line {line_no}#{expected_hash} has changed "
                f"(now {line_no}#{actual_hash}). "
                f"Re-read with read_file() and retry.\n"
                f"Fresh anchors around line {line_no}:\n{fresh}"
            )
        return line_no

    # Validate every anchor against one snapshot before touching the file.
    try:
        for edit in edits:
            edit["_start"] = _validate(edit["anchor"], "start")
            if edit["op"] == "replace" and edit.get("end"):
                end_no = _validate(edit["end"], "end")
                if end_no < edit["_start"]:
                    raise ValueError(
                        f"[E_STALE_ANCHOR] Range start {edit['_start']} must be "
                        f"<= end {end_no}."
                    )
                edit["_end"] = end_no
            else:
                edit["_end"] = edit["_start"]
    except ValueError as ex:
        return str(ex)

    # Apply edits bottom-up (descending start line) so lower anchors stay valid.
    edits_sorted = sorted(edits, key=lambda e: e["_start"], reverse=True)

    min_affected = n + 1
    max_affected = 0

    for edit in edits_sorted:
        op = edit["op"]
        s = edit["_start"]
        e = edit["_end"]
        content_lines = edit["content"].split("\n") if edit["content"] else []

        if op == "replace":
            if s == 1 and e == visible_n and not content_lines:
                return (
                    "[E_STALE_ANCHOR] Refusing to delete the whole file via edit. "
                    "Use a ```file:``` block to overwrite."
                )
            # Replace inclusive span s..e with the new lines.
            del lines[s - 1:e]
            for nl in reversed(content_lines):
                lines.insert(s - 1, nl)
            lo = s
            hi = s + len(content_lines) - 1
        elif op in ("insert_after", "append"):
            for nl in reversed(content_lines):
                lines.insert(s, nl)
            lo = s + 1
            hi = s + len(content_lines)
        elif op in ("insert_before", "prepend"):
            for nl in reversed(content_lines):
                lines.insert(s - 1, nl)
            lo = s
            hi = s + len(content_lines) - 1
        elif op == "delete":
            if visible_n == 1:
                return (
                    "[E_STALE_ANCHOR] Refusing to delete the only line via edit. "
                    "Use a ```file:``` block to overwrite."
                )
            del lines[s - 1]
            lo = hi = s
        else:
            continue

        if content_lines or op == "delete":
            min_affected = min(min_affected, lo)
            max_affected = max(max_affected, hi)

    # Write back atomically
    new_text = "\n".join(lines)
    try:
        _atomic_write(path, new_text)
    except Exception as ex:
        return f"Error writing {path}: {ex}"

    # Return fresh anchors for the affected region
    n_new = len(lines)
    region_start = max(1, min_affected - 1)
    region_end = min(n_new, max_affected + 1)
    fresh = _tag_lines_for_range(lines, region_start, region_end)

    return f"Edited {len(edits)} region(s) in {path}.\nFresh anchors:\n{fresh}"
