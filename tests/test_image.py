import pytest

from app.pipeline.normalize import normalize_image


@pytest.mark.anyio
async def test_normalize_image_combines_ocr_text_and_caption(monkeypatch):
    async def fake_generate_json(input_data, schema, thinking_level="low"):
        return {"has_readable_text": True, "extracted_text": "Drinking hot water cures COVID"}

    monkeypatch.setattr("app.pipeline.normalize.generate_json", fake_generate_json)

    result = await normalize_image(b"fake-bytes", "image/jpeg", "forwarded from a friend")
    assert "Drinking hot water cures COVID" in result
    assert "forwarded from a friend" in result


@pytest.mark.anyio
async def test_normalize_image_returns_none_when_no_text_and_no_caption(monkeypatch):
    async def fake_generate_json(input_data, schema, thinking_level="low"):
        return {"has_readable_text": False, "extracted_text": ""}

    monkeypatch.setattr("app.pipeline.normalize.generate_json", fake_generate_json)

    result = await normalize_image(b"fake-bytes", "image/jpeg", None)
    assert result is None


@pytest.mark.anyio
async def test_normalize_image_uses_caption_alone_when_no_readable_text(monkeypatch):
    async def fake_generate_json(input_data, schema, thinking_level="low"):
        return {"has_readable_text": False, "extracted_text": ""}

    monkeypatch.setattr("app.pipeline.normalize.generate_json", fake_generate_json)

    result = await normalize_image(b"fake-bytes", "image/jpeg", "hot water cures covid, share this!")
    assert result == "hot water cures covid, share this!"


@pytest.mark.anyio
async def test_normalize_image_ignores_model_text_when_has_readable_text_is_false(monkeypatch):
    """Defense in depth: even if the model fills extracted_text despite saying
    has_readable_text=false, don't use it -- trust the flag, not the string."""

    async def fake_generate_json(input_data, schema, thinking_level="low"):
        return {"has_readable_text": False, "extracted_text": "some text that slipped through"}

    monkeypatch.setattr("app.pipeline.normalize.generate_json", fake_generate_json)

    result = await normalize_image(b"fake-bytes", "image/jpeg", None)
    assert result is None
