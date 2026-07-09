# Tuning Log

Record every parameter change here during iterative tuning.

---

## 2026-07-09: Observation truncation fix (A + B)

**Problem:** `_truncate_observation` (react_agent.py:36) keeps head+tail and cuts
the MIDDLE. For code, bugs live in the middle, so models lost the part they
needed. No `read_file` tool existed, so any file read (via `cat`/`open().read()`)
passed through the same truncation. Default `max_observation_length=2000` made
files >2000 chars partially invisible.

**Change A:** `iterative_tune.py::run_one` → `run_deep_agent(..., max_observation_length=8000)`
**Old:** `max_observation_length` default 2000 (not passed)
**New:** explicitly 8000
**Reason:** raise the ceiling so small/mid-size files are never truncated.

**Change B:** added a `read_file(filename)` tool (agent.py) registered in the
deep-agent tool list, listed in the system prompt (prompts.py), and made its
output EXEMPT from truncation.
- `code_executor.py`: `ExecutionResult` now carries `tool_calls`; `execute_code`
  records each invoked tool name.
- `react_agent.py`: observation is truncated only when `"read_file" not in result.tool_calls`.
**Old:** no read_file tool; all observations truncated at max_observation_length.
**New:** `read_file()` returns the FULL file (middle included), never truncated.
**Reason:** give the model a first-class, truncation-free way to inspect a file.

**Verification:** unit test on a 60k-char file — `read_file` preserves the middle
marker and emits no `[truncated]`; `cat` (non-read_file) remains truncateable.
End-to-end run on a 4.8k-char buggy file with `max_observation_length=8000`
showed `any [truncated] in logs: False`.

