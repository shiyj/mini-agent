#!/usr/bin/env python3
"""Fix a buggy Python script using the deep agent.

This demo:
  1. Copies modify_python.py.example onto modify_python.py
  2. Asks the deep agent to find and fix the bug (one-sentence prompt)
  3. Runs the fixed file to confirm it works
"""

import shutil
import subprocess
import sys
from pathlib import Path

from mini_agent import get_local_model, run_deep_agent

# Work under examples/data/ so the agent edits scripts in a clean directory
DATA_DIR = Path(__file__).resolve().parent / "data"
EXAMPLE_SRC = DATA_DIR / "modify_python.py.example"
TARGET = DATA_DIR / "modify_python.py"

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

print("[2/3] Creating model ...")
model = get_local_model("ornith:9b", temperature=0.4)

print("[3/3] Running deep agent ...")
print("=" * 60)

# The agent receives just one sentence — it reads, fixes, runs, and verifies autonomously
TASK = f"modify_python.py cannot run, find the problem and fix it."

result = run_deep_agent(
    prompt=TASK,
    model=model,
    target_file=str(TARGET),
    max_steps=10,
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
