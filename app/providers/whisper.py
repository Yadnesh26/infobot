import httpx

from app.config import settings

_TIMEOUT = 60  # per the plan's resilience section
_URL = "https://api.openai.com/v1/audio/transcriptions"


class TranscriptionError(Exception):
    pass


async def transcribe(audio_bytes: bytes, filename: str = "audio.wav", mime_type: str = "audio/wav") -> str:
    """Fallback for when ElevenLabs fails/times out/is over quota. Requires
    OPENAI_API_KEY; raises cleanly if it's not configured so the caller's
    fallback chain can fail honestly rather than hang.
    """
    if not settings.OPENAI_API_KEY:
        raise TranscriptionError("No OPENAI_API_KEY configured for the Whisper fallback")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _URL,
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            data={"model": "whisper-1"},
            files={"file": (filename, audio_bytes, mime_type)},
        )
        resp.raise_for_status()
        data = resp.json()

    text = (data.get("text") or "").strip()
    if not text:
        raise TranscriptionError("Whisper returned an empty transcription")
    return text
