"""ReAct loop — Thought → Code → Observation cycle.

Implements the core Thought → Code → Observation cycle:
1. Send system prompt + task + conversation history to the model
2. Parse the model's response for markdown blocks
3. Write all ```file:path```/```python:path```/```bash:path``` blocks to disk
4. Execute the FIRST ```python```/```bash``` block with injected tools
5. Feed the observation back to the model
6. Repeat until final_answer() is called or max_steps reached

Designed for small models (9B): minimal prompt, explicit stop conditions,
no planning phase.
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import TYPE_CHECKING

from .code_executor import ExecutionResult, execute_code
from .context import ContextCompressor
from .hashline import apply_edit_block
from . import prompts
from .tools import Tool
from .types import (
    ChatMessage,
    MessageRole,
    TokenUsage,
    get_clean_message_list,
    remove_content_after_stop_sequences,
)

if TYPE_CHECKING:
    from .model import ResponsesModel


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------

# Languages whose block content is executed (as opposed to written to disk).
_EXECUTABLE_LANGS = {"python", "py", "bash", "sh", "shell"}


def parse_step(text: str) -> list[tuple[str, str | None, str]]:
    """Parse one model turn into a list of (lang, path, content) blocks.

    Recognizes fenced blocks of the form:
        ```lang
        content
        ```
        ```lang:path
        content
        ```

    - lang can be any identifier (file, python, py, bash, sh, shell, etc.)
    - path is optional; if absent for python/bash/sh/shell, the block is saved
      to a temp file /tmp/mini-agent-{timestamp}-{step}.py for record.
    - Blocks are returned in document order.

    Returns:
        List of (lang, path_or_none, content).
    """
    # Strip special tokens
    text = re.sub(r'<\|mask_start\|>.*?<\|mask_end\|>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|.*?\|>', '', text)

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
    step: int,
    verbose: bool = False,
) -> tuple[bool, list[str], list[str], list[str]]:
    """Write all blocks to disk.

    - ``file:path`` (or any non-executable lang) -> write raw content to path.
    - ``edit:path`` -> apply hash-anchored edits to the file on disk.
    - ``python:path`` / ``bash:path`` / ``sh:path`` / ``shell:path`` -> write
      content to path (also executable).
    - ``python`` / ``bash`` / ``sh`` / ``shell`` without path -> write to
      /tmp/mini-agent-{timestamp}-{step}.{ext} for record.
    - ``file`` / ``edit`` without path -> error (E010_NO_PATH), reported back
      to the caller so the model can be nudged.
    - All paths resolved relative to CWD; parent dirs created.

    Returns:
        (wrote_any, edit_reports, file_reports, path_errors) - ``wrote_any`` is
        True if at least one block was written/edited; ``edit_reports`` is a
        list of result strings from edit blocks; ``file_reports`` is a list of
        "written" messages for ``file:`` blocks; ``path_errors`` is a list of
        error strings for file/edit blocks that had no path.
    """
    wrote_any = False
    edit_reports: list[str] = []
    file_reports: list[str] = []
    path_errors: list[str] = []
    ts = time.strftime("%Y%m%d_%H%M%S")

    for lang, path, content in blocks:
        if lang == "edit":
            if path is None:
                path_errors.append(f"[E010_NO_PATH] edit block without path")
                if verbose:
                    print("[write_blocks] Error: edit block without path", file=sys.stderr)
                continue
            full_path = os.path.abspath(path)
            result = apply_edit_block(full_path, content)
            edit_reports.append(result)
            wrote_any = True
            if verbose:
                print(f"[write_blocks] Edited {full_path}: {result[:120]}", file=sys.stderr)
            continue

        if path is None:
            if lang in _EXECUTABLE_LANGS:
                # Save executable blocks without path to temp for record
                ext = "py" if lang in {"python", "py"} else "sh"
                path = f"/tmp/mini-agent-{ts}-{step}.{ext}"
            else:
                # Non-executable without path - error
                path_errors.append(f"[E010_NO_PATH] {lang} block without path")
                if verbose:
                    print(f"[write_blocks] Error: {lang} block without path", file=sys.stderr)
                continue

        # Resolve path relative to CWD
        full_path = os.path.abspath(path)

        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            wrote_any = True
            if lang == "file":
                file_reports.append(f"Written {full_path}")
            if verbose:
                print(f"[write_blocks] Wrote {lang} block to {full_path}", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[write_blocks] Failed to write {full_path}: {e}", file=sys.stderr)

    return wrote_any, edit_reports, file_reports, path_errors


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
    tools: list[Tool],
    max_steps: int = 8,
    max_observation_length: int = 2000,
    verbose: bool = True,
    compressor: ContextCompressor | None = None,
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
    recent_outputs: list[str] = []
    consecutive_loops = 0
    wrote_file = False
    no_progress = 0
    syntax_warns = 0
    written_path: str | None = None  # path the model wrote this/last step (from its blocks)

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

        # 1. Call the model
        cleaned = get_clean_message_list(messages, flatten_messages_as_text=True)
        api_messages: list[dict] = []
        for msg in cleaned:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                content = "".join(parts)
            api_messages.append({"role": role, "content": content})

        response = model.generate(
            messages=[
                {"role": m["role"], "content": [{"type": "text", "text": m["content"]}]}
                for m in api_messages
            ],
            stop_sequences=stop_sequences,
        )

        output = response.content or ""

        # Apply stop sequences
        output = remove_content_after_stop_sequences(output, stop_sequences) or output

        _log(verbose, f"Model output:\n{output}", limit=500)

        # 2a. Loop detection — repeated identical output means the model is stuck
        recent_outputs.append(output)
        if len(recent_outputs) >= 3 and recent_outputs[-1] == recent_outputs[-2] == recent_outputs[-3]:
            consecutive_loops += 1
            if consecutive_loops >= 3:
                _log(verbose, "[loop] Stuck after 2 warnings — stopping")
                return prompts.format_error(prompts.E006_LOOP, target_file=written_path)
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": prompts.format_error(prompts.E006_LOOP, target_file=written_path),
            })
            _log(verbose, f"[loop] Warning #{consecutive_loops} — identical output detected")
            continue
        else:
            consecutive_loops = 0

        # 2. Parse the step: all blocks
        blocks = parse_step(output)
        if not blocks:
            # Model didn't produce any valid blocks — tell it (E001) and retry
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": prompts.format_error(prompts.E001_NO_CODE),
            })
            continue

        # 3. Write ALL file/executable blocks to disk FIRST
        wrote_this_step, edit_reports, file_reports, path_errors = write_blocks(blocks, step, verbose)
        if wrote_this_step:
            wrote_file = True

        # 3a. Path error detection - file:/edit: block without a path
        if path_errors:
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": prompts.format_error(prompts.E010_NO_PATH, target_file=written_path),
            })
            _log(verbose, f"[path-error] {path_errors}")
            continue

        # Track the path the model actually wrote (file:/edit:/executable:path),
        # so error-recovery prompts and the run instruction can reference it.
        for lang, p, _ in blocks:
            if p and (lang == "file" or lang == "edit" or lang in _EXECUTABLE_LANGS):
                written_path = p
                break

        # 3a. Stale anchor detection — if any edit block hit a hash mismatch,
        # inject E009 recovery prompt and retry
        stale = [r for r in edit_reports if r.startswith("[E_STALE_ANCHOR]")]
        if stale:
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": prompts.format_error(prompts.E009_STALE_ANCHOR, target_file=written_path),
            })
            continue

        # 4. Find the FIRST executable block (python/bash/sh/shell) to run
        exec_code = None
        exec_lang = None
        for lang, path, content in blocks:
            if lang in _EXECUTABLE_LANGS:
                exec_code = content
                exec_lang = lang
                break

        if exec_code is None:
            if edit_reports:
                # Edit-only step — surface edit results as observation
                observation = "\n".join(edit_reports)
                messages.append({"role": "assistant", "content": output})
                messages.append({"role": "user", "content": f"Observation:\n{observation}"})
                continue
            if file_reports:
                # A ```file:path``` block was written but there is no executable
                # block to verify it. Surface "file written" as an observation
                # instead of a spurious E001, and prompt the model to run/verify.
                observation = "\n".join(file_reports) + (
                    f"\nFile written. Call run_python_file('{written_path}') "
                    "to verify it works, then final_answer(...)."
                )
                messages.append({"role": "assistant", "content": output})
                messages.append({"role": "user", "content": f"Observation:\n{observation}"})
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

        # 5a. SyntaxError recovery: the model's own generated code
        # failed to compile — almost always a quote/escape conflict from
        # embedding a whole file inside a string literal. Nudge it to the
        # ```file:path block path.
        if result.error and result.error.startswith("SyntaxError") and syntax_warns < 3:
            syntax_warns += 1
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": prompts.format_error(prompts.E008_SYNTAX, target_file=written_path),
            })
            _log(verbose, f"[syntax-warning] #{syntax_warns} — code had a SyntaxError")
            continue

        # 5b. Track whether the file on disk was actually written this step.
        # A ```file:```/```edit:``` block writing a file counts (wrote_this_step);
        # merely MENTIONING a write in source text does not.
        if not result.error and wrote_this_step:
            wrote_file = True

        # 5c. No-progress guard: if the model keeps producing steps that neither
        # write, verify, nor finalize, it is stuck. Stop early.
        # Verification (run_python_file/execute_bash_command) only counts as
        # progress AFTER a write — running the still-buggy file is not progress.
        verified = "run_python_file" in result.tool_calls or "execute_bash_command" in result.tool_calls
        made_progress = wrote_this_step or result.is_final_answer
        if verified and not wrote_file:
            made_progress = False
        if made_progress:
            no_progress = 0
        else:
            no_progress += 1
        if no_progress >= 6:
            _log(verbose, "[no-progress] Stopping after 6 unproductive steps")
            return prompts.format_error(prompts.E007_NO_PROGRESS, target_file=written_path)

        # 6. Build observation
        parts: list[str] = []
        if edit_reports:
            parts.extend(edit_reports)
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

        # 7. Check for final_answer
        if result.is_final_answer:
            # Guard: a final_answer without the file ever being written means the
            # fix lives only in exec() memory — the file on disk is still buggy.
            if not wrote_file:
                messages.append({"role": "assistant", "content": output})
                messages.append({
                    "role": "user",
                    "content": prompts.format_error(prompts.E002_FINAL_NO_WRITE, target_file=written_path),
                })
                _log(verbose, "[final_answer-warning] final_answer without writing the file")
                continue
            _log(verbose, f"\n{'='*20} Done (final_answer) {'='*20}")
            return str(result.output) if result.output is not None else result.logs

        # 7a. Warn if code ran without writing the file — file on disk is NOT fixed.
        # Only warn after step 2 (model should read first, then fix), and do NOT
        # warn on verification steps (run_python_file / execute_bash_command)
        if (
            not result.error
            and not wrote_this_step
            and "run_python_file" not in result.tool_calls
            and "execute_bash_command" not in result.tool_calls
            and step >= 3
        ):
            read_only = "open(" in exec_code or ".read(" in exec_code or "read()" in exec_code
            # Detect the "assign fixed CODE to a variable but never write" pattern.
            # Only treat as a fix variable if the assigned value looks like code
            # (contains def/import/class/return), NOT plain data like DATA = """...""".
            var = None
            m = re.search(r'(\w+)\s*=\s*("""|\'\'\')', exec_code)
            if m:
                val = exec_code[m.end():m.end() + 300]
                if re.search(r'\b(def |import |class |return |print\()', val):
                    var = m.group(1)
            if var:
                code_key = prompts.E004_WRITE_VAR
            elif read_only:
                code_key = prompts.E003_WRITE_READONLY
            else:
                code_key = prompts.E005_WRITE_GENERIC
            msg = prompts.format_error(code_key, target_file=written_path, var=var)
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": msg,
            })
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
            return last_obs.split("Execution logs:\n", 1)[1]

    return "Max steps reached without final answer"