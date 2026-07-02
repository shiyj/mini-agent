"""Agent implementations — two depths: fast (direct generation) and deep (ReAct loop).

Architecture:
  ┌─ Fast Agent (极速流) ───────────────────────────────┐
  │  Calls model.generate() directly, no agent scaffold. │
  │  Uses only system_instruction from prompts.json.     │
  │  Single pass, ~50 words system prompt.               │
  │  Use: boilerplate, syntax conversion, one-shot gen   │
  └──────────────────────────────────────────────────────┘

  ┌─ Deep Agent (深思流) ────────────────────────────────┐
  │  Full ReAct loop via CodeAgent with custom (concise) │
  │  prompt_templates that replace the default 174-line  │
  │  smolagents system prompt.                            │
  │  max_steps=8-15, 3 tools, temp=0.4-0.5.             │
  │  Use: complex scripts, debugging, iterative tasks    │
  └──────────────────────────────────────────────────────┘

Users create the model externally and pass it in.
"""

import subprocess
from smolagents import (
    CodeAgent, tool,
    PromptTemplates, PlanningPromptTemplate,
    ManagedAgentPromptTemplate, FinalAnswerPromptTemplate,
)

from .config import load_prompts
from .model import OllamaResponsesModel


# ---------------------------------------------------------------------------
# 1. Convenience model factory (optional — users can build models directly)
# ---------------------------------------------------------------------------

def get_local_model(
    model_name: str,
    api_base: str = "http://localhost:11434",
    temperature: float = 0.0,
    max_output_tokens: int = 512,
) -> OllamaResponsesModel:
    """Create a model that talks to Ollama's ``/v1/responses`` endpoint.

    No litellm dependency — uses ``httpx`` directly.  The Responses API
    handles thinking/content separation natively, and the model's output
    text is extracted from ``output_text`` items in the response.

    Args:
        model_name: Ollama model tag, e.g. ``"ornith:9b"``.
        api_base: Ollama server URL.
        temperature: Sampling temperature.
        max_output_tokens: Maximum tokens the model may generate.
            Prevents runaway thinking.

    Returns:
        A configured ``OllamaResponsesModel``.
    """
    return OllamaResponsesModel(
        model_name=model_name,
        api_base=api_base,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
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
# 3. Custom prompt templates for deep agent
#    (replaces smolagents default 174-line code_agent.yaml)
# ---------------------------------------------------------------------------

_DEEP_SYSTEM_PROMPT = """You are a coding agent.

1. Write code with write_file() — save to the target file.
2. Run it with run_python_file() — check the output.
3. If there are errors, fix them and repeat.
4. Once the script runs correctly, call final_answer().

Output code in ```python ... ``` blocks."""

_DEEP_FINAL_ANSWER = FinalAnswerPromptTemplate(
    pre_messages="The agent got stuck. Based on the conversation below, provide a final answer.",
    post_messages="Task: {task}\n\nProvide the best answer you can from the above.",
)

# Unused by our config (no planning_interval, no managed agents) — keep empty.
_DEEP_PROMPT_TEMPLATES = PromptTemplates(
    system_prompt=_DEEP_SYSTEM_PROMPT,
    planning=PlanningPromptTemplate(initial_plan="", update_plan_pre_messages="", update_plan_post_messages=""),
    managed_agent=ManagedAgentPromptTemplate(task="", report=""),
    final_answer=_DEEP_FINAL_ANSWER,
)


# ---------------------------------------------------------------------------
# 4. Fast agent — direct model call, no CodeAgent overhead
# ---------------------------------------------------------------------------

def run_fast_agent(
    prompt: str,
    model: OllamaResponsesModel,
    prefill: bool | None = None,
) -> str:
    """Single-pass code generation. No ReAct loop, no tool calling.

    Args:
        prompt: Task description.
        model: Any smolagents-compatible model.
        prefill: Whether to use assistant-prefill to bypass thinking mode.
            When True, appends ``{"role": "assistant", "content": "```python\\n"}``
            to the messages, forcing the model to continue from inside a code
            block.  Defaults to the ``prefill`` key in ``prompts.json``
            (``fast_agent`` section); falls back to True if unset.

    Returns:
        The raw model output (code / text).
    """
    prompts = load_prompts()
    sec = prompts.get("fast_agent", {})
    system_instruction = sec.get("system_instruction", "Output only code. No explanation.")

    if prefill is None:
        prefill = sec.get("prefill", True)

    prefill_text = sec.get("prefill_text", "```python\n")

    messages = [
        {"role": "system", "content": [{"type": "text", "text": system_instruction}]},
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

def _capture_env_snapshot() -> str:
    try:
        snapshot_parts = []
        cwd = subprocess.run(
            "pwd", shell=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        snapshot_parts.append(f"cwd: {cwd}")

        ls_out = subprocess.run(
            "ls -1 2>/dev/null | head -40",
            shell=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if ls_out:
            snapshot_parts.append(f"files:\n{ls_out}")

        pip_out = subprocess.run(
            "pip list --format=columns 2>/dev/null | head -25",
            shell=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if pip_out:
            snapshot_parts.append(f"packages:\n{pip_out}")

        return "\n".join(snapshot_parts)
    except Exception:
        return ""


def run_deep_agent(
    prompt: str,
    model: OllamaResponsesModel,
    target_file: str = "output.py",
    max_steps: int = 8,
) -> str:
    """Deep ReAct: write → run → observe → fix → repeat.

    Uses a custom system prompt (~6 lines) instead of smolagents' default
    174-line template.  Before the task begins the agent receives an
    environment snapshot (cwd, files, installed packages).

    Args:
        prompt: Task description.
        model: Any smolagents-compatible model.
        target_file: File the agent should write its script to.
        max_steps: Maximum ReAct iterations (8-15 recommended).

    Returns:
        The final agent output after all iterations.
    """
    agent = CodeAgent(
        tools=[write_file, run_python_file, execute_bash_command],
        model=model,
        max_steps=max_steps,
        additional_authorized_imports=[
            "os", "sys", "time", "subprocess",
        ],
        executor_kwargs={
            "additional_functions": {"open": open},
        },
        prompt_templates=_DEEP_PROMPT_TEMPLATES,
        code_block_tags="markdown",
    )

    prompts = load_prompts()
    section = prompts.get("deep_agent", {})
    template = section.get("user_template", "{prompt}")

    formatted = {}
    for key, value in section.items():
        if key == "user_template":
            continue
        formatted[key] = value.replace("{target_file}", target_file)

    context = dict(formatted)
    context["env_snapshot"] = _capture_env_snapshot()
    context["target_file"] = target_file
    context["prompt"] = prompt
    full_prompt = template.format(**context)
    return agent.run(full_prompt)