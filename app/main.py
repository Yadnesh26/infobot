import json
import logging

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.db import cache as db_cache
from app.db import submissions as db_submissions
from app.pipeline.normalize import AudioTooLongError, extract_audio_track, normalize_audio, normalize_image
from app.pipeline.orchestrator import run_text_pipeline
from app.util import hash_phone
from app.whatsapp.client import download_media, mark_read, send_text_reply
from app.whatsapp.parser import InboundMessage, extract_messages
from app.whatsapp.verify import valid_signature

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("infobot")

app = FastAPI()


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
        # Idempotency is checked inside handle_message against the DB now (M3),
        # not here, so the ack below stays fast even though that check is a
        # network call -- see submissions.claim_submission.
        bg.add_task(handle_message, msg)

    # Ack immediately — the pipeline runs in the background task above.
    return Response(status_code=200)


async def handle_message(msg: InboundMessage) -> None:
    try:
        if msg.is_reaction:
            logger.info("Reaction %s on %s from %s", msg.reaction_emoji, msg.reaction_target_wamid, msg.sender)
            return

        is_new = await db_submissions.claim_submission(
            wamid=msg.wamid,
            wa_user_hash=hash_phone(msg.sender),
            input_type=msg.type,
            forwarded=msg.forwarded,
            frequently_forwarded=msg.frequently_forwarded,
        )
        if not is_new:
            logger.info("Dropping duplicate delivery of %s", msg.wamid)
            return

        await mark_read(msg.wamid)

        reply_text, pending_write, cache_hit = await _compose_reply(msg)
        await send_text_reply(to=msg.sender, body=reply_text, reply_to_wamid=msg.wamid)

        # Cache write happens only after a successful send, so a DB hiccup here
        # never costs the user their answer.
        if pending_write is not None:
            try:
                await db_cache.insert_claim(pending_write)
            except Exception:
                logger.exception("Cache write failed after successful send for wamid=%s", msg.wamid)

        try:
            await db_submissions.mark_submission(msg.wamid, "done", cache_hit=cache_hit)
        except Exception:
            logger.exception("Failed to mark submission done for wamid=%s", msg.wamid)

    except Exception as exc:
        logger.exception("Pipeline failed for wamid=%s", msg.wamid)
        try:
            await db_submissions.mark_submission(msg.wamid, "error", error=str(exc))
        except Exception:
            logger.exception("Failed to mark submission error for wamid=%s", msg.wamid)
        try:
            await send_text_reply(
                to=msg.sender,
                body="Sorry, something went wrong processing that message. Please try again.",
                reply_to_wamid=msg.wamid,
            )
        except Exception:
            logger.exception("Failed to send apology reply for wamid=%s", msg.wamid)


async def _compose_reply(msg: InboundMessage) -> tuple[str, dict | None, str | None]:
    if msg.type == "text" and msg.text:
        result = await run_text_pipeline(msg.text, msg.frequently_forwarded)
        return result.reply_text, result.pending_claim_write, result.cache_hit

    if msg.type == "image":
        return await _compose_image_reply(msg)

    if msg.type in ("audio", "video"):
        return await _compose_audio_or_video_reply(msg)

    return "Got your message, but I couldn't find any text to check.", None, None


async def _compose_image_reply(msg: InboundMessage) -> tuple[str, dict | None, str | None]:
    if not msg.media_id:
        return "Got your image, but there was no attachment to download.", None, None

    try:
        image_bytes = await download_media(msg.media_id)
    except Exception:
        logger.exception("Failed to download image for wamid=%s", msg.wamid)
        return "I couldn't download that image -- could you try resending it?", None, None

    mime_type = msg.media_mime_type or "image/jpeg"
    extracted_text = await normalize_image(image_bytes, mime_type, msg.caption)

    if extracted_text is None:
        return (
            "I couldn't find any readable text in that image, and there's no "
            "caption either -- nothing here for me to check.",
            None,
            None,
        )

    result = await run_text_pipeline(extracted_text, msg.frequently_forwarded)
    return result.reply_text, result.pending_claim_write, result.cache_hit


async def _compose_audio_or_video_reply(msg: InboundMessage) -> tuple[str, dict | None, str | None]:
    if not msg.media_id:
        return f"Got your {msg.type}, but there was no attachment to download.", None, None

    try:
        media_bytes = await download_media(msg.media_id)
    except Exception:
        logger.exception("Failed to download %s for wamid=%s", msg.type, msg.wamid)
        return f"I couldn't download that {msg.type} -- could you try resending it?", None, None

    if msg.type == "video":
        try:
            audio_bytes = await extract_audio_track(media_bytes, msg.media_mime_type)
        except Exception:
            logger.exception("Failed to extract audio from video for wamid=%s", msg.wamid)
            return "I couldn't process the audio in that video -- could you try resending it?", None, None
        audio_mime_type = "audio/wav"
    else:
        audio_bytes = media_bytes
        audio_mime_type = msg.media_mime_type

    try:
        transcript = await normalize_audio(audio_bytes, audio_mime_type)
    except AudioTooLongError as exc:
        minutes = settings.MAX_AUDIO_SECONDS // 60
        logger.info(
            "Rejecting %s from wamid=%s: %.0fs exceeds cap", msg.type, msg.wamid, exc.duration_seconds
        )
        return (
            f"That {msg.type} is too long to check (over {minutes} minutes). "
            "Please trim it or send a shorter clip.",
            None,
            None,
        )

    if transcript is None:
        return (
            f"I couldn't transcribe that {msg.type} -- could you try resending it, "
            "or send the text of the claim instead?",
            None,
            None,
        )

    combined = transcript
    if msg.caption and msg.caption.strip():
        combined = f"{transcript}\n{msg.caption.strip()}"

    result = await run_text_pipeline(combined, msg.frequently_forwarded)
    return result.reply_text, result.pending_claim_write, result.cache_hit
