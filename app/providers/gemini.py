import asyncio
import base64
import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger("infobot.gemini")

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_TIMEOUT = 30  # seconds, per the plan's resilience section


class GeminiError(Exception):
    pass


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

    Retries once total across either a transient provider error (e.g. the "high
    demand" 503 this model returns under load) or a JSON parse failure, then
    raises — a stuck provider should fail fast into the caller's own fallback
    path rather than hang the pipeline.
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

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            raw_text = await _call_interactions(body)
        except Exception as exc:
            last_error = exc
            logger.warning("Gemini call failed (attempt %d): %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(2)  # brief backoff before the one retry (transient 429/503)
            continue

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning(
                "Gemini returned unparseable JSON (attempt %d): %r", attempt + 1, raw_text[:200]
            )
            continue

    raise GeminiError(f"Gemini call failed after retries: {last_error}")


async def embed_text(text: str, output_dimensionality: int = 768) -> list[float]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_API_BASE}/models/{settings.GEMINI_EMBED_MODEL}:embedContent",
            params={"key": settings.GEMINI_API_KEY},
            json={
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": output_dimensionality,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return data["embedding"]["values"]


async def _call_interactions(body: dict) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_API_BASE}/interactions",
            params={"key": settings.GEMINI_API_KEY},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    for step in data.get("steps", []):
        if step.get("type") == "model_output":
            for part in step.get("content", []):
                if part.get("type") == "text":
                    return part["text"]

    raise GeminiError("No model_output text found in Gemini response")
