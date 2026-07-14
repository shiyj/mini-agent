#!/usr/bin/env python3
"""Iterative tuning: run fix_python_error with 8 temperatures, N rounds each.

Each run produces a timestamped test_suite_{timestamp}.jsonl in examples/logs/.
Each line in that file is a JSON record for one test (timestamp, round_id,
temperature, success, duration). The request/response log for each test is
also in examples/logs/ as fix_python_error_{timestamp}.jsonl.

No files are written to the skills directory.

Usage:
    uv run python3 .agents/skills/iterative-tuning/iterative_tune.py
    uv run python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 5
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
EXAMPLES_DIR = BASE_DIR / "examples"
LOGS_DIR = EXAMPLES_DIR / "logs"

TEMPERATURES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
ROUNDS_PER_TEMP = 10


def run_one_test(round_id: int, temperature: float) -> dict:
    """Run fix_python_error.py once and return result info."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    cmd = [
        sys.executable,
        str(EXAMPLES_DIR / "fix_python_error.py"),
        "--temperature", str(temperature),
    ]

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(BASE_DIR),
        )
        duration = time.time() - start
        success = result.returncode == 0
        error_msg = result.stderr.strip()[-500:] if not success else ""

        # Parse step count from stdout (printed by fix_python_error.py as "STEPS: N")
        steps = 0
        for line in result.stdout.splitlines():
            if line.startswith("STEPS: "):
                try:
                    steps = int(line.split(": ", 1)[1])
                except ValueError:
                    pass
                break
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        success = False
        error_msg = "Timeout"
        steps = 0
    except Exception as e:
        duration = time.time() - start
        success = False
        error_msg = str(e)
        steps = 0

    return {
        "timestamp": timestamp,
        "round_id": round_id,
        "temperature": temperature,
        "success": success,
        "steps": steps,
        "duration": round(duration, 2),
        "error": error_msg,
    }


def main():
    parser = argparse.ArgumentParser(description="Iterative tuning for fix_python_error")
    parser.add_argument("--rounds", type=int, default=ROUNDS_PER_TEMP,
                        help=f"Rounds per temperature (default: {ROUNDS_PER_TEMP})")
    args = parser.parse_args()

    rounds_per_temp = args.rounds
    total = rounds_per_temp * len(TEMPERATURES)
    suite_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    suite_file = LOGS_DIR / f"test_suite_{suite_timestamp}.jsonl"

    LOGS_DIR.mkdir(exist_ok=True)

    print(f"Running {total} tests: {rounds_per_temp} rounds x {len(TEMPERATURES)} temperatures")
    print(f"Test suite log: {suite_file}")
    print()

    test_id = 0

    for temp_idx, temp in enumerate(TEMPERATURES):
        print(f"Temperature {temp_idx + 1}/{len(TEMPERATURES)}: T={temp}")

        for r in range(rounds_per_temp):
            round_id = temp_idx * rounds_per_temp + r
            test_id += 1

            print(f"  Test {test_id}/{total}: T={temp} ... ", end="", flush=True)
            result = run_one_test(round_id, temp)
            print("OK" if result["success"] else "FAIL")

            with open(suite_file, "a") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

        print()

    print("=" * 60)
    print(f"Done! {test_id} tests completed.")
    print(f"Test suite: {suite_file}")


if __name__ == "__main__":
    main()