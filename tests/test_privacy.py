import base64

import pytest

from app.db import feedback, submissions
from app.util import hash_id, hash_phone, ref

NUMBER = "919876543210"
# Shaped like a real WhatsApp message id: it embeds the sender's number in base64.
WAMID = "wamid." + base64.urlsafe_b64encode(b"\x1c\x18\x0c" + NUMBER.encode() + b"\x15\x02\x00\x12\x18\x20ABCDEF").decode().rstrip("=")
REPLY_WAMID = "wamid." + base64.urlsafe_b64encode(b"\x1c\x18\x0c" + NUMBER.encode() + b"\x15\x02\x00\x12\x18\x20REPLY1").decode().rstrip("=")


def _decoded(wamid: str) -> str:
    body = wamid.split(".", 1)[1]
    return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("latin1")


def test_the_fixture_id_really_embeds_the_number():
    """Guards the premise of this whole file: if this stops being true the
    other tests prove nothing."""
    assert NUMBER in _decoded(WAMID)
    assert NUMBER in _decoded(REPLY_WAMID)


def test_hash_id_is_deterministic_and_reveals_nothing():
    h = hash_id(WAMID)
    assert h == hash_id(WAMID)
    assert h != WAMID
    assert len(h) == 64
    assert NUMBER not in h


def test_id_hash_and_phone_hash_spaces_are_separate():
    assert hash_id(NUMBER) != hash_phone(NUMBER)


def test_ref_is_short_and_not_the_raw_id():
    assert len(ref(WAMID)) == 10
    assert ref(WAMID) not in WAMID


@pytest.mark.anyio
async def test_claim_submission_never_stores_the_raw_message_id(monkeypatch):
    captured = {}

    async def fake_post(path, json_body, params=None, prefer=None):
        captured["body"] = json_body
        return [{"id": "x"}]

    monkeypatch.setattr("app.db.submissions.db.post", fake_post)

    await submissions.claim_submission(WAMID, hash_phone(NUMBER), "text", True, False)

    assert captured["body"]["wa_message_id"] == hash_id(WAMID)
    assert WAMID not in str(captured["body"])
    assert NUMBER not in str(captured["body"])


@pytest.mark.anyio
async def test_reply_id_is_stored_and_looked_up_by_the_same_hash(monkeypatch):
    seen = {}

    async def fake_patch(path, params, body):
        seen["patch_params"], seen["patch_body"] = params, body

    async def fake_get(path, params):
        seen["get_params"] = params
        return [{"id": "sub-1"}]

    monkeypatch.setattr("app.db.submissions.db.patch", fake_patch)
    monkeypatch.setattr("app.db.submissions.db.get", fake_get)

    await submissions.set_reply_wamid(WAMID, REPLY_WAMID)
    found = await submissions.find_submission_id_by_reply_wamid(REPLY_WAMID)

    assert found == "sub-1"
    # What set wrote is exactly what find searches for -- the reaction join depends on it.
    assert seen["patch_body"]["reply_wamid"] == hash_id(REPLY_WAMID)
    assert seen["get_params"]["reply_wamid"] == f"eq.{hash_id(REPLY_WAMID)}"
    for value in (seen["patch_params"], seen["patch_body"], seen["get_params"]):
        assert REPLY_WAMID not in str(value) and WAMID not in str(value)


@pytest.mark.anyio
async def test_feedback_never_stores_the_raw_reply_id(monkeypatch):
    captured = {}

    async def fake_post(path, json_body, params=None, prefer=None):
        captured["body"] = json_body

    monkeypatch.setattr("app.db.feedback.db.post", fake_post)

    await feedback.insert_feedback("sub-1", REPLY_WAMID, "\U0001F44D")

    assert captured["body"]["reply_message_id"] == hash_id(REPLY_WAMID)
    assert REPLY_WAMID not in str(captured["body"])
