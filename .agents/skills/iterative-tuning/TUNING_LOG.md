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
