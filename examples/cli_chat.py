#!/usr/bin/env python3
"""Interactive CLI chat - ChatSession with Deep Agent (sub-agent) support."""

from mini_agent import load_model, ChatSession, pick_model


HELP = """Commands:
  /deep [task]   run a task through the Deep Agent (sub-agent). If no task is
                 given inline, you will be prompted for it. Always triggers the
                 Deep Agent - reliable code/file/system operations.
  /history       show conversation history
  /export [path] export history to JSON (default examples/logs/chat_export.json)
  /reset         clear conversation history
  /quit          exit
Anything else is sent to the chat model, which may auto-invoke the Deep Agent
(by emitting a ```deep block) when it decides the task needs code execution.
"""


def main():
    print("=" * 60)
    print("Mini-Agent Interactive Chat (Deep Agent enabled)")
    print("=" * 60)
    print(HELP)
    print("=" * 60)

    print("\nLoading model...")
    log_file = f"examples/logs/cli_chat_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    model_name, provider_name = pick_model()
    print(f"\nSelected: {model_name}" + (f" (provider: {provider_name})" if provider_name else ""))
    model, _ = load_model(
        model_name=model_name,
        provider_name=provider_name,
        log_file=log_file,
    )
    print(f"Request/response log: {log_file}")

    chat = ChatSession(
        model=model,
        system_prompt="You are a concise, professional programming assistant.",
        session_id=f"cli-chat-{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}",
        persist_path="examples/logs/cli_chat_session.jsonl",
    )

    print(f"Session ID: {chat.session_id}")
    print(f"Persist path: {chat._persist_path}")
    print("-" * 60)

    while True:
        try:
            user = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye!")
            break

        if not user:
            continue

        # --- commands -------------------------------------------------------
        if user == "/quit":
            print("Goodbye!")
            break
        elif user == "/help" or user == "/?":
            print(HELP)
            continue
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
        elif user.startswith("/deep"):
            task = user[5:].strip()
            if not task:
                try:
                    task = input("Task: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nCanceled.")
                    continue
            if task:
                print("\nDeep Agent is working (this may take a while)...", flush=True)
                try:
                    result = chat.deep_chat(task)
                    print("\n--- Deep Agent result ---")
                    print(result)
                except Exception as e:
                    print(f"\nDeep Agent error: {e}")
            continue

        # --- normal chat (streaming) ----------------------------------------
        try:
            print("Assistant: ", end="", flush=True)
            for chunk in chat.chat(user, stream=True):
                print(chunk, end="", flush=True)
            print()

            tokens = chat.total_tokens
            if tokens["total"] > 0 and tokens["total"] % 5000 < 100:
                print(f"  [Token: in={tokens['input']}, out={tokens['output']}, total={tokens['total']}]")
        except Exception as e:
            print(f"\nError: {e}")

    print("\nSession ended.")


if __name__ == "__main__":
    main()