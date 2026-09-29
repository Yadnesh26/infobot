import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import _compose_image_reply, app
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

    async def fake_run_text_pipeline(raw_text, frequently_forwarded):
        sent["pipeline_input"] = raw_text
        return PipelineResult("FAKE VERDICT REPLY")

    async def fake_claim_submission(**kwargs):
        return is_new

    async def fake_mark_submission(*args, **kwargs):
        sent["marked_status"] = args[1] if len(args) > 1 else kwargs.get("status")

    monkeypatch.setattr("app.main.mark_read", fake_mark_read)
    monkeypatch.setattr("app.main.send_text_reply", fake_send_text_reply)
    monkeypatch.setattr("app.main.run_text_pipeline", fake_run_text_pipeline)
    monkeypatch.setattr("app.main.db_submissions.claim_submission", fake_claim_submission)
    monkeypatch.setattr("app.main.db_submissions.mark_submission", fake_mark_submission)


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
    assert len(sent["send_calls"]) == 1
    assert sent["send_calls"][0]["reply_to_wamid"] == "wamid.TEST123"
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


@pytest.mark.anyio
async def test_compose_image_reply_runs_ocr_text_through_text_pipeline(monkeypatch):
    from app.pipeline.orchestrator import PipelineResult

    async def fake_download_media(media_id):
        assert media_id == "media123"
        return b"fake-image-bytes"

    async def fake_normalize_image(image_bytes, mime_type, caption):
        assert image_bytes == b"fake-image-bytes"
        assert mime_type == "image/jpeg"
        return "hot water cures covid"

    async def fake_run_text_pipeline(raw_text, frequently_forwarded):
        assert raw_text == "hot water cures covid"
        return PipelineResult("IMAGE VERDICT REPLY")

    monkeypatch.setattr("app.main.download_media", fake_download_media)
    monkeypatch.setattr("app.main.normalize_image", fake_normalize_image)
    monkeypatch.setattr("app.main.run_text_pipeline", fake_run_text_pipeline)

    msg = InboundMessage(
        wamid="wamid.IMG1", sender="919876543210", type="image",
        media_id="media123", media_mime_type="image/jpeg", caption=None,
    )
    reply_text, pending_write, cache_hit = await _compose_image_reply(msg)
    assert reply_text == "IMAGE VERDICT REPLY"


@pytest.mark.anyio
async def test_compose_image_reply_graceful_when_no_text_found(monkeypatch):
    async def fake_download_media(media_id):
        return b"fake-image-bytes"

    async def fake_normalize_image(image_bytes, mime_type, caption):
        return None

    monkeypatch.setattr("app.main.download_media", fake_download_media)
    monkeypatch.setattr("app.main.normalize_image", fake_normalize_image)

    msg = InboundMessage(
        wamid="wamid.IMG2", sender="919876543210", type="image",
        media_id="media123", media_mime_type="image/jpeg", caption=None,
    )
    reply_text, pending_write, cache_hit = await _compose_image_reply(msg)
    assert "couldn't find any readable text" in reply_text
    assert pending_write is None


@pytest.mark.anyio
async def test_compose_image_reply_handles_download_failure(monkeypatch):
    async def fake_download_media(media_id):
        raise RuntimeError("network error")

    monkeypatch.setattr("app.main.download_media", fake_download_media)

    msg = InboundMessage(
        wamid="wamid.IMG3", sender="919876543210", type="image",
        media_id="media123", media_mime_type="image/jpeg", caption=None,
    )
    reply_text, pending_write, cache_hit = await _compose_image_reply(msg)
    assert "couldn't download" in reply_text
    assert pending_write is None
