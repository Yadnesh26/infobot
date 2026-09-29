from pathlib import Path

from app.providers.gemini import build_image_input, generate_json

_PROMPT_IMAGE = (Path(__file__).parent.parent / "prompts" / "extract_image_text.txt").read_text(encoding="utf-8")

_SCHEMA_IMAGE = {
    "type": "object",
    "properties": {
        "has_readable_text": {"type": "boolean"},
        "extracted_text": {"type": "string"},
    },
    "required": ["has_readable_text", "extracted_text"],
}


def normalize_text(text: str) -> str:
    """Text passes through unchanged -- no preprocessing, no translation.

    The LLM handles script and code-mixing downstream.
    """
    return text


async def normalize_image(image_bytes: bytes, mime_type: str, caption: str | None) -> str | None:
    """Single multimodal call: OCR the image and combine with any caption.

    Returns None when there's genuinely nothing to check -- no readable text
    and no caption -- so the caller can reply gracefully instead of running
    the rest of the pipeline on an empty string. Per the plan, this is one
    call, not a separate OCR stage: folding extraction into the same call
    that reads the image avoids doubling the Gemini quota cost per image.
    """
    input_data = build_image_input(_PROMPT_IMAGE, image_bytes, mime_type)
    data = await generate_json(input_data, _SCHEMA_IMAGE)

    has_text = bool(data.get("has_readable_text", False))
    extracted = (data.get("extracted_text") or "").strip()

    parts = []
    if has_text and extracted:
        parts.append(extracted)
    if caption and caption.strip():
        parts.append(caption.strip())

    combined = "\n".join(parts).strip()
    return combined or None
