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
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None, timeout=None):
            captured["query_len"] = len(json["query"])
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

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


@pytest.mark.anyio
async def test_general_search_excludes_social_echoes_but_factcheck_pass_does_not(monkeypatch):
    bodies = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None, timeout=None):
            bodies.append(json)
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

    await tavily.search("some claim")
    await tavily.search("some claim", include_domains=["boomlive.in"])

    assert "facebook.com" in bodies[0]["exclude_domains"]
    assert "include_domains" not in bodies[0]
    assert bodies[1]["include_domains"] == ["boomlive.in"]
    assert "exclude_domains" not in bodies[1]


# --- the searches run together, and one failing does not lose the rest ---


@pytest.mark.anyio
async def test_searches_run_at_the_same_time_not_one_after_another(monkeypatch):
    import asyncio
    import time

    async def slow_search(query, max_results=5, include_domains=None):
        await asyncio.sleep(0.2)
        return [{"url": f"https://example.com/{query}/{bool(include_domains)}", "title": query}]

    monkeypatch.setattr("app.providers.tavily.search", slow_search)
    started = time.perf_counter()
    rows = await tavily.search_for_claim("english claim", "दावा", "hi")
    elapsed = time.perf_counter() - started
    assert len(rows) == 3  # english, original language, fact-check pass
    assert elapsed < 0.45  # three 0.2 s searches in sequence would take 0.6 s


@pytest.mark.anyio
async def test_results_keep_the_general_then_original_then_factcheck_order(monkeypatch):
    async def fake_search(query, max_results=5, include_domains=None):
        label = "factcheck" if include_domains else query
        return [{"url": f"https://x.in/{label}", "title": label}]

    monkeypatch.setattr("app.providers.tavily.search", fake_search)
    rows = await tavily.search_for_claim("english claim", "दावा", "mr")
    assert [r["title"] for r in rows] == ["english claim", "दावा", "factcheck"]


@pytest.mark.anyio
async def test_one_failed_search_still_returns_what_the_others_found(monkeypatch):
    async def flaky(query, max_results=5, include_domains=None):
        if include_domains:
            raise RuntimeError("fact-check pass failed")
        return [{"url": "https://x.in/general", "title": "general"}]

    monkeypatch.setattr("app.providers.tavily.search", flaky)
    rows = await tavily.search_for_claim("english claim", "english claim", "en")
    assert [r["title"] for r in rows] == ["general"]


@pytest.mark.anyio
async def test_when_every_search_fails_the_error_reaches_the_caller(monkeypatch):
    async def down(query, max_results=5, include_domains=None):
        raise RuntimeError("tavily down")

    monkeypatch.setattr("app.providers.tavily.search", down)
    with pytest.raises(RuntimeError, match="tavily down"):
        await tavily.search_for_claim("english claim", "english claim", "en")
