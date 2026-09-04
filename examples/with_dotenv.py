#!/usr/bin/env python3
"""Demo: use mini-agent with API keys loaded from a .env file.

No environment-variable wrangling required. Just:
    cp .env.example .env
    # edit .env, put your real OPENAI_API_KEY / OPENROUTER_API_KEY in it
    python3 examples/with_dotenv.py openai/gpt-4o-mini
"""

import sys
from datetime import datetime

from mini_agent import load_model, ChatSession, pick_model


def main() -> None:
    print("=" * 60)
    print("mini-agent with .env-loaded API keys")
    print("=" * 60)

    # Step 1 - copy the template (one-time):
    #     cp .env.example .env
    #     # then edit .env and put your real keys in it
    # The .env file is gitignored. Never commit real keys.

    # Step 2 - pick a model. If you pass nothing, the interactive picker
    # asks; if you pass "openai/gpt-4o-mini" or "poolside/laguna-xs-2.1:free"
    # it skips straight to that model.
    cli_arg = sys.argv[1] if len(sys.argv) > 1 else None
    model_name, provider_name = pick_model(default=cli_arg)
    print(f"\nSelected: {model_name}" + (f" (provider: {provider_name})" if provider_name else ""))

    # Step 3 - load_model() auto-loads .env from the project root and
    # substitutes ${OPENAI_API_KEY} / ${OPENROUTER_API_KEY} into the auth
    # header. If the key is missing, the model client will receive
    # api_key=None and the upstream call will return 401.
    log_file = f"examples/logs/dotenv_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    print(f"Request/response log: {log_file}")
    model, _ = load_model(
        model_name=model_name,
        provider_name=provider_name,
        log_file=log_file,
    )

    # Step 4 - chat normally. The key point: NO `export` was needed.
    chat = ChatSession(
        model=model,
        system_prompt="You are a helpful assistant.",
        enable_deep_agent=True,
    )
    print("\nChat session ready. Type 'quit' to exit.\n")
    while True:
        try:
            user = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if not user or user.lower() in ("quit", "exit"):
            break
        print(chat.chat(user))


if __name__ == "__main__":
    main()