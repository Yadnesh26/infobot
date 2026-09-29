import json
import logging

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.pipeline.orchestrator import run_text_pipeline
from app.whatsapp.client import mark_read, send_text_reply
from app.whatsapp.parser import InboundMessage, extract_messages
from app.whatsapp.verify import valid_signature

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("infobot")

app = FastAPI()

# In-memory idempotency guard for M1. Meta retries webhook deliveries, so the same
# wamid can arrive more than once. This is process-local and non-persistent — it
# will be replaced by a DB-backed unique constraint on submissions.wa_message_id
# once Supabase is wired up (Phase 3 / M3), which also survives restarts.
_seen_wamids: set[str] = set()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.WA_VERIFY_TOKEN
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("forbidden", status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request, bg: BackgroundTasks):
    raw = await request.body()

    if not valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Rejected webhook with invalid signature")
        return Response(status_code=403)

    payload = json.loads(raw)
    messages = extract_messages(payload)

    for msg in messages:
        if msg.wamid in _seen_wamids:
            logger.info("Dropping duplicate delivery of %s", msg.wamid)
            continue
        _seen_wamids.add(msg.wamid)
        bg.add_task(handle_message, msg)

    # Ack immediately — the pipeline runs in the background task above.
    return Response(status_code=200)


async def handle_message(msg: InboundMessage) -> None:
    try:
        if msg.is_reaction:
            logger.info("Reaction %s on %s from %s", msg.reaction_emoji, msg.reaction_target_wamid, msg.sender)
            return

        await mark_read(msg.wamid)

        reply_text = await _compose_reply(msg)
        await send_text_reply(to=msg.sender, body=reply_text, reply_to_wamid=msg.wamid)

    except Exception:
        logger.exception("Pipeline failed for wamid=%s", msg.wamid)
        try:
            await send_text_reply(
                to=msg.sender,
                body="Sorry, something went wrong processing that message. Please try again.",
                reply_to_wamid=msg.wamid,
            )
        except Exception:
            logger.exception("Failed to send apology reply for wamid=%s", msg.wamid)


async def _compose_reply(msg: InboundMessage) -> str:
    if msg.type == "text" and msg.text:
        return await run_text_pipeline(msg.text, msg.frequently_forwarded)
    if msg.type in ("image", "audio", "video"):
        extra = f" Caption: \"{msg.caption}\"" if msg.caption else ""
        return f"Got your {msg.type}.{extra} (Media verification not wired up yet -- text-only for now.)"
    return "Got your message, but I couldn't find any text to check."
