from dataclasses import dataclass
from pathlib import Path

from app.providers.gemini import generate_json

_PROMPT = (Path(__file__).parent.parent / "prompts" / "extract_classify.txt").read_text(encoding="utf-8")

_SCHEMA = {
    "type": "object",
    "properties": {
        "detected_language": {"type": "string"},
        "is_verifiable_claim": {"type": "boolean"},
        "claim_original": {"type": "string"},
        "claim_english": {"type": "string"},
        "search_query": {"type": "string"},
        "search_query_original": {"type": "string"},
        "tier": {"type": "string", "enum": ["t1", "t2", "t3a", "t3b"]},
        "domain": {
            "type": "string",
            "enum": ["health", "science", "news", "finance", "social", "other"],
        },
    },
    "required": ["detected_language", "is_verifiable_claim", "tier", "domain"],
}


@dataclass
class ClassifyResult:
    detected_language: str
    is_verifiable_claim: bool
    claim_original: str
    claim_english: str
    tier: str
    domain: str
    search_query: str = ""
    search_query_original: str = ""


async def extract_and_classify(raw_text: str, frequently_forwarded: bool) -> ClassifyResult:
    prompt = f"{_PROMPT}\n\nMessage:\n{raw_text}"
    data = await generate_json(prompt, _SCHEMA)

    tier = data.get("tier", "t1")
    if frequently_forwarded and tier == "t1":
        tier = "t2"  # high virality justifies the extra scrutiny/search cost

    # The model occasionally lets its own hedging leak into one field and leaves
    # the other blank, especially on ambiguous/self-correcting claims. Neither
    # downstream search nor verification can work from an empty string, so fall
    # back to whichever field it did fill in rather than searching for "".
    claim_original = data.get("claim_original") or ""
    claim_english = data.get("claim_english") or ""
    if not claim_english:
        claim_english = claim_original
    if not claim_original:
        claim_original = claim_english

    return ClassifyResult(
        detected_language=data.get("detected_language", ""),
        is_verifiable_claim=bool(data.get("is_verifiable_claim", False)),
        claim_original=claim_original,
        claim_english=claim_english,
        tier=tier,
        domain=data.get("domain", "other"),
        search_query=(data.get("search_query") or "").strip(),
        search_query_original=(data.get("search_query_original") or "").strip(),
    )
