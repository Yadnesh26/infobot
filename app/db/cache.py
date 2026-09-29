import hashlib
import logging
import re
from datetime import datetime, timezone

from app.db import client as db

logger = logging.getLogger("infobot.cache")

_ZERO_WIDTH_RE = re.compile(r"[​-‏‪-‮﻿]")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]+",
    flags=re.UNICODE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_hash(text: str) -> str:
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def hash_claim(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


async def lookup_exact(claim_hash: str) -> dict | None:
    rows = await db.get("claims", {"claim_hash": f"eq.{claim_hash}", "select": "*", "limit": "1"})
    return rows[0] if rows else None


async def lookup_semantic(embedding: list[float], threshold: float, limit: int = 1) -> dict | None:
    rows = await db.post(
        "rpc/match_claims",
        {"query_embedding": embedding, "match_threshold": threshold, "match_count": limit},
    )
    if not rows:
        return None
    top = rows[0]
    logger.info("Semantic cache hit: similarity=%.4f claim_id=%s", top.get("similarity", 0), top.get("id"))
    return top


async def increment_seen(claim_id: str) -> None:
    """Best-effort, non-atomic increment. A rare race under simultaneous identical
    forwards can undercount times_seen slightly -- acceptable for a popularity
    stat, unlike the rate-limit counter, which does need to be atomic.
    """
    rows = await db.get("claims", {"id": f"eq.{claim_id}", "select": "times_seen"})
    if not rows:
        return
    current = rows[0]["times_seen"]
    await db.patch(
        "claims",
        {"id": f"eq.{claim_id}"},
        {"times_seen": current + 1, "last_seen_at": datetime.now(timezone.utc).isoformat()},
    )


async def insert_claim(payload: dict) -> None:
    await db.post("claims", payload, prefer="return=minimal")
