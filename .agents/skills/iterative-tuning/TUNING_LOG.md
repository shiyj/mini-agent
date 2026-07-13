# Tuning Log — fix_python_error (ornith:9b)

## 2026-07-10: 100-round baseline (segments 1–10, 10 each)

**Run:** `iterative_tune.py --rounds 100 --segment-size 10`
**Model:** ornith:9b (Q4_K_M, ~5.6GB) @ localhost:11434
**Task:** fix `modify_python.py.example` (3 planted bugs: `avg=0` overwrite,
`recods` typo, `above` never initialized)

### Baseline results

| Metric | Value |
|--------|-------|
| Success (bug fixed) | **88 / 100 (88.0%)** |
| Exceptions | 1 (ReadTimeout, round 93) |
| Avg steps | 5.4 |
| Avg duration | 55.7s |
| Max duration | **2093s (round 93, ReadTimeout)** |

### Temperature vs success (aggregate over 100 rounds)

| Band | Rate |
|------|------|
| 0.0–0.2 | 22/23 (96%) |
| 0.2–0.4 | 25/27 (93%) |
| 0.4–0.6 | 20/26 (77%) |
| 0.6–0.8 | 21/24 (88%) |

→ Low temp (0.0–0.4) is clearly best. The two worst failures live in the
high band: round 41 (T=0.79) gave up in 1 step (`final_answer("Failed")`),
round 93 (T=0.66) hung 2093s on a ReadTimeout.

### Error-code frequency (per segment, every segment showed E001)

- **E001** dominates every segment (70–100% of rounds). The model *understands*
  all 3 bugs perfectly (stated in reasoning every time) but struggles to *apply*
  the fix.
- **E006** (loop) 30–60% of segments.
- **E009** (stale hash-anchor edit) 20–30%.
- **E008** (SyntaxError from embedding whole file in a string) occasional.

### Key findings

**F1 — E001 false positive on `file:` blocks (most impactful bug).**
In `react_agent.py`, a `file:` block write sets `wrote_this_step=True` but does
NOT populate `edit_reports`. At lines 346–358, when there is no executable
(`python`/`bash`) block, an empty `edit_reports` falls through to `E001_NO_CODE`
instead of being treated like an edit step. So a model that writes the file
correctly via `file:` but omits a `python` verify block gets a spurious E001 and
wastes a step / loops (E006). Successful rounds (e.g. 51, 74) used `edit:` +
`python` blocks; failing rounds leaned on lone `file:` blocks.

**F2 — `file:` full-rewrite often still buggy.** When the model dumps the whole
file it frequently forgets one of the 3 fixes (keeps `recods` or `avg=0`),
so the rewrite is written but the bug persists. Targeted `edit:` blocks are far
more reliable. E001/E008 recovery prompts currently *push* the model toward
`file:` blocks — contradicting F2.

**F3 — No hard request timeout.** One round (93) hung 2093s before a
ReadTimeout. `model.py` sets `httpx.Timeout(180.0)` but the effective hang was
~35min. A per-request cap (~120s) and/or per-round wall-clock guard is needed.

**F4 — High-temp degeneracy.** T≥0.6 produces immediate give-ups (round 41)
and timeouts (round 93). The harness samples `random.uniform(0.0, 0.8)`.

### Proposed changes (one at a time, then re-tune)

1. **F1 fix** (`react_agent.py` ~line 346): after a `file:` write with no
   executable block, surface an observation ("File written to <path>. Call
   run_python_file(...) to verify, then final_answer().") instead of E001.
   Expected: big drop in E001/E006, fewer steps.
2. **F2/F8 prompts** (`prompts.py`): steer toward targeted `edit:` blocks for
   specific line changes; reserve `file:` for full rewrites only when needed.
   Soften E001/E008 wording that over-recommends `file:`.
3. **F3** (`model.py`): lower request timeout to ~120s; add per-round timeout
   guard in `iterative_tune.run_one`.
4. **F4** (`iterative_tune.py`): narrow temperature sampling to 0.0–0.5
   (`random.uniform(0.0, 0.5)`) for stability.

---

## 2026-07-10: Implemented F1 + F2 (F3 intentionally NOT done — user said no timeout changes)

**F1 — `react_agent.py`**
- `write_blocks` now returns a third value `file_reports` (list of "Written <path>"
  messages for `file:` blocks), alongside `wrote_any` / `edit_reports`.
- Loop unpacks `(wrote_this_step, edit_reports, file_reports)`.
- In the `exec_code is None` branch, a non-empty `file_reports` now surfaces a
  "File written. Call run_python_file(...) to verify, then final_answer(...)"
  observation instead of falling through to the E001 nudge.
- Unit-tested: a lone `file:` block populates `file_reports`, writes the target,
  and routes to the observation branch (no spurious E001).

**F2 — `prompts.py`**
- `E001_NO_CODE`: now prefers targeted `edit:{target_file}` for specific line
  fixes and only recommends `file:{target_file}` (with a run_python_file check)
  when many changes are needed.
- `E008_SYNTAX`: leads with a targeted `edit:` fix; `file:` full rewrite is now
  the fallback, not the primary instruction.
- `E006_LOOP` / `E007_NO_PROGRESS`: mention `edit:` as a lighter alternative to
  `file:` blocks.

**Verification:** `import` OK; 2-round smoke run OK; F1 branch unit-tested OK.
Full re-tune (100 rounds) pending to measure E001/E006 reduction + success lift.

---

## 2026-07-10: Re-tune after F1+F2 (100 rounds, varied temp 0.0–0.8)

Same harness, same temperature sampling (`random.uniform(0.0, 0.8)`) — only
`react_agent.py` + `prompts.py` changed. Ran ~10–15 min (vs ~50–90 min baseline).

### Results vs baseline

| Metric | Baseline | After F1+F2 | Δ |
|--------|----------|-------------|---|
| Success (bug fixed) | 88 / 100 (88%) | 89 / 100 (89%) | +1pp |
| Avg steps | 5.4 | 5.1 | −0.3 |
| Avg duration | 55.7s | 27.4s | −51% |
| Max duration | 2093s | 91s | −96% |
| Exceptions | 1 (ReadTimeout) | 0 | −1 |
| **E001 (summed)** | **~84** | **31** | **−63%** |
| **E006 (summed)** | **~33** | **9** | **−73%** |
| E008 (summed) | ~15 | 7 | −53% |
| E009 (summed) | ~25 | 16 | −36% |

Per-segment success (new): 9,9,7,8,9,9,8,10,10,10 — stronger finish (last 3 = 9–10).

Temperature buckets (new): 0.0–0.2 25/26 (96%), 0.2–0.4 22/25 (88%),
0.4–0.6 21/21 (**100%**, vs 77% baseline), 0.6–0.8 21/28 (75%).

### Interpretation
- Absolute success +1pp (ceiling already high for this easy task), but the
  **behavior is much healthier**: E001 false-positives on `file:` writes dropped
  63%, E006 loops −73%, and average wall-clock time halved with no 35-min hang.
- The 0.4–0.6 band went from the worst (77%) to perfect (100%) — F1's "verify
  after write" observation helps the model actually confirm the fix instead of
  spinning.
- High band (0.6–0.8) is still the weakest (75%) but no catastrophic give-up /
  timeout this run (F3/F4 not applied yet).

---

## 2026-07-10: Re-tune after prompt rewrite + wrote_file logic fix (100 rounds, varied temp)

Scope: standardize on `file:`/`edit:` blocks and remove the dedicated write tool
entirely (code, tool, docs); sync logic — `wrote_file` now also
flips True when a `file:`/`edit:` block writes the target (react_agent.py
`write_blocks` now returns `wrote_target`). Also: edit-first system prompt,
read→edit sequencing, multi-line-edit caveat, paired edit:/file: examples,
"Common mistakes" contrast block; E001/E008/E009 tightened.

### Results vs previous baseline (F1+F2 only)

| Metric | F1+F2 base | After prompt rewrite | Δ |
|--------|-----------|----------------------|---|
| Success | 89 / 100 (89%) | **92 / 100 (92%)** | +3pp |
| Avg steps | 5.1 | **3.3** | −35% |
| Avg duration | 27.4s | 23.1s | −16% |
| Max duration | 91s | 103s | ~same |
| Exceptions | 0 | 0 | — |
| **E001 (sum)** | 31 | **7** | **−77%** |
| **E006 (sum)** | 9 | **1** | **−89%** |
| **E009 (sum)** | 16 | **2** | **−88%** |
| E008 (sum) | 7 | 15 | +8 (regression, see note) |

Temp bands: 0.0–0.2 25/25 (100%), 0.2–0.4 13/15 (87%), 0.4–0.6 31/32 (**97%**),
0.6–0.8 23/28 (82%). Per-seg: 9,10,8,10,8,10,10,8,10,9.

### Interpretation
- E001/E006/E009 collapsed — the `wrote_file` fix + edit-first prompting removed
  the false E002 on block writes and the multi-line-edit stale-anchor loops.
- Steps dropped to 3.3 (model now fixes in ~1 write + 1 verify step).
- **Trade-off:** E008 (SyntaxError) rose 7→15. Hypothesis: the E008 "WRONG"
   contrast example containing a triple-quoted string assignment may prime the
   9B model to emit that exact pattern. It is recoverable (retries), contributing
   to only 5 of the 8 failures. Candidate follow-up: drop the E008 WRONG snippet
   or replace with a non-code negative statement.

---

## 2026-07-13: E008_SYNTAX prompt rewrite (single-parameter change)

**Change:** `prompts.py::ERROR_PROMPTS["E008_SYNTAX"]` only (one parameter at a time).
**Context:** In the 2026-07-13 100-round run (88% success), E008 was the dominant
error in failed rounds — the 9B model kept wrapping whole-file content in a
```python``` string / `open()`, causing SyntaxError. The old E008 prompt said
"use a ```file:``` block" but was generic; the model was already emitting
```file:``` blocks yet still embedded content in python strings.

**Old:** "Error E008: your code had a SyntaxError and did NOT run. You MUST write
files ONLY with a ```file:``` or ```edit:``` block — never by putting file content
inside a Python string or calling open() from a ```python``` block. Put the ENTIRE
corrected file in a ```file:``` block (raw, no quotes, no escaping), then in a
```python``` block call run_python_file() and final_answer()..."

**New:** Hard 3-rule format — (1) ```file:``` block holds ENTIRE raw file, no
quotes/escaping; (2) the ```python``` block MUST contain ONLY `run_python_file()`
+ `final_answer()` and nothing else; (3) never wrap code in quotes inside a
```python``` block. Includes a minimal correct ```file:```+```python``` example.

**Reason:** Make the recovery concrete and give a copy-paste-correct pattern
instead of an abstract instruction. Expect E008 frequency to drop and the
SyntaxError→recovery loop to converge faster.

**Verification pending:** 100-round re-tune (see INSPECTION_REPORT.md append).

### Re-tune results (100 rounds, varied temp 0.0–0.8, clean tuning_data)

| Metric | Before (88% run) | After E008 fix | Δ |
|--------|------------------|----------------|---|
| Success | 88 / 100 (88%) | **95 / 100 (95%)** | **+7pp** |
| Failed rounds | 12 | **5** | −7 (−58%) |
| E008 in failed rounds | 6 | **2** | −67% |
| E001 in failed rounds | 6 | 1 | −5 |
| E009 in failed rounds | 2 | 1 | −1 |
| E006 in failed rounds | 1 | 0 | −1 |
| E008 (round-level, transient) | 8 | 9 | ~flat |

Per-segment success (new): 10,10,9,10,10,10,10,9,10,7 — more stable than before
(8,9,10,7,10,8,8,9,9,10); no 70% dip except last-segment variance.

### Interpretation
- The concrete 3-rule E008 prompt reduced E008 in *failed* rounds (6→2) and
  halved total failures, lifting success 88%→95%. The 9B model responds better
  to a copy-paste-correct pattern than an abstract "use file: block" rule.
- Round-level E008 is flat (8→9) because that counts any transient trigger
  (self-healing); the failed-round signal is what moved.
- E001 in failed rounds also dropped 6→1 — likely reduced instruction confusion
  from the clearer E008; sample small, variance possible.
- Conclusion: change is a clear net win; keep it. Next candidate if pushing
  higher: address the residual E009 stale-anchor loops (multi-line edits) and
  the last-segment variance (high-temp give-ups).

---

## 2026-07-13: Narrow temperature sampling 0.0–0.8 → 0.0–0.5 (single-parameter)

**Change:** `iterative_tune.py` line 426 — `random.uniform(0.0, 0.8)` →
`random.uniform(0.0, 0.5)`. One parameter at a time (F4 from the 07-10 plan).
Prompt (`E008_SYNTAX` rewrite) and all `react_agent.py` logic unchanged — this
builds ON TOP of the 95% run, so the new run A/B-compares against 95%.

**Reason:** In the 95% run the only weak segment was S10 (70%) with high-temp
exploration failures (E008/E009 loops, give-ups). The earlier baseline analysis
showed the 0.6–0.8 band was consistently weakest. Narrowing the sample to
0.0–0.5 should remove the high-temp tail and lift the floor.

**Baseline to beat:** 95 / 100 (95%). Report backed up as
`INSPECTION_REPORT_beforeTempNarrow_*.md`. Verification: 100-round re-tune.

### Re-tune results (100 rounds, temp 0.0–0.5)

| Metric | Baseline (95%, 0–0.8) | After temp-narrow | Δ |
|--------|------------------------|-------------------|---|
| Success | 95 / 100 (95%) | **96 / 100 (96%)** | +1pp |
| Failed rounds | 5 | 4 | −1 |
| Failed-round E008 | 2 | 2 | ~flat |
| Failed-round E001 | 1 | 2 | +1 |
| Failed-round E009 | 1 | 1 | ~flat |

Per-segment (new): 10,10,9,10,8,10,9,10,10,10
Per-segment (base): 10,10,9,10,10,10,10,9,10,7

### Interpretation
- Narrowing temp removed the worst high-temp segment (base S10=7 → new S10=10),
  but a different segment dipped (new S5=8, S7=9). Net only +1pp.
- Conclusion: the 9B model + this task has effectively **hit a performance floor
  near 95–96%**. Failures are no longer dominated by one error code — they are
  spread across E008/E001/E009 with no single cause to attack. Run-to-run
  variance (~±2pp) now exceeds the gain from any single remaining tweak.
- Keep temp 0.0–0.5 (it eliminates the 70%-style tail risk at no cost). Further
  gains would need a different lever: harder task, bigger model, or a structural
  agent change (not prompt wording). Stop here unless a new failure mode appears.

---

## 2026-07-13: E008_SYNTAX wording — drop the error reason (DO / DON'T only)

**Change:** `prompts.py::ERROR_PROMPTS["E008_SYNTAX"]` only. Removed the diagnostic
cause sentence ("your code did NOT run — a SyntaxError. You put file content INSIDE
a ```python``` block...") and kept ONLY the DO / DO-NOT instructions + minimal
example. Temperature sampling REVERTED to full range 0.0–0.8 (user wants to measure
the whole temperature domain's effect), so this A/B-compares against the **95% run**
(full temp 0–0.8 + concrete-3-rule E008). The 96% run (temp 0–0.5) is a separate
axis and is NOT the baseline here.

**Reason (user request):** the 9B model should get actionable DO/DON'T steps
without a fault explanation that may prime the exact bad pattern (cf. the earlier
E008 "WRONG snippet" hypothesis). Test whether dropping the reason changes behavior
across the FULL temperature range.

**Baseline to beat:** 95 / 100 (95%, full temp). Report backed up as
`INSPECTION_REPORT_beforeTempNarrow_20260713_1434.md` (the 95% run = full temp +
old E008 wording). Verification: 100-round re-tune, full temp 0–0.8.

