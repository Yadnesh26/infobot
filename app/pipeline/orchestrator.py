import logging
from dataclasses import dataclass

from app.config import settings
from app.db import cache as db_cache
from app.pipeline.classify import extract_and_classify
from app.pipeline.compose import (
    compose_cached_reply,
    compose_not_a_claim_reply,
    compose_t1_reply,
    compose_t2_reply,
    compose_t3a_reply,
    compose_t3b_reply,
    compose_unsupported_tier_reply,
)
from app.pipeline.normalize import normalize_text
from app.pipeline.verify import verify_t1, verify_t2, verify_t3a
from app.providers.gemini import embed_text

logger = logging.getLogger("infobot.pipeline")


@dataclass
class PipelineResult:
    reply_text: str
    cache_hit: str | None = None  # None | "exact" | "semantic"
    pending_claim_write: dict | None = None


async def run_text_pipeline(raw_text: str, frequently_forwarded: bool) -> PipelineResult:
    text = normalize_text(raw_text)
    claim_hash = db_cache.hash_claim(text)

    exact = await db_cache.lookup_exact(claim_hash)
    if exact:
        await db_cache.increment_seen(exact["id"])
        return PipelineResult(compose_cached_reply(exact, frequently_forwarded), cache_hit="exact")

    # The semantic cache is an optimization, not a requirement: if embedding
    # fails (Gemini quota/outage), skip it and carry on to a real check rather
    # than failing the whole request. The claim is then cached by exact hash only.
    embedding: list[float] | None
    try:
        embedding = await embed_text(text)
    except Exception:
        logger.warning("Embedding failed; skipping semantic cache lookup", exc_info=True)
        embedding = None

    if embedding is not None:
        semantic = await db_cache.lookup_semantic(embedding, settings.SEMANTIC_MATCH_THRESHOLD)
        if semantic:
            await db_cache.increment_seen(semantic["id"])
            return PipelineResult(compose_cached_reply(semantic, frequently_forwarded), cache_hit="semantic")

    classify = await extract_and_classify(text, frequently_forwarded)

    if not classify.is_verifiable_claim:
        return PipelineResult(compose_not_a_claim_reply())

    tier = classify.tier
    verdict: str
    confidence: int | None = None
    explanation_en = ""
    sources: list[dict] = []

    if tier == "t1":
        verify = await verify_t1(classify.claim_english, classify.claim_original, classify.detected_language)
        reply = compose_t1_reply(classify, verify, frequently_forwarded)
        verdict, confidence, explanation_en, sources = (
            verify.verdict,
            verify.confidence,
            verify.explanation_english,
            verify.sources,
        )
    elif tier == "t2":
        verify = await verify_t2(classify.claim_english, classify.claim_original, classify.detected_language)
        reply = compose_t2_reply(classify, verify, frequently_forwarded)
        verdict, confidence, explanation_en, sources = (
            verify.verdict,
            verify.confidence,
            verify.explanation_english,
            verify.sources,
        )
    elif tier == "t3a":
        t3a = await verify_t3a(classify.claim_english, classify.claim_original, classify.detected_language)
        if t3a.needs_escalation:
            # Runtime self-test per the plan: classify-time tier assignment can be
            # wrong, and a t3a answer that turns out to depend on the person's
            # age/conditions/medications must never be forced out anyway.
            tier = "t3b"
            reply = compose_t3b_reply()
            verdict = "refused"
        else:
            reply = compose_t3a_reply(t3a, frequently_forwarded)
            verdict = "guidance"
            explanation_en = t3a.guidance_english
    elif tier == "t3b":
        reply = compose_t3b_reply()
        verdict = "refused"
    else:
        return PipelineResult(compose_unsupported_tier_reply(tier))

    # Don't cache "unverifiable" -- unlike a real verdict, it's a statement about
    # today's available sources, not the claim itself. Especially for T2, a
    # breaking rumor's sources improve over time; caching the gap would make it
    # permanent instead of letting the next forward try again. "guidance" and
    # "refused" (t3a/t3b) are cacheable -- the schema's verdict column was
    # designed for exactly these two values.
    pending_write = None
    if verdict != "unverifiable":
        pending_write = {
            "claim_text_en": classify.claim_english,
            "claim_hash": claim_hash,
            "embedding": embedding,
            "tier": tier,
            "verdict": verdict,
            "confidence": confidence,
            "explanation_en": explanation_en,
            "sources": sources,
        }
    return PipelineResult(reply, pending_claim_write=pending_write)
