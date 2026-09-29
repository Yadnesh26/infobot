from app.pipeline.classify import ClassifyResult
from app.pipeline.verify import VerifyResult

_VERDICT_LABELS = {
    "true": "True",
    "false": "False",
    "misleading": "Misleading",
    "unverifiable": "Unverifiable",
}


def compose_not_a_claim_reply() -> str:
    return (
        "This doesn't look like a factual claim to check (looks like a greeting "
        "or personal message) -- nothing to verify here."
    )


def compose_unsupported_tier_reply(tier: str) -> str:
    return (
        f"This claim needs deeper checking (classified as {tier}) that this test "
        "version doesn't support yet. Full support for this type of claim is "
        "coming in a later build."
    )


def compose_t1_reply(classify: ClassifyResult, verify: VerifyResult, frequently_forwarded: bool) -> str:
    verdict_label = _VERDICT_LABELS.get(verify.verdict, verify.verdict)
    lines = [f"\U0001f50d Verdict: {verdict_label}"]
    if verify.confidence:
        lines.append(f"Confidence: {verify.confidence}")
    lines.append("")
    lines.append(verify.explanation_original_language or verify.explanation_english)
    if frequently_forwarded:
        lines.append("")
        lines.append("This message has been forwarded many times.")
    lines.append("")
    lines.append("— Verified by InfoBot (general-knowledge check only, no sources searched yet)")
    return "\n".join(lines)
