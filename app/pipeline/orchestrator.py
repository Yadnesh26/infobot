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
from app.pipeline.guard import MAX_INPUT_CHARS, clean_text, guess_language, screen_text
from app.pipeline.messages import UNCLEAR, pick
from app.pipeline.normalize import normalize_text
from app.pipeline.verify import translate_texts, verify_t1, verify_t2, verify_t3a
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


async def _none() -> None:
    return None


def _language_of_text(target: str, english: str, local: str) -> str:
    """Which language `local` is really in. A model that failed to translate hands
    back the English text, and labelling that as Hindi would mix the two."""
    return target if (target == "en" or local != english) else "en"


async def _outcome_from_row(claim: Claim, row: dict, hit: str, target: str) -> ClaimOutcome:
    """A cached answer, translated for the user if they read another language
    (the cache stores English only)."""
    explanation = row.get("explanation_en") or ""
    local = explanation
    if target != "en" and explanation and row.get("verdict") != "refused":
        local = (await translate_texts([explanation], target))[0]
    return ClaimOutcome(
        claim=claim,
        tier=row.get("tier") or claim.tier,
        verdict=row.get("verdict", ""),
        confidence=row.get("confidence"),
        explanation_en=explanation,
        explanation_local=local,
        sources=row.get("sources") or [],
        cache_hit=hit,
        local_lang=_language_of_text(target, explanation, local),
    )


async def _verify_claim(claim: Claim, reply_lang: str | None = None) -> ClaimOutcome:
    tier = claim.tier
    lang = claim.language
    target = reply_lang or lang
    if tier == "t1":
        v = await verify_t1(claim.claim_english, claim.claim_original, lang, reply_lang)
        o = ClaimOutcome(claim, tier, v.verdict, v.confidence, v.explanation_english, v.explanation_original_language, [])
    elif tier == "t2":
        v = await verify_t2(
            claim.claim_english, claim.claim_original, lang, claim.search_query, claim.search_query_original, reply_lang
        )
        o = ClaimOutcome(claim, tier, v.verdict, v.confidence, v.explanation_english, v.explanation_original_language, v.sources)
    elif tier == "t3a":
        t = await verify_t3a(claim.claim_english, claim.claim_original, lang, reply_lang)
        if t.needs_escalation:
            # Runtime self-test per the plan: classify-time tier assignment can be
            # wrong, and a t3a answer that turns out to depend on the person's
            # age/conditions/medications must never be forced out anyway.
            return ClaimOutcome(claim, "t3b", "refused")
        o = ClaimOutcome(claim, tier, "guidance", None, t.guidance_english, t.guidance_original_language, [])
    else:
        return ClaimOutcome(claim, "t3b", "refused")
    o.local_lang = _language_of_text(target, o.explanation_en, o.explanation_local)
    return o


async def _resolve_claim(
    claim: Claim, single: bool, text_hash: str, cacheable: bool, reply_lang: str | None = None
) -> ClaimOutcome:
    """Cache lookup, then verification, for one claim. For a one-claim message
    the claim is stored under the whole message's hash so an identical forward
    is answered before classification; for several claims each is keyed by its
    own English text."""
    claim_hash = text_hash if single else db_cache.hash_claim(claim.claim_english)
    target = reply_lang or claim.language or "en"

    # The embedding (needed for the semantic lookup and for a cache write) is requested while
    # the exact lookup is still in flight. A single claim was already looked up by its message
    # hash before classification, and missed, so that lookup is not repeated.
    embed_task = asyncio.create_task(_embed(claim.claim_english))
    row = None if single else await db_cache.lookup_exact(claim_hash)
    if row:
        embed_task.cancel()
        await db_cache.increment_seen(row["id"])
        return await _outcome_from_row(claim, row, "exact", target)

    embedding = await embed_task
    if embedding is not None:
        row = await db_cache.lookup_semantic(embedding, settings.SEMANTIC_MATCH_THRESHOLD)
        if row:
            await db_cache.increment_seen(row["id"])
            return await _outcome_from_row(claim, row, "semantic", target)

    outcome = await _verify_claim(claim, reply_lang)

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


async def run_text_pipeline(
    raw_text: str, frequently_forwarded: bool, allow_cache: bool = True, reply_lang: str | None = None
) -> PipelineResult:
    """reply_lang is the user's chosen reply language (en/hi/mr) or None; when
    None, each answer follows the language of the claim it answers."""
    text = normalize_text(raw_text)

    # The injection screen and the exact-cache lookup are independent, so they run together;
    # a blocked message is still refused whatever the cache holds. (The screen cleans the text
    # first, so the lookup is keyed on the same cleaned text.)
    cleaned = clean_text(text)[0][:MAX_INPUT_CHARS].strip()
    pre_hash = db_cache.hash_claim(cleaned) if cleaned else None
    guard, exact = await asyncio.gather(
        screen_text(text), db_cache.lookup_exact(pre_hash) if pre_hash else _none()
    )
    meta: dict = {"suspicious": guard.suspicious, "guard_score": round(guard.guard_score, 3), "reply_lang": reply_lang}
    if guard.blocked:
        logger.warning("Blocked input (%s)", guard.reason)
        meta.update(blocked=guard.reason, input_kind="abusive_or_manipulation")
        return PipelineResult(compose_blocked_reply(guard.text, reply_lang), meta=meta)
    if not guard.text:
        meta["input_kind"] = "unclear"
        return PipelineResult(pick(UNCLEAR, reply_lang or "en"), meta=meta)

    cacheable = allow_cache and not guard.suspicious
    text = guard.text
    text_hash = db_cache.hash_claim(text)

    # Identical forward already answered: skip classification entirely.
    if text_hash != pre_hash:  # defensive: the screen never changes what it cleaned, but never trust that silently
        exact = await db_cache.lookup_exact(text_hash)
    if exact:
        await db_cache.increment_seen(exact["id"])
        meta.update(input_kind="claims", n_claims=1)
        # Classification was skipped, so the only clue to the language is the user's
        # choice, or else the script of what they sent.
        target = reply_lang or guess_language(text)
        if target != "en" and exact.get("verdict") != "refused":
            original = [exact.get("claim_text_en") or "", exact.get("explanation_en") or ""]
            headline, explanation = await translate_texts(original, target)
            if explanation == original[1]:
                target = "en"  # translation failed: English text gets English labels, never a mix
            exact = {**exact, "claim_text_en": headline, "explanation_en": explanation}
        else:
            target = "en"
        return PipelineResult(compose_cached_reply(exact, frequently_forwarded, target), cache_hit="exact", meta=meta)

    classify: ClassifyResult = await extract_and_classify(text, frequently_forwarded, reply_lang)
    meta.update(
        input_kind=classify.input_kind,
        n_claims=len(classify.claims),
        tiers=[c.tier for c in classify.claims],
        language=classify.detected_language,
        model_reply=bool(classify.friendly_reply),
    )

    if not classify.is_verifiable_claim:
        return PipelineResult(compose_nonclaim_reply(classify, reply_lang), meta=meta)

    single = len(classify.claims) == 1
    results = await asyncio.gather(
        *[_resolve_claim(c, single, text_hash, cacheable, reply_lang) for c in classify.claims],
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
        compose_claims_reply(outcomes, classify, frequently_forwarded, reply_lang),
        cache_hit=outcomes[0].cache_hit if all_cached and len(hits) == 1 else None,
        pending_claim_writes=[o.write for o in outcomes if o.write],
        meta=meta,
    )
