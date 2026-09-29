import httpx

from app.config import settings

_TIMEOUT = 60  # per the plan's resilience section
_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class TranscriptionError(Exception):
    pass


async def transcribe(audio_bytes: bytes, filename: str = "audio.wav", mime_type: str = "audio/wav") -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _URL,
            headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
            data={"model_id": "scribe_v1"},
            files={"file": (filename, audio_bytes, mime_type)},
        )
        resp.raise_for_status()
        data = resp.json()

    text = (data.get("text") or "").strip()
    if not text:
        raise TranscriptionError("ElevenLabs returned an empty transcription")
    return text
