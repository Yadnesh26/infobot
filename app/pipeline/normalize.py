import asyncio
import logging
import tempfile
from pathlib import Path

from app.config import settings
from app.providers import elevenlabs, whisper
from app.providers.gemini import build_image_input, generate_json

logger = logging.getLogger("infobot.normalize")

_PROMPT_IMAGE = (Path(__file__).parent.parent / "prompts" / "extract_image_text.txt").read_text(encoding="utf-8")

_SCHEMA_IMAGE = {
    "type": "object",
    "properties": {
        "has_readable_text": {"type": "boolean"},
        "extracted_text": {"type": "string"},
    },
    "required": ["has_readable_text", "extracted_text"],
}


def normalize_text(text: str) -> str:
    """Text passes through unchanged -- no preprocessing, no translation.

    The LLM handles script and code-mixing downstream.
    """
    return text


async def normalize_image(image_bytes: bytes, mime_type: str, caption: str | None) -> str | None:
    """Single multimodal call: OCR the image and combine with any caption.

    Returns None when there's genuinely nothing to check -- no readable text
    and no caption -- so the caller can reply gracefully instead of running
    the rest of the pipeline on an empty string. Per the plan, this is one
    call, not a separate OCR stage: folding extraction into the same call
    that reads the image avoids doubling the Gemini quota cost per image.
    """
    input_data = build_image_input(_PROMPT_IMAGE, image_bytes, mime_type)
    data = await generate_json(input_data, _SCHEMA_IMAGE)

    has_text = bool(data.get("has_readable_text", False))
    extracted = (data.get("extracted_text") or "").strip()

    parts = []
    if has_text and extracted:
        parts.append(extracted)
    if caption and caption.strip():
        parts.append(caption.strip())

    combined = "\n".join(parts).strip()
    return combined or None


class AudioTooLongError(Exception):
    def __init__(self, duration_seconds: float):
        self.duration_seconds = duration_seconds
        super().__init__(f"Audio duration {duration_seconds:.0f}s exceeds the {settings.MAX_AUDIO_SECONDS}s cap")


async def _ffprobe_duration(file_path: Path) -> float:
    """ffprobe runs out-of-process already, so this is non-blocking with
    respect to the event loop without needing asyncio.to_thread.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(file_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode(errors='replace')[-500:]}")
    return float(stdout.decode().strip() or "0")


def _suffix_for_mime(mime_type: str | None, default: str) -> str:
    if not mime_type:
        return default
    mime_type = mime_type.split(";")[0].strip().lower()
    mapping = {
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "audio/wav": ".wav",
        "audio/amr": ".amr",
        "video/mp4": ".mp4",
        "video/3gpp": ".3gp",
    }
    return mapping.get(mime_type, default)


async def extract_audio_track(video_bytes: bytes, mime_type: str | None) -> bytes:
    """Extract the audio track from a video as WAV, discarding video entirely --
    per the plan, only the audio track is analyzed, never the visuals.
    """
    suffix = _suffix_for_mime(mime_type, ".mp4")
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = Path(tmpdir) / f"in{suffix}"
        out_path = Path(tmpdir) / "out.wav"
        in_path.write_bytes(video_bytes)

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", str(in_path), "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", str(out_path), "-y",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg audio extraction failed: {stderr.decode(errors='replace')[-500:]}")
        return out_path.read_bytes()


async def normalize_audio(audio_bytes: bytes, mime_type: str | None) -> str | None:
    """ElevenLabs Scribe first, Whisper fallback, honest None if both fail.

    Duration is capped *before* any transcription call -- the cap exists to
    bound per-message ElevenLabs cost, not just latency, so it must happen
    before money is spent, not after.
    """
    suffix = _suffix_for_mime(mime_type, ".wav")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / f"audio{suffix}"
        path.write_bytes(audio_bytes)
        duration = await _ffprobe_duration(path)

    if duration > settings.MAX_AUDIO_SECONDS:
        raise AudioTooLongError(duration)

    try:
        return await elevenlabs.transcribe(audio_bytes, mime_type=mime_type or "audio/wav")
    except Exception as exc:
        logger.warning("ElevenLabs transcription failed, falling back to Whisper: %s", exc)

    try:
        return await whisper.transcribe(audio_bytes, mime_type=mime_type or "audio/wav")
    except Exception as exc:
        logger.warning("Whisper fallback also failed: %s", exc)

    return None
