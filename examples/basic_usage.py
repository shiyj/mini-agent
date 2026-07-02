#!/usr/bin/env python3
"""Basic usage: create a model and run the fast agent with prefill enabled."""

from mini_agent import get_local_model, run_fast_agent

# 1. Create a model — explicitly configure model + server
model = get_local_model(
    model_name="ornith:9b",
    temperature=0.0,
)

# 2. Run the fast agent
#    prefill=True (default) appends an assistant message "```python\n"
#    so the model skips thinking and continues directly with code.
#    Set prefill=False to see the difference — the model may spend
#    50-90s in thinking mode before producing output.
result = run_fast_agent(
    prompt="Write a Python one-liner that prints the first 20 Fibonacci numbers.",
    model=model,
)
print(result)
