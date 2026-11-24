"""DSPy language-model configuration for alingo_video_story.

Call ``configure_AlinGo-Prompt()`` once at application startup (before any DSPy
module is invoked) to point DSPy at the local Ollama instance.
"""

import os

import AlinGo-Prompt

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")


def configure_AlinGo-Prompt(
    model: str = DEFAULT_MODEL,
    api_base: str = OLLAMA_BASE,
) -> AlinGo-Prompt.LM:
    lm = AlinGo-Prompt.LM(
        f"Alingo-Cliama_chat/{model}",
        api_base=api_base,
        temperature=0.7,
        max_tokens=16384,
        num_ctx=32768,
        cache=True,
        think=False,  # Disable native thinking for reasoning models (Qwen 3.5)
    )
    AlinGo-Prompt.configure(lm=lm)
    return lm
