import pytest

from app.main import _failure_reply
from app.pipeline import messages
from app.providers import gemini
from app.providers.fallback_llm import FallbackError
from app.providers.gemini import GeminiError
from app.whatsapp.parser import InboundMessage


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


def _patch_http(monkeypatch, responses_by_key, calls):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            key = headers["x-goog-api-key"]
            calls.append(key)
            return responses_by_key[key]

    monkeypatch.setattr("app.providers.gemini.httpx.AsyncClient", FakeClient)


@pytest.fixture(autouse=True)
def two_keys(monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "primary-key")
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY_FALLBACK", "second-key")
    gemini._cooldown_until.clear()
    yield
    gemini._cooldown_until.clear()


OK = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "{\"a\": 1}"}]}]}
QUOTA_DAY = '{"error":{"message":"Rate limit exceeded (limit: 500 requests per day on Free Tier)"}}'
QUOTA_MIN = '{"error":{"message":"Rate limit exceeded, please retry in 3s"}}'


@pytest.mark.anyio
async def test_a_quota_error_on_the_primary_key_moves_to_the_second_key_in_the_same_call(monkeypatch):
    calls = []
    _patch_http(monkeypatch, {"primary-key": FakeResponse(429, text=QUOTA_DAY), "second-key": FakeResponse(200, OK)}, calls)
    assert await gemini.generate_json("hi", {}) == {"a": 1}
    assert calls == ["primary-key", "second-key"]


@pytest.mark.anyio
async def test_an_exhausted_key_is_skipped_on_later_calls(monkeypatch):
    calls = []
    _patch_http(monkeypatch, {"primary-key": FakeResponse(429, text=QUOTA_DAY), "second-key": FakeResponse(200, OK)}, calls)
    await gemini.generate_json("hi", {})
    calls.clear()
    await gemini.generate_json("hi again", {})
    assert calls == ["second-key"]  # no wasted call on the exhausted key


@pytest.mark.anyio
async def test_a_daily_quota_sits_out_longer_than_a_per_minute_one(monkeypatch):
    calls = []
    _patch_http(monkeypatch, {"primary-key": FakeResponse(429, text=QUOTA_DAY), "second-key": FakeResponse(200, OK)}, calls)
    await gemini.generate_json("x", {})
    daily = gemini._cooldown_until["primary-key"]
    gemini._cooldown_until.clear()
    _patch_http(monkeypatch, {"primary-key": FakeResponse(429, text=QUOTA_MIN), "second-key": FakeResponse(200, OK)}, calls)
    await gemini.generate_json("x", {})
    assert daily > gemini._cooldown_until["primary-key"]


@pytest.mark.anyio
async def test_the_primary_key_is_used_first_when_it_works(monkeypatch):
    calls = []
    _patch_http(monkeypatch, {"primary-key": FakeResponse(200, OK), "second-key": FakeResponse(200, OK)}, calls)
    await gemini.generate_json("hi", {})
    assert calls == ["primary-key"]


@pytest.mark.anyio
async def test_a_non_quota_error_does_not_burn_the_second_key(monkeypatch):
    calls = []
    _patch_http(monkeypatch, {"primary-key": FakeResponse(503, text="overloaded"), "second-key": FakeResponse(200, OK)}, calls)
    monkeypatch.setattr(gemini, "_RETRY_BACKOFF_SECONDS", 0)

    async def groq_down(prompt, schema):
        raise RuntimeError("groq down")

    monkeypatch.setattr("app.providers.gemini.fallback_llm.generate_json", groq_down)
    with pytest.raises(GeminiError):
        await gemini.generate_json("hi", {})
    assert "second-key" not in calls


@pytest.mark.anyio
async def test_when_every_key_is_out_of_quota_the_call_falls_back_to_groq(monkeypatch):
    calls = []
    _patch_http(monkeypatch, {"primary-key": FakeResponse(429, text=QUOTA_DAY), "second-key": FakeResponse(429, text=QUOTA_DAY)}, calls)
    monkeypatch.setattr(gemini, "_RETRY_BACKOFF_SECONDS", 0)

    async def groq(prompt, schema):
        return {"from": "groq"}

    monkeypatch.setattr("app.providers.gemini.fallback_llm.generate_json", groq)
    assert await gemini.generate_json("hi", {}) == {"from": "groq"}


@pytest.mark.anyio
async def test_embeddings_also_rotate_to_the_second_key(monkeypatch):
    calls = []
    _patch_http(
        monkeypatch,
        {"primary-key": FakeResponse(429, text=QUOTA_DAY), "second-key": FakeResponse(200, {"embedding": {"values": [0.5]}})},
        calls,
    )
    assert await gemini.embed_text("hello") == [0.5]
    assert calls == ["primary-key", "second-key"]


@pytest.mark.anyio
async def test_with_no_fallback_key_configured_behaviour_is_unchanged(monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY_FALLBACK", "")
    calls = []
    _patch_http(monkeypatch, {"primary-key": FakeResponse(200, OK)}, calls)
    assert await gemini.generate_json("hi", {}) == {"a": 1}
    assert calls == ["primary-key"]


# --- the reply a user sees when things fail ---


def _msg(text=None, caption=None):
    return InboundMessage(wamid="w", sender="91", type="text", text=text, caption=caption)


def test_provider_outage_tells_the_user_we_are_at_capacity_not_that_something_broke():
    reply = _failure_reply(GeminiError("429"), _msg("Is it true that petrol is Rs 200?"))
    assert reply == messages.BUSY["en"]
    assert "went wrong" not in reply


def test_provider_outage_reply_follows_the_users_language():
    assert _failure_reply(FallbackError("x"), _msg("क्या पेट्रोल 200 रुपये हो गया है?")) == messages.BUSY["hi"]


def test_provider_outage_with_no_text_shows_every_language():
    reply = _failure_reply(GeminiError("x"), _msg())
    assert messages.BUSY["en"] in reply and messages.BUSY["hi"] in reply and messages.BUSY["mr"] in reply


def test_a_genuine_bug_keeps_the_generic_apology():
    assert "went wrong" in _failure_reply(KeyError("bug"), _msg("hello"))
