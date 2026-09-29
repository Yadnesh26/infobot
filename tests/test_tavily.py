import pytest

from app.providers import tavily


@pytest.mark.anyio
async def test_search_skips_empty_query():
    assert await tavily.search("   ") == []


@pytest.mark.anyio
async def test_search_truncates_overlong_query(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured["query_len"] = len(json["query"])
            return FakeResponse()

    monkeypatch.setattr("app.providers.tavily.httpx.AsyncClient", FakeAsyncClient)

    await tavily.search("x" * 1000)
    assert captured["query_len"] == tavily._MAX_QUERY_CHARS


@pytest.mark.anyio
async def test_search_for_claim_runs_both_languages_when_non_english(monkeypatch):
    calls = []

    async def fake_search(query, max_results=5, include_domains=None):
        calls.append((query, include_domains))
        return [{"url": f"https://example.com/{len(calls)}", "title": "x", "content": "y"}]

    monkeypatch.setattr("app.providers.tavily.search", fake_search)

    results = await tavily.search_for_claim("claim in english", "मराठी दावा", "mr")

    queries = [c[0] for c in calls if c[1] is None]
    assert "claim in english" in queries
    assert "मराठी दावा" in queries
    assert len(results) == 3  # 2 language searches + 1 domain-boosted pass


@pytest.mark.anyio
async def test_search_for_claim_skips_second_language_when_english(monkeypatch):
    calls = []

    async def fake_search(query, max_results=5, include_domains=None):
        calls.append((query, include_domains))
        return [{"url": "https://example.com/1", "title": "x", "content": "y"}]

    monkeypatch.setattr("app.providers.tavily.search", fake_search)

    await tavily.search_for_claim("claim in english", "claim in english", "en")

    plain_queries = [c[0] for c in calls if c[1] is None]
    assert plain_queries == ["claim in english"]  # only one language pass, not two


@pytest.mark.anyio
async def test_search_for_claim_dedupes_by_url(monkeypatch):
    async def fake_search(query, max_results=5, include_domains=None):
        return [{"url": "https://same.example.com/1", "title": "dup", "content": "y"}]

    monkeypatch.setattr("app.providers.tavily.search", fake_search)

    results = await tavily.search_for_claim("claim", "claim", "en")
    assert len(results) == 1
