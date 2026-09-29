from app.db import client as db


async def claim_submission(
    wamid: str, wa_user_hash: str, input_type: str, forwarded: bool, frequently_forwarded: bool
) -> bool:
    """Insert a submissions row for this wamid, returning True if this is the
    first time we've seen it. Backed by the DB's unique constraint on
    wa_message_id, so unlike the M1 in-memory set this survives a process
    restart -- the whole point of moving idempotency here in M3.
    """
    rows = await db.post(
        "submissions",
        {
            "wa_message_id": wamid,
            "wa_user_hash": wa_user_hash,
            "input_type": input_type,
            "was_forwarded": forwarded,
            "frequently_fwd": frequently_forwarded,
            "status": "pending",
        },
        params={"on_conflict": "wa_message_id"},
        prefer="resolution=ignore-duplicates,return=representation",
    )
    return len(rows) > 0


async def mark_submission(wamid: str, status: str, *, error: str | None = None, cache_hit: str | None = None) -> None:
    body: dict = {"status": status}
    if error is not None:
        body["error"] = error
    if cache_hit is not None:
        body["cache_hit"] = cache_hit
    await db.patch("submissions", {"wa_message_id": f"eq.{wamid}"}, body)


async def set_reply_wamid(wamid: str, reply_wamid: str) -> None:
    """Stored so a later reaction webhook (which only carries the reply's own
    wamid) can be joined back to the submission it's reacting to.
    """
    await db.patch("submissions", {"wa_message_id": f"eq.{wamid}"}, {"reply_wamid": reply_wamid})


async def find_submission_id_by_reply_wamid(reply_wamid: str) -> str | None:
    rows = await db.get(
        "submissions", {"reply_wamid": f"eq.{reply_wamid}", "select": "id", "limit": "1"}
    )
    return rows[0]["id"] if rows else None
