import re
from dataclasses import dataclass, field

from app.pipeline import messages
from app.pipeline.classify import Claim, ClassifyResult
from app.pipeline.confidence import confidence_label
from app.pipeline.guard import guess_language, sanitize_output
from app.pipeline.messages import pick, verdict_label
from app.pipeline.verify import T3aResult, VerifyResult

WHATSAPP_TEXT_LIMIT = 4096
_SAFE_LIMIT = 3900
_NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
_DIVIDER = "━━━━━━━━━━━━"


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
    local_lang: str = ""  # the language explanation_local is actually in ("" = use the claim's)


def compose_t3b_reply(lang: str | None = None) -> str:
    """The hard stop. Static on purpose: a fixed text per language (messages.MEDICAL_STOP), the
    same every time for a given language, regardless of the claim text or forward count. Never
    model-generated. One language at a time: the user's own, else English."""
    return pick(messages.MEDICAL_STOP, lang)


# --------------------------------------------------------------------------
# Text hygiene. WhatsApp formats *bold*, _italic_, ~strike~ and `mono` inside
# any text, so characters from users or models could break or hijack a reply.
# --------------------------------------------------------------------------


def _plain(text: str) -> str:
    """For user-derived one-liners (claim headlines): no formatting characters at all."""
    return re.sub(r"\s+", " ", re.sub(r"[*_~`]", "", text or "")).strip()


def _body(text: str) -> str:
    """For model-written paragraphs: drop the markers that would switch on bold,
    strike or monospace; underscores stay (they appear inside ordinary words)."""
    return re.sub(r"[*~`]", "", text or "").strip()


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _headline(claim: Claim, limit: int = 160) -> str:
    return _short(_plain(sanitize_output(claim.claim_original or claim.claim_english, 240)), limit)


# --------------------------------------------------------------------------
# Verdict cards
# --------------------------------------------------------------------------


def _sources_lines(sources: list[dict], lang: str, limit: int, title_max: int) -> list[str]:
    if not sources or limit <= 0:
        return []
    lines = ["", f"*{pick(messages.SOURCES_WORD, lang)}*"]
    for i, s in enumerate(sources[:limit], 1):
        url = s.get("url", "")
        title = _plain(sanitize_output(s.get("title") or "", title_max))
        lines.append(f"{i}. {title}" if title and title != url else f"{i}.")
        lines.append(url)
    return lines


def _block_lines(
    *, verdict: str, confidence: int | None, headline: str, explanation: str, sources: list[dict],
    lang: str, prefix: str = "", source_limit: int = 4, title_max: int = 90, general_only: bool = False,
) -> list[str]:
    """The core of a reply: the answer first, so it is readable at a glance."""
    emoji = messages.VERDICT_EMOJI.get(verdict, "")
    lines = [f"{prefix}{emoji} *{verdict_label(verdict, lang)}*".strip()]
    level = confidence_label(confidence)
    if level:
        lines.append(
            pick(messages.CONFIDENCE_LINE, lang).format(
                dot=messages.CONFIDENCE_DOT[level], level=pick(messages.CONFIDENCE_LEVEL[level], lang)
            )
        )
    if headline:
        lines += ["", f"*{pick(messages.CLAIM_WORD, lang)}:* _{headline}_"]
    lines += ["", f"*{pick(messages.WHY_WORD, lang)}:* {_body(explanation)}"]
    if general_only and not sources and verdict != "unverifiable":
        lines += ["", f"_{pick(messages.GENERAL_KNOWLEDGE_NOTE, lang)}_"]
    lines += _sources_lines(sources, lang, source_limit, title_max)
    return lines


def _footer(lang: str, guidance: bool = False) -> str:
    return f"_{pick(messages.FOOTER_GUIDANCE if guidance else messages.FOOTER, lang)}_"


def _guidance_lines(text: str, lang: str, headline: str = "") -> list[str]:
    lines = [pick(messages.GUIDANCE_HEADER, lang)]
    if headline:
        lines += ["", f"_{headline}_"]
    return lines + ["", _body(text)]


def _single_card(
    verdict, confidence, headline, explanation, sources, ff: bool, general_only: bool, lang: str = "en",
    source_limit: int = 4,
) -> str:
    lines = _block_lines(
        verdict=verdict, confidence=confidence, headline=headline, explanation=explanation, sources=sources,
        lang=lang, general_only=general_only, source_limit=source_limit,
    )
    if ff:
        lines += ["", pick(messages.FORWARDED, lang)]
    return "\n".join(lines + ["", _footer(lang)])


def _guidance_card(text: str, ff: bool, lang: str, headline: str = "") -> str:
    lines = _guidance_lines(text, lang, headline)
    if ff:
        lines += ["", pick(messages.FORWARDED, lang)]
    return "\n".join(lines + ["", _footer(lang, guidance=True)])


def _lang_of(classify) -> str:
    return getattr(classify, "detected_language", None) or "en"


def compose_t1_reply(classify, verify: VerifyResult, frequently_forwarded: bool, headline: str = "") -> str:
    return _single_card(
        verify.verdict, verify.confidence, headline,
        verify.explanation_original_language or verify.explanation_english,
        [], frequently_forwarded, True, _lang_of(classify),
    )


def compose_t2_reply(classify, verify: VerifyResult, frequently_forwarded: bool, headline: str = "") -> str:
    return _single_card(
        verify.verdict, verify.confidence, headline,
        verify.explanation_original_language or verify.explanation_english,
        verify.sources, frequently_forwarded, False, _lang_of(classify),
    )


def compose_t3a_reply(t3a: T3aResult, frequently_forwarded: bool, lang: str = "en", headline: str = "") -> str:
    """Deliberately shaped nothing like a verdict card -- an info header, no
    verdict word, no confidence line -- so it visibly reads as general guidance."""
    return _guidance_card(t3a.guidance_original_language or t3a.guidance_english, frequently_forwarded, lang, headline)


def compose_cached_reply(row: dict, frequently_forwarded: bool, lang: str = "en") -> str:
    """Built from a claims row read back from the cache. Only an English
    explanation is stored; callers that want another language translate it first
    and pass the result in row['explanation_en'] along with lang."""
    verdict = row.get("verdict", "")
    headline = _short(_plain(row.get("claim_text_en") or ""), 160)
    if verdict == "refused":
        return compose_t3b_reply(lang)
    if verdict == "guidance":
        return _guidance_card(row.get("explanation_en") or "", frequently_forwarded, lang, headline)
    sources = row.get("sources") or []
    return _single_card(
        verdict, row.get("confidence"), headline, row.get("explanation_en") or "", sources,
        frequently_forwarded, True, lang,
    )


# --------------------------------------------------------------------------
# One or several claims
# --------------------------------------------------------------------------


def _outcome_lang(o: ClaimOutcome, reply_lang: str | None) -> str:
    return o.local_lang or reply_lang or o.claim.language or "en"


def _summary_line(index: int, o: ClaimOutcome, lang: str) -> str:
    headline = _short(_headline(o.claim), 70)
    if o.verdict in messages.VERDICT_EMOJI:
        label = f"{messages.VERDICT_EMOJI[o.verdict]} {verdict_label(o.verdict, lang, bold_case=False)}"
    else:
        label = pick(messages.SPECIAL_LABEL[o.verdict], lang)
    return f"{_NUMBER_EMOJI[index]} {label} — {headline}"


def _multi_block(index: int, o: ClaimOutcome, lang: str, source_limit: int, title_max: int) -> list[str]:
    num = f"{_NUMBER_EMOJI[index]} "
    headline = _headline(o.claim)
    if o.verdict == "refused":
        return [f"{num}*{pick(messages.SPECIAL_LABEL['refused'], lang)}*", "", f"_{headline}_", "", pick(messages.MEDICAL_SHORT, lang)]
    if o.verdict == "error":
        return [f"{num}*{pick(messages.SPECIAL_LABEL['error'], lang)}*", "", f"_{headline}_", "", pick(messages.BUSY, lang)]
    if o.verdict == "guidance":
        lines = _guidance_lines(o.explanation_local or o.explanation_en, lang, headline)
        lines[0] = num + lines[0]
        return lines
    return _block_lines(
        verdict=o.verdict, confidence=o.confidence, headline=headline,
        explanation=o.explanation_local or o.explanation_en, sources=o.sources, lang=lang, prefix=num,
        source_limit=source_limit, title_max=title_max, general_only=True,
    )


def _fit(text: str) -> str:
    if len(text) <= _SAFE_LIMIT:
        return text
    return text[: _SAFE_LIMIT - 1].rstrip() + "…"


def _note_lines(classify: ClassifyResult, lang: str) -> list[str]:
    note = classify.friendly_reply
    return ["", f"💬 *{pick(messages.NOTE_REST, lang)}*", note] if note else []


def compose_claims_reply(
    outcomes: list[ClaimOutcome], classify: ClassifyResult, frequently_forwarded: bool, reply_lang: str | None = None
) -> str:
    if len(outcomes) == 1:
        o = outcomes[0]
        lang = _outcome_lang(o, reply_lang)
        if o.verdict == "refused":
            return compose_t3b_reply(lang)
        if o.verdict == "error":
            return pick(messages.BUSY, lang)
        headline = _headline(o.claim)
        note = _note_lines(classify, lang)
        if o.verdict == "guidance":
            card = _guidance_card(o.explanation_local or o.explanation_en, frequently_forwarded, lang, headline)
            return _fit(_with_note(card, note))
        for limit in (4, 3, 2, 1, 0):
            card = _single_card(
                o.verdict, o.confidence, headline, o.explanation_local or o.explanation_en, o.sources,
                frequently_forwarded, True, lang, source_limit=limit,
            )
            card = _with_note(card, note)
            if len(card) <= _SAFE_LIMIT:
                return card
        return _fit(card)

    lang = reply_lang or outcomes[0].claim.language or classify.detected_language
    langs = [_outcome_lang(o, reply_lang) for o in outcomes]
    summary = [pick(messages.SUMMARY_HEADER, lang).format(n=len(outcomes))]
    # One language for the at-a-glance lines, so they scan as a list even when the claims
    # themselves came in different languages; each detailed block below keeps its own.
    summary += [_summary_line(i, o, lang) for i, o in enumerate(outcomes)]

    def build(source_limit: int, title_max: int) -> str:
        blocks = ["\n".join(_multi_block(i, o, lg, source_limit, title_max)) for i, (o, lg) in enumerate(zip(outcomes, langs))]
        tail: list[str] = []
        if classify.more_claims_omitted:
            tail += ["", pick(messages.MULTI_OMITTED, lang)]
        tail += _note_lines(classify, lang)
        if frequently_forwarded:
            tail += ["", pick(messages.FORWARDED, lang)]
        tail += ["", _footer(lang)]
        body = f"\n\n{_DIVIDER}\n\n".join(["\n".join(summary), *blocks])
        return body + "\n" + "\n".join(tail)

    for limit, title_max in ((3, 90), (2, 70), (1, 60), (0, 0)):
        text = build(limit, title_max)
        if len(text) <= _SAFE_LIMIT:
            return text
    return _fit(text)


def _with_note(card: str, note: list[str]) -> str:
    if not note:
        return card
    body, _, footer = card.rpartition("\n\n")  # keep the footer last
    return body + "\n" + "\n".join(note) + "\n\n" + footer


# --------------------------------------------------------------------------
# Everything that is not a checkable claim
# --------------------------------------------------------------------------


def _capability(lang: str) -> str:
    return f"💡 _{pick(messages.CAPABILITY, lang)}_"


def compose_nonclaim_reply(classify: ClassifyResult, reply_lang: str | None = None) -> str:
    """Low-rejection replies: say what the message was, why InfoBot won't act on
    it, and what would work instead. The model writes the first part (about this
    specific message, sanitised); a fixed per-kind text stands in if it gave
    none, and the fixed capability line follows."""
    lang = reply_lang or classify.detected_language
    kind = classify.input_kind
    if kind == "health_advice_request":
        return compose_t3b_reply(lang)
    if kind == "media_authenticity":
        return pick(messages.MEDIA_AUTHENTICITY, lang)
    if kind == "abusive_or_manipulation":
        return pick(messages.BLOCKED, lang)
    if kind == "unclear":
        return pick(messages.UNCLEAR, lang)

    fallback = messages.KIND_FALLBACK.get(kind)
    if classify.friendly_reply:
        if "\n" in classify.friendly_reply:
            # A multi-part reply already ends by saying what InfoBot can check;
            # repeating the fixed line would say it twice.
            return classify.friendly_reply
        return f"{classify.friendly_reply}\n\n{_capability(lang)}"
    if fallback is None:
        return f"{pick(messages.NOT_A_CLAIM_FALLBACK, lang)}\n\n{_capability(lang)}"
    if kind == "question_about_bot":
        return pick(fallback, lang)  # already says what InfoBot does
    return f"{pick(fallback, lang)}\n\n{_capability(lang)}"


def _lang_for(lang: str | None, text: str = "") -> str:
    """The user's chosen language, else a guess from their own words, else English.
    Never all three stacked: that reads as a mess to everyone."""
    if lang:
        return lang
    return guess_language(text) if text else "en"


def compose_blocked_reply(text: str = "", lang: str | None = None) -> str:
    return pick(messages.BLOCKED, _lang_for(lang, text))


def compose_unreadable_media(kind: str, caption: str = "", lang: str | None = None) -> str:
    return pick(messages.UNREADABLE[kind], _lang_for(lang, caption))


def compose_photo_only(description: str, lang: str | None = None) -> str:
    lang = _lang_for(lang)
    authenticity = pick(messages.MEDIA_AUTHENTICITY, lang)
    if lang != "en":
        return authenticity  # the description is written in English; don't mix languages
    seen = messages.SEEN_IN_PHOTO.format(description=sanitize_output(description, 160).rstrip("."))
    return f"📷 {seen}\n\n{authenticity}"


def compose_unsupported(lang: str | None = None) -> str:
    """A sticker, location, contact or document: say what does work."""
    lang = _lang_for(lang)
    return f"{pick(messages.NOT_A_CLAIM_FALLBACK, lang)}\n\n{_capability(lang)}"


def compose_busy(text: str = "", lang: str | None = None) -> str:
    return pick(messages.BUSY, _lang_for(lang, text))


def compose_too_long(kind: str, caption: str = "", lang: str | None = None) -> str:
    return pick(messages.TOO_LONG[kind], _lang_for(lang, caption))
