#!/usr/bin/env python3
"""Query the host machine's OS, CPU, GPU, and memory via plain chat().

Demonstrates automatic Deep Agent delegation: the user only writes a simple
system prompt and calls chat() normally. ChatSession automatically injects
the Deep Agent protocol into the system prompt; the model decides on its own
to emit a ```deep block, which ChatSession parses and routes to run_deep_agent.
"""

from mini_agent import load_model, ChatSession


def main():
    print("=" * 60)
    print("System Info Inquiry via plain chat() + auto Deep Agent")
    print("=" * 60)
    print("\nLoading model...")
    model, _ = load_model("ornith:9b", "ollama-local")

    # Simple system prompt - Deep Agent capability is auto-injected by ChatSession
    chat = ChatSession(
        model=model,
        system_prompt="You are a helpful assistant who knows a lot about computers.",
        enable_deep_agent=True,
    )

    # Plain chat() - the model auto-decides to use the Deep Agent
    print("\nAsking: please check the current OS, CPU, GPU and memory info ...\n")
    result = chat.chat("please check the current OS, CPU, GPU and memory info")

    print("=" * 60)
    print("Chat result:")
    print("=" * 60)
    print(result)
    print()
    print(f"Tokens used: {chat.total_tokens}")
    print(f"History turns: {len(chat.history)}")


if __name__ == "__main__":
    main()