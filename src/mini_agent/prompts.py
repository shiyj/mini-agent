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
# Use `format_error(code, detail=...)` to fill placeholders.
# ---------------------------------------------------------------------------
# Error-code string constants — use these instead of raw strings
E001_NO_CODE = "E001_NO_CODE"
E002_FINAL_NO_ACTION = "E002_FINAL_NO_ACTION"
E003_FINAL_AFTER_ERROR = "E003_FINAL_AFTER_ERROR"
E004_WRITE_READONLY = "E004_WRITE_READONLY"
E005_WRITE_VAR = "E005_WRITE_VAR"
E006_WRITE_GENERIC = "E006_WRITE_GENERIC"
E007_LOOP = "E007_LOOP"
E008_NO_PROGRESS = "E008_NO_PROGRESS"
E009_STALE_ANCHOR = "E009_STALE_ANCHOR"
E010_SYNTAX = "E010_SYNTAX"
E011_NO_PATH = "E011_NO_PATH"
E012_FINAL_WITH_TOOLS = "E012_FINAL_WITH_TOOLS"
E013_INVALID_PATH = "E013_INVALID_PATH"

ERROR_PROMPTS = {
    # E001: model produced no parseable ```python block
    "E001_NO_CODE": (
        "Error E001: No executable ```python``` block found. "
        "You MUST include a code block that runs. "
        "Call your tools, observe the results, then call final_answer() ALONE."
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
    # E004_WRITE_READONLY: code only read/printed the file, never wrote it
    "E004_WRITE_READONLY": (
        "Warning E004: You only read/printed the file — it is NOT fixed on disk. "
        "Overwrite it now with a ```file:{detail}``` block (raw content, no "
        "escaping) or an ```edit:{detail}``` block, then run_python_file() to "
        "verify, then final_answer()."
    ),
    # E005: corrected code was assigned to a variable but never written
    "E005_WRITE_VAR": (
        "Warning E005: You stored the corrected code in a variable but "
        "never saved it. Write it to disk NOW: use a ```file:{detail}``` block "
        "with the raw corrected content, or an ```edit:{detail}``` block to "
        "change just that line. Then run_python_file() and final_answer()."
    ),
    # E006: code ran but did not save anything (generic)
    "E006_WRITE_GENERIC": (
        "Warning E006: Your code ran but did NOT save changes to the file. Running "
        "or printing code without saving does NOT fix the file on disk. Write it "
        "with a ```file:{detail}``` block (raw content) or an "
        "```edit:{detail}``` block, then run_python_file() to test it, then "
        "call final_answer()."
    ),
    # E007: model emitted identical output 3+ times (stuck in a loop)
    "E007_LOOP": (
        "Warning E007: your last 3 responses were identical — you are stuck in a loop. "
        "Stop exploring or printing. Make a concrete change: use an "
        "```edit:{detail}``` block to fix the specific broken line(s), or a "
        "```file:{detail}``` block with the corrected content, then "
        "run_python_file() to verify, then call final_answer()."
    ),
    # E008: no write/verify/finalize progress for 6 consecutive steps
    "E008_NO_PROGRESS": (
        "Warning E008: Stuck — no progress toward fixing the file (no file write/run "
        "in 6 consecutive steps). Save the corrected content to disk using an "
        "```edit:{detail}``` block for targeted changes, or a "
        "```file:{detail}``` block for a full rewrite."
    ),
    # E009: hash-anchored edit failed because the file changed since read
    "E009_STALE_ANCHOR": (
        "Error E009: Hash-anchored edit failed — the file changed since your last "
        "read_file() (often because you edited several lines at once, which shifts "
        "later anchors). Call read_file('{detail}') to get fresh N#HHHH "
        "anchors, then retry with a SINGLE ```edit:{detail}``` block for one "
        "line, OR rewrite the whole file with a ```file:{detail}``` block."
    ),
    # E010: the model's OWN generated code failed to compile (SyntaxError),
    # almost always a quote/escape conflict from embedding a whole file
    # inside a Python string literal. Point it at the ```edit: / ```file: block.
    "E010_SYNTAX": (
        "Error E010 - what to DO and what NOT to do:\n"
        "DO:\n"
        "1) Write the file with a ```file:{detail}``` block ONLY. That block holds "
        "the ENTIRE corrected file, RAW - no surrounding quotes, no escaping, no triple-quotes.\n"
        "2) Your ```python``` block must contain ONLY these two lines and NOTHING else:\n"
        "```python\nrun_python_file('{detail}')\nfinal_answer('done')\n```\n"
        "3) For a single-line fix, use an ```edit:{detail}``` block instead of a full rewrite.\n"
        "DO NOT:\n"
        "- Do NOT put file content inside a ```python``` block (as a string, with open(), "
        "or wrapped in quotes).\n"
        "- Do NOT wrap code/text in quotes inside a ```python``` block.\n"
        "Minimal correct pattern - write the file, then verify:\n"
        "```file:{detail}\ndef load_data(source):\n    records = []\n```\n"
        "then ONLY:\n"
        "```python\nrun_python_file('{detail}')\nfinal_answer('done')\n```"
    ),
    # E011: file: or edit: block has no path - the block is useless without one
    "E011_NO_PATH": (
        "Error E011: A ```file:``` or ```edit:``` block was found WITHOUT a file path. "
        "You MUST specify the path in the fence header, like "
        "```file:<your_file_path>``` or ```edit:<your_file_path>```. "
        "A bare ```file``` or ```edit``` with no path does nothing. "
        "Rewrite your block with the correct path, then run_python_file() to verify."
    ),
    # E012: final_answer() called together with other tools in the same step
    "E012_FINAL_WITH_TOOLS": (
        "Error E012: You called final_answer() together with other tools. "
        "First run your tools and observe the results, then call final_answer() ALONE in the next step."
    ),
    # E013: file: or edit: block has a path that looks like a placeholder or is invalid
    "E013_INVALID_PATH": (
        "Error E013: A ```file:``` or ```edit:``` block has an invalid path. "
        "The path contained placeholder text (e.g. <your_file_path>), "
        "markdown artifacts, or is otherwise not a valid file path. "
        "You MUST specify a real file path like ```file:<your_file_path>``` "
        "or ```edit:<your_file_path>```. Fix the path and try again."
    ),
}


def format_error(code: str, detail: str | None = None) -> str:
    """Return the recovery prompt for ``code``, filling ``{detail}``."""
    return ERROR_PROMPTS[code].format(detail=detail or "")


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
        You are a helpful AI assistant with tools. Solve the task by taking actions and observing their results.

        ## Workflow
        1. Use a tool to take an action (e.g., read_file to read a file, run_python_file to run code, execute_bash_command for shell commands).
        2. Observe the tool's output carefully.
        3. Based on the observation, take the next action OR call final_answer(x) if the task is complete.

        ## Rules (MUST follow)
        - Code goes in fenced blocks: ```python to RUN code (which may call tools), ```file:<your_file_path> to write a whole file, ```edit:<your_file_path> to change specific lines.
        - Files are written ONLY via ```file:<your_file_path>``` / ```edit:<your_file_path>``` blocks. You MUST NOT write files from inside a ```python``` block (no open(), no string assignment of file content).
        - final_answer(x) MUST be called ALONE, in its own ```python``` block, with NO other tool calls.
          First run your tools and observe their output, then call final_answer() in a separate step.
        - All ```file:```/```edit:``` blocks are written to disk FIRST; then ONLY the FIRST ```python``` block runs.
        - After each action, read the tool's output carefully before deciding the next step.
        - If a tool reports an error, fix the error before calling final_answer().
        - Do NOT call final_answer() as your first action — you must do some work first.
        - Only the FIRST ```python``` block in a step is executed.
        - Tool output is printed automatically; do NOT add print() just to inspect.

        ## Fix lines (edit:)
        - read_file('<your_file_path>') shows `12#a3f4 print(x)`. Copy the N#HHHH anchor EXACTLY.
        - Single line:
          ```edit:<your_file_path>
          replace 12#a3f4 print(y)
          ```
        - **Multiple consecutive lines:** give `replace` a SECOND `end` anchor
          (the last line to change) and put the new lines right below it - this
          replaces the whole block in ONE edit (no `file:` rewrite needed):
          ```edit:<your_file_path>
          replace 12#a3f4 15#9abc
          def greet(name):
              return f"Hello, {{name}}"
          ```
        - Operations: `replace` (one line, or a `start..end` span), `insert_before`,
          `insert_after` / `append`, `prepend`, `delete`. One operation per line.
        - Prefer a targeted `edit:` (single line or `start..end` span) over a full
          ```file:``` rewrite whenever the change is local - it keeps anchors valid
          and avoids SyntaxError from rewriting the whole file.

        ## Fix the WHOLE file (file:)
        - ```file:<your_file_path>
          def main():
              records = [1, 2, 3]
              print(sum(records))
          if __name__ == "__main__":
              main()
          ```
        - Then verify in a ```python``` block:
          ```python
          run_python_file('<your_file_path>')
          final_answer("Fixed")
          ```

        ## Tools
        {tool_list}

        ## Common mistakes (do NOT)
        - Do NOT write files from a ```python``` block (open()/string assignment) -> SyntaxError or no effect. Use a ```file:```/```edit:``` block.
        - Do NOT put the whole file inside a Python string (e.g. content='...multi-line...') -> SyntaxError. Use a ```file:``` block with raw content.
        - Do NOT call final_answer() before run_python_file() succeeds.
        - Do NOT emit more than one ```python``` block expecting all to run - only the FIRST runs.
        - Do NOT put explanation text inside a code block.
    """)