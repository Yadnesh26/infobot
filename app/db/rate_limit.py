from datetime import datetime, timezone

from app.config import settings
from app.db import client as db


async def check_and_increment(wa_user_hash: str) -> bool:
    """True if this request is under the per-hour cap, False if it should be
    rejected. Atomic via a single Postgres function call (increment_rate_limit)
    -- a read-then-write from application code would race under a burst of
    messages from the same user arriving within the same second.
    """
    window_start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    count = await db.post(
        "rpc/increment_rate_limit",
        {"p_user_hash": wa_user_hash, "p_window_start": window_start.isoformat()},
    )
    return count <= settings.RATE_LIMIT_PER_HOUR
