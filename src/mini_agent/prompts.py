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
