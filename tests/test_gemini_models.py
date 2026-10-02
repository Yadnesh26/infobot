import pytest

from app.providers import gemini
from app.providers.gemini import GeminiError

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(gemini, "_RETRY_BACKOFF_SECONDS", 0)


def _script(monkeypatch, outcomes):
    """Replace the network call: `outcomes` maps a model name to a list of results (a string is
    the model's text; an exception is raised). Returns the list of models actually called."""
    called = []

    async def fake(model, parts, schema, timeout=15):
        called.append(model)
        result = outcomes[model].pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(gemini, "_call_model", fake)
    return called


async def _groq_must_not_run(prompt, schema):
    raise AssertionError("Groq must not be called")


@pytest.mark.anyio
async def test_the_primary_model_answers_and_the_fallback_model_is_left_alone(monkeypatch):
    called = _script(monkeypatch, {"primary-model": ['{"a": 1}'], "fallback-model": []})
    monkeypatch.setattr(gemini.fallback_llm, "generate_json", _groq_must_not_run)
    assert await gemini.generate_json("hi", SCHEMA) == {"a": 1}
    assert called == ["primary-model"]


@pytest.mark.anyio
async def test_an_overloaded_primary_model_hands_straight_over_to_the_fallback_model(monkeypatch):
    called = _script(monkeypatch, {
        "primary-model": [gemini.GeminiHTTPError(503, "high demand", None)],
        "fallback-model": ['{"a": 2}'],
    })
    monkeypatch.setattr(gemini.fallback_llm, "generate_json", _groq_must_not_run)
    assert await gemini.generate_json("hi", SCHEMA) == {"a": 2}
    assert called == ["primary-model", "fallback-model"]


@pytest.mark.anyio
async def test_a_timeout_moves_on_to_the_next_model_without_waiting(monkeypatch):
    import httpx

    monkeypatch.setattr(gemini, "_RETRY_BACKOFF_SECONDS", 99)  # would hang the test if a backoff happened
    called = _script(monkeypatch, {"primary-model": [httpx.ReadTimeout("")], "fallback-model": ['{"a": 3}']})
    assert await gemini.generate_json("hi", SCHEMA) == {"a": 3}
    assert called == ["primary-model", "fallback-model"]


@pytest.mark.anyio
async def test_unparseable_json_from_the_primary_is_retried_on_the_fallback(monkeypatch):
    called = _script(monkeypatch, {"primary-model": ["not json"], "fallback-model": ['{"a": 4}']})
    assert await gemini.generate_json("hi", SCHEMA) == {"a": 4}
    assert called == ["primary-model", "fallback-model"]


@pytest.mark.anyio
async def test_when_every_gemini_model_fails_text_goes_to_groq(monkeypatch):
    called = _script(monkeypatch, {"primary-model": [RuntimeError("x")], "fallback-model": [RuntimeError("y")]})

    async def groq(prompt, schema):
        return {"from": "groq"}

    monkeypatch.setattr(gemini.fallback_llm, "generate_json", groq)
    assert await gemini.generate_json("hi", SCHEMA) == {"from": "groq"}
    assert called == ["primary-model", "fallback-model"]


@pytest.mark.anyio
async def test_an_image_gets_a_second_try_at_the_primary_and_never_goes_to_groq(monkeypatch):
    called = _script(monkeypatch, {
        "primary-model": [RuntimeError("a"), RuntimeError("c")], "fallback-model": [RuntimeError("b")],
    })
    monkeypatch.setattr(gemini.fallback_llm, "generate_json", _groq_must_not_run)
    with pytest.raises(GeminiError):
        await gemini.generate_json(gemini.build_image_input("read", b"x", "image/png"), SCHEMA)
    assert called == ["primary-model", "fallback-model", "primary-model"]


@pytest.mark.anyio
async def test_the_same_model_named_twice_is_only_tried_once(monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_MODEL_FALLBACK", "primary-model")
    called = _script(monkeypatch, {"primary-model": [RuntimeError("x")]})

    async def groq(prompt, schema):
        return {"from": "groq"}

    monkeypatch.setattr(gemini.fallback_llm, "generate_json", groq)
    await gemini.generate_json("hi", SCHEMA)
    assert called == ["primary-model"]


@pytest.mark.anyio
async def test_with_no_model_configured_it_says_so_instead_of_silently_using_groq(monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_MODEL", "")
    monkeypatch.setattr(gemini.settings, "GEMINI_MODEL_FALLBACK", "")
    with pytest.raises(GeminiError, match="not configured"):
        await gemini.generate_json("hi", SCHEMA)


# ---------------------------------------------------------------- the request and the response


def test_text_and_images_become_the_apis_own_parts():
    assert gemini._to_parts("hello") == [{"text": "hello"}]
    parts = gemini._to_parts(gemini.build_image_input("read this", b"\x01\x02", "image/png"))
    assert parts[0] == {"text": "read this"}
    assert parts[1] == {"inlineData": {"mimeType": "image/png", "data": "AQI="}}


@pytest.mark.anyio
async def test_the_request_names_the_model_in_the_url_and_asks_for_json_matching_the_schema(monkeypatch):
    seen = {}

    async def fake_post(url, payload, timeout=15):
        seen.update(url=url, payload=payload, timeout=timeout)
        return {"candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}]}

    monkeypatch.setattr(gemini, "_post", fake_post)
    assert await gemini.generate_json("hi", SCHEMA) == {"a": 1}
    assert seen["url"].endswith("/models/primary-model:generateContent")
    cfg = seen["payload"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["responseJsonSchema"] == SCHEMA
    assert seen["payload"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert seen["timeout"] == gemini._TIMEOUT


@pytest.mark.anyio
async def test_thinking_parts_are_not_mistaken_for_the_answer(monkeypatch):
    async def fake_post(url, payload, timeout=15):
        return {"candidates": [{"content": {"parts": [{"text": "let me think", "thought": True}, {"text": '{"a": 5}'}]}}]}

    monkeypatch.setattr(gemini, "_post", fake_post)
    assert await gemini.generate_json("hi", SCHEMA) == {"a": 5}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response,reason",
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
        ({"candidates": []}, "no candidates"),
        ({"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}, "SAFETY"),
    ],
)
async def test_a_blocked_or_empty_response_is_an_error_that_names_why_so_the_next_model_gets_a_turn(monkeypatch, response, reason):
    async def fake_post(url, payload, timeout=15):
        return response

    monkeypatch.setattr(gemini, "_post", fake_post)
    with pytest.raises(GeminiError, match=reason):
        await gemini._call_model("primary-model", [{"text": "x"}], SCHEMA)
