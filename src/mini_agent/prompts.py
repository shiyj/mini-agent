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

# Fast-agent prefill config (moved from config/prompts.json)
FAST_AGENT_PREFILL = True
FAST_AGENT_PREFILL_TEXT = "```python\n"


# ---------------------------------------------------------------------------
# Error-code -> recovery prompt registry.
#
# Design rule: the SYSTEM PROMPT must stay short and generic.
# When the agent detects a specific failure mid-run, it injects a
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
E008_SYNTAX = "E008_SYNTAX"
E009_STALE_ANCHOR = "E009_STALE_ANCHOR"

ERROR_PROMPTS = {
    # E001: model produced no parseable ```python block
    "E001_NO_CODE": (
        "Error E001: No executable ```python```/```bash``` block found. "
        "You MUST include a code block that runs. Prefer small, targeted fixes: "
        "use an ```edit:{target_file}``` block to change just the broken line(s) "
        "(e.g. fix a typo), then a ```python``` block with "
        "run_python_file('{target_file}') to verify, then final_answer(...). "
        "Only rewrite the whole file with a ```file:{target_file}``` block when "
        "many changes are needed, and always follow it with a run_python_file() "
        "check."
    ),
    # E002: called final_answer() but never persisted the fix
    "E002_FINAL_NO_WRITE": (
        "Warning E002: you called final_answer() but never saved the corrected "
        "code to disk (target: {target_file}). Use a ```file:{target_file}``` block "
        "with the raw file content, or call write_file() with the fixed code, then "
        "run_python_file() to verify, then final_answer()."
    ),
    # E003: code only read/printed the file, never wrote it
    "E003_WRITE_READONLY": (
        "Warning E003: You only read/printed the file. You MUST overwrite it with "
        "the corrected code using a ```file:{target_file}``` block (or write_file()), "
        "then run_python_file() to verify, then final_answer()."
    ),
    # E004: corrected code was assigned to a variable but never written
    "E004_WRITE_VAR": (
        "Warning E004: You assigned the corrected code to the variable `{var}` but "
        "never saved it. Use a ```file:{target_file}``` block with the raw content, "
        "or call write_file(filename='{target_file}', content={var}) right now to "
        "persist the fix, then run_python_file() to verify, then final_answer()."
    ),
    # E005: code ran but did not save anything (generic)
    "E005_WRITE_GENERIC": (
        "Warning E005: Your code ran but did NOT save changes to the file. Running or "
        "printing code without saving does NOT fix the file on disk. Use a "
        "```file:{target_file}``` block with the raw content, or write_file() to save "
        "the corrected code, then run_python_file() to test it, then call final_answer()."
    ),
    # E006: model emitted identical output 3+ times (stuck in a loop)
    "E006_LOOP": (
        "Warning E006: your last 3 responses were identical — you are stuck in a loop. "
        "Stop exploring or printing. Make a concrete change: use an "
        "```edit:{target_file}``` block to fix the specific broken line(s), or a "
        "```file:{target_file}``` block with the corrected content, then "
        "run_python_file() to verify, then call final_answer()."
    ),
    # E007: no write/verify/finalize progress for 6 consecutive steps
    "E007_NO_PROGRESS": (
        "Warning E007: Stuck — no progress toward fixing the file (no file write/run "
        "in 6 consecutive steps). Save the corrected content to disk using an "
        "```edit:{target_file}``` block for targeted changes, or a "
        "```file:{target_file}``` block / write_file() for a full rewrite."
    ),
    # E009: hash-anchored edit failed because the file changed since read
    "E009_STALE_ANCHOR": (
        "Error E009: Hash-anchored edit failed — the file changed since your "
        "last read_file(). Call read_file('{target_file}') to get fresh "
        "anchors, then retry with an ```edit:{target_file}``` block."
    ),
    # E008: the model's OWN generated code failed to compile (SyntaxError),
    # almost always a quote/escape conflict from embedding a whole file
    # inside a Python string literal. Point it at the ```edit: / ```file: block.
    "E008_SYNTAX": (
        "Error E008: your code had a SyntaxError and did NOT run — almost certainly a "
        "quote/escape conflict from putting the whole file inside a string. "
        "STOP using content='...' for multi-line files. Prefer a targeted fix: "
        "use an ```edit:{target_file}``` block to change just the broken line(s). "
        "If a full rewrite is truly needed, put the ENTIRE corrected file content "
        "in a ```file:{target_file}``` block (raw, no quotes, no escaping), then in "
        "a ```python``` block call run_python_file('{target_file}') and "
        "final_answer('...')."
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
        1. Read the file with read_file() if needed
        2. Write the corrected file using a ```file:{target_file}``` block (raw content, no escaping)
           OR edit specific lines with an ```edit:path``` block (see below)
        3. Run it with run_python_file() in a ```python``` block
        4. When it works, call final_answer(result) in the SAME ```python``` block

        ## Rules
        - Write Python/bash code in ```python``` or ```bash``` blocks
        - **CRITICAL**: To write a file, use a ```file:path``` block with RAW content (no quotes, no escaping).
        - All ```file:```/```edit:``` blocks are written to disk FIRST, then your first ```python```/```bash``` block runs.
        - ONLY THE FIRST ```python```/```bash``` block is executed.
        - ```python:path```/```bash:path``` blocks are also saved to `path` (or /tmp/mini-agent-*.py if no path).
        - Tool results are automatically printed — no need to call print() yourself.
        - When code runs successfully, call final_answer() right away.
        - Do NOT re-run the same code multiple times.

        ## How to write a file
        - To CREATE or FIX a file: use a ```file:path/to/file.py``` block with the EXACT raw content.
        - To RUN code: use a ```python``` block (only the first one runs).
        - You can name it: ```python:path/to/run.py``` (also saves a copy there).
        - Example — fix a bug in {target_file}:
          ```file:{target_file}
          def main():
              records = [1, 2, 3]
              print(sum(records))
          if __name__ == "__main__":
              main()
          ```
          ```python
          run_python_file('{target_file}')
          final_answer("Fixed")
          ```

        ## Precise editing (hash-anchored)
        - read_file('path') returns lines tagged with ``LINE#HASH``, e.g. ``12#a3f4 print(x)``.
        - To edit specific lines precisely, use an ```edit:path``` block:
          ```edit:path/to/file.py
          replace 12#a3f4 print(y)
          insert_after 15#9abc new_line()
          delete 20#def0
          ```
        - Operations: replace, insert_before, insert_after, delete.
        - One edit per line; copy the ``N#HHHH`` anchor directly from read_file output.
        - After editing, use ```python``` with run_python_file() to verify.

        ## Tools
        {tool_list}

        ## Important
        - ALWAYS save the file first (```file:path```/```edit:path``` or write_file())
        - ALWAYS run the saved file with run_python_file() to verify
        - ALWAYS call final_answer() when your code works
        - Never repeat working code
    """)