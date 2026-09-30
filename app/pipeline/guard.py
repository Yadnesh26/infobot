"""Everything that stands between untrusted input and the rest of the system.

Layers, cheapest first:
  1. Structural: strip invisible characters, cap length, cap media size.
  2. Pattern rules for the things a classifier misses (role-label overrides,
     prompt/secret extraction, hidden instructions).
  3. An ML injection classifier (Llama Prompt Guard 2 on Groq).
Nothing here trusts the model: later stages also wrap untrusted text in
delimiters, validate every URL the model cites against what was actually
retrieved, and sanitise everything the model writes before a user sees it.

The worst realistic outcome of a successful injection is not one odd reply, it
is a poisoned cache entry served to every user who sends that claim. So
anything suspicious is also excluded from the cache, even if it is let through.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger("infobot.guard")

MAX_INPUT_CHARS = 6000
MAX_IMAGE_BYTES = 6 * 1024 * 1024  # WhatsApp itself caps images at 5 MB
MAX_AV_BYTES = 17 * 1024 * 1024  # ...and audio/video at 16 MB

_GUARD_CHUNK_CHARS = 700  # the guard model reads ~512 tokens
_GUARD_MAX_CHUNKS = 6

# The section labels normalize.py puts in front of each piece of a media message.
_LABEL_LINE = re.compile(
    r"^\[(?:Text inside the image|Caption sent with the (?:image|video|audio)|Voice note transcript|"
    r"Video transcript|What the image shows)\][ 	]*$",
    re.M,
)

# Invisible characters that can hide instructions from a human reader. Tag
# characters (U+E0000 block) have no legitimate use in a forwarded message.
# U+200C/U+200D are deliberately kept: Devanagari conjuncts use them.
_TAG_CHARS = re.compile("[\U000e0000-\U000e007f]")
_INVISIBLE = re.compile("[​‎‏‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_STRONG_PATTERNS = [
    (
        "override_instructions",
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,50}\b(previous|prior|above|earlier|all|any|your|these)\b.{0,40}\b(instructions?|prompts?|rules?|directions?|guidelines?)\b",
    ),
    ("role_label", r"(^|\n)\s*(system|assistant|developer)\s*(prompt|message)?\s*[:\]>]"),
    ("prompt_reference", r"\b(system|developer|hidden|secret|initial)\s+(prompt|instructions?|message)\b"),
    (
        "extraction",
        r"\b(reveal|print|show|repeat|leak|dump|output|display|tell me)\b.{0,50}\b(system prompt|your (prompts?|instructions|rules|api[ _-]?keys?|secrets?|tokens?|passwords?|credentials?|config\w*)|(the |all |any )?(api[ _-]?keys?|env(ironment)? variables?|\.env))\b",
    ),
    ("jailbreak", r"\b(jailbreak|developer mode|do anything now|dan mode|you are now|from now on you)\b|\bDAN\b"),
    ("chat_template", r"<\|?(im_start|im_end|system|endoftext)\|?>|\[/?INST\]|###\s*(system|instruction)s?\b"),
    (
        "force_verdict",
        r"\b(mark|label|rate|rank|set|treat|answer|reply|respond|say|output)\b.{0,40}\b(this|the claim|it|that)\b.{0,30}\b(as|to be|with|only)\b.{0,20}\b(true|verified|correct|high confidence|hacked)\b",
    ),
    # Hindi / Marathi: "ignore/forget all previous instructions"
    (
        "override_instructions_indic",
        r"(पिछले|पिछली|सभी|सारे|पहले के|मागील|आधीच्या|सर्व).{0,25}(निर्देश|आदेश|सूचना|नियम).{0,25}(भूल|अनदेखा|नज़रअंदाज़|नजरअंदाज|विसर|दुर्लक्ष)",
    ),
    ("prompt_reference_indic", r"(सिस्टम|सिस्टीम)\s*(प्रॉम्प्ट|प्रोम्प्ट|निर्देश|सूचना)"),
]
_STRONG = [(name, re.compile(pat, re.I | re.S)) for name, pat in _STRONG_PATTERNS]


@dataclass
class GuardResult:
    text: str
    blocked: bool = False
    reason: str | None = None
    suspicious: bool = False  # let through, but never cache
    truncated: bool = False
    guard_score: float = 0.0


def clean_text(text: str) -> tuple[str, bool]:
    """Returns (cleaned text, had_hidden_tag_characters)."""
    had_tags = bool(_TAG_CHARS.search(text))
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub("", text)
    return text, had_tags


def _pattern_hit(text: str) -> str | None:
    for name, rx in _STRONG:
        if rx.search(text):
            return name
    return None


async def _guard_score(text: str) -> float:
    """Highest injection probability over the text's chunks. Fails open (0.0):
    the pattern rules and the structural defences still apply without it."""
    if not settings.GROQ_API_KEY:
        return 0.0
    # Score what the user (or the image / recording) actually said, not our own
    # section labels: a stack of bracketed headers reads to the classifier like a
    # structured prompt and scores ~1.0 on perfectly ordinary posters.
    segments = [seg.strip() for seg in _LABEL_LINE.split(text) if seg.strip()]
    chunks = [seg[i : i + _GUARD_CHUNK_CHARS] for seg in segments for i in range(0, len(seg), _GUARD_CHUNK_CHARS)]
    chunks = chunks[:_GUARD_MAX_CHUNKS]
    if not chunks:
        return 0.0

    async def one(client: httpx.AsyncClient, chunk: str) -> float:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            json={"model": settings.PROMPT_GUARD_MODEL, "messages": [{"role": "user", "content": chunk}]},
        )
        resp.raise_for_status()
        return float(resp.json()["choices"][0]["message"]["content"])

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            return max(await asyncio.gather(*[one(client, c) for c in chunks]))
    except Exception:
        logger.warning("Prompt-guard call failed; continuing on pattern rules alone", exc_info=True)
        return 0.0


async def screen_text(text: str) -> GuardResult:
    cleaned, had_tags = clean_text(text)
    truncated = len(cleaned) > MAX_INPUT_CHARS
    cleaned = cleaned[:MAX_INPUT_CHARS].strip()

    if had_tags:
        return GuardResult(cleaned, blocked=True, reason="hidden_characters", truncated=truncated)

    hit = _pattern_hit(cleaned)
    if hit:
        return GuardResult(cleaned, blocked=True, reason=f"pattern:{hit}", truncated=truncated)

    score = await _guard_score(cleaned) if cleaned else 0.0
    if score >= settings.PROMPT_GUARD_BLOCK_THRESHOLD:
        return GuardResult(cleaned, blocked=True, reason="ml_injection", truncated=truncated, guard_score=score)
    return GuardResult(
        cleaned,
        suspicious=score >= settings.PROMPT_GUARD_FLAG_THRESHOLD,
        truncated=truncated,
        guard_score=score,
    )


def wrap_untrusted(text: str, tag: str = "untrusted_message") -> str:
    """Delimit attacker-influenced text so a prompt can tell data from
    instructions. Any copy of the delimiter inside the text is removed so it
    can't close the block early."""
    safe = text.replace(f"</{tag}>", "").replace(f"<{tag}>", "")
    return f"<{tag}>\n{safe}\n</{tag}>"


_URL = re.compile(r"(https?://|www\.)\S+", re.I)
_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:[^)]*)\)")
_LONG_NUMBER = re.compile(r"(\+?\d[\d\s\-()]{9,}\d)")


def sanitize_output(text: str, max_len: int = 600) -> str:
    """Model-written text is shown to users and gets forwarded into groups, so
    it must never carry a link, phone number or control character of its own.
    The only links a reply may contain are the ones built from retrieved
    sources."""
    text = _MD_LINK.sub(r"\1", text or "")
    text = _URL.sub("", text)
    text = _LONG_NUMBER.sub("", text)
    text = _CONTROL.sub("", _INVISIBLE.sub("", text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_len:
        cut = text[:max_len]
        boundary = max(cut.rfind(". "), cut.rfind("। "), cut.rfind("? "), cut.rfind("! "))
        text = (cut[: boundary + 1] if boundary > max_len * 0.5 else cut.rstrip()) + ("" if boundary > max_len * 0.5 else "…")
    return text


_MARATHI_MARKERS = re.compile(r"(आहे|नाही|आहेत|तुम्ही|तुमच्या|करा|झाले|झाला|मला|काय|आणि|होते|असे|आम्ही|पण)")


def guess_language(text: str) -> str:
    """Cheap en/hi/mr guess for places where no model has run yet (blocked
    input, unreadable media with a caption)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    deva = sum(1 for c in letters if "ऀ" <= c <= "ॿ")
    if deva / len(letters) < 0.3:
        return "en"
    return "mr" if len(_MARATHI_MARKERS.findall(text)) >= 2 else "hi"
