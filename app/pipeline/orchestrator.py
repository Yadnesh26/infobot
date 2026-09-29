from dataclasses import dataclass

from app.config import settings
from app.db import cache as db_cache
from app.pipeline.classify import extract_and_classify
from app.pipeline.compose import (
    compose_cached_reply,
    compose_not_a_claim_reply,
    compose_t1_reply,
    compose_unsupported_tier_reply,
)
from app.pipeline.normalize import normalize_text
from app.pipeline.verify import verify_t1
from app.providers.gemini import embed_text


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

    embedding = await embed_text(text)
    semantic = await db_cache.lookup_semantic(embedding, settings.SEMANTIC_MATCH_THRESHOLD)
    if semantic:
        await db_cache.increment_seen(semantic["id"])
        return PipelineResult(compose_cached_reply(semantic, frequently_forwarded), cache_hit="semantic")

    classify = await extract_and_classify(text, frequently_forwarded)

    if not classify.is_verifiable_claim:
        return PipelineResult(compose_not_a_claim_reply())

    if classify.tier != "t1":
        return PipelineResult(compose_unsupported_tier_reply(classify.tier))

    verify = await verify_t1(classify.claim_english, classify.claim_original, classify.detected_language)
    reply = compose_t1_reply(classify, verify, frequently_forwarded)

    pending_write = {
        "claim_text_en": classify.claim_english,
        "claim_hash": claim_hash,
        "embedding": embedding,
        "tier": classify.tier,
        "verdict": verify.verdict,
        "confidence": verify.confidence,
        "explanation_en": verify.explanation_english,
    }
    return PipelineResult(reply, pending_claim_write=pending_write)
