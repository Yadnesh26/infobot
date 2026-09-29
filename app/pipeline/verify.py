from dataclasses import dataclass
from pathlib import Path

from app.providers.gemini import generate_json

_PROMPT = (Path(__file__).parent.parent / "prompts" / "verify_t1.txt").read_text(encoding="utf-8")

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["true", "false", "misleading", "unverifiable"]},
        "model_is_confident": {"type": "boolean"},
        "explanation_english": {"type": "string"},
        "explanation_original_language": {"type": "string"},
    },
    "required": ["verdict", "model_is_confident", "explanation_english"],
}


@dataclass
class VerifyResult:
    verdict: str
    confidence: str | None
    explanation_english: str
    explanation_original_language: str


async def verify_t1(claim_english: str, claim_original: str, detected_language: str) -> VerifyResult:
    prompt = (
        f"{_PROMPT}\n\n"
        f"Claim (English): {claim_english}\n"
        f"Claim (original, language={detected_language}): {claim_original}"
    )
    data = await generate_json(prompt, _SCHEMA)

    verdict = data.get("verdict", "unverifiable")
    if verdict == "unverifiable":
        confidence = None
    else:
        # Structural cap, not self-reported: Tier 1 has no retrieval, so it never
        # reaches "High" no matter how certain the model claims to be.
        confidence = "Medium" if data.get("model_is_confident") else "Low"

    explanation_english = data.get("explanation_english", "")
    return VerifyResult(
        verdict=verdict,
        confidence=confidence,
        explanation_english=explanation_english,
        explanation_original_language=data.get("explanation_original_language") or explanation_english,
    )
