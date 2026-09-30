import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.db import cache as db_cache
from app.db import feedback as db_feedback
from app.db import rate_limit as db_rate_limit
from app.db import submissions as db_submissions
from app.db import trending as db_trending
from app.pipeline.compose import (
    compose_busy,
    compose_photo_only,
    compose_too_long,
    compose_unreadable_media,
)
from app.pipeline.guard import MAX_AV_BYTES, MAX_IMAGE_BYTES
from app.pipeline.messages import CAPABILITY, NOT_A_CLAIM_FALLBACK, all_langs
from app.pipeline.normalize import (
    AudioTooLongError,
    MediaUnreadableError,
    extract_audio_track,
    label_transcript,
    normalize_audio,
    normalize_image,
)
from app.pipeline.orchestrator import PipelineResult, run_text_pipeline
from app.providers.fallback_llm import FallbackError
from app.providers.gemini import GeminiError
from app.util import hash_phone, ref
from app.whatsapp.client import download_media, mark_read, send_text_reply
from app.whatsapp.parser import InboundMessage, extract_messages
from app.whatsapp.verify import valid_signature

logging.basicConfig(level=logging.INFO)
# httpx logs every request URL at INFO. Keep it quiet: URLs can carry identifiers,
# and a key in a query string would end up in the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("infobot")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await db_submissions.abandon_stale_pending()
    except Exception:
        logger.exception("Startup sweep of stale pending submissions failed")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/trending")
async def trending():
    return await db_trending.get_trending()


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
            await _handle_reaction(msg)
            return

        wa_user_hash = hash_phone(msg.sender)

        is_new = await db_submissions.claim_submission(
            wamid=msg.wamid,
            wa_user_hash=wa_user_hash,
            input_type=msg.type,
            forwarded=msg.forwarded,
            frequently_forwarded=msg.frequently_forwarded,
        )
        if not is_new:
            logger.info("Dropping duplicate delivery of %s", ref(msg.wamid))
            return

        # Cap per-user throughput before spending anything on the pipeline --
        # one user forwarding their whole chat history shouldn't exhaust the
        # daily Gemini/ElevenLabs/Tavily quota for everyone else.
        under_limit = await db_rate_limit.check_and_increment(wa_user_hash)
        if not under_limit:
            logger.info("Rate limit exceeded for wamid=%s", ref(msg.wamid))
            await send_text_reply(
                to=msg.sender,
                body="You've sent quite a few messages in the last hour -- please wait a bit before sending more.",
                reply_to_wamid=msg.wamid,
            )
            await db_submissions.mark_submission(msg.wamid, "rate_limited")
            return

        # A read receipt is a courtesy -- if it fails, the user still deserves their answer.
        try:
            await mark_read(msg.wamid)
        except Exception:
            logger.warning("mark_read failed for wamid=%s, continuing", ref(msg.wamid), exc_info=True)

        result = await _compose_reply(msg)
        logger.info("Pipeline meta for %s: %s", ref(msg.wamid), result.meta)
        send_result = await send_text_reply(to=msg.sender, body=result.reply_text, reply_to_wamid=msg.wamid)

        # Cache writes happen only after a successful send, so a DB hiccup here
        # never costs the user their answer.
        for pending_write in result.pending_claim_writes:
            try:
                await db_cache.insert_claim(pending_write)
            except Exception:
                logger.exception("Cache write failed after successful send for wamid=%s", ref(msg.wamid))

        try:
            reply_wamid = send_result.get("messages", [{}])[0].get("id")
            if reply_wamid:
                await db_submissions.set_reply_wamid(msg.wamid, reply_wamid)
        except Exception:
            logger.exception("Failed to store reply_wamid for wamid=%s", ref(msg.wamid))

        try:
            await db_submissions.mark_submission(msg.wamid, "done", cache_hit=result.cache_hit)
        except Exception:
            logger.exception("Failed to mark submission done for wamid=%s", ref(msg.wamid))

    except Exception as exc:
        logger.exception("Pipeline failed for wamid=%s", ref(msg.wamid))
        try:
            await db_submissions.mark_submission(msg.wamid, "error", error=repr(exc)[:300])
        except Exception:
            logger.exception("Failed to mark submission error for wamid=%s", ref(msg.wamid))
        try:
            await send_text_reply(
                to=msg.sender,
                body=_failure_reply(exc, msg),
                reply_to_wamid=msg.wamid,
            )
        except Exception:
            logger.exception("Failed to send apology reply for wamid=%s", ref(msg.wamid))


_GENERIC_FAILURE = "Sorry, something went wrong processing that message. Please try again."


def _failure_reply(exc: Exception, msg: InboundMessage) -> str:
    """If the AI providers are what failed (quota, outage), say so and promise
    nothing is wrong with the message; a real bug keeps the generic apology. The
    text follows the user's language when it can be guessed from a caption."""
    if isinstance(exc, (GeminiError, FallbackError)):
        return compose_busy((msg.text or msg.caption or "")[:300])
    return _GENERIC_FAILURE


async def _compose_reply(msg: InboundMessage) -> PipelineResult:
    if msg.type == "text" and msg.text:
        return await run_text_pipeline(msg.text, msg.frequently_forwarded)

    if msg.type == "image":
        return await _compose_image_reply(msg)

    if msg.type in ("audio", "video"):
        return await _compose_audio_or_video_reply(msg)

    # Stickers, locations, contacts, documents...
    return PipelineResult(
        f"{all_langs(NOT_A_CLAIM_FALLBACK)}\n\n{all_langs(CAPABILITY)}", meta={"input_kind": "unsupported_type"}
    )


async def _compose_image_reply(msg: InboundMessage) -> PipelineResult:
    caption = (msg.caption or "").strip()
    if not msg.media_id:
        return PipelineResult(compose_unreadable_media("image", caption), meta={"input_kind": "unreadable"})

    try:
        image_bytes = await download_media(msg.media_id)
    except Exception:
        logger.exception("Failed to download image for wamid=%s", ref(msg.wamid))
        return PipelineResult(compose_busy(caption), meta={"input_kind": "download_failed"})

    if len(image_bytes) > MAX_IMAGE_BYTES:
        logger.info("Rejecting oversized image (%d bytes) for wamid=%s", len(image_bytes), ref(msg.wamid))
        return PipelineResult(compose_unreadable_media("image", caption), meta={"input_kind": "too_large"})

    mime_type = msg.media_mime_type or "image/jpeg"
    try:
        reading = await normalize_image(image_bytes, mime_type, msg.caption)
    except GeminiError:
        # Vision has no fallback provider. A caption is still readable text, so
        # check that rather than fail outright.
        logger.exception("Image reading failed for wamid=%s", ref(msg.wamid))
        if caption:
            return await run_text_pipeline(
                f"[Caption sent with the image]\n{caption}", msg.frequently_forwarded
            )
        return PipelineResult(compose_busy(), meta={"input_kind": "vision_unavailable"})

    if reading.text is None:
        # A photo with nothing written on it and no caption: say what we can
        # see, and be honest that we can't judge a photo itself.
        if reading.description:
            return PipelineResult(compose_photo_only(reading.description), meta={"input_kind": "photo_only"})
        return PipelineResult(compose_unreadable_media("image"), meta={"input_kind": "unreadable"})

    return await run_text_pipeline(reading.text, msg.frequently_forwarded)


async def _compose_audio_or_video_reply(msg: InboundMessage) -> PipelineResult:
    caption = (msg.caption or "").strip()
    if not msg.media_id:
        return PipelineResult(compose_unreadable_media(msg.type, caption), meta={"input_kind": "unreadable"})

    try:
        media_bytes = await download_media(msg.media_id)
    except Exception:
        logger.exception("Failed to download %s for wamid=%s", msg.type, ref(msg.wamid))
        return PipelineResult(compose_busy(caption), meta={"input_kind": "download_failed"})

    if len(media_bytes) > MAX_AV_BYTES:
        logger.info("Rejecting oversized %s (%d bytes) for wamid=%s", msg.type, len(media_bytes), ref(msg.wamid))
        return PipelineResult(compose_too_long(msg.type, caption), meta={"input_kind": "too_large"})

    try:
        if msg.type == "video":
            audio_bytes = await extract_audio_track(media_bytes, msg.media_mime_type)
            audio_mime_type = "audio/wav"
        else:
            audio_bytes = media_bytes
            audio_mime_type = msg.media_mime_type
        transcript = await normalize_audio(audio_bytes, audio_mime_type)
    except AudioTooLongError as exc:
        logger.info("Rejecting %s from wamid=%s: %.0fs exceeds cap", msg.type, ref(msg.wamid), exc.duration_seconds)
        return PipelineResult(compose_too_long(msg.type, caption), meta={"input_kind": "too_long"})
    except MediaUnreadableError:
        logger.warning("Could not decode %s for wamid=%s", msg.type, ref(msg.wamid), exc_info=True)
        return PipelineResult(compose_unreadable_media(msg.type, caption), meta={"input_kind": "unreadable"})

    if transcript is None:
        return PipelineResult(compose_busy(caption), meta={"input_kind": "transcription_failed"})

    if not transcript:
        # No speech. A caption may still carry the claim.
        if caption:
            return await run_text_pipeline(f"[Caption sent with the {msg.type}]\n{caption}", msg.frequently_forwarded)
        return PipelineResult(compose_unreadable_media(msg.type), meta={"input_kind": "no_speech"})

    return await run_text_pipeline(label_transcript(transcript, msg.type, msg.caption), msg.frequently_forwarded)


async def _handle_reaction(msg: InboundMessage) -> None:
    """A reaction never runs the pipeline -- just join it back to the
    submission it's reacting to (via the reply_wamid we stored when we sent
    that reply) and log it as feedback.
    """
    if not msg.reaction_target_wamid or not msg.reaction_emoji:
        return
    try:
        submission_id = await db_submissions.find_submission_id_by_reply_wamid(msg.reaction_target_wamid)
        if submission_id is None:
            logger.info(
                "Reaction %s on unknown reply %s -- probably reacting to something other than our own reply",
                msg.reaction_emoji, ref(msg.reaction_target_wamid),
            )
            return
        await db_feedback.insert_feedback(submission_id, msg.reaction_target_wamid, msg.reaction_emoji)
    except Exception:
        logger.exception("Failed to record reaction feedback for wamid=%s", ref(msg.wamid))
