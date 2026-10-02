import asyncio
import json
import logging

from app import http
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

    base = {
        "model": settings.GROQ_MODEL,
        "temperature": 0,
    }
    if "gpt-oss" in settings.GROQ_MODEL:
        base["reasoning_effort"] = "low"

    # Attempt 1 asks for schema-constrained output. If Groq rejects that (it
    # validates strictly and a near-miss is a 400), attempt 2 falls back to plain
    # JSON mode with the schema spelled out in the prompt.
    strict = {
        **base,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "response", "schema": schema}},
    }
    loose = {
        **base,
        "messages": [{
            "role": "user",
            "content": f"{prompt}\n\nReturn only a JSON object matching this JSON Schema:\n{json.dumps(schema)}",
        }],
        "response_format": {"type": "json_object"},
    }

    last_error: Exception | None = None
    for attempt, body in enumerate((strict, loose)):
        try:
            resp = await http.post(
                "groq", _URL, timeout=_TIMEOUT, headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"}, json=body
            )
            if resp.status_code >= 400:
                raise FallbackError(f"Groq HTTP {resp.status_code}: {resp.text[:300]}")
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as exc:
            last_error = exc
            logger.warning("Groq fallback failed (attempt %d): %r", attempt + 1, exc)
            if attempt == 0 and "429" in str(exc):
                await asyncio.sleep(3)

    raise FallbackError(f"Groq fallback failed after retries: {last_error!r}")
