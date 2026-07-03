"""Sandbox patch for smolagents' local Python executor.

This module patches smolagents' LocalPythonExecutor to:
1. Allow Python builtins (bytearray, bytes, etc.) by default with a blacklist
2. Provide folder protection for `open()` to restrict file access

Usage:
    import mini_agent.sandbox_patch  # Apply patches at module load
"""

import ast
import builtins
import os
import smolagents.local_python_executor as lpe


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Builtins that should NEVER be available (code execution, import bypass, etc.)
FORBIDDEN_BUILTINS: set[str] = {
    "eval", "exec", "compile", "__import__",
    "breakpoint", "help", "input",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr", "hasattr",
    "type", "property", "super",
}

# If set, open() will be allowed but restricted to this directory (resolved to absolute path)
# Set to None to disable folder protection (open() will be fully allowed)
OPEN_FOLDER_PROTECTION: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_path_allowed(path: str) -> bool:
    """Check if a file path is within the allowed directory."""
    if OPEN_FOLDER_PROTECTION is None:
        return True
    
    try:
        real_path = os.path.realpath(path)
        allowed = os.path.realpath(OPEN_FOLDER_PROTECTION)
        return real_path.startswith(allowed + os.sep) or real_path == allowed
    except (TypeError, ValueError):
        return False


def _safe_open(file, mode: str = "r", *args, **kwargs):
    """Safe wrapper around builtins.open with folder protection."""
    if not _is_path_allowed(str(file)):
        raise lpe.InterpreterError(
            f"Cannot open file outside allowed directory: {file}"
        )
    return builtins.open(file, mode, *args, **kwargs)


# Mapping of builtin names to their safe implementations
# Used when we want to override a builtin's behavior (e.g., open -> safe_open)
_SAFELY_ALLOWED_BUILTINS: dict[str, object] = {}


def _get_safe_builtin(name: str) -> object:
    """Get a builtin, potentially wrapped with safety checks."""
    if name == "open" and OPEN_FOLDER_PROTECTION is not None:
        return _safe_open
    return getattr(builtins, name)


# ---------------------------------------------------------------------------
# Monkey-patch functions
# ---------------------------------------------------------------------------

_original_evaluate_call = lpe.evaluate_call
_original_evaluate_name = lpe.evaluate_name


def _patched_evaluate_call(call, state, static_tools, custom_tools, authorized_imports):
    """Patched evaluate_call that allows builtins by default."""
    if isinstance(call.func, ast.Name):
        name = call.func.id
        # Fall through to builtins if not found in state/tools/ERRORS
        if (name not in state
            and name not in static_tools
            and name not in custom_tools
            and name not in lpe.ERRORS
            and name in dir(builtins)
            and name not in FORBIDDEN_BUILTINS):
            state[name] = _get_safe_builtin(name)
    return _original_evaluate_call(call, state, static_tools, custom_tools, authorized_imports)


def _patched_evaluate_name(name, state, static_tools, custom_tools, authorized_imports):
    """Patched evaluate_name that allows builtins by default."""
    if (name.id not in state
        and name.id not in static_tools
        and name.id not in custom_tools
        and name.id not in lpe.ERRORS
        and name.id in dir(builtins)
        and name.id not in FORBIDDEN_BUILTINS):
        return _get_safe_builtin(name.id)
    return _original_evaluate_name(name, state, static_tools, custom_tools, authorized_imports)


# ---------------------------------------------------------------------------
# Apply patches
# ---------------------------------------------------------------------------

lpe.evaluate_call = _patched_evaluate_call
lpe.evaluate_name = _patched_evaluate_name