"""System prompts — all prompt text lives here, not scattered across files.

Designed for small models (9B): short, direct, explicit stop conditions,
no planning phase, clear final_answer trigger.
"""

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tools import Tool


FAST_AGENT_SYSTEM = (
    "You are a fast code generator. "
    "Output only executable Python code. "
    "No explanation, no thinking blocks."
)


# ---------------------------------------------------------------------------
# Error-code -> recovery prompt registry.
#
# Design rule (see project discussion): the SYSTEM PROMPT must stay short and
# generic. When the agent detects a specific failure mid-run, it injects a
# TARGETED recovery prompt as a user message — NOT by bloating the system
# prompt. Each failure mode gets exactly ONE stable error code mapping to ONE
# prompt. `react_agent.py` references these codes so all wording lives here.
# Use `format_error(code, target_file=..., var=...)` to fill placeholders.
# ---------------------------------------------------------------------------
# Error-code string constants — use these instead of raw strings
E001_NO_CODE = "E001_NO_CODE"
E002_FINAL_NO_WRITE = "E002_FINAL_NO_WRITE"
E003_WRITE_READONLY = "E003_WRITE_READONLY"
E004_WRITE_VAR = "E004_WRITE_VAR"
E005_WRITE_GENERIC = "E005_WRITE_GENERIC"
E006_LOOP = "E006_LOOP"
E007_NO_PROGRESS = "E007_NO_PROGRESS"

ERROR_PROMPTS = {
    # E001: model produced no parseable ```python block
    "E001_NO_CODE": (
        "Error E001: No valid Python code block found. "
        "Please write your code in a ```python``` block and try again."
    ),
    # E002: called final_answer() but never persisted the fix with write_file()
    "E002_FINAL_NO_WRITE": (
        "Warning E002: you called final_answer() but never used write_file() to save "
        "the corrected code to disk (target: {target_file}). The file on disk is still "
        "buggy. Call write_file() with the fixed code first, then verify with "
        "run_python_file(), then call final_answer()."
    ),
    # E003: code only read/printed the file, never wrote it
    "E003_WRITE_READONLY": (
        "Warning E003: You only read/printed the file. You MUST use write_file() to "
        "overwrite it with the corrected code, then run_python_file() to verify, then "
        "final_answer()."
    ),
    # E004: corrected code was assigned to a variable but never written
    "E004_WRITE_VAR": (
        "Warning E004: You assigned the corrected code to the variable `{var}` but never "
        "saved it. Call write_file(filename='{target_file}', content={var}) right now to "
        "persist the fix, then run_python_file() to verify, then final_answer()."
    ),
    # E005: code ran but did not save anything (generic)
    "E005_WRITE_GENERIC": (
        "Warning E005: Your code ran but did NOT save changes to the file. Running or "
        "printing code without write_file() does NOT fix the file on disk. Use "
        "write_file() to save the corrected code, then run_python_file() to test it, then "
        "call final_answer()."
    ),
    # E006: model emitted identical output 3+ times (stuck in a loop)
    "E006_LOOP": (
        "Warning E006: your last 3 responses were identical — you are stuck in a loop. "
        "Stop exploring or printing. Use write_file() to overwrite the file with the "
        "corrected code, run_python_file() to verify, then call final_answer()."
    ),
    # E007: no write/verify/finalize progress for 6 consecutive steps
    "E007_NO_PROGRESS": (
        "Warning E007: Stuck — no progress toward fixing the file (no write_file/run in "
        "6 consecutive steps). You must call write_file() with the corrected content to "
        "actually fix the file on disk."
    ),
}


def format_error(code: str, target_file: str | None = None, var: str | None = None) -> str:
    """Return the recovery prompt for ``code``, filling ``{target_file}``/``{var}``."""
    return ERROR_PROMPTS[code].format(target_file=target_file or "", var=var or "")



def build_deep_system_prompt(
    tools: list["Tool"],
    target_file: str = "output.py",
) -> str:
    """Build the system prompt for the deep ReAct agent.

    Args:
        tools: List of available tools (write_file, run_python_file, etc.).
        target_file: The file the agent should save scripts to.

    Returns:
        A concise system prompt with workflow, rules, tools, and example.
    """
    tool_list = "\n".join(t.to_prompt() for t in tools)

    return textwrap.dedent(f"""\
        You are a Python programmer. Solve tasks by writing and running code.

        ## Workflow
        1. Use write_file() to save your script to {target_file}
        2. Use run_python_file() to run it
        3. If there are errors, fix and write_file() again, then run again
        4. When the script works, call final_answer(result) IMMEDIATELY

        ## Rules
        - Write Python code in ```python``` blocks
        - ALWAYS use write_file() to save scripts
        - Use read_file() to inspect existing files — its output is NEVER truncated, so you see the whole file
        - Tool results are automatically printed — no need to call print() yourself
        - When code runs successfully, call final_answer() right away
        - Do NOT re-run the same code multiple times

        ## Tools
        {tool_list}

        ## Example
        Task: "Write a script to {target_file} that prints the sum of 1 to 100"
        ```python
        code = "print(sum(range(1, 101)))"
        write_file(filename="{target_file}", content=code)
        run_python_file(filename="{target_file}")
        final_answer("done")
        ```

        ## Important
        - ALWAYS use write_file() to save your script first
        - ALWAYS use run_python_file() to execute the saved script
        - ALWAYS call final_answer() when your code works
        - Never repeat working code
    """)
