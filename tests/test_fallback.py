import pytest

from app.providers import fallback_llm, gemini, whisper


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(gemini, "_RETRY_BACKOFF_SECONDS", 0)


@pytest.mark.anyio
async def test_text_call_falls_back_to_groq_when_gemini_fails(monkeypatch):
    async def gemini_down(model, parts, schema, timeout=15):
        raise RuntimeError("503 high demand")

    async def fake_groq(prompt, schema):
        return {"from": "groq"}

    monkeypatch.setattr(gemini, "_call_model", gemini_down)
    monkeypatch.setattr(gemini.fallback_llm, "generate_json", fake_groq)

    assert await gemini.generate_json("a text prompt", {"type": "object"}) == {"from": "groq"}


@pytest.mark.anyio
async def test_image_call_never_falls_back(monkeypatch):
    """Vision has no fallback provider -- it must fail, not silently send an
    image-shaped input to a text-only model."""

    async def gemini_down(model, parts, schema, timeout=15):
        raise RuntimeError("503 high demand")

    async def groq_must_not_run(prompt, schema):
        raise AssertionError("Groq must not be called for image input")

    monkeypatch.setattr(gemini, "_call_model", gemini_down)
    monkeypatch.setattr(gemini.fallback_llm, "generate_json", groq_must_not_run)

    image_input = gemini.build_image_input("read this", b"bytes", "image/png")
    with pytest.raises(gemini.GeminiError):
        await gemini.generate_json(image_input, {"type": "object"})


@pytest.mark.anyio
async def test_raises_when_gemini_and_groq_both_fail(monkeypatch):
    async def gemini_down(model, parts, schema, timeout=15):
        raise RuntimeError("gemini down")

    async def groq_down(prompt, schema):
        raise fallback_llm.FallbackError("groq down")

    monkeypatch.setattr(gemini, "_call_model", gemini_down)
    monkeypatch.setattr(gemini.fallback_llm, "generate_json", groq_down)

    with pytest.raises(gemini.GeminiError):
        await gemini.generate_json("a text prompt", {"type": "object"})


@pytest.mark.anyio
async def test_gemini_success_never_touches_groq(monkeypatch):
    async def gemini_ok(model, parts, schema, timeout=15):
        return '{"ok": true}'

    async def groq_must_not_run(prompt, schema):
        raise AssertionError("Groq must not be called when Gemini succeeds")

    monkeypatch.setattr(gemini, "_call_model", gemini_ok)
    monkeypatch.setattr(gemini.fallback_llm, "generate_json", groq_must_not_run)

    assert await gemini.generate_json("a text prompt", {"type": "object"}) == {"ok": True}


@pytest.mark.anyio
async def test_fallback_llm_refuses_without_api_key(monkeypatch):
    monkeypatch.setattr(fallback_llm.settings, "GROQ_API_KEY", "")
    with pytest.raises(fallback_llm.FallbackError):
        await fallback_llm.generate_json("prompt", {"type": "object"})


def test_whisper_prefers_groq_then_openai_then_raises(monkeypatch):
    monkeypatch.setattr(whisper.settings, "GROQ_API_KEY", "g")
    monkeypatch.setattr(whisper.settings, "OPENAI_API_KEY", "o")
    assert "groq.com" in whisper._endpoint()[0]

    monkeypatch.setattr(whisper.settings, "GROQ_API_KEY", "")
    assert "openai.com" in whisper._endpoint()[0]

    monkeypatch.setattr(whisper.settings, "OPENAI_API_KEY", "")
    with pytest.raises(whisper.TranscriptionError):
        whisper._endpoint()
