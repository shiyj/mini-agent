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
    # Strip special tokens and thinking blocks
    text = re.sub(r'<\|mask_start\|>.*?<\|mask_end\|>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|.*?\|>', '', text)
    if ' thinking' in text:
        text = re.sub(r" thinking.*? response", "", text, flags=re.DOTALL)
    elif ' response' in text:
        text = text.replace(' response', '')

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
    # Conversation history
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

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

        # 2. Parse code from model output
        code = parse_code(output)
        if code is None:
            # Model didn't produce valid code — tell it and retry
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": (
                    "Error: No valid Python code block found. "
                    "Please write your code in a ```python``` block and try again."
                ),
            })
            continue

        # 3. Execute the code
        if verbose:
            print(f"\nExecuting code:\n{code[:300]}", file=sys.stderr)

        result = execute_code(code, tools)

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
            if verbose:
                print(f"\n{'='*20} Done (final_answer) {'='*20}", file=sys.stderr)
            return str(result.output) if result.output is not None else result.logs

        # 5a. Warn if code ran without write_file() — file on disk is NOT fixed
        if not result.error and "write_file(" not in code and "def " in code:
            messages.append({"role": "assistant", "content": output})
            messages.append({
                "role": "user",
                "content": (
                    "Warning: Your code ran but did NOT save changes to the file. "
                    "Running code without write_file() does NOT fix the file on disk. "
                    "Use write_file() to save the corrected code, then run_python_file() to test it."
                ),
            })
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
