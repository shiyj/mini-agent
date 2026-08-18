#!/usr/bin/env python3
"""Compare fast agent vs deep agent on a simple coding task.

Run:  python examples/fast_vs_deep.py
"""

from mini_agent import ResponsesModel, load_model, run_fast_agent, run_deep_agent

# Fast agent: model straight from config (provider + model name, zero params)
fast_model, _ = load_model("ornith:9b", "ollama-local")

# Deep agent: manual ResponsesModel for a custom temperature - use this when
# you need full control instead of config defaults
deep_model = ResponsesModel(
    model_name="ornith:9b",
    api_base="http://127.0.0.1:11434",
    temperature=0.4,
)

TASK = (
    "Write a Python script named /tmp/check_primes.py that:\n"
    "1. Computes all primes up to 50000 using the Sieve of Eratosthenes.\n"
    "2. Prints the count of primes found.\n"
    "3. Prints the 100th prime.\n"
    "The script must be syntactically valid and runnable."
)

print("=" * 60)
print("Fast agent - single pass, no execution")
print("=" * 60)
fast_result = run_fast_agent(prompt=TASK, model=fast_model)
print(fast_result)

print("\n" + "=" * 60)
print("Deep agent - write, run, fix if needed")
print("=" * 60)
deep_result = run_deep_agent(
    prompt=TASK,
    model=deep_model,
    max_steps=8,
)
print(deep_result)
