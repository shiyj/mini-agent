#!/usr/bin/env python3
"""Chat session demo — demonstrates ChatSession features."""

from mini_agent import load_model, ChatSession


def main():
    # 1. Load model from config (provider + model name)
    print("Loading model from config...")
    model, _ = load_model("ornith:9b", "ollama-local")

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