#!/usr/bin/env python3
"""Basic usage: create a model and run the fast agent."""

from mini_agent import get_local_model, run_fast_agent

# 1. Create a model — explicitly configure model + server
model = get_local_model(
    model_name="ornith:9b",
    api_base="http://172.20.104.158:11434",
    temperature=0.0,
)

# 2. Run the fast agent (single-pass code generation)
result = run_fast_agent(
    prompt="Write a Python one-liner that prints the first 20 Fibonacci numbers.",
    model=model,
)
print(result)
