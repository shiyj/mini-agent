---
name: iterative-tuning
description: >
  Run fix_python_error.py N rounds across 8 temperatures, record statistics to a
  timestamped test_suite JSONL, and query/summarize the results. The script only
  summarizes and LOCATES the raw request/response logs; root-cause analysis of
  failures is done by the LLM reading those logs. Accepts --rounds.
---

## What this skill does

Runs `examples/fix_python_error.py` across 8 temperature settings (0.1-0.8),
N rounds each (default 10), for a total of N x 8 tests. Each test's result is
appended immediately to a timestamped `test_suite_{timestamp}.jsonl` file, so
partial results are available even if the run is interrupted.

## Tools

Two independent files with strictly separate responsibilities:

**`iterative_tune.py`** — ONLY runs the batch tests. It starts
`fix_python_error.py` across the 8 temperatures, appends one JSON record per
test to a timestamped `test_suite_{timestamp}.jsonl`, then exits. It has no
analyze capability.
```bash
uv run python3 .agents/skills/iterative-tuning/iterative_tune.py            # 80 tests
uv run python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 1 # 8 tests
uv run python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 5 # 40 tests
```

**`analyze.py`** — ONLY reads a `test_suite_*.jsonl` and produces a summary /
queries the raw logs. It does not run anything.
```bash
# Summary: totals, success rate by temperature, error-code frequency
uv run python3 .agents/skills/iterative-tuning/analyze.py
uv run python3 .agents/skills/iterative-tuning/analyze.py --suite test_suite_2026-07-14_19-48-26.jsonl

# QUERY: which tests/logs contain a given error code?
uv run python3 .agents/skills/iterative-tuning/analyze.py --error E006

# QUERY: list failed tests and their raw logs
uv run python3 .agents/skills/iterative-tuning/analyze.py --failed
```

`analyze.py` with no flags prints the aggregate summary. `--error E0XX` and
`--failed` print the matching `round_id`/`temperature` and, crucially, the path
to each test's `fix_python_error_*.jsonl` request/response log. Neither form
explains *why* a test failed — that is the LLM's job.

## Output files

All output goes to `examples/logs/`. **No files are written to the skills directory.**

| Path | Contents |
|------|----------|
| `examples/logs/test_suite_{timestamp}.jsonl` | Per-test records, one JSON per line |
| `examples/logs/fix_python_error_{timestamp}.jsonl` | Request/response log for each test |

Each run creates a new `test_suite_{timestamp}.jsonl` (timestamp = run start time).

## test_suite JSONL structure

```json
{
  "timestamp": "2026-07-14_16-48-18",
  "round_id": 0,
  "temperature": 0.1,
  "success": false,
  "steps": 8,
  "duration": 12.5,
  "error": "",
  "error_counts": {"E006": 3, "E009": 2},
  "log_file": "examples/logs/fix_python_error_2026-07-14_16-48-18.jsonl"
}
```

`error_counts` maps each E-code (E001–E012) seen during that test to how many
times it was injected. `log_file` points to that test's request/response log.

## The request/response log format

`fix_python_error_*.jsonl` is line-delimited JSON. Each entry has
`direction` (`request`/`response`), `model`, and `body`. For `request` entries,
`body.input` is the message list; `role == "user"` messages contain the
injected E-code error prompts (e.g. `"Error E006: ..."`) and `role == "assistant"`
messages contain the model's prior outputs. `body.instructions` is the system
prompt. This is the raw material the LLM reads to understand *why* a failure or
error code occurred.

## Analysis workflow (LLM-driven)

`analyze.py` only summarizes and locates. The actual analysis is done by the LLM:

1. `iterative_tune.py --rounds N` to generate data.
2. `analyze.py` to see the aggregate picture (which temperatures are weak,
   which E-codes dominate).
3. `analyze.py --error E006` (or the dominant code) to get the list of
   `fix_python_error_*.jsonl` log files that contain it.
4. **Read those log files** (request entries with `role == "user"` show the
   injected error + the model's previous responses). From the actual model
   outputs, determine *why* the error happened — e.g. the model ran inspection
   code but never wrote the file, or it repeatedly produced the same edit.
5. `analyze.py --failed` to enumerate failed tests, then read their logs to find
   the final error before give-up and the model's behavior.
6. Propose concrete fixes (prompt wording, code in `react_agent.py` /
   `prompts.py`, default temperature, etc.) based on what the logs show.
