#!/usr/bin/env python3
"""Fix a buggy Python script using the deep agent.

This demo:
  1. Copies modify_python.py.example onto modify_python.py
  2. Asks the deep agent to find and fix the bug (one-sentence prompt)
  3. Runs the fixed file to confirm it works
"""

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from mini_agent import get_local_model, run_deep_agent
from mini_agent.config import load_agent_config

# Work under examples/data/ so the agent edits scripts in a clean directory
DATA_DIR = Path(__file__).resolve().parent / "data"
EXAMPLE_SRC = DATA_DIR / "modify_python.py.example"
TARGET = DATA_DIR / "modify_python.py"

# Log file: logs/ directory with timestamp in filename (default)
LOGS_DIR = Path(__file__).resolve().parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Optional args for automated 100-run harness: --run-id / --out-dir
parser = argparse.ArgumentParser(description="Fix a buggy Python script using the deep agent.")
parser.add_argument("--run-id", type=int, default=None, help="Run index for structured logging")
parser.add_argument("--out-dir", type=str, default=None, help="Directory to store per-run api.jsonl")
parser.add_argument("--temperature", type=float, default=0.5, help="Temperature for the model")
args, _ = parser.parse_known_args()

if args.out_dir and args.run_id is not None:
    run_dir = Path(args.out_dir) / f"run_{args.run_id:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    LOG_FILE = run_dir / "api.jsonl"
else:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    LOG_FILE = LOGS_DIR / f"fix_python_error_{timestamp}.jsonl"

print("=" * 60)
print("mini-agent  Fix Python Error Demo")
print("=" * 60)

print(f"\n[1/3] Copying {EXAMPLE_SRC.name}  ->  {TARGET.name} (stripping comments) ...")
# Strip comments during copy so the agent sees buggy code without hints
import re  # noqa: E402
example_text = EXAMPLE_SRC.read_text(encoding="utf-8")
def _strip_inline_comment(line: str) -> str:
    in_sq = False
    in_dq = False
    for i, ch in enumerate(line):
        if ch == '"' and not in_sq:
            in_dq = not in_dq
        elif ch == "'" and not in_dq:
            in_sq = not in_sq
        elif ch == "#" and not in_sq and not in_dq:
            return line[:i].rstrip()
    return line

stripped = "\n".join(
    _strip_inline_comment(line) for line in example_text.splitlines()
)
# Collapse blank lines after comment removal
cleaned = re.sub(r"\n{3,}", "\n\n", stripped).strip() + "\n"
TARGET.write_text(cleaned, encoding="utf-8")

print("[2/3] Loading model config from config/agents.json ...")
print("[3/3] Running deep agent ...")
print("=" * 60)

TASK = f"{TARGET} cannot run, find the problem and fix it."

config = load_agent_config("ornith:9b", provider_name="ollama-local")
model = get_local_model(
    model_name=config["model_name"],
    api_base=config["baseURL"],
    api_key=config.get("apiKey"),
    temperature=args.temperature,
    max_output_tokens=config["max_output_tokens"],
    context_window=config["context_window"],
    log_file=str(LOG_FILE),
)

result = run_deep_agent(
    prompt=TASK,
    model=model,
    max_steps=config.get("max_steps", 8),
    max_observation_length=config.get("max_observation_length", 2000),
)

print("=" * 60)
print(f"Deep agent result:\n{result}")

# Count steps from log (each request/response pair = 1 step)
import json  # noqa: E402
steps = 0
if LOG_FILE.exists():
    with open(LOG_FILE) as f:
        for line in f:
            try:
                if json.loads(line).get("direction") == "request":
                    steps += 1
            except json.JSONDecodeError:
                pass
print(f"STEPS: {steps}")

print("\n" + "-" * 60)
print("Sanity check: running target file ...")
check = subprocess.run(
    [sys.executable, str(TARGET)],
    capture_output=True, text=True, timeout=15,
)
if check.returncode == 0:
    print("Output:")
    for line in check.stdout.strip().splitlines():
        print(f"  {line}")
    print("\nBug fixed!")
else:
    print(f"Still broken:\n{check.stderr}")
    sys.exit(1)
