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

from mini_agent import run_deep_agent_with_config

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
stripped = "\n".join(
    line if not line.strip().startswith("#") else ""
    for line in example_text.splitlines()
)
# Collapse blank lines after comment removal
cleaned = re.sub(r"\n{3,}", "\n\n", stripped).strip() + "\n"
TARGET.write_text(cleaned, encoding="utf-8")

print("[2/3] Loading model config from config/agents.json ...")
print("[3/3] Running deep agent ...")
print("=" * 60)

# The agent receives just one sentence — it reads, fixes, runs, and verifies autonomously
TASK = f"modify_python.py cannot run, find the problem and fix it."

# Model, base URL, context window, max_steps, observation length and the
# compressor are all resolved from config/agents.json (entry "ornith:9b"
# under provider "ollama-local"). provider_name disambiguates when the
# same model name exists under more than one provider.
result = run_deep_agent_with_config(
    prompt=TASK,
    model_name="ornith:9b",
    provider_name="ollama-local",
    target_file=str(TARGET),
    log_file=str(LOG_FILE),
)

print("=" * 60)
print(f"Deep agent result:\n{result}")

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
