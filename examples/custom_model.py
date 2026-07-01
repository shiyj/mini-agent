#!/usr/bin/env python3
"""Pass a custom model (not from get_local_model) to the agent.

Useful when you want to configure LiteLLMModel with non-default
parameters (e.g. different provider, custom api_key, etc.).
"""

from smolagents import LiteLLMModel
from mini_agent import run_fast_agent

# Build a model manually — here with higher temperature and larger context
my_model = LiteLLMModel(
    model_id="ollama_chat/ornith:9b",
    api_base="http://127.0.0.1:11434",
    temperature=0.8,
    num_ctx=32768,
)

result = run_fast_agent(
    prompt="Generate a creative Python script that prints a Mandelbrot set to the terminal using ASCII characters.",
    model=my_model,
)
print(result)
