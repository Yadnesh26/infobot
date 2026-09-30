import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.pipeline.guard import sanitize_output, wrap_untrusted
from app.providers.gemini import generate_json

_PROMPT = (Path(__file__).parent.parent / "prompts" / "extract_classify.txt").read_text(encoding="utf-8")

KINDS = (
    "claims",
    "greeting",
    "question_about_bot",
    "opinion_or_prediction",
    "personal_or_private",
    "out_of_scope_request",
    "health_advice_request",
    "media_authenticity",
    "unclear",
    "abusive_or_manipulation",
)
TIERS = ("t1", "t2", "t3a", "t3b")

_CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claim_original": {"type": "string"},
        "claim_english": {"type": "string"},
        "language": {"type": "string"},
        "search_query": {"type": "string"},
        "search_query_original": {"type": "string"},
        "tier": {"type": "string", "enum": list(TIERS)},
        "domain": {"type": "string", "enum": ["health", "science", "news", "finance", "social", "other"]},
    },
    "required": ["claim_original", "claim_english", "language", "tier", "domain"],
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "detected_language": {"type": "string"},
        "input_kind": {"type": "string", "enum": list(KINDS)},
        "claims": {"type": "array", "items": _CLAIM_SCHEMA},
        "more_claims_omitted": {"type": "boolean"},
        "context_summary": {"type": "string"},
        "friendly_reply": {"type": "string"},
    },
    "required": ["detected_language", "input_kind", "claims", "friendly_reply"],
}


@dataclass
class Claim:
    claim_original: str
    claim_english: str
    language: str
    tier: str
    domain: str = "other"
    search_query: str = ""
    search_query_original: str = ""


@dataclass
class ClassifyResult:
    detected_language: str
    input_kind: str
    claims: list[Claim] = field(default_factory=list)
    context_summary: str = ""
    friendly_reply: str = ""
    more_claims_omitted: bool = False

    # Single-claim view of the result, for callers that only care about the lead claim.
    @property
    def is_verifiable_claim(self) -> bool:
        return self.input_kind == "claims" and bool(self.claims)

    @property
    def tier(self) -> str:
        return self.claims[0].tier if self.claims else "t1"

    @property
    def claim_english(self) -> str:
        return self.claims[0].claim_english if self.claims else ""

    @property
    def claim_original(self) -> str:
        return self.claims[0].claim_original if self.claims else ""

    @property
    def domain(self) -> str:
        return self.claims[0].domain if self.claims else "other"

    @property
    def search_query(self) -> str:
        return self.claims[0].search_query if self.claims else ""

    @property
    def search_query_original(self) -> str:
        return self.claims[0].search_query_original if self.claims else ""


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def _parse_claims(raw_claims: list, detected_language: str, frequently_forwarded: bool) -> list[Claim]:
    claims: list[Claim] = []
    seen: set[str] = set()
    for c in raw_claims or []:
        if not isinstance(c, dict):
            continue
        # The model occasionally fills only one of the two sentence fields. Neither
        # search nor verification can work from an empty string, so use the other.
        original = (c.get("claim_original") or "").strip()
        english = (c.get("claim_english") or "").strip()
        english = english or original
        original = original or english
        if not english:
            continue
        key = _norm(english)
        if key in seen:
            continue
        seen.add(key)
        tier = c.get("tier") if c.get("tier") in TIERS else "t2"
        domain = c.get("domain") or "other"
        if tier == "t3a" and domain != "health":
            # Soft medical guidance only makes sense for health claims. A folk
            # story or an everyday superstition labelled t3a would just be
            # escalated to the doctor hard stop, which helps nobody; search it.
            tier = "t2"
        if frequently_forwarded and tier == "t1":
            tier = "t2"  # high virality justifies the extra scrutiny/search cost
        claims.append(
            Claim(
                claim_original=original,
                claim_english=english,
                language=(c.get("language") or detected_language or "en").strip(),
                tier=tier,
                domain=domain,
                search_query=(c.get("search_query") or "").strip(),
                search_query_original=(c.get("search_query_original") or "").strip(),
            )
        )
    return claims


async def extract_and_classify(raw_text: str, frequently_forwarded: bool) -> ClassifyResult:
    prompt = f"{_PROMPT}\n\n{wrap_untrusted(raw_text)}"
    data = await generate_json(prompt, _SCHEMA)

    detected = (data.get("detected_language") or "en").strip()
    kind = data.get("input_kind") if data.get("input_kind") in KINDS else "claims"
    raw_claims = data.get("claims") or []
    claims = _parse_claims(raw_claims, detected, frequently_forwarded)

    omitted = bool(data.get("more_claims_omitted", False))
    if len(claims) > settings.MAX_CLAIMS_PER_MESSAGE:
        claims = claims[: settings.MAX_CLAIMS_PER_MESSAGE]
        omitted = True

    # Trust the claims over the label: a message the model called "opinion" that
    # still yielded a checkable claim should get that claim checked, and a
    # "claims" message with nothing usable in it is simply unclear.
    if claims:
        kind = "claims"
    elif kind == "claims":
        kind = "unclear"

    return ClassifyResult(
        detected_language=detected,
        input_kind=kind,
        claims=claims,
        context_summary=sanitize_output(data.get("context_summary") or "", 200),
        friendly_reply=sanitize_output(data.get("friendly_reply") or "", 420),
        more_claims_omitted=omitted,
    )
