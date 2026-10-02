import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import _compose_audio_or_video_reply, _compose_image_reply, app
from app.pipeline.normalize import AudioTooLongError, ImageReading
from app.whatsapp.parser import InboundMessage, extract_messages

settings.WA_APP_SECRET = "test-secret"
settings.WA_VERIFY_TOKEN = "test-verify-token"

client = TestClient(app)

FIXTURE = Path(__file__).parent / "fixtures" / "text_message.json"


def _sign(body: bytes) -> str:
    digest = hmac.new(settings.WA_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_webhook_verification_get():
    resp = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "12345",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_webhook_verification_get_wrong_token():
    resp = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"},
    )
    assert resp.status_code == 403


def test_parses_fixture_into_message():
    payload = json.loads(FIXTURE.read_text())
    messages = extract_messages(payload)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.wamid == "wamid.TEST123"
    assert msg.type == "text"
    assert msg.text == "Drinking hot water cures COVID instantly"
    assert msg.forwarded is True


def test_parses_image_message_with_mime_type_and_caption():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.IMG1",
                                    "from": "919876543210",
                                    "type": "image",
                                    "image": {
                                        "id": "media123",
                                        "mime_type": "image/jpeg",
                                        "caption": "share this everyone",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    messages = extract_messages(payload)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.type == "image"
    assert msg.media_id == "media123"
    assert msg.media_mime_type == "image/jpeg"
    assert msg.caption == "share this everyone"


def test_post_webhook_rejects_bad_signature():
    body = FIXTURE.read_bytes()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


def _patch_common(monkeypatch, sent, *, is_new=True):
    from app.pipeline.orchestrator import PipelineResult

    async def fake_mark_read(wamid):
        return None

    async def fake_send_text_reply(to, body, reply_to_wamid):
        sent.setdefault("send_calls", []).append(
            {"to": to, "body": body, "reply_to_wamid": reply_to_wamid}
        )
        return {"messages": [{"id": "wamid.REPLY"}]}

    async def fake_run_text_pipeline(raw_text, frequently_forwarded, **_kw):
        sent["pipeline_input"] = raw_text
        return PipelineResult("FAKE VERDICT REPLY")

    async def fake_claim_submission(**kwargs):
        return is_new

    async def fake_mark_submission(*args, **kwargs):
        sent["marked_status"] = args[1] if len(args) > 1 else kwargs.get("status")

    async def fake_check_and_increment(wa_user_hash):
        return True

    async def fake_set_reply_wamid(wamid, reply_wamid):
        sent["reply_wamid"] = reply_wamid

    monkeypatch.setattr("app.main.mark_read", fake_mark_read)
    monkeypatch.setattr("app.main.send_text_reply", fake_send_text_reply)
    monkeypatch.setattr("app.main.run_text_pipeline", fake_run_text_pipeline)
    monkeypatch.setattr("app.main.db_submissions.claim_submission", fake_claim_submission)
    monkeypatch.setattr("app.main.db_submissions.mark_submission", fake_mark_submission)
    monkeypatch.setattr("app.main.db_rate_limit.check_and_increment", fake_check_and_increment)
    monkeypatch.setattr("app.main.db_submissions.set_reply_wamid", fake_set_reply_wamid)


def test_post_webhook_accepts_valid_signature_and_acks_fast(monkeypatch):
    sent = {}
    _patch_common(monkeypatch, sent, is_new=True)

    body = FIXTURE.read_bytes()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert sent["pipeline_input"] == "Drinking hot water cures COVID instantly"
    assert sent["reply_wamid"] == "wamid.REPLY"
    assert len(sent["send_calls"]) == 1
    assert sent["send_calls"][0]["reply_to_wamid"] == "wamid.TEST123"
    assert sent["send_calls"][0]["body"] == "FAKE VERDICT REPLY"
    assert sent["marked_status"] == "done"


def test_post_webhook_still_replies_when_mark_read_fails(monkeypatch):
    """Regression: a failing read receipt used to abort the whole request,
    costing the user their answer over a purely cosmetic call."""
    sent = {}
    _patch_common(monkeypatch, sent, is_new=True)

    async def failing_mark_read(wamid):
        raise RuntimeError("meta returned 400")

    monkeypatch.setattr("app.main.mark_read", failing_mark_read)

    body = FIXTURE.read_bytes()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert len(sent["send_calls"]) == 1
    assert sent["send_calls"][0]["body"] == "FAKE VERDICT REPLY"
    assert sent["marked_status"] == "done"


def test_post_webhook_drops_duplicate_delivery(monkeypatch):
    sent = {}
    _patch_common(monkeypatch, sent, is_new=False)

    body = FIXTURE.read_bytes()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert "send_calls" not in sent  # claim_submission said "not new" -> no reply sent


def _img_msg(wamid="wamid.IMG", caption=None):
    return InboundMessage(
        wamid=wamid, sender="919876543210", type="image",
        media_id="media123", media_mime_type="image/jpeg", caption=caption,
    )


def _av_msg(kind, wamid="wamid.AV", caption=None):
    return InboundMessage(
        wamid=wamid, sender="919876543210", type=kind,
        media_id="media456", media_mime_type="audio/ogg" if kind == "audio" else "video/mp4", caption=caption,
    )


def _patch_download(monkeypatch, payload=b"fake-bytes"):
    async def fake_download_media(media_id):
        return payload

    monkeypatch.setattr("app.main.download_media", fake_download_media)


def _patch_pipeline(monkeypatch, seen):
    from app.pipeline.orchestrator import PipelineResult

    async def fake_run_text_pipeline(raw_text, frequently_forwarded, **_kw):
        seen["text"] = raw_text
        return PipelineResult("PIPELINE REPLY")

    monkeypatch.setattr("app.main.run_text_pipeline", fake_run_text_pipeline)


@pytest.mark.anyio
async def test_compose_image_reply_runs_labelled_text_through_text_pipeline(monkeypatch):
    seen = {}
    _patch_download(monkeypatch, b"fake-image-bytes")
    _patch_pipeline(monkeypatch, seen)

    async def fake_normalize_image(image_bytes, mime_type, caption):
        assert image_bytes == b"fake-image-bytes" and mime_type == "image/jpeg"
        return ImageReading(text="[Text inside the image]\nhot water cures covid", has_image_text=True)

    monkeypatch.setattr("app.main.normalize_image", fake_normalize_image)

    result = await _compose_image_reply(_img_msg())
    assert result.reply_text == "PIPELINE REPLY"
    assert seen["text"] == "[Text inside the image]\nhot water cures covid"


@pytest.mark.anyio
async def test_compose_image_reply_photo_only_describes_and_explains_limits(monkeypatch):
    _patch_download(monkeypatch)

    async def fake_normalize_image(image_bytes, mime_type, caption):
        return ImageReading(text=None, description="A flooded street")

    monkeypatch.setattr("app.main.normalize_image", fake_normalize_image)

    result = await _compose_image_reply(_img_msg())
    assert "A flooded street" in result.reply_text
    assert "Google Lens" in result.reply_text
    assert result.pending_claim_writes == []


@pytest.mark.anyio
async def test_compose_image_reply_with_nothing_at_all_asks_for_a_clearer_image_in_one_language(monkeypatch):
    _patch_download(monkeypatch)

    async def fake_normalize_image(image_bytes, mime_type, caption):
        return ImageReading(text=None)

    monkeypatch.setattr("app.main.normalize_image", fake_normalize_image)

    result = await _compose_image_reply(_img_msg())
    assert "couldn't read any text" in result.reply_text
    # Regression: an unknown language used to stack English, Hindi and Marathi.
    assert "पढ़ने लायक" not in result.reply_text and "वाचता येईल" not in result.reply_text

    hindi = await _compose_image_reply(_img_msg(), "hi")
    assert "पढ़ने लायक" in hindi.reply_text and "couldn't read" not in hindi.reply_text


@pytest.mark.anyio
async def test_a_blurry_image_is_not_answered_with_the_cannot_judge_authenticity_text(monkeypatch):
    """Regression from manual testing: a blurred poster got 'I can't tell whether a
    photo is genuine...' instead of a request for a clearer picture."""
    _patch_download(monkeypatch)

    async def fake_normalize_image(image_bytes, mime_type, caption):
        return ImageReading(text=None, description="A heavily blurred image", kind="unreadable")

    monkeypatch.setattr("app.main.normalize_image", fake_normalize_image)

    result = await _compose_image_reply(_img_msg())
    assert "Google Lens" not in result.reply_text
    assert "clearer" in result.reply_text
    assert result.meta["input_kind"] == "unreadable"


@pytest.mark.anyio
async def test_compose_image_reply_handles_download_failure(monkeypatch):
    async def fake_download_media(media_id):
        raise RuntimeError("network error")

    monkeypatch.setattr("app.main.download_media", fake_download_media)

    result = await _compose_image_reply(_img_msg())
    assert "try again" in result.reply_text
    assert result.pending_claim_writes == []


@pytest.mark.anyio
async def test_compose_image_reply_rejects_oversized_image_before_any_model_call(monkeypatch):
    from app.pipeline.guard import MAX_IMAGE_BYTES

    _patch_download(monkeypatch, b"x" * (MAX_IMAGE_BYTES + 1))

    async def must_not_run(*a, **k):
        raise AssertionError("an oversized image must not reach the vision model")

    monkeypatch.setattr("app.main.normalize_image", must_not_run)
    result = await _compose_image_reply(_img_msg())
    assert result.meta["input_kind"] == "too_large"


@pytest.mark.anyio
async def test_compose_image_reply_falls_back_to_caption_when_vision_is_down(monkeypatch):
    from app.providers.gemini import GeminiError

    seen = {}
    _patch_download(monkeypatch)
    _patch_pipeline(monkeypatch, seen)

    async def vision_down(image_bytes, mime_type, caption):
        raise GeminiError("gemini down, and images have no fallback")

    monkeypatch.setattr("app.main.normalize_image", vision_down)

    result = await _compose_image_reply(_img_msg(caption="hot water cures covid"))
    assert result.reply_text == "PIPELINE REPLY"
    assert seen["text"] == "[Caption sent with the image]\nhot water cures covid"


@pytest.mark.anyio
async def test_compose_image_reply_says_busy_when_vision_is_down_and_no_caption(monkeypatch):
    from app.providers.gemini import GeminiError

    _patch_download(monkeypatch)

    async def vision_down(image_bytes, mime_type, caption):
        raise GeminiError("gemini down, and images have no fallback")

    monkeypatch.setattr("app.main.normalize_image", vision_down)

    result = await _compose_image_reply(_img_msg())
    assert "overloaded" in result.reply_text


@pytest.mark.anyio
async def test_compose_audio_reply_labels_transcript_and_caption(monkeypatch):
    seen = {}
    _patch_download(monkeypatch, b"fake-audio-bytes")
    _patch_pipeline(monkeypatch, seen)

    async def fake_normalize_audio(audio_bytes, mime_type):
        assert audio_bytes == b"fake-audio-bytes" and mime_type == "audio/ogg"
        return "hot water cures covid"

    monkeypatch.setattr("app.main.normalize_audio", fake_normalize_audio)

    result = await _compose_audio_or_video_reply(_av_msg("audio", caption="is this true?"))
    assert result.reply_text == "PIPELINE REPLY"
    assert seen["text"] == (
        "[Voice note transcript]\nhot water cures covid\n\n[Caption sent with the audio]\nis this true?"
    )


@pytest.mark.anyio
async def test_compose_video_reply_extracts_audio_track_first(monkeypatch):
    seen, calls = {}, {}
    _patch_download(monkeypatch, b"fake-video-bytes")
    _patch_pipeline(monkeypatch, seen)

    async def fake_extract_audio_track(video_bytes, mime_type):
        calls["extracted"] = True
        assert video_bytes == b"fake-video-bytes"
        return b"fake-extracted-audio"

    async def fake_normalize_audio(audio_bytes, mime_type):
        assert audio_bytes == b"fake-extracted-audio" and mime_type == "audio/wav"
        return "some transcribed claim"

    monkeypatch.setattr("app.main.extract_audio_track", fake_extract_audio_track)
    monkeypatch.setattr("app.main.normalize_audio", fake_normalize_audio)

    result = await _compose_audio_or_video_reply(_av_msg("video"))
    assert calls.get("extracted") is True
    assert result.reply_text == "PIPELINE REPLY"
    assert seen["text"].startswith("[Video transcript]")


@pytest.mark.anyio
async def test_compose_audio_reply_rejects_over_duration_cap(monkeypatch):
    _patch_download(monkeypatch)

    async def fake_normalize_audio(audio_bytes, mime_type):
        raise AudioTooLongError(999)

    monkeypatch.setattr("app.main.normalize_audio", fake_normalize_audio)

    result = await _compose_audio_or_video_reply(_av_msg("audio"))
    assert "too long" in result.reply_text
    assert result.pending_claim_writes == []


@pytest.mark.anyio
async def test_compose_audio_reply_graceful_when_transcription_fails(monkeypatch):
    _patch_download(monkeypatch)

    async def fake_normalize_audio(audio_bytes, mime_type):
        return None

    monkeypatch.setattr("app.main.normalize_audio", fake_normalize_audio)

    result = await _compose_audio_or_video_reply(_av_msg("audio"))
    assert "overloaded" in result.reply_text


@pytest.mark.anyio
async def test_compose_audio_reply_with_no_speech_says_so(monkeypatch):
    _patch_download(monkeypatch)

    async def fake_normalize_audio(audio_bytes, mime_type):
        return ""

    monkeypatch.setattr("app.main.normalize_audio", fake_normalize_audio)

    result = await _compose_audio_or_video_reply(_av_msg("audio"))
    assert "couldn't make out any speech" in result.reply_text
    assert result.meta["input_kind"] == "no_speech"


@pytest.mark.anyio
async def test_compose_audio_reply_with_no_speech_still_checks_the_caption(monkeypatch):
    seen = {}
    _patch_download(monkeypatch)
    _patch_pipeline(monkeypatch, seen)

    async def fake_normalize_audio(audio_bytes, mime_type):
        return ""

    monkeypatch.setattr("app.main.normalize_audio", fake_normalize_audio)

    result = await _compose_audio_or_video_reply(_av_msg("audio", caption="petrol is Rs 200 now"))
    assert result.reply_text == "PIPELINE REPLY"
    assert "petrol is Rs 200 now" in seen["text"]


@pytest.mark.anyio
async def test_compose_video_reply_with_undecodable_file_is_handled(monkeypatch):
    from app.pipeline.normalize import MediaUnreadableError

    _patch_download(monkeypatch)

    async def broken_extract(video_bytes, mime_type):
        raise MediaUnreadableError("ffmpeg failed")

    monkeypatch.setattr("app.main.extract_audio_track", broken_extract)

    result = await _compose_audio_or_video_reply(_av_msg("video"))
    assert "couldn't make out any speech" in result.reply_text
    assert result.meta["input_kind"] == "unreadable"


@pytest.mark.anyio
async def test_compose_audio_reply_rejects_oversized_file_before_any_processing(monkeypatch):
    from app.pipeline.guard import MAX_AV_BYTES

    _patch_download(monkeypatch, b"x" * (MAX_AV_BYTES + 1))

    async def must_not_run(*a, **k):
        raise AssertionError("an oversized file must not be processed")

    monkeypatch.setattr("app.main.normalize_audio", must_not_run)
    monkeypatch.setattr("app.main.extract_audio_track", must_not_run)
    result = await _compose_audio_or_video_reply(_av_msg("video"))
    assert result.meta["input_kind"] == "too_large"


@pytest.mark.anyio
async def test_compose_reply_for_unsupported_message_type_points_to_what_works():
    from app.main import _compose_reply

    msg = InboundMessage(wamid="wamid.STK", sender="919876543210", type="sticker")
    result = await _compose_reply(msg)
    assert "fact-check" in result.reply_text


# --- M8: rate limiting, reactions/feedback, trending ---


def test_post_webhook_rejects_over_rate_limit(monkeypatch):
    sent = {}
    _patch_common(monkeypatch, sent, is_new=True)

    async def fake_check_and_increment(wa_user_hash):
        return False

    monkeypatch.setattr("app.main.db_rate_limit.check_and_increment", fake_check_and_increment)

    body = FIXTURE.read_bytes()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert "pipeline_input" not in sent  # pipeline never ran
    assert len(sent["send_calls"]) == 1
    assert "wait a little" in sent["send_calls"][0]["body"]
    assert sent["marked_status"] == "rate_limited"


@pytest.mark.anyio
async def test_reaction_with_known_reply_records_feedback(monkeypatch):
    from app.main import handle_message

    recorded = {}

    async def fake_find_submission_id(reply_wamid):
        assert reply_wamid == "wamid.REPLY123"
        return "submission-abc"

    async def fake_insert_feedback(submission_id, reply_message_id, emoji):
        recorded["submission_id"] = submission_id
        recorded["reply_message_id"] = reply_message_id
        recorded["emoji"] = emoji

    monkeypatch.setattr("app.main.db_submissions.find_submission_id_by_reply_wamid", fake_find_submission_id)
    monkeypatch.setattr("app.main.db_feedback.insert_feedback", fake_insert_feedback)

    msg = InboundMessage(
        wamid="wamid.REACT1", sender="919876543210", type="reaction",
        is_reaction=True, reaction_emoji="\U0001F44D", reaction_target_wamid="wamid.REPLY123",
    )
    await handle_message(msg)

    assert recorded == {
        "submission_id": "submission-abc",
        "reply_message_id": "wamid.REPLY123",
        "emoji": "\U0001F44D",
    }


@pytest.mark.anyio
async def test_reaction_on_unknown_reply_does_not_crash_or_insert(monkeypatch):
    from app.main import handle_message

    async def fake_find_submission_id(reply_wamid):
        return None

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("insert_feedback should not be called when the reply is unknown")

    monkeypatch.setattr("app.main.db_submissions.find_submission_id_by_reply_wamid", fake_find_submission_id)
    monkeypatch.setattr("app.main.db_feedback.insert_feedback", fail_if_called)

    msg = InboundMessage(
        wamid="wamid.REACT2", sender="919876543210", type="reaction",
        is_reaction=True, reaction_emoji="\U0001F44D", reaction_target_wamid="wamid.UNKNOWN",
    )
    await handle_message(msg)  # must not raise


def test_trending_endpoint_returns_data_from_db(monkeypatch):
    async def fake_get_trending(days=7, limit=20):
        return [{"claim_text_en": "x", "verdict": "false", "times_seen": 3, "last_seen_at": "2026-09-01T00:00:00Z"}]

    monkeypatch.setattr("app.main.db_trending.get_trending", fake_get_trending)

    resp = client.get("/trending")
    assert resp.status_code == 200
    assert resp.json()[0]["claim_text_en"] == "x"


@pytest.mark.anyio
async def test_when_vision_fails_and_the_caption_holds_no_claim_the_user_is_told_we_could_not_read_the_image(monkeypatch):
    """Regression: a poster with the caption 'Is this true?' got 'I couldn't pick out a specific
    claim' when the real problem was that the image could not be read."""
    from app.pipeline.orchestrator import PipelineResult
    from app.providers.gemini import GeminiError

    _patch_download(monkeypatch)

    async def vision_down(image_bytes, mime_type, caption):
        raise GeminiError("timeout")

    async def caption_only(raw_text, frequently_forwarded, **_kw):
        return PipelineResult("I couldn't pick out a specific claim", meta={"input_kind": "unclear"})

    monkeypatch.setattr("app.main.normalize_image", vision_down)
    monkeypatch.setattr("app.main.run_text_pipeline", caption_only)
    result = await _compose_image_reply(_img_msg(caption="Is this true? Please check"))
    assert "overloaded" in result.reply_text and result.meta["input_kind"] == "vision_unavailable"
