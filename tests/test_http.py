import httpx
import pytest

from app import http


def test_one_client_is_reused_per_service_and_services_do_not_share():
    a = http.client("gemini")
    assert http.client("gemini") is a
    assert http.client("tavily") is not a


def test_reset_forgets_pools_so_a_test_never_inherits_a_connection():
    first = http.client("gemini")
    http.reset()
    assert http.client("gemini") is not first


@pytest.mark.anyio
async def test_close_all_closes_every_pool_and_the_next_call_gets_a_fresh_one():
    first = http.client("gemini")
    await http.close_all()
    assert first.is_closed
    assert http.client("gemini") is not first


class _Scripted:
    """A stand-in client: each call pops the next scripted result (a response or an exception)."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def post(self, url, timeout=None, **kwargs):
        self.calls.append({"url": url, "timeout": timeout, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.anyio
async def test_the_per_call_timeout_and_arguments_reach_the_client(monkeypatch):
    scripted = _Scripted(["ok"])
    monkeypatch.setattr(http, "client", lambda service: scripted)
    assert await http.post("svc", "https://example.test/x", timeout=7, headers={"k": "v"}, json={"a": 1}) == "ok"
    assert scripted.calls == [{"url": "https://example.test/x", "timeout": 7, "headers": {"k": "v"}, "json": {"a": 1}}]


@pytest.mark.anyio
@pytest.mark.parametrize("stale", [httpx.RemoteProtocolError("closed"), httpx.ReadError("reset"), httpx.WriteError("broken pipe")])
async def test_a_connection_the_server_closed_while_idle_is_retried_once_on_a_fresh_one(monkeypatch, stale):
    scripted = _Scripted([stale, "ok"])
    monkeypatch.setattr(http, "client", lambda service: scripted)
    assert await http.post("svc", "https://example.test/x", timeout=5) == "ok"
    assert len(scripted.calls) == 2


@pytest.mark.anyio
async def test_a_second_stale_failure_is_raised_not_retried_forever(monkeypatch):
    scripted = _Scripted([httpx.ReadError("a"), httpx.ReadError("b"), "never reached"])
    monkeypatch.setattr(http, "client", lambda service: scripted)
    with pytest.raises(httpx.ReadError):
        await http.post("svc", "https://example.test/x", timeout=5)
    assert len(scripted.calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.ConnectError("refused")])
async def test_timeouts_and_refused_connections_are_not_retried_here(monkeypatch, error):
    """A timeout means the service is slow; repeating it would only double the wait. The callers
    (the Gemini model chain, the Groq fallback) decide what to do next."""
    scripted = _Scripted([error, "never reached"])
    monkeypatch.setattr(http, "client", lambda service: scripted)
    with pytest.raises(type(error)):
        await http.post("svc", "https://example.test/x", timeout=5)
    assert len(scripted.calls) == 1


@pytest.mark.anyio
async def test_warming_ignores_services_that_are_down(monkeypatch):
    seen = []

    async def fake_get(service, url, *, timeout, **kwargs):
        seen.append(service)
        if service == "down":
            raise httpx.ConnectError("refused")
        return "ok"

    monkeypatch.setattr(http, "get", fake_get)
    await http.warm({"up": "https://a.test/", "down": "https://b.test/", "also-up": "https://c.test/"})
    assert sorted(seen) == ["also-up", "down", "up"]
