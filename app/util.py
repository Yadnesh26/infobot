import hashlib

from app.config import settings


def hash_phone(number: str) -> str:
    return hashlib.sha256((settings.PHONE_HASH_SALT + number).encode("utf-8")).hexdigest()
