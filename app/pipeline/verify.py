from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.providers.gemini import generate_json
from app.providers.tavily import search_for_claim

_PROMPT_T1 = (Path(__file__).parent.parent / "prompts" / "verify_t1.txt").read_text(encoding="utf-8")
_PROMPT_T2 = (Path(__file__).parent.parent / "prompts" / "verify_t2.txt").read_text(encoding="utf-8")

_SCHEMA_T1 = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["true", "false", "misleading", "unverifiable"]},
        "model_is_confident": {"type": "boolean"},
        "explanation_english": {"type": "string"},
        "explanation_original_language": {"type": "string"},
    },
    "required": ["verdict", "model_is_confident", "explanation_english"],
}

_SCHEMA_T2 = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["true", "false", "misleading", "unverifiable"]},
        "used_sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "supports_verdict": {"type": "boolean"},
                },
                "required": ["url", "supports_verdict"],
            },
        },
        "explanation_english": {"type": "string"},
        "explanation_original_language": {"type": "string"},
    },
    "required": ["verdict", "used_sources", "explanation_english"],
}


@dataclass
class VerifyResult:
    verdict: str
    confidence: int | None  # 0-100, matches the claims.confidence schema column
    explanation_english: str
    explanation_original_language: str
    sources: list[dict] = field(default_factory=list)  # [{"title": ..., "url": ...}]


async def verify_t1(claim_english: str, claim_original: str, detected_language: str) -> VerifyResult:
    prompt = (
        f"{_PROMPT_T1}\n\n"
        f"Claim (English): {claim_english}\n"
        f"Claim (original, language={detected_language}): {claim_original}"
    )
    data = await generate_json(prompt, _SCHEMA_T1)

    verdict = data.get("verdict", "unverifiable")
    if verdict == "unverifiable":
        confidence = None
    else:
        # Structural cap, not self-reported: Tier 1 has no retrieval, so its
        # numeric confidence is pinned well below the "High" bucket (>=70)
        # regardless of how certain the model claims to be.
        confidence = 60 if data.get("model_is_confident") else 25

    explanation_english = data.get("explanation_english", "")
    return VerifyResult(
        verdict=verdict,
        confidence=confidence,
        explanation_english=explanation_english,
        explanation_original_language=data.get("explanation_original_language") or explanation_english,
    )


def _domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except ValueError:
        return url


def _derive_t2_confidence(used_sources: list[dict]) -> int | None:
    """Structural, not self-reported: derived from how many *independent*
    sources (distinct domains, as a cheap proxy -- this won't catch two
    outlets syndicating the same wire copy) agree with the stated verdict,
    per the plan's confidence table. Never asks the model for a number.
    """
    supporting_domains = {_domain(s["url"]) for s in used_sources if s.get("supports_verdict")}
    contradicting_domains = {_domain(s["url"]) for s in used_sources if not s.get("supports_verdict")}

    if supporting_domains and contradicting_domains:
        return 20  # Low -- sources conflict
    if len(supporting_domains) >= 2:
        return 85  # High -- multiple independent credible sources agree
    if len(supporting_domains) == 1:
        return 55  # Medium -- single credible source
    if contradicting_domains:
        return 20  # nothing actually backs the stated verdict -- treat as Low
    return None  # no usable sources at all


async def verify_t2(claim_english: str, claim_original: str, detected_language: str) -> VerifyResult:
    search_results = await search_for_claim(claim_english, claim_original, detected_language)

    if not search_results:
        return VerifyResult(
            verdict="unverifiable",
            confidence=None,
            explanation_english="No credible sources could be found to check this claim.",
            explanation_original_language="No credible sources could be found to check this claim.",
            sources=[],
        )

    results_block = "\n".join(
        f"- title: {r.get('title', '')}\n  url: {r.get('url', '')}\n  content: {r.get('content', '')[:500]}"
        for r in search_results
    )
    prompt = (
        f"{_PROMPT_T2}\n\n"
        f"Claim (English): {claim_english}\n"
        f"Claim (original, language={detected_language}): {claim_original}\n\n"
        f"Retrieved results:\n{results_block}"
    )
    data = await generate_json(prompt, _SCHEMA_T2)

    used_sources = data.get("used_sources") or []
    verdict = data.get("verdict", "unverifiable")

    # Safety net: never trust a verdict the model gave with nothing behind it.
    if not used_sources:
        verdict = "unverifiable"

    confidence = _derive_t2_confidence(used_sources) if used_sources else None

    explanation_english = data.get("explanation_english", "")
    return VerifyResult(
        verdict=verdict,
        confidence=confidence,
        explanation_english=explanation_english,
        explanation_original_language=data.get("explanation_original_language") or explanation_english,
        sources=[{"title": s.get("title", ""), "url": s["url"]} for s in used_sources],
    )
