from datetime import datetime, timedelta, timezone

from app.db import client as db


async def get_trending(days: int = 7, limit: int = 20) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return await db.get(
        "claims",
        {
            "select": "claim_text_en,verdict,times_seen,last_seen_at",
            "last_seen_at": f"gt.{since}",
            "order": "times_seen.desc",
            "limit": str(limit),
        },
    )
