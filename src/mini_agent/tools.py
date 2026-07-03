"""Tool abstraction — replaces smolagents.tools.Tool and @tool decorator.

Tools are plain Python callables with metadata for prompt generation.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable


class Tool:
    """A callable tool with name, description, and input schema.

    Attributes:
        name: Function name, e.g. "write_file".
        description: Docstring or explicit description.
        func: The underlying callable.
        inputs: Input parameter schema dict.
    """

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable,
        inputs: dict[str, dict[str, str]] | None = None,
    ):
        self.name = name
        self.description = description
        self.func = func
        self.inputs = inputs or {}

    def __call__(self, **kwargs) -> Any:
        return self.func(**kwargs)

    def to_prompt(self) -> str:
        """Generate a function signature string for the system prompt."""
        params = ", ".join(
            f"{name}: {info.get('type', 'str')}"
            for name, info in self.inputs.items()
        )
        desc = self.description.strip().split("\n")[0] if self.description else ""
        return f"- {self.name}({params}): {desc}"


def tool(func: Callable) -> Tool:
    """Decorator that wraps a function into a Tool.

    Extracts parameter names and type annotations from the function signature.
    The docstring becomes the tool description.
    """
    sig = inspect.signature(func)
    inputs: dict[str, dict[str, str]] = {}
    for name, param in sig.parameters.items():
        ann = param.annotation
        if ann is inspect.Parameter.empty:
            type_str = "str"
        elif hasattr(ann, "__name__"):
            type_str = ann.__name__
        else:
            type_str = str(ann)
        inputs[name] = {"type": type_str, "description": ""}

    doc = func.__doc__ or func.__name__
    # Take first paragraph of docstring as description
    desc = doc.strip().split("\n\n")[0]

    return Tool(name=func.__name__, description=desc, func=func, inputs=inputs)
