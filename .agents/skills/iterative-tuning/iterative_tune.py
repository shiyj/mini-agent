#!/usr/bin/env python3
"""Iterative tuning: run fix_python_error in segments, inspect, adjust.

No modifications to mini_agent source needed — reconstructs step-by-step
traces from the existing JSONL API logs.

Usage:
    uv run python3 .agents/skills/iterative-tuning/iterative_tune.py [--rounds 100] [--segment-size 10]
"""

import argparse
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from mini_agent import get_local_model, run_deep_agent
from mini_agent.model import ResponsesModel
from mini_agent.react_agent import parse_code


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
EXAMPLE_SRC = BASE_DIR / "examples" / "data" / "modify_python.py.example"
SKILL_DIR = Path(__file__).resolve().parent
REPORT_FILE = SKILL_DIR / "INSPECTION_REPORT.md"
TUNING_DATA = SKILL_DIR / "tuning_data"


# ---------------------------------------------------------------------------
# Prepare & check target
# ---------------------------------------------------------------------------

def prepare_target(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / "modify_python.py"
    example_text = EXAMPLE_SRC.read_text(encoding="utf-8")
    stripped = "\n".join(
        line if not line.strip().startswith("#") else ""
        for line in example_text.splitlines()
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", stripped).strip() + "\n"
    target.write_text(cleaned, encoding="utf-8")
    return target


def check_fixed(target: Path) -> bool:
    result = subprocess.run(
        [sys.executable, str(target)],
        capture_output=True, text=True, timeout=15,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Reconstruct step trace from JSONL log (no project code changes needed)
# ---------------------------------------------------------------------------

ERROR_RE = re.compile(r"\bE\d{3}\b")


def reconstruct_trace(log_path: Path) -> list[dict]:
    """Read api.jsonl and reconstruct step-by-step execution trace.

    Works purely from request/response pairs — no step_callback needed.
    """
    if not log_path.exists():
        return []

    entries: list[dict] = []
    with open(log_path) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    steps: list[dict] = []
    # entries are interleaved: request, response, request, response, ...
    for i in range(0, len(entries) - 1, 2):
        req = entries[i]
        resp = entries[i + 1]
        if req.get("direction") != "request" or resp.get("direction") != "response":
            continue

        body = req.get("body", {})
        input_items: list[dict] = body.get("input", []) or []
        response_data = resp.get("response", {})

        # Extract model output from response
        output_text = ResponsesModel._extract_output_text(response_data) or ""

        # Parse code from model output
        code = parse_code(output_text)

        # Detect error code from the last user message in this request
        # (the loop injects error codes as user messages between steps)
        error_code: str | None = None
        for msg in reversed(input_items):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                m = ERROR_RE.search(content)
                if m:
                    error_code = m.group(0)
                    break

        # Detect observation from last user message
        observation: str | None = None
        for msg in reversed(input_items):
            if msg.get("role") == "user" and "Observation:" in msg.get("content", ""):
                observation = msg["content"]
                break

        # Detect wrote_file from observation content
        wrote_file = observation is not None and "Written" in observation

        steps.append({
            "step": i // 2 + 1,
            "output": output_text,
            "code": code,
            "error_code": error_code,
            "observation": observation,
            "wrote_file": wrote_file,
        })

    return steps


# ---------------------------------------------------------------------------
# Run one round
# ---------------------------------------------------------------------------

def run_one(round_id: int, segment_dir: Path, temperature: float) -> dict:
    target = prepare_target(segment_dir / f"round_{round_id:04d}")
    log_file = segment_dir / f"round_{round_id:04d}" / "api.jsonl"

    model = get_local_model(
        "ornith:9b",
        temperature=temperature,
        log_file=str(log_file),
    )

    result: dict = {
        "round_id": round_id,
        "temperature": temperature,
        "success": False,
        "bug_fixed": False,
        "final_answer": False,
        "error": None,
        "duration": 0,
        "total_steps": 0,
        "trace": [],
    }

    start = time.time()
    try:
        agent_result = run_deep_agent(
            prompt="modify_python.py cannot run, find the problem and fix it.",
            model=model,
            target_file=str(target),
            max_steps=10,
        )
        result["success"] = True
        result["final_answer"] = agent_result is not None
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    result["duration"] = round(time.time() - start, 1)
    result["bug_fixed"] = check_fixed(target)

    # Reconstruct trace from JSONL log
    trace = reconstruct_trace(log_file)
    result["trace"] = trace
    result["total_steps"] = len(trace)

    err = result.get("error") or ""
    status = "OK" if result["bug_fixed"] else "FAIL"
    print(f"  Round {round_id:4d} T={temperature:.2f} {status} "
          f"steps={result['total_steps']} {err[:60]}")

    return result


# ---------------------------------------------------------------------------
# Analysis & reporting
# ---------------------------------------------------------------------------

def bar(count: int, total: int, width: int = 16) -> str:
    filled = round(count / max(total, 1) * width)
    return "█" * filled + "░" * (width - filled)


def analyze_segment(results: list[dict]) -> dict:
    total = len(results)
    bug_fixed = sum(1 for r in results if r["bug_fixed"])
    final_answer = sum(1 for r in results if r["final_answer"])
    exceptions = sum(1 for r in results if not r["success"])

    all_durations = [r["duration"] for r in results if r.get("duration", 0) > 0]
    all_steps = [r["total_steps"] for r in results if r.get("total_steps", 0) > 0]

    error_counts: dict[str, int] = {}
    for r in results:
        seen: set[str] = set()
        for t in r.get("trace", []):
            ec = t.get("error_code")
            if ec and ec not in seen:
                seen.add(ec)
                error_counts[ec] = error_counts.get(ec, 0) + 1

    failed_rounds = [r for r in results if not r["bug_fixed"]]

    failure_patterns: dict[str, int] = {}
    for r in failed_rounds:
        seen_fail: set[str] = set()
        for t in r.get("trace", []):
            ec = t.get("error_code")
            if ec and ec not in seen_fail:
                seen_fail.add(ec)
                failure_patterns[ec] = failure_patterns.get(ec, 0) + 1

    temp_buckets = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0}
    temp_ok = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0}
    for r in results:
        t = r["temperature"]
        if t < 0.2:
            b = "0.0-0.2"
        elif t < 0.4:
            b = "0.2-0.4"
        elif t < 0.6:
            b = "0.4-0.6"
        else:
            b = "0.6-0.8"
        temp_buckets[b] += 1
        if r["bug_fixed"]:
            temp_ok[b] += 1

    return {
        "total": total,
        "bug_fixed": bug_fixed,
        "final_answer": final_answer,
        "exceptions": exceptions,
        "success_rate": round(bug_fixed / max(total, 1) * 100, 1),
        "avg_duration": round(sum(all_durations) / max(len(all_durations), 1), 1),
        "avg_steps": round(sum(all_steps) / max(len(all_steps), 1), 1),
        "error_counts": dict(sorted(error_counts.items())),
        "failure_patterns": dict(sorted(failure_patterns.items())),
        "temp_buckets": temp_buckets,
        "temp_ok": temp_ok,
        "failed_rounds": failed_rounds,
    }


def shorten(text: str | None, max_len: int = 200) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def generate_report(segment_id: int, seg_start: int, seg_end: int,
                    analysis: dict, cumulative: dict | None) -> str:
    lines: list[str] = []
    lines.append(f"## Segment {segment_id} (Rounds {seg_start}-{seg_end})")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_\n")

    lines.append("### 本轮概览\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Rounds | {analysis['total']} |")
    lines.append(f"| Bug fixed | {analysis['bug_fixed']} ({analysis['success_rate']}%) |")
    lines.append(f"| Final answer called | {analysis['final_answer']} |")
    lines.append(f"| Exceptions | {analysis['exceptions']} |")
    lines.append(f"| Avg steps | {analysis['avg_steps']} |")
    lines.append(f"| Avg duration | {analysis['avg_duration']}s |")
    if cumulative:
        cum_rate = cumulative["bug_fixed"] / max(cumulative["total"], 1) * 100
        lines.append(f"| **Cumulative** | **{cumulative['bug_fixed']}/{cumulative['total']} ({cum_rate:.1f}%)** |")
    lines.append("")

    if analysis["failed_rounds"]:
        lines.append("### 失败轮次逐步骤回溯\n")
        for r in analysis["failed_rounds"][:5]:
            lines.append(f"#### Round {r['round_id']} (T={r['temperature']:.2f}, {r['total_steps']} steps)")
            lines.append(f"- Final error: `{r.get('error', 'N/A')}`")
            lines.append(f"- Duration: {r['duration']}s\n")
            lines.append("| Step | Error | Output (200) | Code (100) | Observation (150) |")
            lines.append("|------|-------|--------------|------------|-------------------|")
            for t in r.get("trace", []):
                ec = t.get("error_code") or "-"
                out = shorten(t.get("output", ""), 200).replace("\n", "↵")
                cd = shorten(str(t.get("code", "")), 100).replace("\n", "↵")
                obs = shorten(str(t.get("observation", "")), 150).replace("\n", "↵")
                lines.append(f"| {t['step']} | {ec} | {out} | {cd} | {obs} |")
            lines.append("")

        if len(analysis["failed_rounds"]) > 5:
            lines.append(f"_... and {len(analysis['failed_rounds']) - 5} more failed rounds_\n")

    lines.append("### Error Code 频率\n")
    ec = analysis["error_counts"]
    if ec:
        lines.append("| Code | Count | % of Rounds |")
        lines.append("|------|-------|-------------|")
        for code, count in sorted(ec.items(), key=lambda x: -x[1]):
            lines.append(f"| {code} | {count} | {count / max(analysis['total'], 1) * 100:.0f}% |")
    else:
        lines.append("_(none triggered)_")
    lines.append("")

    lines.append("### 失败轮 Error Code 频率\n")
    fp = analysis["failure_patterns"]
    if fp:
        lines.append("| Code | Count | % of Failed |")
        lines.append("|------|-------|-------------|")
        for code, count in sorted(fp.items(), key=lambda x: -x[1]):
            lines.append(f"| {code} | {count} | {count / max(len(analysis['failed_rounds']), 1) * 100:.0f}% |")
    else:
        lines.append("_(no error codes in failed rounds)_")
    lines.append("")

    lines.append("### 温度 vs 成功率\n")
    for bucket in ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8"]:
        total_b = analysis["temp_buckets"][bucket]
        ok_b = analysis["temp_ok"].get(bucket, 0)
        rate = f"{ok_b / max(total_b, 1) * 100:.0f}%" if total_b > 0 else "-"
        b = bar(ok_b, max(total_b, 1))
        lines.append(f"| {bucket:>8} | {b} {ok_b}/{total_b} ({rate}) |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------

def save_segment_data(segment_id: int, results: list[dict],
                      analysis: dict) -> Path:
    path = TUNING_DATA / f"segment_{segment_id:03d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "segment_id": segment_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "analysis": {k: v for k, v in analysis.items() if k != "failed_rounds"},
        "results": [
            {k: v for k, v in r.items() if k != "trace"}
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def load_cumulative() -> dict | None:
    segments = sorted(TUNING_DATA.glob("segment_*.json"))
    if not segments:
        return None
    total = 0
    bug_fixed = 0
    for seg_path in segments:
        data = json.loads(seg_path.read_text())
        total += data["analysis"]["total"]
        bug_fixed += data["analysis"]["bug_fixed"]
    return {"total": total, "bug_fixed": bug_fixed}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Iterative tuning for fix_python_error")
    parser.add_argument("--rounds", type=int, default=100,
                        help="Total rounds to run (default: 100)")
    parser.add_argument("--segment-size", type=int, default=10,
                        help="Rounds per inspection segment (default: 10)")
    args = parser.parse_args()

    total_rounds = args.rounds
    segment_size = args.segment_size
    num_segments = (total_rounds + segment_size - 1) // segment_size

    print(f"Iterative Tuning: {total_rounds} rounds, {segment_size} per segment, "
          f"{num_segments} segments total")
    print(f"Report: {REPORT_FILE}")
    print(f"Data:   {TUNING_DATA}")
    print()

    REPORT_FILE.write_text(
        f"# Iterative Tuning Report\n\n"
        f"Config: {total_rounds} rounds, {segment_size} per segment\n"
        f"Task: fix_python_error (modify_python.py.example)\n"
        f"Model: ornith:9b\n"
        f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"---\n\n"
    )

    all_results: list[dict] = []

    for seg_id in range(1, num_segments + 1):
        seg_start = (seg_id - 1) * segment_size
        seg_end = min(seg_id * segment_size, total_rounds)

        print(f"\n{'='*60}")
        print(f"Segment {seg_id}/{num_segments} (Rounds {seg_start}-{seg_end})")
        print(f"{'='*60}")

        seg_dir = TUNING_DATA / f"segment_{seg_id:03d}"
        seg_results: list[dict] = []

        for round_idx in range(seg_start, seg_end):
            temperature = round(random.uniform(0.0, 0.8), 2)
            r = run_one(round_idx, seg_dir, temperature)
            seg_results.append(r)
            all_results.append(r)

        print(f"\nAnalyzing segment {seg_id}...")
        analysis = analyze_segment(seg_results)
        cumulative = load_cumulative()

        report_section = generate_report(
            seg_id, seg_start, seg_end, analysis, cumulative,
        )

        with open(REPORT_FILE, "a") as f:
            f.write(report_section + "\n\n---\n\n")

        data_path = save_segment_data(seg_id, seg_results, analysis)
        print(f"Data saved: {data_path}")

        print("\n" + report_section, file=sys.stderr)

        if seg_id < num_segments:
            print(f"\n{'='*60}")
            print(f"Segment {seg_id} done. Review report above / in {REPORT_FILE}")
            print("Type 'continue' to run next segment, or adjust params and 'continue'.")
            print("Type 'stop' to quit.")
            print(f"{'='*60}")
            while True:
                try:
                    cmd = input("> ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    cmd = "stop"
                if cmd == "stop":
                    print("Stopping.")
                    sys.exit(0)
                elif cmd.startswith("continue"):
                    break
                else:
                    print("Unknown command. Type 'continue' or 'stop'.")

    print(f"\nDone! Full report: {REPORT_FILE}")


if __name__ == "__main__":
    main()
