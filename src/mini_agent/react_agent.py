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
from .prompts import build_deep_system_prompt
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


def parse_code(text: str) -> str | None:
    """Extract Python code from the LLM's markdown output.

    Strips <think>...</think> blocks (Qwen thinking mode) first.
    Then extracts the LAST ```python``` code block (models sometimes
    repeat code in both thinking and response sections).

    Returns None if no code can be extracted.
    """
    # Strip <think>...</think> blocks (Qwen/ornith thinking mode)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # If only </think> is present (opening tag consumed by SSE), take everything after it
    if "</think>" in text:
        text = text.split("</think>", 1)[1]

    # Try ```python ... ``` pattern first
    pattern_py = r"```(?:python|py)\s*\n(.*?)```"
    matches = re.findall(pattern_py, text, re.DOTALL)
    if matches:
        return "\n\n".join(m.strip() for m in matches)

    # Try generic ``` ... ``` pattern
    pattern_generic = r"```\s*\n(.*?)```"
    matches = re.findall(pattern_generic, text, re.DOTALL)
    if matches:
        return "\n\n".join(m.strip() for m in matches)

    # Maybe the LLM outputted code directly without code fences
    try:
        import ast
        ast.parse(text)
        return text.strip()
    except SyntaxError:
        pass

    return None


def react_loop(
    task: str,
    model: "ResponsesModel",
    tools: list[Tool],
    max_steps: int = 8,
    verbose: bool = True,
) -> str:
    """Run the ReAct loop until final_answer or max_steps.

    Args:
        task: The task description.
        model: A ``ResponsesModel`` instance.
        tools: List of Tool objects available to the agent.
        max_steps: Maximum number of Thought/Code/Observation cycles.
        verbose: If True, print step progress to stderr.

    Returns:
        The final answer string, or an error message.
    """
    system_prompt = build_deep_system_prompt(tools)

    # Conversation history
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    stop_sequences = ["Observation:"]

    for step in range(1, max_steps + 1):
        if verbose:
            print(f"\n{'='*20} Step {step} {'='*20}", file=sys.stderr)

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

        if verbose:
            print(f"\nObservation:\n{observation[:500]}", file=sys.stderr)

        # 5. Check for final_answer
        if result.is_final_answer:
            if verbose:
                print(f"\n{'='*20} Done (final_answer) {'='*20}", file=sys.stderr)
            return str(result.output) if result.output is not None else result.logs

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
