from app.db import client as db


async def insert_feedback(submission_id: str, reply_message_id: str, emoji: str) -> None:
    await db.post(
        "feedback",
        {"submission_id": submission_id, "reply_message_id": reply_message_id, "emoji": emoji},
        prefer="return=minimal",
    )
