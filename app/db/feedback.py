from app.db import client as db
from app.util import hash_id


async def insert_feedback(submission_id: str, reply_message_id: str, emoji: str) -> None:
    await db.post(
        "feedback",
        {"submission_id": submission_id, "reply_message_id": hash_id(reply_message_id), "emoji": emoji},
        prefer="return=minimal",
    )
