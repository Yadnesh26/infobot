import asyncio
import base64
import json
import logging
import time

import httpx

from app.config import settings
from app.providers import fallback_llm

logger = logging.getLogger("infobot.gemini")

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_TIMEOUT = 30  # seconds, per the plan's resilience section
_RETRY_BACKOFF_SECONDS = 2  # brief pause before a retry (transient 429/503)
_MAX_BACKOFF_SECONDS = 8
_MAX_CONCURRENT_CALLS = 4  # one 3-claim message makes ~7 calls; a burst of users must not trip the rate limit
_slots = asyncio.Semaphore(_MAX_CONCURRENT_CALLS)

# A key that just hit its quota is skipped for a while, so every request does not
# first waste a call on the exhausted one. A daily-quota 429 sits out much longer.
_cooldown_until: dict[str, float] = {}
_COOLDOWN_SECONDS = 30
_DAILY_COOLDOWN_SECONDS = 600


class GeminiError(Exception):
    pass


class GeminiHTTPError(GeminiError):
    def __init__(self, status: int, body: str, retry_after: float | None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"Gemini HTTP {status}: {body}")


def build_image_input(text_prompt: str, image_bytes: bytes, mime_type: str) -> list:
    """Shape the Interactions API's multimodal `input` array: a text part plus
    an inline base64 image part."""
    return [
        {"type": "text", "text": text_prompt},
        {"type": "image", "data": base64.b64encode(image_bytes).decode("ascii"), "mime_type": mime_type},
    ]


async def generate_json(input_data: str | list, schema: dict, thinking_level: str = "low") -> dict:
    """Call the Gemini Interactions API and parse a JSON response matching schema.

    input_data is either a plain prompt string, or a multimodal list built by
    build_image_input.

    Retries once across a transient provider error (e.g. the "high demand" 503
    this model returns under load) or a JSON parse failure, then raises — a stuck
    provider should fail fast into the caller's own fallback path rather than
    hang the pipeline. Image calls have no fallback, so they get one more try.
    """
    body = {
        "model": settings.GEMINI_MODEL,
        "input": input_data,
        "generation_config": {"thinking_level": thinking_level},
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": schema,
        },
    }

    attempts = 2 if isinstance(input_data, str) else 3
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            raw_text = await _call_interactions(body)
        except Exception as exc:
            last_error = exc
            logger.warning("Gemini call failed (attempt %d): %s", attempt + 1, exc)
            if attempt < attempts - 1:
                wait = getattr(exc, "retry_after", None) or _RETRY_BACKOFF_SECONDS * (attempt + 1)
                await asyncio.sleep(min(wait, _MAX_BACKOFF_SECONDS))
            continue

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning(
                "Gemini returned unparseable JSON (attempt %d): %r", attempt + 1, raw_text[:200]
            )
            continue

    # Gemini is out of quota or down. Text-only calls can fall back to Groq;
    # image calls cannot (no vision fallback), so they fail and the caller
    # asks the user for the text version instead.
    if isinstance(input_data, str):
        logger.warning("Gemini failed after retries (%r); falling back to Groq", last_error)
        try:
            return await fallback_llm.generate_json(input_data, schema)
        except Exception as exc:
            raise GeminiError(f"Gemini and Groq fallback both failed: gemini={last_error!r} groq={exc!r}")

    raise GeminiError(f"Gemini call failed after retries: {last_error!r}")


def _keys() -> list[str]:
    """Usable keys in order of preference: the primary, then the fallback key
    (another account, so another quota). If every key is cooling down, try them
    all anyway rather than fail without a call."""
    keys = [k for k in (settings.GEMINI_API_KEY, settings.GEMINI_API_KEY_FALLBACK) if k]
    now = time.monotonic()
    ready = [k for k in keys if _cooldown_until.get(k, 0) <= now]
    return ready or keys


async def _post(url: str, payload: dict) -> dict:
    """POST with key rotation: a 429 (quota) moves on to the next key at once."""
    last: GeminiHTTPError | None = None
    for key in _keys():
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers={"x-goog-api-key": key}, json=payload)
        if resp.status_code < 400:
            return resp.json()
        try:
            retry_after = float(resp.headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        last = GeminiHTTPError(resp.status_code, resp.text[:300], retry_after)
        if resp.status_code != 429:
            raise last
        daily = "per day" in resp.text
        _cooldown_until[key] = time.monotonic() + (_DAILY_COOLDOWN_SECONDS if daily else _COOLDOWN_SECONDS)
        logger.warning("Gemini key ending ...%s is out of quota (%s); trying the next key", key[-4:], "daily" if daily else "per-minute")
    raise last or GeminiError("No Gemini API key configured")


async def embed_text(text: str, output_dimensionality: int = 768) -> list[float]:
    data = await _post(
        f"{_API_BASE}/models/{settings.GEMINI_EMBED_MODEL}:embedContent",
        {"content": {"parts": [{"text": text}]}, "outputDimensionality": output_dimensionality},
    )
    return data["embedding"]["values"]


async def _call_interactions(body: dict) -> str:
    async with _slots:
        data = await _post(f"{_API_BASE}/interactions", body)

    for step in data.get("steps", []):
        if step.get("type") == "model_output":
            for part in step.get("content", []):
                if part.get("type") == "text":
                    return part["text"]

    raise GeminiError("No model_output text found in Gemini response")
