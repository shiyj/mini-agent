"""Core agent implementations.

Provides:
- get_local_model()      – configure LiteLLMModel for local Ollama
- write_file / run_python_file / execute_bash_command – agent tools
- run_fast_agent()       – fast code generation (no execution)
- run_deep_agent()       – self-correcting agent with execution feedback

Prompt templates are read from config/prompts.json, never hardcoded.
"""

import subprocess
from smolagents import CodeAgent, LiteLLMModel, tool

from .config import load_config, load_prompts, build_prompt, OllamaConfig


# ---------------------------------------------------------------------------
# 1. Model factory
# ---------------------------------------------------------------------------

def get_local_model(
    model_name: str | None = None,
    temperature: float = 0.2,
    ctx_size: int = 8192,
    config: OllamaConfig | None = None,
) -> LiteLLMModel:
    """Configure a LiteLLMModel that talks to a local Ollama instance."""
    if config is None:
        config = load_config()
    if model_name is None:
        model_name = config.get_default_model()

    # Use the ollama_chat/ prefix for litellm's built-in Ollama provider.
    # litellm appends /api/chat to the base URL, so strip /v1 if present.
    base_url = config.base_url
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]

    return LiteLLMModel(
        model_id=f"ollama_chat/{model_name}",
        api_base=base_url,
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
# 3. Fast-track agent  (pure code generation, no execution)
# ---------------------------------------------------------------------------

def run_fast_agent(
    prompt: str,
    model_name: str | None = None,
    temperature: float = 0.0,
    max_steps: int = 2,
    config: OllamaConfig | None = None,
) -> str:
    """Run the fast-track agent that generates code without executing it."""
    model = get_local_model(
        model_name=model_name, temperature=temperature,
        ctx_size=8192, config=config,
    )

    agent = CodeAgent(
        tools=[],
        model=model,
        max_steps=max_steps,
        additional_authorized_imports=["math", "json", "os", "sys"],
    )

    full_prompt = build_prompt("fast_agent", prompt=prompt)
    return agent.run(full_prompt)


# ---------------------------------------------------------------------------
# 4. Deep-thinking agent  (generate → execute → fix → repeat)
# ---------------------------------------------------------------------------

def run_deep_agent(
    prompt: str,
    target_file: str = "output.py",
    model_name: str | None = None,
    temperature: float = 0.5,
    max_steps: int = 8,
    ctx_size: int = 16384,
    config: OllamaConfig | None = None,
) -> str:
    """Run the deep-thinking agent that writes code, executes it, reads
    errors, and iteratively fixes them until the task is done."""
    model = get_local_model(
        model_name=model_name, temperature=temperature,
        ctx_size=ctx_size, config=config,
    )

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
    deep_cfg = prompts.get("deep_agent", {})
    template = deep_cfg.get("user_template", "{prompt}")

    # Pre-substitute {target_file} into sub-fields that contain it
    formatted = {}
    for key, value in deep_cfg.items():
        if key == "user_template":
            continue
        formatted[key] = value.replace("{target_file}", target_file)

    # Build final context with all placeholders
    context = dict(formatted)
    context["target_file"] = target_file
    context["prompt"] = prompt
    full_prompt = template.format(**context)
    return agent.run(full_prompt)