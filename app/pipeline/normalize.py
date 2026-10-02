import asyncio
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.pipeline.guard import sanitize_output
from app.providers import elevenlabs, whisper
from app.providers.gemini import build_image_input, generate_json

logger = logging.getLogger("infobot.normalize")

_PROMPT_IMAGE = (Path(__file__).parent.parent / "prompts" / "extract_image_text.txt").read_text(encoding="utf-8")

_SCHEMA_IMAGE = {
    "type": "object",
    "properties": {
        "has_readable_text": {"type": "boolean"},
        "extracted_text": {"type": "string"},
        "description": {"type": "string"},
        "content_kind": {"type": "string", "enum": ["text_graphic", "photo", "unreadable"]},
    },
    "required": ["has_readable_text", "extracted_text"],
}

_FFPROBE_TIMEOUT = 20
_FFMPEG_TIMEOUT = 90


_NOTHING_TO_SEE = re.compile(
    r"blur|out of focus|indistinct|featureless|illegible|unrecogni[sz]able|too dark|"
    r"no (?:visible|discernible|readable|clear)|nothing (?:visible|discernible|clear)|"
    r"(?:solid|plain|blank) (?:grey|gray|black|white|colou?r)|^blank",
    re.I,
)


def normalize_text(text: str) -> str:
    """Text passes through unchanged -- no preprocessing, no translation.

    The LLM handles script and code-mixing downstream.
    """
    return text


@dataclass
class ImageReading:
    """What was found in one image message. `text` is the labelled text to send
    through the pipeline, or None when there is nothing to check in words."""

    text: str | None
    description: str = ""
    has_image_text: bool = False
    has_caption: bool = False
    kind: str = "photo"  # text_graphic | photo | unreadable


async def normalize_image(image_bytes: bytes, mime_type: str, caption: str | None) -> ImageReading:
    """Single multimodal call: read the text in the image, and describe it.

    Per the plan this is one call, not a separate OCR stage: folding extraction
    into the same call that reads the image avoids doubling the Gemini quota
    cost per image. Each piece is labelled so the classifier can tell whether
    the caption and the image text make one claim or several, and so a photo
    with no text still gives it something to reason about.
    """
    input_data = build_image_input(_PROMPT_IMAGE, image_bytes, mime_type)
    data = await generate_json(input_data, _SCHEMA_IMAGE)

    extracted = (data.get("extracted_text") or "").strip() if data.get("has_readable_text") else ""
    description = sanitize_output(data.get("description") or "", 200)
    caption = (caption or "").strip()

    parts = []
    if extracted:
        parts.append(f"[Text inside the image]\n{extracted}")
    if caption:
        parts.append(f"[Caption sent with the image]\n{caption}")
    if parts and description:
        parts.append(f"[What the image shows]\n{description}")

    kind = data.get("content_kind") if data.get("content_kind") in ("text_graphic", "photo", "unreadable") else "photo"
    if not extracted and _NOTHING_TO_SEE.search(description):
        # The model often calls a blurred or blank picture a "photo" while describing it as
        # blurred or featureless. Trust the description: there is nothing to judge or read.
        kind = "unreadable"

    return ImageReading(
        text="\n\n".join(parts) or None,
        description=description,
        has_image_text=bool(extracted),
        has_caption=bool(caption),
        kind=kind,
    )


class AudioTooLongError(Exception):
    def __init__(self, duration_seconds: float):
        self.duration_seconds = duration_seconds
        super().__init__(f"Audio duration {duration_seconds:.0f}s exceeds the {settings.MAX_AUDIO_SECONDS}s cap")


class MediaUnreadableError(Exception):
    """The file could not be decoded at all (corrupt, wrong format, tool failure)."""


async def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run a subprocess off the event loop. asyncio's own subprocess support is
    unavailable on Windows under uvicorn --reload (selector event loop), so a
    blocking subprocess.run in a worker thread is the portable choice."""
    try:
        return await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaUnreadableError(f"{cmd[0]} timed out after {timeout}s") from exc
    except OSError as exc:
        raise MediaUnreadableError(f"{cmd[0]} could not be run: {exc}") from exc


async def _ffprobe_duration(file_path: Path) -> float:
    proc = await _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
        _FFPROBE_TIMEOUT,
    )
    if proc.returncode != 0:
        raise MediaUnreadableError(f"ffprobe failed: {proc.stderr.decode(errors='replace')[-300:]}")
    try:
        return float(proc.stdout.decode().strip() or "0")
    except ValueError as exc:
        raise MediaUnreadableError("ffprobe returned no duration") from exc


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

    Only the first MAX_AUDIO_SECONDS + 5 seconds are converted, so a very long
    video can't tie up ffmpeg; the extra seconds keep the later duration check
    able to see that it was too long.
    """
    suffix = _suffix_for_mime(mime_type, ".mp4")
    limit = str(settings.MAX_AUDIO_SECONDS + 5)
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = Path(tmpdir) / f"in{suffix}"
        out_path = Path(tmpdir) / "out.wav"
        in_path.write_bytes(video_bytes)

        proc = await _run(
            ["ffmpeg", "-i", str(in_path), "-vn", "-t", limit, "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", str(out_path), "-y"],
            _FFMPEG_TIMEOUT,
        )
        if proc.returncode != 0 or not out_path.exists():
            raise MediaUnreadableError(f"ffmpeg audio extraction failed: {proc.stderr.decode(errors='replace')[-300:]}")
        return out_path.read_bytes()


# Speech-to-text engines mark non-speech sounds inline: "(laughter)", "[music]".
_AUDIO_EVENT = re.compile(
    r"[\(\[]\s*(?:laugh\w*|music\w*|applause|clapping|noise|silence|sigh\w*|cough\w*|background[^)\]]*|static|wind|"
    r"inaudible|unintelligible|crosstalk|sing\w*|beep\w*|clears? throat|breath\w*|speaking[^)\]]*|foreign[^)\]]*|"
    r"sound\w*|ringing|whistl\w*|cheer\w*|crowd[^)\]]*|pause|chuckl\w*|sniff\w*|footsteps)\s*[\)\]]",
    re.I,
)


# Anything in square brackets is a sound label, never speech ("[wind blowing]",
# "[tone]"); so is a short parenthesised phrase built on an -ing word ("(birds chirping)").
_BRACKET_LABEL = re.compile(r"\[[^\[\]]{1,40}\]")
_PAREN_ING_LABEL = re.compile(r"\(\s*(?:\w+\s+){0,2}\w+ing(?:\s+\w+){0,2}\s*\)", re.I)


def clean_transcript(text: str) -> str:
    text = _BRACKET_LABEL.sub(" ", text or "")
    text = _PAREN_ING_LABEL.sub(" ", text)
    text = _AUDIO_EVENT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Nothing left but punctuation or a stray syllable is not speech.
    return text if sum(c.isalnum() for c in text) >= 3 else ""


def label_transcript(transcript: str, kind: str, caption: str | None) -> str:
    """Attach the transcript's origin, and any caption, so the classifier can
    tell them apart."""
    label = "Video transcript" if kind == "video" else "Voice note transcript"
    parts = [f"[{label}]\n{transcript}"]
    if caption and caption.strip():
        parts.append(f"[Caption sent with the {kind}]\n{caption.strip()}")
    return "\n\n".join(parts)


async def normalize_audio(audio_bytes: bytes, mime_type: str | None) -> str | None:
    """ElevenLabs Scribe first, Whisper fallback.

    Returns the cleaned transcript, "" when the audio holds no speech, or None
    when both engines failed.

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
        return clean_transcript(await elevenlabs.transcribe(audio_bytes, mime_type=mime_type or "audio/wav"))
    except elevenlabs.TranscriptionError:
        # The engine answered, and heard nothing. A second engine would only
        # tend to hallucinate words into silence.
        return ""
    except Exception as exc:
        logger.warning("ElevenLabs transcription failed, falling back to Whisper: %r", exc)

    try:
        return clean_transcript(await whisper.transcribe(audio_bytes, mime_type=mime_type or "audio/wav"))
    except Exception as exc:
        logger.warning("Whisper fallback also failed: %r", exc)

    return None
