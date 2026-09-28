import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.whatsapp.parser import extract_messages

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


def test_post_webhook_rejects_bad_signature():
    body = FIXTURE.read_bytes()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


def test_post_webhook_accepts_valid_signature_and_acks_fast(monkeypatch):
    sent = {}

    async def fake_mark_read(wamid):
        return None

    async def fake_send_text_reply(to, body, reply_to_wamid):
        sent["to"] = to
        sent["body"] = body
        sent["reply_to_wamid"] = reply_to_wamid
        return {"messages": [{"id": "wamid.REPLY"}]}

    monkeypatch.setattr("app.main.mark_read", fake_mark_read)
    monkeypatch.setattr("app.main.send_text_reply", fake_send_text_reply)

    body = FIXTURE.read_bytes()
    resp = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert sent["reply_to_wamid"] == "wamid.TEST123"
    assert "Drinking hot water cures COVID instantly" in sent["body"]
