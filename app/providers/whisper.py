import httpx

from app.config import settings

_TIMEOUT = 60  # per the plan's resilience section
_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"


class TranscriptionError(Exception):
    pass


def _endpoint() -> tuple[str, str, str]:
    """(url, api key, model). Groq hosts Whisper on its free tier with the same
    key we already use for the text fallback, so prefer it; use OpenAI only
    when that's the one configured.
    """
    if settings.GROQ_API_KEY:
        return _GROQ_URL, settings.GROQ_API_KEY, settings.GROQ_WHISPER_MODEL
    if settings.OPENAI_API_KEY:
        return _OPENAI_URL, settings.OPENAI_API_KEY, "whisper-1"
    raise TranscriptionError("No GROQ_API_KEY or OPENAI_API_KEY configured for the Whisper fallback")


async def transcribe(audio_bytes: bytes, filename: str = "audio.wav", mime_type: str = "audio/wav") -> str:
    """Fallback for when ElevenLabs fails/times out/is over quota. Raises
    cleanly when no provider is configured so the caller's fallback chain can
    fail honestly rather than hang.
    """
    url, api_key, model = _endpoint()

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": model},
            files={"file": (filename, audio_bytes, mime_type)},
        )
        resp.raise_for_status()
        data = resp.json()

    text = (data.get("text") or "").strip()
    if not text:
        raise TranscriptionError("Whisper returned an empty transcription")
    return text
