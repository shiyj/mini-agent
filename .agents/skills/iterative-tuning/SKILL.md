---
name: iterative-tuning
description: >
  Run N rounds of fix_python_error, inspect model-agent conversations
  every X rounds, and tune parameters based on observed failure patterns.
  All introspection works from JSONL API logs — no project code changes.
---

## What this skill does

Provides `iterative_tune.py` (co-located in this directory) which:

1. Runs `fix_python_error` in segments (default 10 rounds per segment, 100 total)
2. After each segment, **pauses** and generates an inspection report
3. Reconstructs every step's full trace from `api.jsonl` logs — model output, parsed code, error code triggered (E001–E007), and observation — without modifying any project source
4. Waits for human review before proceeding

## When to use

- Agent success rate has degraded after a change
- You added a new error code (E008+) and want to verify its impact
- You're switching models (e.g., `ornith:9b` → `qwen3-coder`) and need to re-tune `temperature`, `max_steps`, or prompt wording
- A specific error code (e.g., E004 — variable‑assign‑but‑no‑write) dominates failures and prompt wording needs adjustment

## How to run

```bash
uv run python3 .agents/skills/iterative-tuning/iterative_tune.py

# Custom counts:
uv run python3 .agents/skills/iterative-tuning/iterative_tune.py --rounds 200 --segment-size 25
```

Output goes to:

| Path | Contents |
|------|----------|
| `.agents/skills/iterative-tuning/INSPECTION_REPORT.md` | Human‑readable report, appended per segment |
| `.agents/skills/iterative-tuning/tuning_data/segment_*.json` | Machine‑readable segment data for cumulative stats |

## Reading the inspection report

### Overview section

Shows success rate, average steps, cumulative trend across all segments so far.

Watch for: **success rate dropping** across segments (tool changes may have negative impact) or **climbing** (tuning is working).

### Failed-round step traces

A table per failed round, one row per step:

```
| Step | Error | Output (200) | Code (100) | Observation (150) |
|------|-------|--------------|------------|-------------------|
| 1    | -     | Let me read… | open(...)  | logs: contents…  |
| 2    | E005  | I will fix…  | CODE = """| logs: …         |
| 3    | E005  | Same code…   | same       | logs: …         |
| 4    | E006  | Same code…   | same       | logs: …         |
```

Each row shows what the model *said*, what code it *produced*, which error code *fired*, and the *observation* it received back.

### Error code frequency

```
| Code | Count | % of Rounds |
|------|-------|-------------|
| E005 | 12    | 60%         |
| E004 | 5     | 25%         |
| E001 | 3     | 15%         |
```

High E005 → model keeps running code without writing the file (````file:````/````edit:```` blocks). Consider tightening E005's prompt or increasing `max_steps`.

High E004 → model assigns corrected code to a variable (`FIXED = """…"""`) but never writes it to disk via a ````file:````/````edit:```` block. If the error message already names the variable but the model still ignores it, the recovery prompt may need restructuring.

High E006 → model is stuck in a loop (identical output repeatedly). May indicate the recovery prompt (E006) is ineffective, or `max_steps` is too high and the model burns steps.

### Temperature vs success rate

```
|  0.0-0.2 | ████████░░░░░░░░ 4/8 (50%) |
|  0.2-0.4 | ████████████████ 8/8 (100%) |
|  0.4-0.6 | ██████░░░░░░░░░░ 3/8 (38%) |
|  0.6-0.8 | ██░░░░░░░░░░░░░░ 1/8 (13%) |
```

If a temperature band has significantly better or worse performance, adjust the random range or fix the temperature.

> **Error code reference**: See [doc/prompts.md](../../doc/prompts.md) for the full list of error codes (E001–E008), their trigger conditions, and recovery prompt content.

## Recording modifications

Before continuing after a failing segment, adjust one parameter at a time and **log each change** in `TUNING_LOG.md` (co-located in this directory):

```markdown
## 2026-07-08: Segment 3 → Segment 4

**Change:** `prompts.py::ERROR_PROMPTS["E005_WRITE_GENERIC"]`
**Old:** "Warning E005: Your code ran but did NOT save changes to the file…"
**New:** "CRITICAL E005: You MUST write the file with a ````file:````/````edit:```` block before continuing. Running code without saving it does NOT fix the file on disk."
**Reason:** E005 triggered in 60% of failed rounds in segment 3 — model ignored the mild warning.
```

## Available tuning parameters

| Parameter | File | Typical range | Effect |
|-----------|------|---------------|--------|
| `temperature` | `iterative_tune.py` (co-located) | 0.0–0.8 | Higher → more diversity, more errors |
| `max_steps` | `iterative_tune.py` (co-located) | 6–15 | Higher → more chances, more token cost |
| `ERROR_PROMPTS` wording | `src/mini_agent/prompts.py` | — | More specific → better recovery |
| `consecutive_loops` threshold | `src/mini_agent/react_agent.py` | 2–5 | Lower → earlier loop detection |
| `no_progress` threshold | `src/mini_agent/react_agent.py` | 4–8 | Lower → earlier no‑progress stop |
| `max_observation_length` | `react_loop()` param | 1000–5000 | Lower → less noise |
