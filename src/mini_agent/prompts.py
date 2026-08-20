"""System prompts — all prompt text lives here, not scattered across files.

Designed for small models (9B): short, direct, explicit stop conditions,
no planning phase, clear final_answer trigger. Editing is done ONLY by
rewriting the whole file with a ```file:path block (no hash anchors, no
surgical line edits — small models reliably rewrite whole files but botch
line-anchored edits).
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

# Fast-agent prefill config
FAST_AGENT_PREFILL = True
FAST_AGENT_PREFILL_TEXT = "```python\n"


# ---------------------------------------------------------------------------
# Error-code -> recovery prompt registry.
#
# Design rule: the SYSTEM PROMPT must stay short and generic. When the agent
# detects a specific failure mid-run, it injects a TARGETED recovery prompt as
# a user message — NOT by bloating the system prompt. Use
# `format_error(code, detail=...)` to fill placeholders.
# ---------------------------------------------------------------------------
E001_NO_CODE = "E001_NO_CODE"
E002_FINAL_NO_ACTION = "E002_FINAL_NO_ACTION"
E003_FINAL_AFTER_ERROR = "E003_FINAL_AFTER_ERROR"
E004_NO_WRITE = "E004_NO_WRITE"

ERROR_PROMPTS = {
    # E001: model produced no parseable ```python block
    "E001_NO_CODE": (
        "Error E001: No executable ```python``` block found. "
        "To read/run/verify, use ```python\n"
        "read_file('path')\nrun_python_file('path')\nfinal_answer('done')\n```."
    ),
    # E002: final_answer() called without any prior tool use
    "E002_FINAL_NO_ACTION": (
        "Error E002: You called final_answer() without doing any work. "
        "Use your tools first, observe the results, then call final_answer()."
    ),
    # E003: final_answer() called after an error in the previous step
    "E003_FINAL_AFTER_ERROR": (
        "Error E003: Your last action failed: {detail}. "
        "Fix the error before calling final_answer()."
    ),
    # E004: code ran but never wrote a file to disk
    "E004_NO_WRITE": (
        "Warning E004: Your code ran but did NOT save any file. Running or "
        "printing code does not fix anything on disk. Write the corrected file "
        "with a ```file:<path>``` block (raw content, no quotes), then "
        "run_python_file('<path>') to verify, then final_answer()."
    ),
}


def format_error(code: str, detail: str | None = None) -> str:
    """Return the recovery prompt for ``code``, filling ``{detail}``."""
    return ERROR_PROMPTS[code].format(detail=detail or "")


# ---------------------------------------------------------------------------
# Deep Agent aware system prompt
# ---------------------------------------------------------------------------

DEEP_AGENT_AWARE_SYSTEM = """## Deep Agent (code execution)

You have access to a Deep Agent that can execute code and operate on files on this machine.

### When to answer directly
Questions, explanations, and advice: answer directly. Do NOT output any special block.

### When to use the Deep Agent
When the user's request needs code execution, running scripts, reading/writing files, checking the system, fixing bugs, or any multi-step task, invoke the Deep Agent by outputting a fenced block whose fence language is exactly the word deep, containing ONLY a self-contained task description:

    ```deep
    One clear, self-contained task description. Include every detail the Deep Agent needs, since it cannot see this conversation.
    ```

### Rules
- The Deep Agent executes the task independently and its result is returned to the conversation after the block.
- The Deep Agent does NOT remember previous calls - your task description must be self-contained (mention file paths, earlier decisions, and goals from the conversation).
- Never answer "I cannot run code" or "I have no access to the system" - you CAN, via the deep block.
- Do NOT use this block for tasks a direct answer can satisfy.
"""


def build_deep_system_prompt(
    tools: list["Tool"],
) -> str:
    """Build the system prompt for the deep ReAct agent.

    Args:
        tools: List of available tools (read_file, run_python_file, etc.).

    Returns:
        A concise system prompt with workflow, rules, tools, and example.
    """
    tool_list = "\n".join(t.to_prompt() for t in tools)

    return textwrap.dedent(f"""\
        You are a coding agent with tools. Solve the task by reading files, rewriting them, and running them.

        ## Workflow
        1. Use a tool to inspect (read_file) and to run code (run_python_file).
        2. Observe the tool's output carefully.
        3. Take the next action, or call final_answer(x) when the task is complete.

        ## Rules (MUST follow)
        - ALL tool calls go in a ```python``` block (do NOT use tool names as fence types):
          ```python
          read_file('data/modify_python.py')
          run_python_file('data/modify_python.py')
          final_answer('fixed')
          ```
        - Write a WHOLE file with a ```file:<path>``` block (raw content, no quotes):
          ```file:data/modify_python.py
          def main():
              print('hello')
          if __name__ == '__main__':
              main()
          ```
        - Editing is done by REWRITING the whole file with ```file:path```. There is no
          line-editing syntax — always output the full corrected content.
        - Do NOT write files from inside a ```python``` block (no open(), no string assignment).
        - All ```file:``` blocks are written to disk FIRST; only the FIRST ```python``` block runs.
        - After each action, read the tool's output carefully before deciding the next step.
        - If a tool reports an error, fix it before calling final_answer().
        - Do NOT call final_answer() as your first action — do some work first.
        - final_answer(x) may be called together with other tool calls in the same ```python``` block.
        - Tool output is printed automatically; do NOT add print() just to inspect.

        ## Tools
        {tool_list}
    """)