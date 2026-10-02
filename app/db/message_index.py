"""How a message was read the first time. A picture or voice note is read by a model, and a
model does not read the same thing the same way twice; a cache keyed on that reading cannot
make "same message in, same reply out". This index is keyed on the message itself (a hash of
the media bytes, or of the text), and stores the reading, so a repeat is read identically and
then answered from the claims cache. Fails soft: a miss just means the full pipeline runs."""

import logging

from app.db import client as db

logger = logging.getLogger("infobot.message_index")


async def get(msg_key: str) -> dict | None:
    try:
        rows = await db.get(
            "message_index", {"msg_key": f"eq.{msg_key}", "select": "msg_key,reply_lang,interpretation", "limit": "1"}
        )
        return rows[0] if rows else None
    except Exception:
        logger.warning("Could not read message_index", exc_info=True)
        return None


async def put(entry: dict) -> None:
    """First reading wins: a later duplicate never overwrites it."""
    try:
        await db.post(
            "message_index", entry, params={"on_conflict": "msg_key"},
            prefer="resolution=ignore-duplicates,return=minimal",
        )
    except Exception:
        logger.warning("Could not write message_index", exc_info=True)
