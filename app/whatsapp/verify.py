import hashlib
import hmac

from app.config import settings


def valid_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Validate Meta's X-Hub-Signature-256 header against the raw request body.

    Must be computed against the raw bytes, not re-serialized JSON — re-serialization
    changes whitespace and breaks the hash.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        settings.WA_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)
