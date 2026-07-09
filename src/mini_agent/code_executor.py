"""Code executor — replaces smolagents' 1768-line LocalPythonExecutor.

Uses exec() with injected globals for tool calling and final_answer detection.
No whitelist, no AST interpreter — just plain Python with full builtins.
The tools themselves (run_python_file, execute_bash_command) provide process isolation.
"""

from __future__ import annotations

import io
import sys
import traceback
from typing import Any

from .tools import Tool


class ExecutionResult:
    """Result of executing a code block."""

    def __init__(
        self,
        output: Any,
        logs: str,
        is_final_answer: bool,
        error: str | None = None,
        tool_calls: list[str] | None = None,
    ):
        self.output = output
        self.logs = logs
        self.is_final_answer = is_final_answer
        self.error = error
        self.tool_calls = tool_calls or []

    def __repr__(self) -> str:
        return f"ExecutionResult(output={self.output!r}, is_final_answer={self.is_final_answer}, error={self.error!r})"


def execute_code(
    code: str,
    tools: list[Tool],
    timeout: int = 30,
) -> ExecutionResult:
    """Execute Python code with injected tools and final_answer.

    The code runs in a restricted namespace:
    - Tool functions are available by name
    - final_answer(x) captures x and sets a flag
    - print() output is captured
    - Full Python builtins are available (no whitelist)

    Args:
        code: Python code to execute.
        tools: List of Tool objects to inject.
        timeout: Not used for exec(), but kept for API compatibility.

    Returns:
        ExecutionResult with output, logs, and final_answer status.
    """
    final_answer_value: list[Any] = [None]
    got_final_answer: list[bool] = [False]

    def final_answer(x: Any = None) -> Any:
        final_answer_value[0] = x
        got_final_answer[0] = True
        return x

    # Build execution environment — single dict for globals and locals
    # so that imports and function definitions share the same namespace
    exec_ns: dict[str, Any] = {"__name__": "__main__", "__builtins__": __builtins__}

    # Track which tools were invoked this execution (used to decide whether
    # the resulting observation may be exempt from truncation, e.g. read_file).
    called: list[str] = []

    # Inject tools into the execution namespace with auto-print
    # so tool return values are always visible in the logs,
    # regardless of whether the model remembers to print().
    for t in tools:
        def _make_wrapper(tool: Tool):
            def _wrapper(*args, **kwargs):
                called.append(tool.name)
                result = tool(*args, **kwargs)
                if result is not None:
                    print(result)
                return result
            return _wrapper
        exec_ns[t.name] = _make_wrapper(t)

    # Inject final_answer
    exec_ns["final_answer"] = final_answer

    # Capture stdout
    old_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured

    try:
        exec(compile(code, "<agent_code>", "exec"), exec_ns)
    except Exception as e:
        sys.stdout = old_stdout
        tb = traceback.format_exc()
        logs = captured.getvalue()
        return ExecutionResult(
            output=None,
            logs=logs,
            is_final_answer=got_final_answer[0],
            error=f"{type(e).__name__}: {e}\n{tb}",
            tool_calls=called,
        )
    finally:
        sys.stdout = old_stdout

    logs = captured.getvalue()

    # If final_answer was called, use its value
    if got_final_answer[0]:
        output = final_answer_value[0]
    else:
        # Try to get the last expression value (like REPL)
        output = exec_ns.get("_", None)

    return ExecutionResult(
        output=output,
        logs=logs,
        is_final_answer=got_final_answer[0],
        error=None,
        tool_calls=called,
    )