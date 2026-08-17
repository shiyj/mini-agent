#!/usr/bin/env python3
"""ReAct agent loop with tool use, error recovery, and strict final_answer verification.

All prompt text lives in prompts.py; this file contains only the execution logic.
"""

import os
import re
import sys
import time
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

    # Normalize: ```python\nfile:path → correct fence
    text = re.sub(
        r'```python\s*\nfile:([^\n]+)',
        r'```file:\1',
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
    step: int,
    verbose: bool = False,
    fallback_path: str | None = None,
) -> tuple[bool, list[str], list[str]]:
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
        (wrote_any, file_reports, path_errors) - ``wrote_any`` is True if at
        least one block was written; ``file_reports`` lists "written" messages
        for ``file:`` blocks; ``path_errors`` lists error strings for blocks
        that had no path, a placeholder path, or used the unsupported ``edit:``.
    """
    wrote_any = False
    file_reports: list[str] = []
    path_errors: list[str] = []
    ts = time.strftime("%Y%m%d_%H%M%S")

    for lang, path, content in blocks:
        # 'edit:' is no longer a supported edit path — nudge back to file: rewrite
        if lang == "edit":
            path_errors.append("[E000] edit: is not supported. Rewrite the whole file with a ```file:<path>``` block.")
            continue

        if path is None:
            if lang in _EXECUTABLE_LANGS:
                # Save executable blocks without path to temp for record
                ext = "py" if lang in {"python", "py"} else "sh"
                path = f"/tmp/mini-agent-{ts}-{step}.{ext}"
            else:
                # Non-executable without path - error
                path_errors.append(f"[E011] {lang} block without path")
                continue

        # Placeholder paths (e.g. <your_file_path>) are mistakes — try to recover
        if "<your_file_path>" in (path or ""):
            real = _find_real_path(blocks, fallback_path)
            if real:
                path = real
            else:
                path_errors.append(f"[E013] {lang} block has invalid path: {path}")
                continue

        full_path = os.path.abspath(path)

        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            wrote_any = True
            if lang == "file":
                file_reports.append(f"Written {full_path}")
        except Exception:
            pass  # write failures are not fatal; let the model retry

    return wrote_any, file_reports, path_errors


def _find_real_path(blocks: list[tuple[str, str | None, str]], fallback: str | None = None) -> str | None:
    """Find a real file path from other blocks, for auto-replacing ``<your_file_path>``."""
    for _, p, _ in blocks:
        if p and "<your_file_path>" not in p:
            return p
    if fallback:
        return fallback
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
    recent_outputs: list[str] = []
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

        # 2a. Loop detection — a step identical to the previous one means the model
        # is stuck. Nudge it once toward the ```file: rewrite path.
        if recent_outputs and output == recent_outputs[-1]:
            nudge = (
                "Warning: your output matches the previous step — you appear to be stuck. "
                "Rewrite the whole file with a "
                f"```file:{written_path or '<path>'}``` block, run it, then final_answer()."
            )
            messages.append({"role": "assistant", "content": output})
            messages.append({"role": "user", "content": nudge})
            _log(verbose, "[loop] Identical output — nudging once")
            continue
        recent_outputs.append(output)

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
        _, file_reports, path_errors = write_blocks(blocks, step, verbose, written_path)
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

        # 5a. SyntaxError recovery — the model's own code failed to compile,
        # usually a quote/escape clash from embedding the whole file in a string.
        # Nudge it to the ```file:path``` rewrite path.
        if result.error and result.error.startswith("SyntaxError") and syntax_warns < 3:
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

        # B-rule: track if this step had an error
        step_had_error = bool(result.error)
        if not step_had_error and result.logs:
            error_indicators = ["FAILED", "Traceback", "Error:", "exit="]
            if any(ind in result.logs for ind in error_indicators):
                step_had_error = True

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
            # --- Rule B (E003): final_answer after an error in the previous step ---
            if last_step_had_error or step_had_error:
                error_summary = result.error or "previous step failed"
                if not result.error and result.logs:
                    for ind in ["FAILED", "Traceback", "Error:", "exit="]:
                        idx = result.logs.find(ind)
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
            return str(result.output) if result.output is not None else result.logs

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
            return last_obs.split("Execution logs:\n", 1)[1]

    return "Max steps reached without final answer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_clean_message_list(messages: list[dict], flatten_messages_as_text: bool = True) -> list[dict]:
    """Remove internal metadata and flatten content if needed."""
    cleaned = []
    for msg in messages:
        content = msg.get("content", "")
        if flatten_messages_as_text and isinstance(content, list):
            # Flatten list of content parts to single string
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            content = "".join(parts)
        cleaned.append({"role": msg["role"], "content": content})
    return cleaned


def remove_content_after_stop_sequences(text: str, stop_sequences: list[str]) -> str | None:
    """Remove everything after the first occurrence of any stop sequence."""
    if not stop_sequences:
        return text
    for seq in stop_sequences:
        idx = text.find(seq)
        if idx != -1:
            return text[:idx]
    return text