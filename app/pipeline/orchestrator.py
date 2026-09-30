import asyncio
import logging
from dataclasses import dataclass, field

from app.config import settings
from app.db import cache as db_cache
from app.pipeline.classify import Claim, ClassifyResult, extract_and_classify
from app.pipeline.compose import (
    ClaimOutcome,
    compose_blocked_reply,
    compose_cached_reply,
    compose_claims_reply,
    compose_nonclaim_reply,
)
from app.pipeline.guard import screen_text
from app.pipeline.messages import UNCLEAR, pick
from app.pipeline.normalize import normalize_text
from app.pipeline.verify import verify_t1, verify_t2, verify_t3a
from app.providers.gemini import embed_text

logger = logging.getLogger("infobot.pipeline")


@dataclass
class PipelineResult:
    reply_text: str
    cache_hit: str | None = None  # None | "exact" | "semantic" (all claims cached)
    pending_claim_writes: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # for logs and the scenario harness

    @property
    def pending_claim_write(self) -> dict | None:
        """First pending write -- kept for callers written for one claim."""
        return self.pending_claim_writes[0] if self.pending_claim_writes else None


async def _embed(text: str) -> list[float] | None:
    # The semantic cache is an optimization, not a requirement: if embedding
    # fails (Gemini quota/outage), skip it and carry on to a real check.
    try:
        return await embed_text(text)
    except Exception:
        logger.warning("Embedding failed; skipping semantic cache", exc_info=True)
        return None


async def _cached_outcome(text_hash: str | None, embedding: list[float] | None) -> tuple[dict | None, str | None]:
    row = await db_cache.lookup_exact(text_hash) if text_hash else None
    if row:
        await db_cache.increment_seen(row["id"])
        return row, "exact"
    if embedding is not None:
        row = await db_cache.lookup_semantic(embedding, settings.SEMANTIC_MATCH_THRESHOLD)
        if row:
            await db_cache.increment_seen(row["id"])
            return row, "semantic"
    return None, None


def _outcome_from_row(claim: Claim, row: dict, hit: str) -> ClaimOutcome:
    explanation = row.get("explanation_en") or ""
    return ClaimOutcome(
        claim=claim,
        tier=row.get("tier") or claim.tier,
        verdict=row.get("verdict", ""),
        confidence=row.get("confidence"),
        explanation_en=explanation,
        explanation_local=explanation,
        sources=row.get("sources") or [],
        cache_hit=hit,
    )


async def _verify_claim(claim: Claim) -> ClaimOutcome:
    tier = claim.tier
    lang = claim.language
    if tier == "t1":
        v = await verify_t1(claim.claim_english, claim.claim_original, lang)
        return ClaimOutcome(claim, tier, v.verdict, v.confidence, v.explanation_english, v.explanation_original_language, [])
    if tier == "t2":
        v = await verify_t2(claim.claim_english, claim.claim_original, lang, claim.search_query, claim.search_query_original)
        return ClaimOutcome(claim, tier, v.verdict, v.confidence, v.explanation_english, v.explanation_original_language, v.sources)
    if tier == "t3a":
        t = await verify_t3a(claim.claim_english, claim.claim_original, lang)
        if t.needs_escalation:
            # Runtime self-test per the plan: classify-time tier assignment can be
            # wrong, and a t3a answer that turns out to depend on the person's
            # age/conditions/medications must never be forced out anyway.
            return ClaimOutcome(claim, "t3b", "refused")
        return ClaimOutcome(claim, tier, "guidance", None, t.guidance_english, t.guidance_original_language, [])
    return ClaimOutcome(claim, "t3b", "refused")


async def _resolve_claim(claim: Claim, single: bool, text_hash: str, cacheable: bool) -> ClaimOutcome:
    """Cache lookup, then verification, for one claim. For a one-claim message
    the claim is stored under the whole message's hash so an identical forward
    is answered before classification; for several claims each is keyed by its
    own English text."""
    claim_hash = text_hash if single else db_cache.hash_claim(claim.claim_english)
    embedding = await _embed(claim.claim_english)

    row, hit = await _cached_outcome(claim_hash, embedding)
    if row:
        return _outcome_from_row(claim, row, hit)

    outcome = await _verify_claim(claim)

    # Don't cache "unverifiable" -- unlike a real verdict, it's a statement about
    # today's available sources, not the claim itself. "guidance" and "refused"
    # (t3a/t3b) are cacheable. Nothing suspicious is ever cached: a poisoned
    # entry would be served to every user who later sends that claim.
    if cacheable and outcome.verdict not in ("unverifiable", "error"):
        outcome.write = {
            "claim_text_en": claim.claim_english,
            "claim_hash": claim_hash,
            "embedding": embedding,
            "tier": outcome.tier,
            "verdict": outcome.verdict,
            "confidence": outcome.confidence,
            "explanation_en": outcome.explanation_en,
            "sources": outcome.sources,
        }
    return outcome


async def run_text_pipeline(raw_text: str, frequently_forwarded: bool, allow_cache: bool = True) -> PipelineResult:
    text = normalize_text(raw_text)

    guard = await screen_text(text)
    meta: dict = {"suspicious": guard.suspicious, "guard_score": round(guard.guard_score, 3)}
    if guard.blocked:
        logger.warning("Blocked input (%s)", guard.reason)
        meta.update(blocked=guard.reason, input_kind="abusive_or_manipulation")
        return PipelineResult(compose_blocked_reply(guard.text), meta=meta)
    if not guard.text:
        meta["input_kind"] = "unclear"
        return PipelineResult(pick(UNCLEAR, "en"), meta=meta)

    cacheable = allow_cache and not guard.suspicious
    text = guard.text
    text_hash = db_cache.hash_claim(text)

    # Identical forward already answered: skip classification entirely.
    exact = await db_cache.lookup_exact(text_hash)
    if exact:
        await db_cache.increment_seen(exact["id"])
        meta.update(input_kind="claims", n_claims=1)
        return PipelineResult(compose_cached_reply(exact, frequently_forwarded), cache_hit="exact", meta=meta)

    classify: ClassifyResult = await extract_and_classify(text, frequently_forwarded)
    meta.update(
        input_kind=classify.input_kind,
        n_claims=len(classify.claims),
        tiers=[c.tier for c in classify.claims],
        language=classify.detected_language,
        model_reply=bool(classify.friendly_reply),
    )

    if not classify.is_verifiable_claim:
        return PipelineResult(compose_nonclaim_reply(classify), meta=meta)

    single = len(classify.claims) == 1
    results = await asyncio.gather(
        *[_resolve_claim(c, single, text_hash, cacheable) for c in classify.claims],
        return_exceptions=True,
    )

    outcomes: list[ClaimOutcome] = []
    for claim, res in zip(classify.claims, results):
        if isinstance(res, BaseException):
            if isinstance(res, asyncio.CancelledError):
                raise res
            logger.error("Claim verification failed: %r", res)
            res = ClaimOutcome(claim, claim.tier, "error")
        outcomes.append(res)

    if all(o.verdict == "error" for o in outcomes):
        # Nothing to show: let the caller apologise and record the error.
        first = next(r for r in results if isinstance(r, BaseException))
        raise first

    meta["verdicts"] = [o.verdict for o in outcomes]
    meta["cached"] = [o.cache_hit for o in outcomes]
    hits = {o.cache_hit for o in outcomes}
    all_cached = None not in hits
    return PipelineResult(
        compose_claims_reply(outcomes, classify, frequently_forwarded),
        cache_hit=outcomes[0].cache_hit if all_cached and len(hits) == 1 else None,
        pending_claim_writes=[o.write for o in outcomes if o.write],
        meta=meta,
    )
