#!/usr/bin/env python3
"""ReAct agent loop with tool use, error recovery, and strict final_answer verification.

All prompt text lives in prompts.py; this file contains only the execution logic.
"""

import os
import re
import sys
from typing import TYPE_CHECKING

from . import prompts
from .code_executor import execute_code

if TYPE_CHECKING:
    from .model import ResponsesModel
    from .tools import Tool
    from .context import ContextCompressor

# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------

# Languages whose block content is executed (as opposed to written to disk).
_EXECUTABLE_LANGS = {"python", "py"}

# Structured failure markers emitted by the built-in tools. We match on these
# rather than loose substrings like "Error:" / "exit=" because a script's own
# *normal* output can contain those words and would otherwise false-positive.
#   run_python_file -> "【FAILED exit=N】", "【Timeout】", "【Error】"
#   read_file       -> "Error reading ..."
_ERROR_MARKERS = ("【FAILED", "【Timeout", "【Error", "Error reading", "Traceback")


def parse_step(text: str) -> list[tuple[str, str | None, str]]:
    """Parse one model turn into a list of (lang, path, content) blocks.

    Recognizes fenced blocks of the form:
        ```lang
        content
        ```
        ```lang:path
        content
        ```
    """
    # Strip special tokens
    text = re.sub(r'<\|mask_start\|>.*?<\|mask_end\|>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|.*?\|>', '', text)

    # Normalize: ```python\nfile:path (or edit:path) -> correct fence.
    # edit: is unsupported, but normalizing it routes the block to the E000
    # "rewrite the whole file" nudge instead of a confusing NameError at exec.
    text = re.sub(
        r'```python\s*\n(file|edit):([^\n]+)',
        r'```\1:\2',
        text,
        flags=re.MULTILINE,
    )

    # Normalize: ```read_file(...) or ```run_python_file(...) → ```python block
    # (model sometimes uses tool names as fence languages instead of ```python)
    text = re.sub(
        r'```(read_file|run_python_file)\s*\(([^)]*)\)\s*```',
        r'```python\n\1(\2)\n```',
        text,
    )
    text = re.sub(
        r'```(read_file|run_python_file)\s*\(([^)]*)\)\s*\n',
        r'```python\n\1(\2)\n',
        text,
    )

    blocks: list[tuple[str, str | None, str]] = []

    # Pattern: ```lang[:path]\ncontent\n``` or unclosed ```lang[:path]\ncontent$
    pattern = r"```([A-Za-z0-9_-]+)(?::([^\n]+))?\s*\n(.*?)(?:```|$)"

    for m in re.finditer(pattern, text, re.DOTALL):
        lang = m.group(1).lower()
        path = m.group(2).strip() if m.group(2) else None
        content = m.group(3)
        # Trim trailing newline from content but keep other whitespace
        content = content.rstrip("\n")
        blocks.append((lang, path, content))

    return blocks


# ---------------------------------------------------------------------------
# File writing helpers
# ---------------------------------------------------------------------------


def write_blocks(
    blocks: list[tuple[str, str | None, str]],
    fallback_path: str | None = None,
) -> tuple[list[str], list[str]]:
    """Write all blocks to disk.

    - ``file:path`` -> write raw content to path (whole-file rewrite).
    - ``python:path`` / ``py:path`` -> write content to path (also executable).
    - ``python`` / ``py`` without path -> write to /tmp/mini-agent-{timestamp}-{step}.py for record.
    - ``file`` without path / a placeholder path -> error, reported back to the caller.
    - ``edit:`` is NOT supported — if present it is reported so the model can
      be nudged back to a ``file:`` rewrite.

    Args:
        fallback_path: If a block uses a placeholder path and no other block
            has a real path, use this as fallback.

    Returns:
        (file_reports, path_errors) - ``file_reports`` lists "written" messages
        for ``file:`` blocks; ``path_errors`` lists error strings for blocks
        that had no path, a placeholder/invalid path, used the unsupported
        ``edit:``, or failed to write.
    """
    file_reports: list[str] = []
    path_errors: list[str] = []

    for lang, path, content in blocks:
        # 'edit:' is no longer a supported edit path - nudge back to file: rewrite
        if lang == "edit":
            path_errors.append("[E000] edit: is not supported. Rewrite the whole file with a ```file:<path>``` block.")
            continue

        if path is None:
            if lang in _EXECUTABLE_LANGS:
                # Executable block with no path: nothing to write to disk here
                # (it is executed as-is later). Don't clutter /tmp.
                continue
            # Non-executable without path - error
            path_errors.append(f"[E011] {lang} block without path")
            continue

        real = _clean_path(path)
        if real is None:
            # Placeholder path (e.g. <path> copied from the prompt) -
            # try to recover a real path from a sibling block or the last step.
            real = _find_real_path(blocks, fallback_path)
            if real is None:
                path_errors.append(f"[E013] {lang} block has invalid path: {path}")
                continue

        full_path = os.path.abspath(real)

        try:
            directory = os.path.dirname(full_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            file_reports.append(f"Written {full_path}")
        except Exception as e:
            # Surface write failures - swallowing them silently would let the
            # model believe it wrote the file while it runs a stale one.
            path_errors.append(f"[E012] Failed to write {full_path}: {e}")

    return file_reports, path_errors


# Placeholder words a small model may copy verbatim instead of a real path.
_PLACEHOLDER_PATH_RE = re.compile(
    r"^(<[^>]*>|path|file_path|filename|your_file_path)$",
    re.IGNORECASE,
)


def _clean_path(path: str) -> str | None:
    """Return a cleaned file path, or None if it is a placeholder/artifact.

    Strips whitespace and wrapping quotes/backticks (markdown artifacts);
    rejects angle-bracket placeholders, empty strings, and control characters.
    """
    p = path.strip().strip("`'\"").strip()
    if not p or "<" in p or ">" in p:
        return None
    if any(ord(c) < 32 for c in p):
        return None
    if _PLACEHOLDER_PATH_RE.match(p):
        return None
    return p


def _find_real_path(blocks: list[tuple[str, str | None, str]], fallback: str | None = None) -> str | None:
    """Find a real file path from other blocks, for recovering placeholder paths."""
    for lang, p, _ in blocks:
        # Skip sibling file: blocks so a stray placeholder can't overwrite a
        # sibling file with its own content.
        if p and lang != "file":
            cleaned = _clean_path(p)
            if cleaned:
                return cleaned
    if fallback:
        cleaned = _clean_path(fallback)
        if cleaned:
            return cleaned
    return None


def _truncate_observation(text: str, max_length: int) -> str:
    """Truncate observation text at character level, preserving head and tail."""
    if max_length <= 0 or len(text) <= max_length:
        return text
    half = max_length // 2
    return text[:half] + f"\n... [truncated {len(text) - max_length} chars] ...\n" + text[-half:]


def _log(verbose: bool, text: str, limit: int = 0) -> None:
    """Emit a debug line to stderr only when ``verbose`` is True."""
    if not verbose:
        return
    if limit > 0 and len(text) > limit:
        text = text[:limit] + (
            f"\n... [debug truncated: showing first {limit} of "
            f"{len(text)} chars]"
        )
    print(text, file=sys.stderr)


# ---------------------------------------------------------------------------
# Main ReAct loop
# ---------------------------------------------------------------------------

def react_loop(
    system_prompt: str,
    task: str,
    model: "ResponsesModel",
    tools: list["Tool"],
    max_steps: int = 8,
    max_observation_length: int = 2000,
    verbose: bool = True,
    compressor: "ContextCompressor | None" = None,
) -> str:
    """Run the ReAct loop until final_answer or max_steps.

    Args:
        system_prompt: The system prompt (built by the caller from prompts.py).
        task: The task description.
        model: A ``ResponsesModel`` instance.
        tools: List of Tool objects available to the agent.
        max_steps: Maximum number of Thought/Code/Observation cycles.
        max_observation_length: Max characters for observation content (0 = no truncation).
        verbose: If True, print step progress to stderr.
        compressor: Optional context compressor to bound conversation length.

    Returns:
        The final answer string, or an error message.
    """
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    last_output: str | None = None
    syntax_warns = 0
    written_path: str | None = None  # path the model wrote this/last step (from its blocks)

    # --- tracking for strict final_answer verification ---
    has_used_tool = False          # C-rule: at least one tool call before final_answer
    last_step_had_error = False    # B-rule: previous step had an error
    no_progress = 0                # no-write/verify progress counter
    no_code_counter = 0            # consecutive steps without any code block
    # ---------------------------------------------------

    stop_sequences = ["Observation:"]

    _log(
        verbose,
        "ReAct config: "
        f"model={getattr(model, 'model_name', model)!r}, "
        f"max_steps={max_steps}, "
        f"max_observation_length={max_observation_length}, "
        f"compressor={'on' if compressor is not None else 'off'}",
    )

    for step in range(1, max_steps + 1):
        _log(verbose, f"\n{'='*20} Step {step} {'='*20}")

        # 0. Compress context if a compressor is configured
        if compressor is not None:
            before = len(messages)
            messages = compressor.compress(messages, model)
            _log(
                verbose and len(messages) < before,
                f"[context] {before} -> {len(messages)} messages (compressed)",
            )

        # 0a. Token-based compress check (if compressor configured)
        if compressor is not None and hasattr(model, 'context_window') and hasattr(model, 'max_output_tokens'):
            total_chars = sum(len(msg.get('content', '')) for msg in messages)
            estimated_tokens = total_chars // 4
            max_input_tokens = model.context_window - model.max_output_tokens
            if estimated_tokens > max_input_tokens * 0.85:
                _log(
                    verbose,
                    f"[token-check] Estimated {estimated_tokens} tokens exceeding "
                    f"{max_input_tokens} max, compressing",
                )
                before = len(messages)
                old_keep = getattr(compressor, 'compress_batch', 5)
                setattr(compressor, 'compress_batch', len(messages))
                messages = compressor.compress(messages, model)
                setattr(compressor, 'compress_batch', old_keep)
                _log(
                    verbose and len(messages) < before,
                    f"[token-compress] {before} -> {len(messages)} messages",
                )

        # 1. Call the model. generate() accepts plain string content and cleans
        # merges consecutive same-role messages internally, so pass `messages`
        # directly (no pre-flatten / post-wrap needed).
        response = model.generate(messages=messages, stop_sequences=stop_sequences)

        tu = getattr(response, "token_usage", None)
        if tu is not None:
            _log(
                verbose,
                f"[tokens] in={tu.input_tokens} out={tu.output_tokens}",
            )

        output = response.content or ""

        _log(verbose, f"Model output:\n{output}", limit=500)

        # 2a. Loop detection — a step identical to the previous one means the model
        # is stuck. Nudge it once toward the ```file: rewrite path.
        if last_output is not None and output == last_output:
            nudge = (
                "Warning: your output matches the previous step — you appear to be stuck. "
                "Rewrite the whole file with a "
                f"```file:{written_path or '<path>'}``` block, run it, then final_answer()."
            )
            messages.append({"role": "assistant", "content": output})
            messages.append({"role": "user", "content": nudge})
            _log(verbose, "[loop] Identical output — nudging once")
            continue
        last_output = output

        # 2. Parse the step: all blocks
        blocks = parse_step(output)
        if not blocks:
            # Model didn't produce any valid blocks — tell it (E001) and retry
            no_code_counter += 1
            messages.append({"role": "assistant", "content": output})
            if no_code_counter >= 3:
                template = (
                    f"You have produced {no_code_counter} text-only responses. "
                    f"Copy this exact template:\n"
                    f"```python\n"
                    f"run_python_file('{written_path or 'the_file.py'}')\n"
                    f"final_answer('done')\n"
                    f"```"
                )
                messages.append({"role": "user", "content": template})
            else:
                messages.append({
                    "role": "user",
                    "content": prompts.format_error(prompts.E001_NO_CODE),
                })
            continue
        no_code_counter = 0

        # 3. Write ALL file/executable blocks to disk FIRST
        file_reports, path_errors = write_blocks(blocks, written_path)
        wrote_target = bool(file_reports)

        # 3a. Path error — a file:/edit: block had no path or a placeholder path.
        if path_errors:
            messages.append({"role": "assistant", "content": output})
            all_bad = any("[E000]" in e for e in path_errors)
            nudge = (
                "You used an unsupported ```edit:``` block or a file block without a valid path. "
                "Rewrite the WHOLE file with ```file:<real_path>```, then run it."
            ) if all_bad else "\n".join(path_errors) + (
                "\nSpecify a real path in the ```file:<real_path>``` header, then run it to verify."
            )
            messages.append({"role": "user", "content": nudge})
            _log(verbose, f"[path-error] {path_errors}")
            continue

        # Track the path the model actually wrote so follow-up prompts can reference it.
        for lang, p, _ in blocks:
            if p and (lang == "file" or lang in _EXECUTABLE_LANGS):
                written_path = p
                break

        # 4. Find the FIRST executable block (python) to run
        exec_code = None
        exec_lang = None
        for lang, path, content in blocks:
            if lang in _EXECUTABLE_LANGS:
                exec_code = content
                exec_lang = lang
                break

        if exec_code is None:
            if file_reports:
                # A ```file:path``` block was written but there is no executable
                # block to verify it. Surface that and prompt the model to run it.
                observation = "\n".join(file_reports) + (
                    f"\nFile written. Call run_python_file('{written_path}') "
                    "to verify it works, then final_answer(...)."
                )
                messages.append({"role": "assistant", "content": output})
                messages.append({"role": "user", "content": f"[Result]\n{observation}"})
                continue
            # No executable block — nudge the model to include one
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": prompts.format_error(prompts.E001_NO_CODE),
            })
            continue

        # 5. Execute the code
        _log(verbose, f"\nExecuting {exec_lang} code:\n{exec_code}", limit=300)

        result = execute_code(exec_code, tools)

        # 5a. SyntaxError/IndentationError recovery — the model's own code
        # failed to compile, usually a quote/escape clash from embedding a whole
        # file in a string. Nudge it toward the ```file:path``` rewrite path.
        if (
            result.error
            and result.error_type in {"SyntaxError", "IndentationError", "TabError"}
            and syntax_warns < 3
        ):
            syntax_warns += 1
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": (
                    "SyntaxError in your code. Write the file with a ```file:<real_path>``` "
                    "block containing the raw content (NOT inside a Python string, NOT inside "
                    "a ```python block). Then verify with ```python\nrun_python_file('<path>')\n```."
                ),
            })
            _log(verbose, f"[syntax-warning] #{syntax_warns} — code had a SyntaxError")
            continue

        # C-rule: count a tool call OR writing a ```file: block as real work,
        # so a model that writes a file and then final_answers is not rejected.
        if result.tool_calls or wrote_target:
            has_used_tool = True

        # B-rule: track if this step had an error (structured tool markers, not
        # loose substrings, so normal output mentioning "Error:" doesn't count).
        step_had_error = bool(result.error)
        if not step_had_error and result.logs:
            if any(marker in result.logs for marker in _ERROR_MARKERS):
                step_had_error = True
        # Did this step actually re-verify the file (run it)? A successful
        # run after a prior error forgives that error.
        verified_this_step = "run_python_file" in result.tool_calls

        # 5c. No-progress guard: steps that neither write nor finalize mean the
        # model is stuck (e.g. only reading). Stop early instead of burning steps.
        made_progress = wrote_target or result.is_final_answer
        if made_progress:
            no_progress = 0
        else:
            no_progress += 1
        if no_progress >= 6:
            _log(verbose, "[no-progress] Stopping after 6 unproductive steps")
            return (
                "Stopped: no progress for 6 steps (no file written, no final answer). "
                f"Re-read '{written_path}' and rewrite it with a ```file:``` block."
            )

        # 6. Build observation
        parts: list[str] = []
        if result.error:
            parts.append(f"Execution logs:\n{result.logs}\nError:\n{result.error}")
        else:
            parts.append(f"Execution logs:\n{result.logs}")
            if result.output is not None:
                parts.append(f"Last output: {result.output}")
        observation = "\n".join(parts)

        # Exempt observations produced by read_file() from truncation — the
        # model explicitly asked to see the whole file, so we must not hide
        # the middle (where bugs usually live) behind a head/tail cut.
        if max_observation_length > 0 and "read_file" not in result.tool_calls:
            observation = _truncate_observation(observation, max_observation_length)

        _log(verbose, f"\nObservation:\n{observation}", limit=500)

        # 7. Check for final_answer — STRICT VERIFICATION
        if result.is_final_answer:
            # --- Rule B (E003): reject final_answer if this step errored, OR if a
            # previous step errored and this step did NOT re-verify the file ---
            if step_had_error or (last_step_had_error and not_verified_this_step):
                error_summary = result.error or "previous step failed"
                if not result.error and result.logs:
                    for marker in _ERROR_MARKERS:
                        idx = result.logs.find(marker)
                        if idx != -1:
                            error_summary = result.logs[idx:idx+200]
                            break
                messages.append({"role": "assistant", "content": output})
                messages.append({
                    "role": "user",
                    "content": prompts.format_error(prompts.E003_FINAL_AFTER_ERROR, detail=error_summary),
                })
                _log(verbose, "[final_answer-guard] Rejected: final_answer after error")
                continue

            # --- Rule C (E002): final_answer without any prior tool use ---
            if not has_used_tool:
                messages.append({"role": "assistant", "content": output})
                messages.append({
                    "role": "user",
                    "content": prompts.format_error(prompts.E002_FINAL_NO_ACTION),
                })
                _log(verbose, "[final_answer-guard] Rejected: final_answer without any tool use")
                continue

            # All checks passed — accept final_answer
            _log(verbose, f"\n{'='*20} Done (final_answer) {'='*20}")
            return str(result.output) if result.output is not None else (result.logs or "")[-2000:]

        # Update last_step_had_error for next iteration
        last_step_had_error = step_had_error

        # 7a. Warn if code ran without writing any file — nothing is fixed on disk.
        # Not warned on pure read/verify steps, nor right after the task starts.
        if (
            not result.error
            and not wrote_target
            and "run_python_file" not in result.tool_calls
            and "read_file" not in result.tool_calls
            and step >= 3
        ):
            msg = prompts.format_error(prompts.E004_NO_WRITE, detail=written_path)
            messages.append({"role": "assistant", "content": output})
            messages.append({"role": "user", "content": msg})
            _log(verbose, f"\n[no-write-warning] {msg}")
            continue

        # 8. Add to conversation history
        messages.append({"role": "assistant", "content": output})
        messages.append({"role": "user", "content": f"Observation:\n{observation}"})

    # Max steps reached — try to extract a reasonable answer from last output
    _log(verbose, f"\n{'='*20} Max steps reached {'='*20}")

    if messages and messages[-1].get("role") == "user":
        last_obs = messages[-1]["content"]
        if "Execution logs:\n" in last_obs:
            return last_obs.split("Execution logs:\n", 1)[1][-2000:]

    return "Max steps reached without final answer"


# Helpers ---------------------------------------------------------------
# get_clean_message_list / remove_content_after_stop_sequences are defined
# in types.py (single source of truth) and used by model.generate().
