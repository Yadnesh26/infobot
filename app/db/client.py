from app import http
from app.config import settings

_TIMEOUT = 15


def _headers() -> dict:
    return {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    return f"{settings.SUPABASE_URL}/rest/v1/{path}"


async def get(path: str, params: dict) -> list[dict]:
    resp = await http.get("supabase", _url(path), timeout=_TIMEOUT, headers=_headers(), params=params)
    resp.raise_for_status()
    return resp.json()


async def post(
    path: str, json_body, params: dict | None = None, prefer: str | None = None
) -> list[dict]:
    headers = _headers()
    if prefer:
        headers["Prefer"] = prefer
    resp = await http.post("supabase", _url(path), timeout=_TIMEOUT, headers=headers, params=params or {}, json=json_body)
    resp.raise_for_status()
    return resp.json() if resp.content else []


async def patch(path: str, params: dict, json_body: dict) -> None:
    resp = await http.patch("supabase", _url(path), timeout=_TIMEOUT, headers=_headers(), params=params, json=json_body)
    resp.raise_for_status()
