import logging

import httpx

from app.config import settings

logger = logging.getLogger("infobot.tavily")

_TIMEOUT = 15
_URL = "https://api.tavily.com/search"
_MAX_QUERY_CHARS = 380  # Tavily rejects overlong queries; stay well clear of the limit

# Indian fact-check outlets and health authorities the plan calls out to prioritize.
_FACT_CHECK_DOMAINS = [
    "boomlive.in",
    "altnews.in",
    "vishvasnews.com",
    "newschecker.in",
    "factly.in",
    "who.int",
    "icmr.gov.in",
    "aiims.edu",
]


async def search(query: str, max_results: int = 5, include_domains: list[str] | None = None) -> list[dict]:
    query = query.strip()
    if not query:
        logger.warning("Skipping Tavily search with an empty query")
        return []
    if len(query) > _MAX_QUERY_CHARS:
        query = query[:_MAX_QUERY_CHARS]

    body = {"query": query, "search_depth": "basic", "max_results": max_results}
    if include_domains:
        body["include_domains"] = include_domains

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _URL,
            headers={"Authorization": f"Bearer {settings.TAVILY_API_KEY}"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
    return data.get("results", [])


async def search_for_claim(claim_english: str, claim_original: str, detected_language: str) -> list[dict]:
    """Bilingual search per the plan: a Marathi-specific rumor may only ever have
    been addressed in Marathi coverage, so an English-only search misses it.
    """
    lang = (detected_language or "en").split("-")[0].lower()
    queries = [claim_english]
    if lang != "en" and claim_original and claim_original.strip().lower() != claim_english.strip().lower():
        queries.append(claim_original)

    results: list[dict] = []
    seen_urls: set[str] = set()

    def _add(rows: list[dict]) -> None:
        for r in rows:
            url = r.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(r)

    for q in queries:
        _add(await search(q, max_results=5))

    # One extra pass biased toward known fact-check/health-authority domains.
    _add(await search(queries[0], max_results=5, include_domains=_FACT_CHECK_DOMAINS))

    return results
