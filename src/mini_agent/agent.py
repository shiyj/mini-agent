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

Users create the model via config.load_model() and pass it in.
"""

import subprocess
import sys

from .config import load_model
from .model import ResponsesModel
from .prompts import FAST_AGENT_SYSTEM, FAST_AGENT_PREFILL, FAST_AGENT_PREFILL_TEXT, build_deep_system_prompt
from .react_agent import react_loop
from .tools import Tool, tool


# ---------------------------------------------------------------------------
# 2. Agent tools
# ---------------------------------------------------------------------------

@tool
def read_file(filename: str, offset: int = 0, limit: int = 0) -> str:
    """Read a file and return its plain content (no line tags).

    Use ``offset`` (1-indexed) and ``limit`` to read a range.

    Args:
        filename: Path to the file to read.
        offset: Starting line number (1-indexed, 0 = start of file).
        limit: Max lines to return (0 = all lines).

    Returns:
        The file content, or an error message.
    """
    try:
        with open(filename, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return f"Error reading {filename}: {e}"

    if offset > 0 or limit > 0:
        lines = text.split("\n")
        lo = max(0, offset - 1) if offset > 0 else 0
        hi = lo + limit if limit > 0 else len(lines)
        return "\n".join(lines[lo:hi])
    return text


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
            # sys.executable: run inside the SAME interpreter/venv as the agent,
            # so the script can import packages installed there.
            [sys.executable, filename],
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
    # Read prefill config from prompts.py constants
    if prefill is None:
        prefill = FAST_AGENT_PREFILL

    prefill_text = FAST_AGENT_PREFILL_TEXT

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
    max_steps: int = 8,
    max_observation_length: int = 2000,
    verbose: bool = True,
) -> str:
    """Deep ReAct: read -> rewrite whole file (```file:) -> run -> final.

    System prompt is built by ``build_deep_system_prompt()`` from
    ``prompts.py`` - the single source of truth for all prompt text.

    Uses a default SummaryCompressor with hardcoded defaults internally.

    Args:
        prompt: Task description.
        model: A ``ResponsesModel`` instance. Any object with the same
            ``generate()`` interface works (e.g. a stub in tests).
        max_steps: Maximum ReAct iterations (8-15 recommended).
        max_observation_length: Max characters for observation content (0 = no truncation).
        verbose: Print step-by-step progress to stderr.

    Returns:
        The final agent output after all iterations.
    """
    from .context import SummaryCompressor
    from .config import _DEFAULT_COMPRESSOR

    tools = [run_python_file, read_file]

    # Build the system prompt - all text comes from prompts.py
    system_prompt = build_deep_system_prompt(tools)

    return react_loop(
        system_prompt=system_prompt,
        task=prompt,
        model=model,
        tools=tools,
        max_steps=max_steps,
        max_observation_length=max_observation_length,
        verbose=verbose,
        compressor=_DEFAULT_COMPRESSOR,
    )
