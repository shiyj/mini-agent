"""Agent implementations — ReAct architecture with two depths.

Architecture:
  Both modes are ReAct (Reasoning + Acting) loops built on CodeAgent.
  The difference is loop depth and tool access:

  ┌─ Fast Agent (极速流) ─────────────────────────────┐
  │  max_steps=1, no tools, temperature=0.0            │
  │  Single reasoning pass → raw code. No acting.      │
  │  Use: boilerplate, syntax conversion, one-shot gen │
  └────────────────────────────────────────────────────┘

  ┌─ Deep Agent (深思流) ──────────────────────────────┐
  │  max_steps=8-15, 3 tools, temperature=0.4-0.5      │
  │  Full loop: Reason → Act → Observe → Repeat        │
  │  Use: complex scripts, debugging, iterative tasks   │
  └────────────────────────────────────────────────────┘

Users create the model externally (via LiteLLMModel or get_local_model)
and pass it in — the agent code never touches model config.
"""

import subprocess
from smolagents import CodeAgent, LiteLLMModel, tool

from .config import load_prompts, build_prompt


# ---------------------------------------------------------------------------
# 1. Convenience model factory (optional — users can build models directly)
# ---------------------------------------------------------------------------

def get_local_model(
    model_name: str,
    api_base: str = "http://localhost:11434",
    temperature: float = 0.2,
    ctx_size: int = 8192,
) -> LiteLLMModel:
    """Create a LiteLLMModel pointed at a local Ollama instance.

    Args:
        model_name: Ollama model tag, e.g. "ornith:9b".
        api_base: Ollama server URL.  If it ends with /v1 the suffix
                  is stripped because litellm's ollama_chat provider
                  appends /api/chat on its own.
        temperature: Sampling temperature.
        ctx_size: Context window in tokens.

    Returns:
        A configured LiteLLMModel.
    """
    base = api_base
    if base.endswith("/v1"):
        base = base[:-3]
    return LiteLLMModel(
        model_id=f"ollama_chat/{model_name}",
        api_base=base,
        temperature=temperature,
        num_ctx=ctx_size,
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
# 3. Fast agent — Code-only ReAct (single pass, no tools)
# ---------------------------------------------------------------------------

def run_fast_agent(
    prompt: str,
    model: LiteLLMModel,
    max_steps: int = 1,
) -> str:
    """Fast-track ReAct: single reasoning pass, pure code output.

    Args:
        prompt: Task description.
        model: Any smolagents-compatible model (LiteLLMModel, TransformersModel, …).
        max_steps: Number of reasoning steps (keep at 1 for raw generation).

    Returns:
        The generated code / agent output.
    """
    agent = CodeAgent(
        tools=[],
        model=model,
        max_steps=max_steps,
        additional_authorized_imports=["math", "json", "os", "sys"],
    )
    full_prompt = build_prompt("fast_agent", prompt=prompt)
    return agent.run(full_prompt)


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
    model: LiteLLMModel,
    target_file: str = "output.py",
    max_steps: int = 8,
) -> str:
    """Deep ReAct: write → run → observe → fix → repeat.

    Before the task begins the agent receives an environment snapshot
    (cwd, files, installed packages) so it can make context-aware decisions.

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