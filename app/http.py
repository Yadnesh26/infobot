"""One reused connection pool per service.

From India, opening a connection costs 0.1-0.45 s (TCP + TLS) before a single byte of the
request is sent, and a message makes about a dozen calls (database, models, search, WhatsApp).
Every call used to open its own connection; reusing them takes a few hundred milliseconds off
every call after the first. Pools are separate per service so one slow host cannot starve another.
"""

import logging

import httpx

logger = logging.getLogger("infobot.http")

# Servers close idle connections on their own schedule; 45 s covers the gaps inside one message.
_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=45)
_clients: dict[str, httpx.AsyncClient] = {}

# What a reused connection looks like when the server dropped it while it sat idle.
_STALE = (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)


def client(service: str) -> httpx.AsyncClient:
    existing = _clients.get(service)
    if existing is None:
        existing = httpx.AsyncClient(
            # retries=1 only repeats failed connection attempts, never a request that was sent.
            transport=httpx.AsyncHTTPTransport(limits=_LIMITS, retries=1),
            timeout=30,
        )
        _clients[service] = existing
    return existing


async def _send(service: str, method: str, url: str, timeout: float, kwargs: dict) -> httpx.Response:
    for attempt in (1, 2):
        try:
            return await getattr(client(service), method)(url, timeout=timeout, **kwargs)
        except _STALE as exc:
            if attempt == 2:
                raise
            # The request never got an answer, so repeating it on a fresh connection is safe.
            logger.info("Reused connection to %s was closed by the server (%r); retrying once", service, exc)
    raise AssertionError("unreachable")


async def get(service: str, url: str, *, timeout: float, **kwargs) -> httpx.Response:
    return await _send(service, "get", url, timeout, kwargs)


async def post(service: str, url: str, *, timeout: float, **kwargs) -> httpx.Response:
    return await _send(service, "post", url, timeout, kwargs)


async def patch(service: str, url: str, *, timeout: float, **kwargs) -> httpx.Response:
    return await _send(service, "patch", url, timeout, kwargs)


async def close_all() -> None:
    """On shutdown: close every pool cleanly."""
    for pooled in list(_clients.values()):
        await pooled.aclose()
    _clients.clear()


def reset() -> None:
    """Forget every pool without closing it. For tests, which run each test in its own event
    loop and must not carry a connection from one to the next."""
    _clients.clear()


async def warm(targets: dict[str, str]) -> None:
    """Open one connection per service ahead of the first real request. Best effort: a service
    that is down or answers 404 still leaves a usable connection behind, and any error is ignored."""
    import asyncio

    async def one(service: str, url: str) -> None:
        try:
            await get(service, url, timeout=5)
        except Exception:
            logger.info("Could not warm the connection to %s", service)

    await asyncio.gather(*[one(s, u) for s, u in targets.items()])
