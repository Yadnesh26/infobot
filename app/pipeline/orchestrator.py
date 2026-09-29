from app.pipeline.classify import extract_and_classify
from app.pipeline.compose import (
    compose_not_a_claim_reply,
    compose_t1_reply,
    compose_unsupported_tier_reply,
)
from app.pipeline.normalize import normalize_text
from app.pipeline.verify import verify_t1


async def run_text_pipeline(raw_text: str, frequently_forwarded: bool) -> str:
    text = normalize_text(raw_text)
    classify = await extract_and_classify(text, frequently_forwarded)

    if not classify.is_verifiable_claim:
        return compose_not_a_claim_reply()

    if classify.tier != "t1":
        return compose_unsupported_tier_reply(classify.tier)

    verify = await verify_t1(classify.claim_english, classify.claim_original, classify.detected_language)
    return compose_t1_reply(classify, verify, frequently_forwarded)
