---
name: iterative-tuning
description: >
  Run fix_python_error.py N rounds across 8 temperatures, record statistics
  to a timestamped test_suite JSONL, and analyze failed cases for improvement
  suggestions. Accepts a --rounds parameter to control rounds per temperature.
---

## What this skill does

Runs `examples/fix_python_error.py` across 8 temperature settings (0.1-0.8),
N rounds each (default 10), for a total of N x 8 tests. Each test's result is
appended immediately to a timestamped `test_suite_{timestamp}.jsonl` file, so
partial results are available even if the run is interrupted.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--rounds` | 10 | Rounds per temperature. Total tests = rounds x 8 |

## How to run

```bash
# Default: 10 rounds x 8 temperatures = 80 tests
uv run python3 .agents/skills/iterative-tuning/iterative_tune.py

# Quick test: 1 round x 8 temperatures = 8 tests
uv run python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 1

# Custom: 5 rounds x 8 temperatures = 40 tests
uv run python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 5
```

## Output files

All output goes to `examples/logs/`. **No files are written to the skills directory.**

| Path | Contents |
|------|----------|
| `examples/logs/test_suite_{timestamp}.jsonl` | Per-test records, one JSON per line |
| `examples/logs/fix_python_error_{timestamp}.jsonl` | Request/response log for each test |

Each run creates a new `test_suite_{timestamp}.jsonl` (timestamp = run start time).

## test_suite JSONL structure

Each line is a JSON record:

```json
{"timestamp": "2026-07-14_16-48-18", "round_id": 0, "temperature": 0.1, "success": false, "steps": 8, "duration": 12.5, "error": ""}
```

## Analysis workflow

After a run completes (or is interrupted):

1. Read the latest `test_suite_*.jsonl` in `examples/logs/`
2. Generate a multi-dimensional statistics table by temperature (success rate, avg duration, etc.)
3. For failed tests, find their corresponding `fix_python_error_*.jsonl` by timestamp
4. Analyze the request/response data to identify root causes
5. Suggest agent code or prompt improvements to increase success rate