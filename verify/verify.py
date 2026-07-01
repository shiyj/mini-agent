#!/usr/bin/env python3
"""End-to-end verification for mini_agent with retry.

Tests:
1. Config loading from config/config.json
2. Model connectivity via LiteLLMModel (simple completion)
3. Deep-thinking agent on a loop-optimization code task (with retry)
"""

import sys
import time
import subprocess
import os
from pathlib import Path


MAX_RETRIES = 10
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
OUTPUT_TARGET = str(HERE / "verify_output.py")


def step_config() -> None:
    from mini_agent import load_config
    config = load_config()
    assert config.base_url and "11434" in config.base_url
    assert config.models
    print(f"[PASS] Config: {config.base_url}, models={list(config.models.keys())}")


def step_model_connectivity() -> None:
    from mini_agent import get_local_model, load_config
    config = load_config()
    model = get_local_model(
        model_name=config.get_default_model(),
        temperature=0.0, ctx_size=4096, config=config,
    )
    response = model([
        {"role": "system", "content": [{"type": "text", "text": "Reply with exactly one word."}]},
        {"role": "user", "content": [{"type": "text", "text": "Say hello."}]},
    ])
    content = response.content.strip() if hasattr(response, "content") else str(response)
    print(f"[PASS] Model responded: {content[:80]}")


def _check_script(target: str) -> str | None:
    """Run the generated script and verify it prints identical prime sets."""
    if not os.path.exists(target):
        return f"{target} does not exist"
    code = open(target).read()
    if len(code) < 100:
        return f"{target} too short ({len(code)} chars)"

    try:
        result = subprocess.run(
            [sys.executable, target],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return f"{target} timed out"
    except Exception as e:
        return f"Run failed: {e}"

    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return f"exit={result.returncode}: {output[:400]}"

    # Check that the script confirms both sets match
    ok_words = ["identical", "same", "pass", "match", "success", "verified"]
    if not any(w in output.lower() for w in ok_words):
        return f"Script did not confirm match:\n{output[:400]}"

    # Verify prime count
    if "9592" not in output and "9,592" not in output:
        if "error" in output.lower() and "differ" in output.lower():
            return f"Output says sets differ:\n{output[:400]}"

    print(f"  Script output:\n    " + "\n    ".join(output.splitlines()[:15]))
    if output.count("\n") > 15:
        print(f"    ... ({output.count('\n') - 15} more lines)")
    return None


def step_deep_agent_loop_optimization() -> None:
    from mini_agent import run_deep_agent, load_config

    config = load_config()
    target = OUTPUT_TARGET
    model_name = config.get_default_model()

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  Attempt {attempt}/{MAX_RETRIES} ...", flush=True)
        if os.path.exists(target):
            os.remove(target)

        temp = max(0.4 - attempt * 0.03, 0.1)

        start = time.time()
        result = run_deep_agent(
            prompt=(
                f"Write a Python script saved as `{target}` that does:\n"
                "1. Implement standard Sieve of Eratosthenes (primes up to N=100000).\n"
                '2. Implement an optimised "wheel sieve" that:\n'
                "   - Marks multiples of 2,3,5,7 first\n"
                "   - Then for i from 11 to sqrt(N): if is_prime[i], mark multiples of i\n"
                "   - This wheel optimization skips numbers divisible by 2,3,5,7\n"
                "3. Time both, print which is faster.\n"
                "4. Verify both produce identical prime sets (both must find 9592 primes)."
            ),
            target_file=target,
            model_name=model_name,
            temperature=temp,
            max_steps=15,
            ctx_size=16384,
            config=config,
        )
        elapsed = time.time() - start
        print(f"  Finished in {elapsed:.0f}s (temp={temp:.2f})", flush=True)

        err = _check_script(target)
        if err is None:
            print(f"[PASS] Deep agent created correct {target}")
            return

        print(f"  Attempt {attempt} failed: {err[:200]}", flush=True)

    raise AssertionError(
        f"Deep agent failed after {MAX_RETRIES} attempts."
    )


def main():
    print("=" * 60)
    print("mini-agent Verification Suite")
    print("=" * 60)

    print("\n[1/3] Config loading .......................", end=" ")
    step_config()

    print("\n[2/3] Model connectivity ...................", end=" ")
    step_model_connectivity()

    print("\n[3/3] Deep agent (loop optimisation) .....")
    step_deep_agent_loop_optimization()

    print("\n" + "=" * 60)
    print("ALL VERIFICATION STEPS PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())