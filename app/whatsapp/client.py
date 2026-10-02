from app import http
from app.config import settings

_BASE_URL = "https://graph.facebook.com"


def _url(path: str) -> str:
    return f"{_BASE_URL}/{settings.WA_API_VERSION}/{path}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.WA_TOKEN}"}


async def send_text_reply(to: str, body: str, reply_to_wamid: str) -> dict:
    """Send a text message threaded as a contextual reply to reply_to_wamid.

    Threading via context.message_id is what quotes the reply against the specific
    forwarded message rather than the user's most recent one.
    """
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "context": {"message_id": reply_to_wamid},
        "type": "text",
        "text": {"preview_url": True, "body": body},
    }
    resp = await http.post(
        "meta", _url(f"{settings.WA_PHONE_NUMBER_ID}/messages"), timeout=30, headers=_headers(), json=payload
    )
    resp.raise_for_status()
    return resp.json()


async def send_reply_buttons(to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
    """Send up to three tap-to-reply buttons as (id, title) pairs. WhatsApp allows
    at most 3 buttons and 20 characters per title. Free inside the 24-hour window
    a user's own message opens."""
    if not 1 <= len(buttons) <= 3:
        raise ValueError("WhatsApp reply buttons take between one and three buttons")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": [{"type": "reply", "reply": {"id": bid, "title": title[:20]}} for bid, title in buttons]},
        },
    }
    resp = await http.post(
        "meta", _url(f"{settings.WA_PHONE_NUMBER_ID}/messages"), timeout=30, headers=_headers(), json=payload
    )
    resp.raise_for_status()
    return resp.json()


async def mark_read(wamid: str) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": wamid,
    }
    resp = await http.post(
        "meta", _url(f"{settings.WA_PHONE_NUMBER_ID}/messages"), timeout=10, headers=_headers(), json=payload
    )
    resp.raise_for_status()


async def get_media_url(media_id: str) -> str:
    resp = await http.get("meta", _url(media_id), timeout=15, headers=_headers())
    resp.raise_for_status()
    return resp.json()["url"]


async def download_media(media_id: str) -> bytes:
    """Two-step download: resolve the short-lived URL, then fetch with auth."""
    url = await get_media_url(media_id)
    resp = await http.get("meta", url, timeout=30, headers=_headers())
    resp.raise_for_status()
    return resp.content
