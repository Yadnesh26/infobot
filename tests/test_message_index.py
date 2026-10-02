"""The same message must be read, and so answered, the same way every time."""

import pytest

from app import main
from app.pipeline import messages
from app.pipeline.compose import compose_t3b_reply
from app.pipeline.orchestrator import media_key, run_text_pipeline
from app.whatsapp.parser import InboundMessage
from tests.test_multiclaim import _claim, _classify_json, _stub_cache, _stub_models


def test_medical_stop_is_one_language_at_a_time():
    for lang in ("en", "hi", "mr"):
        reply = compose_t3b_reply(lang)
        assert "104" in reply and reply == compose_t3b_reply(lang)
    assert not any("ऀ" <= c <= "ॿ" for c in compose_t3b_reply("en"))
    assert compose_t3b_reply("hi") != compose_t3b_reply("mr") != compose_t3b_reply("en")
    # Unknown or missing language falls back to English, never to a stack of languages.
    assert compose_t3b_reply(None) == compose_t3b_reply("en") == compose_t3b_reply("xx")
    assert set(messages.MEDICAL_STOP) == {"en", "hi", "mr"}


@pytest.mark.anyio
async def test_repeated_text_is_read_the_same_way_even_if_the_model_would_not(monkeypatch, offline_message_index):
    claims_cache: dict = {}
    _stub_cache(monkeypatch, exact=claims_cache)
    _stub_models(monkeypatch, _classify_json([_claim(1, "t1"), _claim(2, "t1")]), {1: "true", 2: "false"})
    first = await run_text_pipeline("two things", False)
    assert first.index_entry
    await _remember(offline_message_index, first, claims_cache)

    # Now the model would read it as a single medical claim; the remembered reading wins.
    _stub_models(monkeypatch, _classify_json([_claim(9, "t3b")]), {})
    second = await run_text_pipeline("two things", False)
    assert second.reply_text == first.reply_text
    assert second.meta.get("from_index") is True


async def _remember(store, result, claims_cache=None):
    """What the bot writes after a successful send: the index entry and the claim verdicts."""
    store.setdefault(result.index_entry["msg_key"], result.index_entry)
    for w in result.pending_claim_writes:
        (claims_cache if claims_cache is not None else {})[w["claim_hash"]] = {**w, "id": "row"}


@pytest.mark.anyio
async def test_same_image_gets_the_same_reply_when_vision_and_classifier_vary(monkeypatch, offline_message_index):
    claims_cache: dict = {}
    _stub_cache(monkeypatch, exact=claims_cache)
    image = b"poster-bytes"
    calls = {"vision": 0}

    async def download(_id):
        return image

    class Reading:
        def __init__(self, text):
            self.text, self.kind, self.description = text, "text", ""

    async def vision(_bytes, _mime, _caption):
        calls["vision"] += 1
        return Reading(f"poster text variant {calls['vision']}")

    monkeypatch.setattr(main, "download_media", download)
    monkeypatch.setattr(main, "normalize_image", vision)

    async def reply():
        msg = InboundMessage(wamid="w", sender="91", type="image", media_id="m", media_mime_type="image/jpeg")
        return await main._compose_reply(msg, None)

    _stub_models(monkeypatch, _classify_json([_claim(1, "t1")]), {1: "false"})
    first = await reply()
    assert first.index_entry and first.index_entry["msg_key"] == media_key("image", image + b"\x00") + "|-"
    await _remember(offline_message_index, first, claims_cache)

    # Second time the (stand-in) models would say something else entirely.
    _stub_models(monkeypatch, _classify_json([_claim(1, "t3b")]), {1: "true"})
    second = await reply()
    assert calls["vision"] == 1  # vision was not even asked again
    assert second.meta.get("from_index") is True
    assert second.reply_text.splitlines()[0] == first.reply_text.splitlines()[0]


@pytest.mark.anyio
async def test_remembered_reading_is_not_used_for_a_different_reply_language(monkeypatch, offline_message_index):
    _stub_cache(monkeypatch)
    _stub_models(monkeypatch, _classify_json([_claim(1, "t1"), _claim(2, "t1")]), {})
    first = await run_text_pipeline("two things", False)
    await _remember(offline_message_index, first)
    second = await run_text_pipeline("two things", False, reply_lang="hi")
    assert not second.meta.get("from_index")


async def _fake_translate(texts, language):
    return [f"[{language}] {t}" for t in texts]


def _cached_row(verdict="false"):
    return {
        "id": "row", "claim_text_en": "claim 1 english", "tier": "t1", "verdict": verdict, "confidence": 90,
        "explanation_en": "explained", "sources": [],
    }


@pytest.mark.anyio
async def test_cached_english_answer_is_served_in_the_language_chosen_later(monkeypatch):
    from app.db.cache import hash_claim

    _stub_cache(monkeypatch, exact={hash_claim("same text"): _cached_row()})
    monkeypatch.setattr("app.pipeline.orchestrator.translate_texts", _fake_translate)
    en = await run_text_pipeline("same text", False, reply_lang="en")
    hi = await run_text_pipeline("same text", False, reply_lang="hi")
    assert "[hi] explained" in hi.reply_text and "[hi]" not in en.reply_text
    assert messages.verdict_label("false", "hi") in hi.reply_text
    assert messages.verdict_label("false", "hi") not in en.reply_text


@pytest.mark.anyio
@pytest.mark.parametrize("lang", ["en", "hi", "mr"])
async def test_cached_medical_stop_follows_the_chosen_language(monkeypatch, lang):
    from app.db.cache import hash_claim

    _stub_cache(monkeypatch, exact={hash_claim("same text"): _cached_row("refused")})
    r = await run_text_pipeline("same text", False, reply_lang=lang)
    assert r.reply_text == compose_t3b_reply(lang)


@pytest.mark.anyio
async def test_same_image_in_a_new_language_is_answered_in_that_language(monkeypatch, offline_message_index):
    claims_cache: dict = {}
    _stub_cache(monkeypatch, exact=claims_cache)
    monkeypatch.setattr("app.pipeline.orchestrator.translate_texts", _fake_translate)

    async def download(_id):
        return b"img"

    class Reading:
        text, kind, description = "poster text", "text", ""

    async def vision(_bytes, _mime, _caption):
        return Reading()

    monkeypatch.setattr(main, "download_media", download)
    monkeypatch.setattr(main, "normalize_image", vision)
    _stub_models(monkeypatch, _classify_json([_claim(1, "t1"), _claim(2, "t1")]), {1: "true", 2: "false"})

    async def reply(lang):
        msg = InboundMessage(wamid="w", sender="91", type="image", media_id="m", media_mime_type="image/jpeg")
        r = await main._compose_reply(msg, lang)
        if r.index_entry:
            await _remember(offline_message_index, r, claims_cache)
        return r

    en = await reply("en")
    hi = await reply("hi")
    again_en = await reply("en")
    assert "[hi]" not in en.reply_text and "[hi]" in hi.reply_text
    assert "[hi] claim 1 english" in hi.reply_text  # the claim headline too, not only the explanation
    assert again_en.reply_text == en.reply_text and again_en.meta.get("from_index")
    assert set(offline_message_index) == {k for k in offline_message_index if k.endswith(("|en", "|hi"))}
    assert len(offline_message_index) == 2
