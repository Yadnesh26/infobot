"""Per-user reply language. A convenience, never a dependency: every function
here fails soft, so a database hiccup costs the user their preference for one
message, not their answer."""

import logging
from datetime import datetime, timezone

from app.db import client as db

logger = logging.getLogger("infobot.prefs")

LANGUAGES = ("en", "hi", "mr")


async def get(wa_user_hash: str) -> dict:
    """{'language': 'hi' | None, 'prompted': bool}; empty defaults if unknown or unavailable."""
    try:
        rows = await db.get(
            "user_prefs", {"wa_user_hash": f"eq.{wa_user_hash}", "select": "language,prompted", "limit": "1"}
        )
        if rows:
            return {"language": rows[0].get("language"), "prompted": bool(rows[0].get("prompted"))}
    except Exception:
        logger.warning("Could not read language preference", exc_info=True)
    return {"language": None, "prompted": False}


async def _upsert(row: dict) -> bool:
    try:
        # merge-duplicates updates only the columns sent, so marking a user as
        # prompted never erases a language they already chose, and vice versa.
        await db.post(
            "user_prefs", row, params={"on_conflict": "wa_user_hash"},
            prefer="resolution=merge-duplicates,return=minimal",
        )
        return True
    except Exception:
        logger.warning("Could not save language preference", exc_info=True)
        return False


async def set_language(wa_user_hash: str, language: str) -> bool:
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language {language!r}")
    return await _upsert({"wa_user_hash": wa_user_hash, "language": language, "prompted": True, "updated_at": datetime.now(timezone.utc).isoformat()})


async def mark_prompted(wa_user_hash: str) -> bool:
    return await _upsert({"wa_user_hash": wa_user_hash, "prompted": True})
