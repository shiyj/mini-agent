"""hashline multi-line / multi-location edit demo.

Run:  PYTHONPATH=src uv run python3 examples/hashline_edit_demo.py

Prints:
  1) original text WITH hashline tags (N#HHHH anchors)
  2) the edit DSL (the modification script)
  3) modified text WITHOUT tags
"""

import sys
import tempfile

sys.path.insert(0, "src")
from mini_agent.hashline import apply_edit_block, tag_lines


def demo(title: str, original: str, dsl: str) -> None:
    path = tempfile.mktemp(suffix=".py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(original)

    print("=" * 72)
    print(f"Demo: {title}")
    print("=" * 72)
    print("\n--- original text (with tags) ---\n" + tag_lines(original))
    print("--- edit DSL ---\n" + dsl)
    result = apply_edit_block(path, dsl)
    modified = open(path, encoding="utf-8").read()
    print("--- modified text (no tags) ---\n" + modified)
    print("--- result ---\n" + result)
    print()


def main() -> None:
    # Example 1: multi-line range replace (replace two consecutive buggy lines)
    original_1 = (
        "def analyze(records):\n"
        "    temps = [t for _, t in records]\n"
        "    avg = 0  # BUG: overwrites correct average\n"
        "    above = temps[temps > avg]  # BUG: avg never initialized\n"
        "    return avg, above\n"
    )
    anchors_1 = tag_lines(original_1).split("\n")
    a3 = anchors_1[2].split(" ", 1)[0]   # line 3 anchor
    a4 = anchors_1[3].split(" ", 1)[0]   # line 4 anchor
    dsl_1 = (
        f"replace {a3} {a4}\n"
        "    avg = sum(temps) / len(temps)\n"
        "    above = [t for t in temps if t > avg]\n"
    )
    demo("multi-line range replace (replace start..end)", original_1, dsl_1)

    # Example 2: multiple replacements in one block (two separate locations)
    original_2 = (
        "def load_data(source):\n"
        "    records = []\n"
        "    for line in source.strip().splitlines():\n"
        "        date, val = line.split(\",\")\n"
        "        records.append((date.strip(), float(val.strip())))\n"
        "    return records\n"
    )
    anchors_2 = tag_lines(original_2).split("\n")
    a3 = anchors_2[2].split(" ", 1)[0]   # for-loop line
    a4 = anchors_2[3].split(" ", 1)[0]   # date, val line
    dsl_2 = (
        f"replace {a3}\n"
        "    for line in source.strip().splitlines():\n"
        "        if not line.strip():\n"
        "            continue\n"
        f"replace {a4}\n"
        "        date, val = line.split(\",\")\n"
        "        date = date.strip()\n"
        "        val = float(val.strip())\n"
    )
    demo("multiple replacements in one block", original_2, dsl_2)


if __name__ == "__main__":
    main()
