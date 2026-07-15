#!/usr/bin/env python3
"""Iterative tuning: run fix_python_error with 8 temperatures, N rounds each.

This tool ONLY runs the test suite. Each run appends one JSON record per test
to a timestamped ``test_suite_{timestamp}.jsonl`` in ``examples/logs/``.

Analysis is a separate tool: ``analyze.py`` (reads that JSONL and produces a
summary / queries the raw logs).

Usage:
    uv run python3 .agents/skills/iterative-tuning/iterative_tune.py
    uv run python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 5
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
EXAMPLES_DIR = BASE_DIR / "examples"
LOGS_DIR = EXAMPLES_DIR / "logs"

TEMPERATURES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
ROUNDS_PER_TEMP = 10

E_CODE_PATTERN = re.compile(r"E\d{3}")


def _parse_log_file(log_path: Path) -> dict[str, int]:
    """Read a fix_python_error request/response log and count E-codes.

    E-codes appear in user-message content within ``body.input`` of
    ``direction == "request"`` entries.
    """
    counts: dict[str, int] = {}
    if not log_path.exists():
        return counts
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("direction") != "request":
                continue
            inp = entry.get("body", {}).get("input")
            if not isinstance(inp, list):
                continue
            for msg in inp:
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if not isinstance(content, str):
                    continue
                for match in E_CODE_PATTERN.finditer(content):
                    code = match.group()
                    counts[code] = counts.get(code, 0) + 1
    return counts


def run_one_test(round_id: int, temperature: float) -> dict:
    """Run fix_python_error.py once and return result info."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    cmd = [
        sys.executable,
        str(EXAMPLES_DIR / "fix_python_error.py"),
        "--temperature", str(temperature),
    ]

    start = time.time()
    log_path: Path | None = None
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

        # Parse LOG_FILE path and step count from stdout
        steps = 0
        for line in result.stdout.splitlines():
            if line.startswith("LOG_FILE: "):
                log_path = Path(line.split(": ", 1)[1].strip())
            elif line.startswith("STEPS: "):
                try:
                    steps = int(line.split(": ", 1)[1])
                except ValueError:
                    pass
                break  # STEPS comes after LOG_FILE; we can break
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

    # Parse error codes from the log file
    error_counts: dict[str, int] = {}
    if log_path is not None:
        error_counts = _parse_log_file(log_path)

    return {
        "timestamp": timestamp,
        "round_id": round_id,
        "temperature": temperature,
        "success": success,
        "steps": steps,
        "duration": round(duration, 2),
        "error": error_msg,
        "error_counts": error_counts,
        "log_file": str(log_path) if log_path else "",
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
            rec = run_one_test(round_id, temp)
            print("OK" if rec["success"] else "FAIL")

            with open(suite_file, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        print()

    print("=" * 60)
    print(f"Done! {test_id} tests completed.")
    print(f"Test suite: {suite_file}")
    print(f"Analyze with: uv run python3 "
          f".agents/skills/iterative-tuning/analyze.py --suite {suite_file.name}")


if __name__ == "__main__":
    main()
