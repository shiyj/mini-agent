"""Agent implementations — two depths: fast (direct generation) and deep (ReAct loop).

Architecture:
  ┌─ Fast Agent (极速流) ───────────────────────────────┐
  │  Calls model.generate() directly, no agent scaffold. │
  │  Single pass, ~50 words system prompt.               │
  │  Use: boilerplate, syntax conversion, one-shot gen   │
  └──────────────────────────────────────────────────────┘

  ┌─ Deep Agent (深思流) ────────────────────────────────┐
  │  Full ReAct loop: write → run → observe → fix.       │
  │  System prompt built by build_deep_system_prompt().   │
  │  max_steps=8, 3 tools, temp=0.4-0.5.                │
  │  Use: complex scripts, debugging, iterative tasks    │
  └──────────────────────────────────────────────────────┘

Users create the model externally and pass it in.
"""

import subprocess

from .config import load_prompts
from .model import ResponsesModel
from .prompts import FAST_AGENT_SYSTEM, build_deep_system_prompt
from .react_agent import react_loop
from .tools import Tool, tool
from .types import (
    ChatMessage,
    MessageRole,
    get_clean_message_list,
    remove_content_after_stop_sequences,
)


# ---------------------------------------------------------------------------
# 1. Convenience model factory (optional — users can build models directly)
# ---------------------------------------------------------------------------

def get_local_model(
    model_name: str,
    api_base: str = "http://localhost:11434",
    temperature: float = 0.0,
    max_output_tokens: int = 512,
    log_file: str | None = None,
) -> ResponsesModel:
    """Create a model that talks to a ``/v1/responses`` endpoint.

    No smolagents dependency — uses ``httpx`` directly.

    Args:
        model_name: Model tag, e.g. ``"ornith:9b"``.
        api_base: Server base URL.
        temperature: Sampling temperature.
        max_output_tokens: Maximum tokens the model may generate.
        log_file: Optional path to a JSONL file for request/response logging.

    Returns:
        A configured ``ResponsesModel``.
    """
    return ResponsesModel(
        model_name=model_name,
        api_base=api_base,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        log_file=log_file,
    )


# ---------------------------------------------------------------------------
# 2. Agent tools
# ---------------------------------------------------------------------------

@tool
def write_file(filename: str, content: str) -> str:
    """Write text content to a file on disk.

    Args:
        filename: Path to the file to write.
        content: The text content to write.

    Returns:
        A confirmation message with the file size.
    """
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} bytes to {filename}"
    except Exception as e:
        return f"Error writing {filename}: {e}"


@tool
def run_python_file(filename: str) -> str:
    """Run a Python script file and capture its output.

    Args:
        filename: Path to the Python script to run.

    Returns:
        The stdout and stderr output from the script.
    """
    try:
        result = subprocess.run(
            ["python3", filename],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return f"\u3010OK\u3011\n{result.stdout}"
        else:
            return (
                f"\u3010FAILED exit={result.returncode}\u3011\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
    except subprocess.TimeoutExpired:
        return "\u3010Timeout\u3011Script ran longer than 30 seconds."
    except Exception as e:
        return f"\u3010Error\u3011{e}"


@tool
def execute_bash_command(command: str) -> str:
    """Execute a Bash/shell command on the local machine.

    Captures stdout and stderr with a 30-second timeout.

    Args:
        command: The complete shell command string to execute.

    Returns:
        A string containing the command output or error details.
    """
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return f"\u3010Execution OK\u3011\nOutput:\n{result.stdout}"
        else:
            return (
                f"\u3010Execution FAILED\u3011\n"
                f"Return code: {result.returncode}\n"
                f"Stderr:\n{result.stderr}"
            )
    except subprocess.TimeoutExpired:
        return "\u3010System Error\u3011Command timed out after 30 seconds."
    except Exception as e:
        return f"\u3010System Error\u3011{e}"


# ---------------------------------------------------------------------------
# 3. Fast agent — direct model call, no ReAct loop
# ---------------------------------------------------------------------------

def run_fast_agent(
    prompt: str,
    model: ResponsesModel,
    prefill: bool | None = None,
) -> str:
    """Single-pass code generation. No ReAct loop, no tool calling.

    Args:
        prompt: Task description.
        model: A ``ResponsesModel`` instance.
        prefill: Whether to use assistant-prefill to bypass thinking mode.
            Defaults to the ``prefill`` key in ``prompts.json``.

    Returns:
        The raw model output (code / text).
    """
    # Read prefill config from JSON; prompt text lives in prompts.py
    prompts_cfg = load_prompts()
    fast_cfg = prompts_cfg.get("fast_agent", {})

    if prefill is None:
        prefill = fast_cfg.get("prefill", True)

    prefill_text = fast_cfg.get("prefill_text", "```python\n")

    messages = [
        {"role": "system", "content": [{"type": "text", "text": FAST_AGENT_SYSTEM}]},
        {"role": "user", "content": [{"type": "text", "text": prompt}]},
    ]

    if prefill:
        messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": prefill_text}]},
        )

    response = model.generate(messages=messages)
    content = response.content.strip() if response.content else ""

    if prefill and content:
        content = prefill_text + content

    return content


# ---------------------------------------------------------------------------
# 4. Deep agent — Full ReAct loop with tools and iteration
# ---------------------------------------------------------------------------

def run_deep_agent(
    prompt: str,
    model: ResponsesModel,
    target_file: str = "output.py",
    max_steps: int = 8,
) -> str:
    """Deep ReAct: write → run → observe → fix → repeat.

    System prompt is built by ``build_deep_system_prompt()`` from
    ``prompts.py`` — the single source of truth for all prompt text.

    Args:
        prompt: Task description.
        model: A ``ResponsesModel`` instance.
        target_file: File the agent should write its script to.
        max_steps: Maximum ReAct iterations (8-15 recommended).

    Returns:
        The final agent output after all iterations.
    """
    # Deep agent needs more tokens for complex scripts
    model.max_output_tokens = max(model.max_output_tokens, 4096)

    tools = [write_file, run_python_file, execute_bash_command]

    # Build the system prompt — all text comes from prompts.py
    system_prompt = build_deep_system_prompt(tools, target_file=target_file)

    return react_loop(
        system_prompt=system_prompt,
        task=prompt,
        model=model,
        tools=tools,
        max_steps=max_steps,
        verbose=True,
    )
