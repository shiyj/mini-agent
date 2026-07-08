"""ReAct loop — replaces smolagents' 800-line CodeAgent.

Implements the core Thought → Code → Observation cycle:
1. Send system prompt + task + conversation history to the model
2. Parse the model's response for code blocks
3. Execute the code with injected tools
4. Feed the observation back to the model
5. Repeat until final_answer() is called or max_steps reached

Designed for small models (9B): minimal prompt, explicit stop conditions,
no planning phase.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from .code_executor import ExecutionResult, execute_code
from .context import ContextCompressor
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


def _truncate_observation(text: str, max_length: int) -> str:
    """Truncate observation text at character level, preserving head and tail."""
    if max_length <= 0 or len(text) <= max_length:
        return text
    half = max_length // 2
    return text[:half] + f"\n... [truncated {len(text) - max_length} chars] ...\n" + text[-half:]


def parse_code(text: str) -> str | None:
    """Extract Python code from the LLM's markdown output.

    Strips <think>...</think> blocks (Qwen/ornith thinking mode) first.
    Then extracts code blocks in order of preference:
    1. ```python / ```py — returned as-is (Python code)
    2. ```bash / ```sh / ```shell — converted to execute_bash_command() calls
    3. ``` (generic) — returned as-is
    4. Bare code without fences — tested via ast.parse()

    Returns None if no usable code can be extracted.
    """
    # Strip special tokens
    text = re.sub(r'<\|mask_start\|>.*?<\|mask_end\|>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|.*?\|>', '', text)
    # Strip leaked reasoning tags (model sometimes emits <think>...</think> inline)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = text.replace('</think>', '').replace('<think>', '')

    # 1. ```python / ```py — native Python code
    pattern_py = r"```(?:python|py)\s*\n(.*?)```"
    matches = re.findall(pattern_py, text, re.DOTALL)
    if matches:
        return "\n\n".join(m.strip() for m in matches)

    # 1b. Unclosed ```python block (model ran out of tokens)
    unclosed = re.search(r"```(?:python|py)\s*\n(.+)", text, re.DOTALL)
    if unclosed:
        code = unclosed.group(1).strip()
        if code:
            return code

    # 2. ```bash / ```sh / ```shell — convert to execute_bash_command() call
    pattern_bash = r"```(?:bash|sh|shell)\s*\n(.*?)```"
    matches = re.findall(pattern_bash, text, re.DOTALL)
    if matches:
        parts = []
        for block in matches:
            content = block.strip()
            parts.append(
                f'__result = execute_bash_command({repr(content)})\n'
                f"print(__result)"
            )
        return "\n\n".join(parts)

    # 2b. Unclosed ```bash block
    unclosed_bash = re.search(r"```(?:bash|sh|shell)\s*\n(.+)", text, re.DOTALL)
    if unclosed_bash:
        content = unclosed_bash.group(1).strip()
        if content:
            return (
                f'__result = execute_bash_command({repr(content)})\n'
                f"print(__result)"
            )

    # 3. Generic ``` ... ``` — no language tag, try as Python
    pattern_generic = r"```\s*\n(.*?)```"
    matches = re.findall(pattern_generic, text, re.DOTALL)
    if matches:
        return "\n\n".join(m.strip() for m in matches)

    # 3b. Unclosed generic code block
    unclosed_generic = re.search(r"```\s*\n(.+)", text, re.DOTALL)
    if unclosed_generic:
        code = unclosed_generic.group(1).strip()
        if code:
            return code

    # 4. Maybe the LLM outputted code directly without code fences
    try:
        import ast
        ast.parse(text)
        return text.strip()
    except SyntaxError:
        pass

    return None


def react_loop(
    system_prompt: str,
    task: str,
    model: "ResponsesModel",
    tools: list[Tool],
    max_steps: int = 8,
    max_observation_length: int = 2000,
    verbose: bool = True,
    compressor: ContextCompressor | None = None,
    target_file: str = "output.py",
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
        target_file: Path of the file the agent is meant to create/fix. Passed
            through so that recovery prompts (see ``prompts.ERROR_PROMPTS``) can
            name the exact file the model must write to. Keeping it here avoids
            baking the path into the (short, generic) system prompt.

    Returns:
        The final answer string, or an error message.
    """
    # Conversation history
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    recent_outputs: list[str] = []
    consecutive_loops = 0
    wrote_file = False
    no_progress = 0

    stop_sequences = ["Observation:"]

    for step in range(1, max_steps + 1):
        if verbose:
            print(f"\n{'='*20} Step {step} {'='*20}", file=sys.stderr)

        # 0. Compress context if a compressor is configured
        if compressor is not None:
            before = len(messages)
            messages = compressor.compress(messages, model)
            if verbose and len(messages) < before:
                print(
                    f'[context] {before} -> {len(messages)} messages (compressed)',
                    file=sys.stderr,
                )
        
        # 0a. Token-based compress check (if compressor configured)
        if compressor is not None and hasattr(model, 'context_window') and hasattr(model, 'max_output_tokens'):
            # Character-based estimate: ~4 chars per token
            # Calculate current token estimate from all messages
            total_chars = sum(len(msg.get('content', '')) for msg in messages)
            estimated_tokens = total_chars // 4
            
            max_input_tokens = model.context_window - model.max_output_tokens
            if estimated_tokens > max_input_tokens * 0.85:  # Buffer
                if verbose:
                    print(f'[token-check] Estimated {estimated_tokens} tokens exceeding {max_input_tokens} max, compressing', file=sys.stderr)
                before = len(messages)
                # Keep only system, task, recent messages (force compress if needed)
                # Re-use compressor with batch size increased to clear space
                old_keep = getattr(compressor, 'compress_batch', 5)
                setattr(compressor, 'compress_batch', len(messages))
                messages = compressor.compress(messages, model)
                setattr(compressor, 'compress_batch', old_keep)
                if verbose and len(messages) < before:
                    print(f'[token-compress] {before} -> {len(messages)} messages', file=sys.stderr)

        # 1. Call the model
        cleaned = get_clean_message_list(messages, flatten_messages_as_text=True)

        # Flatten content into plain strings for the API
        api_messages: list[dict] = []
        for msg in cleaned:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, list):
                # Flatten content list to text
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

        if verbose:
            print(f"Model output:\n{output[:500]}", file=sys.stderr)

        # 2a. Loop detection — repeated identical output means the model is stuck
        recent_outputs.append(output)
        if len(recent_outputs) >= 3 and recent_outputs[-1] == recent_outputs[-2] == recent_outputs[-3]:
            consecutive_loops += 1
            if consecutive_loops >= 3:
                if verbose:
                    print("[loop] Stuck after 2 warnings — stopping", file=sys.stderr)
                return prompts.format_error(prompts.E006_LOOP, target_file=target_file)
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": prompts.format_error(prompts.E006_LOOP, target_file=target_file),
            })
            if verbose:
                print(f"[loop] Warning #{consecutive_loops} — identical output detected", file=sys.stderr)
            continue
        else:
            consecutive_loops = 0

        # 2. Parse code from model output
        code = parse_code(output)
        if code is None:
            # Model didn't produce valid code — tell it (E001) and retry
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": prompts.format_error(prompts.E001_NO_CODE),
            })
            continue

        # 3. Execute the code
        if verbose:
            print(f"\nExecuting code:\n{code[:300]}", file=sys.stderr)

        result = execute_code(code, tools)

        # 3a. Track whether a write_file() call actually executed and persisted
        # the file. We check the execution logs (not just the source text) so that
        # merely MENTIONING write_file (in a comment or malformed call) does not
        # falsely count as progress.
        if not result.error and result.logs and "Written" in result.logs:
            wrote_file = True

        # 3b. No-progress guard: if the model keeps producing steps that neither
        # write, verify, nor finalize, it is stuck (e.g. re-defining functions
        # without ever calling write_file). Stop early instead of burning steps.
        # Verification (run_python_file/execute_bash_command) only counts as
        # progress AFTER a write — running the still-buggy file is not progress.
        verified = "run_python_file(" in code or "execute_bash_command(" in code
        made_progress = wrote_file or result.is_final_answer
        if verified and not wrote_file:
            made_progress = False
        if made_progress:
            no_progress = 0
        else:
            no_progress += 1
        if no_progress >= 6:
            if verbose:
                print("[no-progress] Stopping after 6 unproductive steps", file=sys.stderr)
            return prompts.format_error(prompts.E007_NO_PROGRESS, target_file=target_file)

        # 4. Build observation
        if result.error:
            observation = f"Execution logs:\n{result.logs}\nError:\n{result.error}"
        else:
            observation = f"Execution logs:\n{result.logs}"
            if result.output is not None:
                observation += f"\nLast output: {result.output}"

        if max_observation_length > 0:
            observation = _truncate_observation(observation, max_observation_length)

        if verbose:
            print(f"\nObservation:\n{observation[:500]}", file=sys.stderr)

        # 5. Check for final_answer
        if result.is_final_answer:
            # Guard: a final_answer without ever calling write_file() means the
            # fix lives only in exec() memory — the file on disk is still buggy.
            if not wrote_file:
                messages.append({"role": "assistant", "content": output})
                messages.append({
                    "role": "user",
                    "content": prompts.format_error(prompts.E002_FINAL_NO_WRITE, target_file=target_file),
                })
                if verbose:
                    print("[final_answer-warning] final_answer without write_file", file=sys.stderr)
                continue
            if verbose:
                print(f"\n{'='*20} Done (final_answer) {'='*20}", file=sys.stderr)
            return str(result.output) if result.output is not None else result.logs

        # 5a. Warn if code ran without write_file() — file on disk is NOT fixed.
        # Only warn after step 2 (model should read first, then fix), and do NOT
        # warn on verification steps (run_python_file / execute_bash_command) which
        # legitimately don't write but confirm the fix. Pick ONE error code per
        # situation (see prompts.ERROR_PROMPTS): E004 when corrected code was
        # assigned to a variable, E003 when only read/printed, else E005.
        if (not result.error
                and "write_file(" not in code
                and "run_python_file(" not in code
                and "execute_bash_command(" not in code
                and step >= 3):
            read_only = ("open(" in code or ".read(" in code or "read()" in code) and "write_file(" not in code
            # Detect the "assign fixed CODE to a variable but never write" pattern.
            # Only treat as a fix variable if the assigned value looks like code
            # (contains def/import/class/return), NOT plain data like DATA = """...""".
            var = None
            m = re.search(r'(\w+)\s*=\s*("""|\'\'\')', code)
            if m:
                val = code[m.end():m.end() + 300]
                if re.search(r'\b(def |import |class |return |print\()', val):
                    var = m.group(1)
            if var:
                code_key = prompts.E004_WRITE_VAR
            elif read_only:
                code_key = prompts.E003_WRITE_READONLY
            else:
                code_key = prompts.E005_WRITE_GENERIC
            msg = prompts.format_error(code_key, target_file=target_file, var=var)
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": msg,
            })
            if verbose:
                print(f"\n[write_file-warning] {msg}", file=sys.stderr)
            continue

        # 6. Add to conversation history
        messages.append({"role": "assistant", "content": output})
        messages.append({"role": "user", "content": f"Observation:\n{observation}"})

    # Max steps reached — try to extract a reasonable answer from last output
    if verbose:
        print(f"\n{'='*20} Max steps reached {'='*20}", file=sys.stderr)

    # Last resort: return the last observation
    if messages and messages[-1].get("role") == "user":
        last_obs = messages[-1]["content"]
        # Extract just the logs part
        if "Execution logs:\n" in last_obs:
            return last_obs.split("Execution logs:\n", 1)[1]

    return "Max steps reached without final answer"
