import hashlib

from app.config import settings


def hash_phone(number: str) -> str:
    return hashlib.sha256((settings.PHONE_HASH_SALT + number).encode("utf-8")).hexdigest()


def hash_id(value: str) -> str:
    """Salted one-way hash for WhatsApp message IDs.

    A wamid is not opaque: it embeds the sender's phone number in base64, so
    storing or logging one raw is storing the number. Hash it like the number
    itself, with a different prefix so the two hash spaces can't be confused.
    """
    return hashlib.sha256(("id:" + settings.PHONE_HASH_SALT + value).encode("utf-8")).hexdigest()


def ref(wamid: str) -> str:
    """Short, non-reversible reference for log lines that need to correlate
    events for one message without printing anything that contains a number."""
    return hash_id(wamid)[:10]
