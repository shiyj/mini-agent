#!/usr/bin/env python3
"""Analyze a test_suite_*.jsonl produced by iterative_tune.py.

This tool ONLY reads the test_suite JSONL and produces a summary / queries the
raw request/response logs. It does NOT explain *why* a test failed — that is
the LLM's job, done by reading the listed ``fix_python_error_*.jsonl`` logs.

Usage:
    # First activate virtual environment
    source .venv/bin/activate

    # Then run
    python3 .agents/skills/iterative-tuning/analyze.py
    python3 .agents/skills/iterative-tuning/analyze.py --suite test_suite_2026-07-14_19-48-26.jsonl
    python3 .agents/skills/iterative-tuning/analyze.py --error E004
    python3 .agents/skills/iterative-tuning/analyze.py --failed
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
LOGS_DIR = BASE_DIR / "examples" / "logs"

# Error code descriptions (self-contained, no external dependency)
ERROR_DESCRIPTIONS: dict[str, str] = {
    "E001": "No executable ```python``` block found in model output",
    "E002": "final_answer() called without any prior tool use",
    "E003": "final_answer() called after an error in previous step",
    "E004": "Code ran but did NOT save any changes to the file",
}


def _latest_suite() -> Path | None:
    files = sorted(LOGS_DIR.glob("test_suite_*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _load(suite: Path) -> list[dict]:
    records = []
    for line in suite.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _print_summary(records: list[dict]) -> None:
    """Aggregate stats. Pure summary — no root-cause claims."""
    total = len(records)
    successes = sum(1 for r in records if r["success"])
    failures = total - successes

    agg: dict[str, int] = {}
    for r in records:
        for code, cnt in r.get("error_counts", {}).items():
            agg[code] = agg.get(code, 0) + cnt

    temp_stats: dict[float, dict] = {}
    for r in records:
        t = r["temperature"]
        if t not in temp_stats:
            temp_stats[t] = {"total": 0, "ok": 0, "duration_sum": 0.0, "steps": 0}
        temp_stats[t]["total"] += 1
        if r["success"]:
            temp_stats[t]["ok"] += 1
        temp_stats[t]["duration_sum"] += r["duration"]
        temp_stats[t]["steps"] += r.get("steps", 0)

    print(f"  Total tests:     {total}")
    print(f"  Passed:          {successes} ({successes/total*100:.1f}%)")
    print(f"  Failed:          {failures} ({failures/total*100:.1f}%)")
    print()
    print(f"  {'Temperature':>12}  {'Rate':>6}  {'Avg steps':>10}  {'Avg duration':>13}")
    print(f"  {'-'*12}  {'-'*6}  {'-'*10}  {'-'*13}")
    for temp in sorted(temp_stats):
        s = temp_stats[temp]
        rate = s["ok"] / s["total"] * 100 if s["total"] else 0
        avg_dur = s["duration_sum"] / s["total"] if s["total"] else 0
        avg_steps = s["steps"] / s["total"] if s["total"] else 0
        print(f"  {temp:>12}  {rate:>5.1f}%  {s['ok']:>3}/{s['total']:<3}  {avg_dur:>7.1f}s  ({avg_steps:.1f} steps)")
    print()

    if agg:
        print(f"  {'Error code':>14}  {'Total':>6}  {'Tests seen':>11}  {'% tests':>8}")
        print(f"  {'-'*14}  {'-'*6}  {'-'*11}  {'-'*8}")
        for code in sorted(agg.keys()):
            tests_with = sum(1 for r in records if code in r.get("error_counts", {}))
            print(f"  {code:>14}  {agg[code]:>6}  {tests_with:>5}/{total:<5}  {tests_with/total*100:>7.1f}%")
        print()


def _query_error(records: list[dict], code: str) -> None:
    """Locate the tests and raw logs that contain ``code``.

    The script only LOCATES the request/response logs; the LLM reads them to
    understand *why* the error happened.
    """
    code = code.upper()
    matched = [r for r in records if code in r.get("error_counts", {})]
    if not matched:
        print(f"  No tests recorded error code {code}.")
        return

    print(f"  Error {code}: {ERROR_DESCRIPTIONS.get(code, '')}")
    print(f"  Appeared in {len(matched)}/{len(records)} tests. Request/response logs:")
    print(f"  {'round_id':>8}  {'temp':>5}  {'ok':>3}  {'count':>5}  log_file")
    print(f"  {'-'*8}  {'-'*5}  {'-'*3}  {'-'*5}  {'-'*40}")
    logs: list[str] = []
    for r in matched:
        cnt = r["error_counts"].get(code, 0)
        lf = r.get("log_file", "")
        logs.append(lf)
        print(f"  {r['round_id']:>8}  {r['temperature']:>5}  "
              f"{'Y' if r['success'] else 'N':>3}  {cnt:>5}  {lf}")
    print()
    print(f"  Distinct log files to read for root-cause analysis ({len(set(logs))}):")
    for lf in sorted(set(logs)):
        if lf:
            print(f"    {lf}")


def _query_failed(records: list[dict]) -> None:
    failed = [r for r in records if not r["success"]]
    if not failed:
        print("  No failed tests.")
        return
    print(f"  {len(failed)} failed tests. Request/response logs:")
    print(f"  {'round_id':>8}  {'temp':>5}  {'steps':>5}  log_file")
    print(f"  {'-'*8}  {'-'*5}  {'-'*5}  {'-'*40}")
    for r in failed:
        print(f"  {r['round_id']:>8}  {r['temperature']:>5}  "
              f"{r.get('steps', 0):>5}  {r.get('log_file', '')}")
    print()
    print("  Read those logs (request entries with role=user contain the")
    print("  injected E-codes and the model's prior responses) to analyze why.")


def main():
    parser = argparse.ArgumentParser(description="Analyze a test_suite_*.jsonl (summary + query)")
    parser.add_argument("--suite", type=str, default=None,
                        help="Path to a specific test_suite_*.jsonl (default: latest)")
    parser.add_argument("--error", type=str, default=None,
                        help="List tests and raw logs that contain this E-code (e.g. E006)")
    parser.add_argument("--failed", action="store_true",
                        help="List failed tests and their raw logs")
    args = parser.parse_args()

    suite = Path(args.suite) if args.suite else _latest_suite()
    if suite and not suite.is_absolute():
        suite = LOGS_DIR / suite
    if suite is None or not suite.exists():
        print(f"No suite file found in {LOGS_DIR}", file=sys.stderr)
        sys.exit(1)

    records = _load(suite)
    print("=" * 78)
    print(f"  ANALYZE: {suite.name}  ({len(records)} records)")
    print("=" * 78)
    print()

    if args.error:
        _query_error(records, args.error)
    elif args.failed:
        _query_failed(records)
    else:
        _print_summary(records)
        print("  Query specifics with: --error E0XX | --failed")
        print("  Then read the listed log files to perform the actual root-cause analysis.")


if __name__ == "__main__":
    main()
