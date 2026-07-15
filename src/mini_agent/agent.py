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

from .config import load_agent_config
from .context import ContextCompressor, SummaryCompressor
from .model import ResponsesModel
from .prompts import FAST_AGENT_SYSTEM, FAST_AGENT_PREFILL, FAST_AGENT_PREFILL_TEXT, build_deep_system_prompt
from .hashline import tag_lines
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
    api_key: str | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = 4096,
    context_window: int = 65536,
    log_file: str | None = None,
    reasoning: dict | None = None,
) -> ResponsesModel:
    """Create a model that talks to a ``/v1/responses`` endpoint.

    Uses ``httpx`` directly.

    Args:
        model_name: Model tag, e.g. ``"ornith:9b"``.
        api_base: Server base URL.
        api_key: Optional API key for authenticated endpoints.
        temperature: Sampling temperature.
        max_output_tokens: Maximum tokens the model may generate.
        context_window: Model's maximum context window in tokens.
        log_file: Optional path to a JSONL file for request/response logging.

    Returns:
        A configured ``ResponsesModel``.
    """
    return ResponsesModel(
        model_name=model_name,
        api_base=api_base,
        api_key=api_key,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        context_window=context_window,
        log_file=log_file,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# 2. Agent tools
# ---------------------------------------------------------------------------

@tool
def read_file(filename: str, raw: bool = False, offset: int = 0, limit: int = 0) -> str:
    """Read a file and return its content with hash-anchored line tags.

    Each line is prefixed with ``LINE#HASH `` so you can reference specific
    lines in an `````edit:path``` block. Set ``raw=True`` to get plain content
    without tags. Use ``offset`` (1-indexed) and ``limit`` to read a range.

    Args:
        filename: Path to the file to read.
        raw: If True, return plain content without line tags.
        offset: Starting line number (1-indexed, 0 = start of file).
        limit: Max lines to return (0 = all lines).

    Returns:
        The file content (tagged by default), or an error message.
    """
    try:
        with open(filename, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        return f"Error reading {filename}: {e}"

    if raw:
        if offset > 0 or limit > 0:
            lines = text.split("\n")
            lo = max(0, offset - 1) if offset > 0 else 0
            hi = lo + limit if limit > 0 else len(lines)
            return "\n".join(lines[lo:hi])
        return text

    tagged = tag_lines(text)
    if offset > 0 or limit > 0:
        lines = tagged.split("\n")
        lo = max(0, offset - 1) if offset > 0 else 0
        hi = lo + limit if limit > 0 else len(lines)
        return "\n".join(lines[lo:hi])
    return tagged


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
    compressor: ContextCompressor | None = None,
) -> str:
    """Deep ReAct: write -> run -> observe -> fix -> repeat.

    System prompt is built by ``build_deep_system_prompt()`` from
    ``prompts.py`` - the single source of truth for all prompt text.

    Args:
        prompt: Task description.
        model: A ``ResponsesModel`` instance.
        max_steps: Maximum ReAct iterations (8-15 recommended).
        max_observation_length: Max characters for observation content (0 = no truncation).
        compressor: Optional context compressor (e.g. SummaryCompressor).

    Returns:
        The final agent output after all iterations.
    """
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
        verbose=True,
        compressor=compressor,
    )


def run_deep_agent_with_config(
    prompt: str,
    model_name: str,
    provider_name: str | None = None,
    log_file: str | None = None,
    reasoning: dict | None = None,
) -> str:
    """One-shot deep agent: load config, create model, build compressor, run.

    This is the simplest entry point — just provide a model name.

    Args:
        prompt: Task description.
        model_name: Model identifier (e.g. ``"deepseek-chat"``, ``"gpt-4o"``).
        provider_name: Optional provider name to disambiguate models.
        log_file: Optional path to a JSONL log file.

    Returns:
        The final agent output after all iterations.
    """
    config = load_agent_config(model_name, provider_name=provider_name)

    model = get_local_model(
        model_name=model_name,
        api_base=config["baseURL"],
        api_key=config.get("apiKey") or None,
        max_output_tokens=config["max_output_tokens"],
        context_window=config["context_window"],
        log_file=log_file,
        reasoning=reasoning,
    )

    compressor_cfg = config.get("compressor", {})
    compressor = SummaryCompressor(
        max_messages=compressor_cfg.get("max_messages", 20),
        compress_batch=compressor_cfg.get("compress_batch", 5),
        keep_recent=compressor_cfg.get("keep_recent", 6),
        max_summary_tokens=compressor_cfg.get("max_summary_tokens", 512),
    )

    return run_deep_agent(
        prompt=prompt,
        model=model,
        max_steps=config.get("max_steps", 8),
        max_observation_length=config.get("max_observation_length", 2000),
        compressor=compressor,
    )
