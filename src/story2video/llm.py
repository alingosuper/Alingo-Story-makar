"""Ollama HTTP client with JSON mode and retries."""

import json
import logging
import time

import requests

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:32b"
MAX_RETRIES = 3
RETRY_DELAY = 2


def chat_json(prompt: str, system: str = "", model: str = DEFAULT_MODEL) -> dict:
    """Send a chat request to Ollama with JSON mode enabled.

    Returns parsed JSON dict, or empty dict on failure.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 4096,
        },
    }

    log.info("LLM request  model=%s", model)
    log.debug("System prompt:\n%s", system)
    log.debug("User prompt:\n%s", prompt)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            t0 = time.time()
            resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
            elapsed = time.time() - t0
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
            parsed = json.loads(content)
            log.info("LLM response model=%s elapsed=%.1fs length=%d", model, elapsed, len(content))
            log.debug("Response JSON:\n%s", json.dumps(parsed, indent=2))
            return parsed
        except requests.RequestException as e:
            log.warning("Attempt %d/%d request error: %s", attempt, MAX_RETRIES, e)
        except json.JSONDecodeError as e:
            log.warning("Attempt %d/%d JSON parse error: %s\nRaw content: %s", attempt, MAX_RETRIES, e, content)

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    log.error("All %d retries exhausted, returning empty dict", MAX_RETRIES)
    return {}
