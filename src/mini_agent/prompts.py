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
        "You MUST include a code block that runs. Prefer a targeted fix: use an "
        "```edit:{target_file}``` block to change just the broken line, then in a "
        "SINGLE ```python``` block call run_python_file('{target_file}') AND "
        "final_answer(...) — both in that same ```python``` block. Only rewrite "
        "the whole file with a ```file:{target_file}``` block when many changes "
        "are needed, and always follow it with a run_python_file() check in a "
        "```python``` block."
    ),
    # E002: called final_answer() but never persisted the fix
    "E002_FINAL_NO_WRITE": (
        "Warning E002: you called final_answer() but the corrected file was never "
        "saved to disk (target: {target_file}). Write the fix NOW using a "
        "```file:{target_file}``` block (whole file) or an ```edit:{target_file}``` "
        "block (one line), then run_python_file() to verify, then call "
        "final_answer() again."
    ),
    # E003: code only read/printed the file, never wrote it
    "E003_WRITE_READONLY": (
        "Warning E003: You only read/printed the file — it is NOT fixed on disk. "
        "Overwrite it now with a ```file:{target_file}``` block (raw content, no "
        "escaping) or an ```edit:{target_file}``` block, then run_python_file() to "
        "verify, then final_answer()."
    ),
    # E004: corrected code was assigned to a variable but never written
    "E004_WRITE_VAR": (
        "Warning E004: You stored the corrected code in the variable `{var}` but "
        "never saved it. Write it to disk NOW: use a ```file:{target_file}``` block "
        "with the raw corrected content, or an ```edit:{target_file}``` block to "
        "change just that line. Then run_python_file() and final_answer()."
    ),
    # E005: code ran but did not save anything (generic)
    "E005_WRITE_GENERIC": (
        "Warning E005: Your code ran but did NOT save changes to the file. Running "
        "or printing code without saving does NOT fix the file on disk. Write it "
        "with a ```file:{target_file}``` block (raw content) or an "
        "```edit:{target_file}``` block, then run_python_file() to test it, then "
        "call final_answer()."
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
        "```file:{target_file}``` block for a full rewrite."
    ),
    # E009: hash-anchored edit failed because the file changed since read
    "E009_STALE_ANCHOR": (
        "Error E009: Hash-anchored edit failed — the file changed since your last "
        "read_file() (often because you edited several lines at once, which shifts "
        "later anchors). Call read_file('{target_file}') to get fresh N#HHHH "
        "anchors, then retry with a SINGLE ```edit:{target_file}``` block for one "
        "line, OR rewrite the whole file with a ```file:{target_file}``` block."
    ),
    # E008: the model's OWN generated code failed to compile (SyntaxError),
    # almost always a quote/escape conflict from embedding a whole file
    # inside a Python string literal. Point it at the ```edit: / ```file: block.
    "E008_SYNTAX": (
        "Error E008: your code had a SyntaxError and did NOT run. You MUST write files "
        "ONLY with a ```file:{target_file}``` or ```edit:{target_file}``` block — never by "
        "putting file content inside a Python string or calling open() from "
        "a ```python``` block. Put the ENTIRE corrected file in a ```file:{target_file}``` "
        "block (raw, no quotes, no escaping), then in a ```python``` block call "
        "run_python_file('{target_file}') and final_answer('...'). For a single-line "
        "fix, prefer an ```edit:{target_file}``` block."
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
        tools: List of available tools (read_file, run_python_file, etc.).
        target_file: The file the agent should save scripts to.

    Returns:
        A concise system prompt with workflow, rules, tools, and example.
    """
    tool_list = "\n".join(t.to_prompt() for t in tools)

    return textwrap.dedent(f"""\
        You are a Python programmer. Fix the task by writing and running code.

        ## Workflow
        1. Read the file with read_file('{target_file}') to see its content and the N#HHHH tags.
        2. Fix it, then verify and finish:
           - ONE line to change -> emit an ```edit:{target_file}``` block (copy the N#HHHH anchor).
           - MANY lines to change -> emit a ```file:{target_file}``` block with the ENTIRE corrected file.
        3. In a SINGLE ```python``` block: run_python_file('{target_file}') then final_answer("done").

        ## Rules (MUST follow)
        - Code goes in fenced blocks: ```python```/```bash``` to RUN, ```file:path``` to write a whole file, ```edit:path``` to change specific lines.
        - **Files are written ONLY via ```file:path``` / ```edit:path``` blocks. You MUST NOT write files from inside a ```python``` block (no open(), no string assignment of file content).**
        - All ```file:```/```edit:``` blocks are written to disk FIRST; then ONLY the FIRST ```python```/```bash``` block runs.
        - When the file runs without error, call final_answer() in the SAME ```python``` block — do NOT re-run the same code.
        - Tool output is printed automatically; do NOT add print() just to inspect.

        ## Fix ONE line (edit:)
        - read_file('path') shows `12#a3f4 print(x)`. Copy the N#HHHH anchor EXACTLY.
        - ```edit:path
          replace 12#a3f4 print(y)
          ```
        - Operations: replace, insert_before, insert_after, delete. One operation per line.
        - **Change only ONE line per ```edit:``` block.** Editing several lines at once invalidates later anchors (Error E009). For multiple fixes, rewrite the whole file with ```file:``` instead.

        ## Fix the WHOLE file (file:)
        - ```file:{target_file}
          def main():
              records = [1, 2, 3]
              print(sum(records))
          if __name__ == "__main__":
              main()
          ```
        - Then verify in a ```python``` block:
          ```python
          run_python_file('{target_file}')
          final_answer("Fixed")
          ```

        ## Tools
        {tool_list}

        ## Common mistakes (do NOT)
        - Do NOT write files from a ```python``` block (open()/string assignment) -> SyntaxError or no effect. Use a ```file:```/```edit:``` block.
        - Do NOT put the whole file inside a Python string (e.g. content='...multi-line...') -> SyntaxError. Use a ```file:``` block with raw content.
        - Do NOT call final_answer() before run_python_file() succeeds.
        - Do NOT emit more than one ```python``` block expecting all to run — only the FIRST runs.
        - Do NOT put explanation text inside a code block.
    """)