import pytest

from app.pipeline.normalize import normalize_image


def _stub_vision(monkeypatch, payload):
    async def fake_generate_json(input_data, schema, thinking_level="low"):
        return payload

    monkeypatch.setattr("app.pipeline.normalize.generate_json", fake_generate_json)


@pytest.mark.anyio
async def test_normalize_image_labels_text_caption_and_description(monkeypatch):
    _stub_vision(
        monkeypatch,
        {"has_readable_text": True, "extracted_text": "Drinking hot water cures COVID", "description": "A WhatsApp screenshot"},
    )

    r = await normalize_image(b"fake-bytes", "image/jpeg", "forwarded from a friend")
    assert "[Text inside the image]\nDrinking hot water cures COVID" in r.text
    assert "[Caption sent with the image]\nforwarded from a friend" in r.text
    assert "[What the image shows]\nA WhatsApp screenshot" in r.text
    assert r.has_image_text and r.has_caption


@pytest.mark.anyio
async def test_normalize_image_with_nothing_written_returns_no_text_but_keeps_description(monkeypatch):
    _stub_vision(monkeypatch, {"has_readable_text": False, "extracted_text": "", "description": "A flooded street"})

    r = await normalize_image(b"fake-bytes", "image/jpeg", None)
    assert r.text is None
    assert r.description == "A flooded street"


@pytest.mark.anyio
async def test_normalize_image_uses_caption_alone_when_no_readable_text(monkeypatch):
    _stub_vision(monkeypatch, {"has_readable_text": False, "extracted_text": "", "description": ""})

    r = await normalize_image(b"fake-bytes", "image/jpeg", "hot water cures covid, share this!")
    assert r.text == "[Caption sent with the image]\nhot water cures covid, share this!"


@pytest.mark.anyio
async def test_normalize_image_ignores_model_text_when_has_readable_text_is_false(monkeypatch):
    """Defense in depth: even if the model fills extracted_text despite saying
    has_readable_text=false, don't use it -- trust the flag, not the string."""
    _stub_vision(monkeypatch, {"has_readable_text": False, "extracted_text": "some text that slipped through"})

    r = await normalize_image(b"fake-bytes", "image/jpeg", None)
    assert r.text is None


@pytest.mark.anyio
async def test_normalize_image_strips_links_from_the_description(monkeypatch):
    _stub_vision(
        monkeypatch,
        {"has_readable_text": False, "extracted_text": "", "description": "A poster saying visit http://evil.example now"},
    )
    r = await normalize_image(b"fake-bytes", "image/jpeg", None)
    assert "evil.example" not in r.description
