import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger("infobot.fallback_llm")

_URL = "https://api.groq.com/openai/v1/chat/completions"
_TIMEOUT = 30  # seconds, matches the Gemini timeout in the plan's resilience section


class FallbackError(Exception):
    pass


async def generate_json(prompt: str, schema: dict) -> dict:
    """Text-only JSON generation on Groq, used when Gemini is down or out of
    quota. Same contract as gemini.generate_json so callers don't care which
    provider answered. No vision: images have no fallback, by design.
    """
    if not settings.GROQ_API_KEY:
        raise FallbackError("No GROQ_API_KEY configured")

    body = {
        "model": settings.GROQ_MODEL,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema},
        },
    }
    if "gpt-oss" in settings.GROQ_MODEL:
        body["reasoning_effort"] = "low"

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    _URL,
                    headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                    json=body,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as exc:
            last_error = exc
            logger.warning("Groq fallback failed (attempt %d): %r", attempt + 1, exc)

    raise FallbackError(f"Groq fallback failed after retries: {last_error!r}")
