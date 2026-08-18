#!/usr/bin/env python3
"""Interactive CLI chat — demonstrates ChatSession interactive mode with persistence."""

from mini_agent import load_model, ChatSession


def main():
    print("=" * 60)
    print("Mini-Agent Interactive Chat")
    print("=" * 60)
    print("Commands: /quit quit  |  /reset reset  |  /history show history  |  /export <path> export")
    print("=" * 60)

    # Load model from config
    print("\nLoading model...")
    model, _ = load_model("ornith:9b", "ollama-local")

    # Create chat session with persistence
    chat = ChatSession(
        model=model,
        system_prompt="You are a concise, professional programming assistant.",
        session_id=f"cli-chat-{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}",
        persist_path="examples/logs/cli_chat_session.jsonl",
    )

    print(f"Session ID: {chat.session_id}")
    print(f"Persist path: {chat._persist_path}")
    print(f"System prompt: {chat.system_prompt}")
    print("-" * 60)

    while True:
        try:
            user = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye!")
            break

        if not user:
            continue

        # Handle commands
        if user == "/quit":
            print("Goodbye!")
            break
        elif user == "/reset":
            chat.reset()
            print("Conversation reset.")
            continue
        elif user == "/history":
            history = chat.history
            if not history:
                print("  (no history)")
            else:
                for i, msg in enumerate(history, 1):
                    role = "You" if msg.role.value == "user" else "Assistant"
                    preview = msg.content[:80] + ("..." if len(msg.content) > 80 else "")
                    print(f"  {i}. {role}: {preview}")
            continue
        elif user.startswith("/export"):
            parts = user.split(maxsplit=1)
            path = parts[1] if len(parts) > 1 else "examples/logs/chat_export.json"
            try:
                chat.export_history(path)
                print(f"Exported to {path}")
            except Exception as e:
                print(f"Export failed: {e}")
            continue

        # Normal chat - streaming output
        try:
            print("Assistant: ", end="", flush=True)
            for chunk in chat.chat(user, stream=True):
                print(chunk, end="", flush=True)
            print()  # newline after streaming

            # Show token usage periodically
            tokens = chat.total_tokens
            if tokens["total"] > 0 and tokens["total"] % 5000 < 100:
                print(f"  [Token: in={tokens['input']}, out={tokens['output']}, total={tokens['total']}]")

        except Exception as e:
            print(f"\nError: {e}")

    print("\nSession ended.")


if __name__ == "__main__":
    main()