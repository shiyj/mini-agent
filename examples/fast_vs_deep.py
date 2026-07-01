#!/usr/bin/env python3
"""Compare fast agent vs deep agent on a simple coding task.

Run:  python examples/fast_vs_deep.py
"""

from mini_agent import get_local_model, run_fast_agent, run_deep_agent

# Shared model — deep uses a slightly higher temperature
fast_model = get_local_model("ornith:9b", temperature=0.0)
deep_model = get_local_model("ornith:9b", temperature=0.4)

TASK = (
    "Write a Python script named /tmp/check_primes.py that:\n"
    "1. Computes all primes up to 50000 using the Sieve of Eratosthenes.\n"
    "2. Prints the count of primes found.\n"
    "3. Prints the 100th prime.\n"
    "The script must be syntactically valid and runnable."
)

print("=" * 60)
print("Fast agent — single pass, no execution")
print("=" * 60)
fast_result = run_fast_agent(prompt=TASK, model=fast_model)
print(fast_result)

print("\n" + "=" * 60)
print("Deep agent — write, run, fix if needed")
print("=" * 60)
deep_result = run_deep_agent(
    prompt=TASK,
    model=deep_model,
    target_file="/tmp/check_primes.py",
    max_steps=8,
)
print(deep_result)
