import asyncio
import base64
import json
import logging
import time

from app import http
from app.config import settings
from app.providers import fallback_llm

logger = logging.getLogger("infobot.gemini")

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Measured 2026-10-01: a short JSON task takes ~2 s on gemini-3.5-flash-lite and 5-8 s on
# gemini-3.1-flash-lite through the older Interactions API. A call that has not answered in
# this long is stuck; the next model (and finally Groq) is faster than waiting for it.
_TIMEOUT = 15
_IMAGE_TIMEOUT = 45  # an image plus the model reading it takes noticeably longer than text
_RETRY_BACKOFF_SECONDS = 1  # only used when the same model is tried twice (images)
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
    """A multimodal prompt: a text part plus an inline base64 image part. This neutral
    shape is turned into the API's own format by _to_parts."""
    return [
        {"type": "text", "text": text_prompt},
        {"type": "image", "data": base64.b64encode(image_bytes).decode("ascii"), "mime_type": mime_type},
    ]


def _to_parts(input_data: str | list) -> list[dict]:
    if isinstance(input_data, str):
        return [{"text": input_data}]
    parts: list[dict] = []
    for part in input_data:
        if part.get("type") == "text":
            parts.append({"text": part["text"]})
        elif part.get("type") == "image":
            parts.append({"inlineData": {"mimeType": part["mime_type"], "data": part["data"]}})
    return parts


def _models() -> list[str]:
    """The primary model, then the fallback model, without repeats."""
    seen: list[str] = []
    for model in (settings.GEMINI_MODEL, settings.GEMINI_MODEL_FALLBACK):
        if model and model not in seen:
            seen.append(model)
    return seen


async def generate_json(input_data: str | list, schema: dict, thinking_level: str = "low") -> dict:
    """Call Gemini and parse a JSON response matching schema.

    input_data is either a plain prompt string, or a multimodal list built by
    build_image_input. (thinking_level is accepted for compatibility and ignored: it made no
    measurable difference to latency on these models.)

    Each model is tried once, primary first: a slow or overloaded model is skipped in favour
    of the fallback model rather than waited on or retried. Images get one more try at the
    primary model, since they have no Groq fallback. A failure of every Gemini model sends
    text prompts to Groq; image prompts raise, and the caller asks for the text version.
    """
    is_image = isinstance(input_data, list)
    parts = _to_parts(input_data)
    models = _models()
    if not models:
        raise GeminiError("GEMINI_MODEL is not configured")
    sequence = models + (models[:1] if is_image else [])
    timeout = _IMAGE_TIMEOUT if is_image else _TIMEOUT

    last_error: Exception | None = None
    for attempt, model in enumerate(sequence):
        try:
            raw_text = await _call_model(model, parts, schema, timeout)
        except Exception as exc:
            last_error = exc
            logger.warning("Gemini %s failed (attempt %d): %r", model, attempt + 1, exc)
            if attempt + 1 < len(sequence) and sequence[attempt + 1] == model:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
            continue

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning("Gemini %s returned unparseable JSON (attempt %d): %r", model, attempt + 1, raw_text[:200])

    # Gemini is out of quota or down. Text-only calls can fall back to Groq;
    # image calls cannot (no vision fallback), so they fail and the caller
    # asks the user for the text version instead.
    if not is_image:
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


async def _post(url: str, payload: dict, timeout: float = _TIMEOUT) -> dict:
    """POST with key rotation: a 429 (quota) moves on to the next key at once."""
    last: GeminiHTTPError | None = None
    for key in _keys():
        resp = await http.post("gemini", url, timeout=timeout, headers={"x-goog-api-key": key}, json=payload)
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


async def _call_model(model: str, parts: list[dict], schema: dict, timeout: float = _TIMEOUT) -> str:
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
    }
    async with _slots:
        data = await _post(f"{_API_BASE}/models/{model}:generateContent", payload, timeout)

    candidates = data.get("candidates") or []
    if candidates:
        text = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []) if not p.get("thought"))
        if text:
            return text
        reason = candidates[0].get("finishReason")
    else:
        reason = data.get("promptFeedback", {}).get("blockReason") or "no candidates"
    # Typically a safety block on abusive input: the next model, then Groq, may still classify it.
    raise GeminiError(f"No text in Gemini response ({reason})")
