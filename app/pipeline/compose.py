from pathlib import Path

from app.pipeline.classify import ClassifyResult
from app.pipeline.confidence import confidence_label
from app.pipeline.verify import T3aResult, VerifyResult

_VERDICT_LABELS = {
    "true": "True",
    "false": "False",
    "misleading": "Misleading",
    "unverifiable": "Unverifiable",
}

# This is the literal reply text, not an LLM prompt -- T3b is a hard stop that
# must never involve model-generated content. Kept as a text file anyway,
# matching every other message in prompts/, since it's exactly the kind of
# text a team would want to review or localize without touching code.
_T3B_MESSAGE = (Path(__file__).parent.parent / "prompts" / "refuse_t3b.txt").read_text(encoding="utf-8").strip()


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


def _format_sources_block(sources: list[dict]) -> list[str]:
    if not sources:
        return []
    lines = ["", "Sources:"]
    for s in sources:
        title = s.get("title") or s.get("url", "")
        url = s.get("url", "")
        lines.append(f"• {title} — {url}" if title != url else f"• {url}")
    return lines


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


def compose_t2_reply(classify: ClassifyResult, verify: VerifyResult, frequently_forwarded: bool) -> str:
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
    lines.extend(_format_sources_block(verify.sources))
    lines.append("")
    lines.append("— Verified by InfoBot")
    return "\n".join(lines)


def compose_t3a_reply(t3a: T3aResult, frequently_forwarded: bool) -> str:
    """Deliberately shaped nothing like compose_t1/t2_reply -- no verdict header,
    no confidence line -- so it visibly reads as general guidance, not a verdict.
    """
    lines = [t3a.guidance_original_language or t3a.guidance_english]
    if frequently_forwarded:
        lines.append("")
        lines.append("This message has been forwarded many times.")
    lines.append("")
    lines.append("— InfoBot (general guidance only, not a verdict)")
    return "\n".join(lines)


def compose_t3b_reply() -> str:
    """The hard stop. Static and parameter-free on purpose -- same message every
    time, regardless of claim text, language, or forward count. The one path in
    this whole system that must never vary or be generated.
    """
    return _T3B_MESSAGE


def _compose_guidance_from_row(row: dict, frequently_forwarded: bool) -> str:
    lines = [row.get("explanation_en") or ""]
    if frequently_forwarded:
        lines.append("")
        lines.append("This message has been forwarded many times.")
    lines.append("")
    lines.append("— InfoBot (general guidance only, not a verdict)")
    return "\n".join(lines)


def compose_cached_reply(row: dict, frequently_forwarded: bool) -> str:
    """Built from a claims row read back from the cache. Only an English
    explanation is stored (matches the committed schema), so cache hits reply
    in English even when the original forward wasn't -- fresh replies stay
    bilingual. Worth reconciling later if it proves confusing in practice.
    """
    verdict = row.get("verdict", "")

    if verdict == "refused":
        return compose_t3b_reply()
    if verdict == "guidance":
        return _compose_guidance_from_row(row, frequently_forwarded)

    verdict_label = _VERDICT_LABELS.get(verdict, verdict)
    lines = [f"\U0001f50d Verdict: {verdict_label}"]
    label = confidence_label(row.get("confidence"))
    if label:
        lines.append(f"Confidence: {label}")
    lines.append("")
    lines.append(row.get("explanation_en") or "")
    if frequently_forwarded:
        lines.append("")
        lines.append("This message has been forwarded many times.")
    sources = row.get("sources") or []
    lines.extend(_format_sources_block(sources))
    lines.append("")
    if sources:
        lines.append("— Verified by InfoBot")
    else:
        lines.append("— Verified by InfoBot (general-knowledge check only, no sources searched yet)")
    return "\n".join(lines)
