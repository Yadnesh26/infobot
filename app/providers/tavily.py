import asyncio
import logging

from app import http
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


# Posts on these sites mostly just repeat the rumour being checked, so a hit
# there is an echo of the claim, not evidence about it. Excluded from the
# general search; fact-checkers' own sites are searched separately below.
_SOCIAL_ECHO_DOMAINS = [
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "tiktok.com",
    "linkedin.com",
    "threads.net",
    "t.me",
    "pinterest.com",
]


async def search(query: str, max_results: int = 5, include_domains: list[str] | None = None) -> list[dict]:
    query = query.strip()
    if not query:
        logger.warning("Skipping Tavily search with an empty query")
        return []
    if len(query) > _MAX_QUERY_CHARS:
        query = query[:_MAX_QUERY_CHARS]

    body = {"query": query, "search_depth": settings.TAVILY_SEARCH_DEPTH, "max_results": max_results}
    if include_domains:
        body["include_domains"] = include_domains
    else:
        body["exclude_domains"] = _SOCIAL_ECHO_DOMAINS

    resp = await http.post(
        "tavily", _URL, timeout=_TIMEOUT, headers={"Authorization": f"Bearer {settings.TAVILY_API_KEY}"}, json=body
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


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

    # The searches do not depend on each other, so they run together (each costs 1-4 s from
    # India; one after another they were the slowest part of a search-based answer). The last
    # is one extra pass biased toward known fact-check/health-authority domains.
    searches = [search(q, max_results=5) for q in queries]
    searches.append(search(queries[0], max_results=5, include_domains=_FACT_CHECK_DOMAINS))
    outcomes = await asyncio.gather(*searches, return_exceptions=True)

    failures = [o for o in outcomes if isinstance(o, BaseException)]
    if len(failures) == len(outcomes):
        raise failures[0]  # nothing came back at all: let the caller treat it as a failure
    for failure in failures:
        logger.warning("One Tavily search failed; using the others: %r", failure)

    # Same order as before (general, original language, fact-check), so the best results lead.
    for rows in outcomes:
        if not isinstance(rows, BaseException):
            _add(rows)

    return results
