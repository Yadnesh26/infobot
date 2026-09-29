from app.pipeline.classify import ClassifyResult
from app.pipeline.confidence import confidence_label
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
    label = confidence_label(verify.confidence)
    if label:
        lines.append(f"Confidence: {label}")
    lines.append("")
    lines.append(verify.explanation_original_language or verify.explanation_english)
    if frequently_forwarded:
        lines.append("")
        lines.append("This message has been forwarded many times.")
    lines.append("")
    lines.append("— Verified by InfoBot (general-knowledge check only, no sources searched yet)")
    return "\n".join(lines)


def compose_cached_reply(row: dict, frequently_forwarded: bool) -> str:
    """Same shape as compose_t1_reply, built from a claims row read back from
    the cache. Only an English explanation is stored (matches the committed
    schema), so cache hits reply in English even when the original forward
    wasn't -- fresh T1 replies stay bilingual. Worth reconciling later if it
    proves confusing in practice.
    """
    verdict_label = _VERDICT_LABELS.get(row.get("verdict", ""), row.get("verdict", ""))
    lines = [f"\U0001f50d Verdict: {verdict_label}"]
    label = confidence_label(row.get("confidence"))
    if label:
        lines.append(f"Confidence: {label}")
    lines.append("")
    lines.append(row.get("explanation_en") or "")
    if frequently_forwarded:
        lines.append("")
        lines.append("This message has been forwarded many times.")
    lines.append("")
    lines.append("— Verified by InfoBot (general-knowledge check only, no sources searched yet)")
    return "\n".join(lines)
