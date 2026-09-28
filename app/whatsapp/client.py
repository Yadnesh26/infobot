import httpx

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
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _url(f"{settings.WA_PHONE_NUMBER_ID}/messages"),
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def mark_read(wamid: str) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": wamid,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _url(f"{settings.WA_PHONE_NUMBER_ID}/messages"),
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()


async def get_media_url(media_id: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(_url(media_id), headers=_headers())
        resp.raise_for_status()
        return resp.json()["url"]


async def download_media(media_id: str) -> bytes:
    """Two-step download: resolve the short-lived URL, then fetch with auth."""
    url = await get_media_url(media_id)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        return resp.content
