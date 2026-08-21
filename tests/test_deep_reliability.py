#!/usr/bin/env python3
"""Multi-round test: verify Deep Agent auto-trigger reliability across modes."""

import sys
import time

from mini_agent import load_model, ChatSession


def run_test(chat, query, mode, timeout_hint=""):
    """Run one query, return (success, detail)."""
    print(f"\n{'='*60}")
    print(f"TEST: {query!r}  mode={mode}")
    print(f"{'='*60}")
    t0 = time.time()
    try:
        if mode == "stream":
            chunks = []
            for chunk in chat.chat(query, stream=True):
                chunks.append(chunk)
            result = "".join(chunks)
        else:
            result = chat.chat(query)

        elapsed = time.time() - t0
        has_deep_block = "```deep" in result
        has_error = "Deep Agent failed" in result
        # A successful delegation returns real data (not a refusal / hallucination)
        refusal_markers = ["I cannot", "I don't have", "无法", "不能访问", "没有权限"]
        is_refusal = any(m in result for m in refusal_markers)

        print(f"  elapsed: {elapsed:.0f}s")
        print(f"  result ({len(result)} chars): {result[:200]!r}")
        print(f"  contains ```deep block: {has_deep_block}")
        print(f"  contains refusal: {is_refusal}")
        print(f"  contains error: {has_error}")

        if has_error:
            return False, "deep agent errored"
        if is_refusal:
            return False, "model refused instead of delegating"
        if has_deep_block:
            return False, "raw ```deep block leaked to user (trigger parsed but not stripped?)"
        # If we got here with reasonable output, consider it a pass
        if len(result) > 5:
            return True, f"ok ({elapsed:.0f}s)"
        return False, "empty/too-short result"
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  EXCEPTION after {elapsed:.0f}s: {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    print("Loading model...")
    model, _ = load_model("ornith:9b", "ollama-local")

    queries = [
        ("现在几点了？", "time"),
        ("What time is it now?", "time-en"),
        ("查看当前系统的CPU型号和内存大小", "sysinfo"),
    ]

    results = []
    for round_idx in range(rounds):
        for query, tag in queries:
            for mode in ["blocking", "stream"]:
                # Fresh session per test to avoid history pollution
                chat = ChatSession(
                    model=model,
                    system_prompt="You are a helpful assistant.",
                    enable_deep_agent=True,
                    deep_agent_config={"max_steps": 6, "verbose": False},
                    debug=True,
                )
                ok, detail = run_test(chat, query, mode)
                results.append((tag, mode, ok, detail))
                status = "PASS" if ok else "FAIL"
                print(f"\n  >>> [{status}] {tag}/{mode}: {detail}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passes = sum(1 for _, _, ok, _ in results if ok)
    print(f"Total: {len(results)}, Pass: {passes}, Fail: {len(results)-passes}")
    for tag, mode, ok, detail in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {tag:>10} / {mode:>8}: {detail}")


if __name__ == "__main__":
    main()