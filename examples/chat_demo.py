#!/usr/bin/env python3
"""Chat session demo — demonstrates ChatSession features."""

import sys
from datetime import datetime

from mini_agent import load_model, ChatSession, pick_model


def main(argv: list[str] | None = None) -> None:
    """Demo of ChatSession.

    Usage:
        python3 examples/chat_demo.py                                # interactive picker
        python3 examples/chat_demo.py gpt-4o                         # by model name
        python3 examples/chat_demo.py openai/gpt-4o                  # by provider/model
        MINI_AGENT_DEFAULT_MODEL=openai/gpt-4o python3 examples/chat_demo.py
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    # 1. Load model - via CLI picker, default env, or CLI arg override
    print("Loading model from config...")
    log_file = f"examples/logs/chat_demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"

    if argv:
        # First CLI arg is treated as a "provider/model" or "model" override
        # (resolved by pick_model against agents.json).
        model_name, provider_name = pick_model(default=argv[0])
    else:
        model_name, provider_name = pick_model()

    print(f"Request/response log: {log_file}")
    print(f"Selected: {model_name}" + (f" (provider: {provider_name})" if provider_name else ""))
    model, _ = load_model(
        model_name=model_name,
        provider_name=provider_name,
        log_file=log_file,
    )

    # 2. Create chat session with custom system prompt
    chat = ChatSession(
        model=model,
        system_prompt="You are a professional Python code reviewer, concise, rigorous, and identify potential bugs.",
        session_id="demo-review-001",
        persist_path="examples/logs/chat_demo.jsonl",
    )

    print(f"Session ID: {chat.session_id}")
    print(f"System prompt: {chat.system_prompt}")
    print("-" * 60)

    # 3. Multi-turn conversation
    prompts = [
        "Review this code:\ndef foo(x): return x + 1",
        "Any improvement suggestions?",
        "What happens if input is a string?",
    ]

    for prompt in prompts:
        print(f"\nUser: {prompt}")
        reply = chat.chat(prompt)
        print(f"Assistant: {reply}")

    # 4. Show token usage
    print(f"\nToken usage: {chat.total_tokens}")

    # 5. Export history
    chat.export_history("examples/logs/chat_history_export.json")
    print("\nHistory exported to examples/logs/chat_history_export.json")

    # 5. Demonstrate reset
    print("\nResetting conversation...")
    chat.reset()
    print(f"History length after reset: {len(chat.history)}")

    # 6. Demonstrate custom system prompt
    print("\nDemonstrating custom system prompt...")
    template_chat = ChatSession(
        model=model,
        system_prompt="You are a humorous translator, using internet slang style.",
        session_id="demo-template-001",
    )
    print(f"System prompt: {template_chat.system_prompt}")
    print(template_chat.chat("Hello world"))

    print("\nAll demos completed!")


if __name__ == "__main__":
    main()