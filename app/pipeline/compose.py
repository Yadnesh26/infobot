from dataclasses import dataclass, field
from pathlib import Path

from app.pipeline import messages
from app.pipeline.classify import Claim, ClassifyResult
from app.pipeline.confidence import confidence_label
from app.pipeline.guard import guess_language, sanitize_output
from app.pipeline.messages import pick
from app.pipeline.verify import T3aResult, VerifyResult

# This is the literal reply text, not an LLM prompt -- T3b is a hard stop that
# must never involve model-generated content. Kept as a text file anyway,
# matching every other message in prompts/, since it's exactly the kind of
# text a team would want to review or localize without touching code.
_T3B_MESSAGE = (Path(__file__).parent.parent / "prompts" / "refuse_t3b.txt").read_text(encoding="utf-8").strip()

WHATSAPP_TEXT_LIMIT = 4096
_SAFE_LIMIT = 3900
_NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


@dataclass
class ClaimOutcome:
    """What happened to one claim: fresh or from cache, verdict or guidance or
    hard stop or failure."""

    claim: Claim
    tier: str
    verdict: str  # true|false|misleading|unverifiable|guidance|refused|error
    confidence: int | None = None
    explanation_en: str = ""
    explanation_local: str = ""
    sources: list[dict] = field(default_factory=list)
    cache_hit: str | None = None  # None | "exact" | "semantic"
    write: dict | None = None


def compose_t3b_reply() -> str:
    """The hard stop. Static and parameter-free on purpose -- same message every
    time, regardless of claim text, language, or forward count. The one path in
    this whole system that must never vary or be generated.
    """
    return _T3B_MESSAGE


# --------------------------------------------------------------------------
# Verdict / guidance bodies (shared by single-claim and multi-claim replies)
# --------------------------------------------------------------------------


def _format_sources_block(sources: list[dict], lang: str, limit: int = 4) -> list[str]:
    if not sources:
        return []
    lines = ["", f"{pick(messages.SOURCES_WORD, lang)}:"]
    for s in sources[:limit]:
        title = sanitize_output(s.get("title") or "", 110) or s.get("url", "")
        url = s.get("url", "")
        lines.append(f"• {title} — {url}" if title != url else f"• {url}")
    return lines


def _verdict_lines(verdict: str, confidence: int | None, explanation: str, sources: list[dict], source_limit: int, lang: str) -> list[str]:
    label = messages.VERDICT_LABEL.get(verdict)
    shown = pick(label, lang) if label else verdict
    lines = [f"\U0001f50d {pick(messages.VERDICT_HEADER, lang)}: {shown}"]
    level = confidence_label(confidence)
    if level:
        lines.append(f"{pick(messages.CONFIDENCE_WORD, lang)}: {pick(messages.CONFIDENCE_LEVEL[level], lang)}")
    lines.append("")
    lines.append(explanation)
    lines.extend(_format_sources_block(sources, lang, source_limit))
    return lines


def _guidance_text(text: str, ff: bool, lang: str) -> str:
    lines = [text]
    if ff:
        lines += ["", pick(messages.FORWARDED, lang)]
    lines += ["", pick(messages.FOOTER_GUIDANCE, lang)]
    return "\n".join(lines)


def _single(verdict, confidence, explanation, sources, ff, general_only: bool, lang: str = "en") -> str:
    lines = _verdict_lines(verdict, confidence, explanation, sources, 4, lang)
    if ff:
        # Keep the note next to the explanation, ahead of the sources.
        marker = f"{pick(messages.SOURCES_WORD, lang)}:"
        idx = lines.index(marker) - 1 if marker in lines else len(lines)
        lines[idx:idx] = ["", pick(messages.FORWARDED, lang)]
    lines.append("")
    lines.append(pick(messages.FOOTER_GENERAL if (general_only and not sources) else messages.FOOTER, lang))
    return "\n".join(lines)


def _lang_of(classify) -> str:
    return getattr(classify, "detected_language", None) or "en"


def compose_t1_reply(classify, verify: VerifyResult, frequently_forwarded: bool) -> str:
    return _single(
        verify.verdict, verify.confidence, verify.explanation_original_language or verify.explanation_english,
        [], frequently_forwarded, True, _lang_of(classify),
    )


def compose_t2_reply(classify, verify: VerifyResult, frequently_forwarded: bool) -> str:
    return _single(
        verify.verdict, verify.confidence, verify.explanation_original_language or verify.explanation_english,
        verify.sources, frequently_forwarded, False, _lang_of(classify),
    )


def compose_t3a_reply(t3a: T3aResult, frequently_forwarded: bool, lang: str = "en") -> str:
    """Deliberately shaped nothing like compose_t1/t2_reply -- no verdict header,
    no confidence line -- so it visibly reads as general guidance, not a verdict.
    """
    return _guidance_text(t3a.guidance_original_language or t3a.guidance_english, frequently_forwarded, lang)


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
        return _guidance_text(row.get("explanation_en") or "", frequently_forwarded, "en")
    sources = row.get("sources") or []
    return _single(verdict, row.get("confidence"), row.get("explanation_en") or "", sources, frequently_forwarded, True)


# --------------------------------------------------------------------------
# One or several claims
# --------------------------------------------------------------------------


def _headline(claim: Claim) -> str:
    text = sanitize_output(claim.claim_original or claim.claim_english, 200)
    return text if len(text) <= 100 else text[:97].rstrip() + "…"


def _lang(o: ClaimOutcome) -> str:
    # A cached answer is stored in English only, so its labels stay English too.
    return "en" if o.cache_hit else (o.claim.language or "en")


def _block(index: int, o: ClaimOutcome) -> list[str]:
    lang = _lang(o)
    lines = [f"{_NUMBER_EMOJI[index]} {_headline(o.claim)}"]
    if o.verdict == "refused":
        lines.append(pick(messages.MEDICAL_SHORT, lang))
    elif o.verdict == "error":
        lines.append(pick(messages.BUSY, lang))
    elif o.verdict == "guidance":
        lines.append(o.explanation_local or o.explanation_en)
    else:
        lines += _verdict_lines(o.verdict, o.confidence, o.explanation_local or o.explanation_en, o.sources, 3, lang)
        if o.tier == "t1" and not o.sources and o.verdict != "unverifiable":
            lines += ["", pick(messages.GENERAL_KNOWLEDGE_NOTE, lang)]
    return lines


def _fit(text: str) -> str:
    if len(text) <= _SAFE_LIMIT:
        return text
    return text[: _SAFE_LIMIT - 1].rstrip() + "…"


def compose_claims_reply(outcomes: list[ClaimOutcome], classify: ClassifyResult, frequently_forwarded: bool) -> str:
    if len(outcomes) == 1:
        o = outcomes[0]
        lang = _lang(o)
        if o.verdict == "refused":
            return compose_t3b_reply()
        if o.verdict == "error":
            return pick(messages.BUSY, lang)
        if o.verdict == "guidance":
            return _guidance_text(o.explanation_local or o.explanation_en, frequently_forwarded, lang)
        return _fit(
            _single(
                o.verdict,
                o.confidence,
                o.explanation_local or o.explanation_en,
                o.sources,
                frequently_forwarded,
                o.tier == "t1" or o.cache_hit is not None,
                lang,
            )
        )

    lang = outcomes[0].claim.language or classify.detected_language
    parts = [pick(messages.MULTI_HEADER, lang).format(n=len(outcomes)), ""]
    for i, o in enumerate(outcomes):
        parts += _block(i, o) + [""]
    if classify.more_claims_omitted:
        parts += [pick(messages.MULTI_OMITTED, lang), ""]
    if frequently_forwarded:
        parts += [pick(messages.FORWARDED, lang), ""]
    parts.append(pick(messages.FOOTER, lang))
    return _fit("\n".join(parts))


# --------------------------------------------------------------------------
# Everything that is not a checkable claim
# --------------------------------------------------------------------------


def compose_nonclaim_reply(classify: ClassifyResult) -> str:
    """Low-rejection replies: say what the message was, why InfoBot won't act on
    it, and what would work instead. The model writes the first part (about this
    specific message, sanitised); a fixed per-kind text stands in if it gave
    none, and the fixed capability line follows."""
    lang = classify.detected_language
    kind = classify.input_kind
    if kind == "health_advice_request":
        return compose_t3b_reply()
    if kind == "media_authenticity":
        return pick(messages.MEDIA_AUTHENTICITY, lang)
    if kind == "abusive_or_manipulation":
        return pick(messages.BLOCKED, lang)
    if kind == "unclear":
        return pick(messages.UNCLEAR, lang)

    fallback = messages.KIND_FALLBACK.get(kind)
    if classify.friendly_reply:
        return f"{classify.friendly_reply}\n\n{pick(messages.CAPABILITY, lang)}"
    if fallback is None:
        return f"{pick(messages.NOT_A_CLAIM_FALLBACK, lang)}\n\n{pick(messages.CAPABILITY, lang)}"
    if kind == "question_about_bot":
        return pick(fallback, lang)  # already says what InfoBot does
    return f"{pick(fallback, lang)}\n\n{pick(messages.CAPABILITY, lang)}"


def compose_blocked_reply(text: str = "") -> str:
    if not text:
        return messages.all_langs(messages.BLOCKED)
    return pick(messages.BLOCKED, guess_language(text))


def compose_unreadable_media(kind: str, caption: str = "") -> str:
    table = messages.UNREADABLE[kind]
    if caption:
        return pick(table, guess_language(caption))
    return messages.all_langs(table)


def compose_photo_only(description: str) -> str:
    seen = messages.SEEN_IN_PHOTO.format(description=sanitize_output(description, 160).rstrip("."))
    return f"{seen}\n\n{messages.all_langs(messages.MEDIA_AUTHENTICITY)}"


def compose_busy(text: str = "") -> str:
    return pick(messages.BUSY, guess_language(text)) if text else messages.all_langs(messages.BUSY)


def compose_too_long(kind: str, caption: str = "") -> str:
    table = messages.TOO_LONG[kind]
    return pick(table, guess_language(caption)) if caption else messages.all_langs(table)
