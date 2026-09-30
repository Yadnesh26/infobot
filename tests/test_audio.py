import pytest

from app.pipeline.normalize import AudioTooLongError, _suffix_for_mime, normalize_audio


def test_suffix_for_mime_known_types():
    assert _suffix_for_mime("audio/ogg", ".wav") == ".ogg"
    assert _suffix_for_mime("audio/ogg; codecs=opus", ".wav") == ".ogg"
    assert _suffix_for_mime("video/mp4", ".mp4") == ".mp4"


def test_suffix_for_mime_unknown_falls_back_to_default():
    assert _suffix_for_mime("application/x-mystery", ".wav") == ".wav"
    assert _suffix_for_mime(None, ".wav") == ".wav"


@pytest.mark.anyio
async def test_normalize_audio_uses_elevenlabs_when_it_succeeds(monkeypatch):
    async def fake_ffprobe_duration(path):
        return 5.0

    async def fake_elevenlabs_transcribe(audio_bytes, mime_type="audio/wav"):
        return "hot water cures covid"

    async def fail_whisper(*args, **kwargs):
        raise AssertionError("whisper should not be called when ElevenLabs succeeds")

    monkeypatch.setattr("app.pipeline.normalize._ffprobe_duration", fake_ffprobe_duration)
    monkeypatch.setattr("app.pipeline.normalize.elevenlabs.transcribe", fake_elevenlabs_transcribe)
    monkeypatch.setattr("app.pipeline.normalize.whisper.transcribe", fail_whisper)

    result = await normalize_audio(b"fake-audio-bytes", "audio/ogg")
    assert result == "hot water cures covid"


@pytest.mark.anyio
async def test_normalize_audio_falls_back_to_whisper_on_elevenlabs_failure(monkeypatch):
    async def fake_ffprobe_duration(path):
        return 5.0

    async def fail_elevenlabs(*args, **kwargs):
        raise RuntimeError("elevenlabs down")

    async def fake_whisper_transcribe(audio_bytes, mime_type="audio/wav"):
        return "fallback transcription"

    monkeypatch.setattr("app.pipeline.normalize._ffprobe_duration", fake_ffprobe_duration)
    monkeypatch.setattr("app.pipeline.normalize.elevenlabs.transcribe", fail_elevenlabs)
    monkeypatch.setattr("app.pipeline.normalize.whisper.transcribe", fake_whisper_transcribe)

    result = await normalize_audio(b"fake-audio-bytes", "audio/ogg")
    assert result == "fallback transcription"


@pytest.mark.anyio
async def test_normalize_audio_returns_none_when_both_providers_fail(monkeypatch):
    async def fake_ffprobe_duration(path):
        return 5.0

    async def fail(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr("app.pipeline.normalize._ffprobe_duration", fake_ffprobe_duration)
    monkeypatch.setattr("app.pipeline.normalize.elevenlabs.transcribe", fail)
    monkeypatch.setattr("app.pipeline.normalize.whisper.transcribe", fail)

    result = await normalize_audio(b"fake-audio-bytes", "audio/ogg")
    assert result is None


@pytest.mark.anyio
async def test_normalize_audio_rejects_over_duration_cap_before_calling_any_provider(monkeypatch):
    from app.config import settings

    async def fake_ffprobe_duration(path):
        return settings.MAX_AUDIO_SECONDS + 1

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("no transcription provider should be called over the duration cap")

    monkeypatch.setattr("app.pipeline.normalize._ffprobe_duration", fake_ffprobe_duration)
    monkeypatch.setattr("app.pipeline.normalize.elevenlabs.transcribe", fail_if_called)
    monkeypatch.setattr("app.pipeline.normalize.whisper.transcribe", fail_if_called)

    with pytest.raises(AudioTooLongError):
        await normalize_audio(b"fake-audio-bytes", "audio/ogg")


# --- real ffmpeg/ffprobe: regression for the Windows selector-event-loop crash ---

import shutil

import pytest

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _tone_wav(seconds: float) -> bytes:
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "t.wav"
        subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(out), "-y"],
            capture_output=True, check=True,
        )
        return out.read_bytes()


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.anyio
async def test_extract_audio_track_really_runs_ffmpeg_off_the_event_loop():
    from app.pipeline.normalize import extract_audio_track

    wav = await extract_audio_track(_tone_wav(1.0), "audio/wav")
    assert wav[:4] == b"RIFF"


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.anyio
async def test_extract_audio_track_only_converts_up_to_the_duration_cap(monkeypatch):
    from app.pipeline import normalize

    monkeypatch.setattr(normalize.settings, "MAX_AUDIO_SECONDS", 2)
    wav = await normalize.extract_audio_track(_tone_wav(30), "audio/wav")
    # 16 kHz mono 16-bit = 32 kB/s; a 7-second cap means far less than 30 s of audio
    assert len(wav) < 32000 * 8


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.anyio
async def test_corrupt_media_raises_media_unreadable_not_a_crash():
    from app.pipeline.normalize import MediaUnreadableError, extract_audio_track, normalize_audio

    with pytest.raises(MediaUnreadableError):
        await extract_audio_track(b"this is not a video", "video/mp4")
    with pytest.raises(MediaUnreadableError):
        await normalize_audio(b"this is not audio", "audio/ogg")


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.anyio
async def test_silent_audio_returns_empty_transcript_and_skips_the_second_engine(monkeypatch):
    from app.pipeline import normalize
    from app.providers import elevenlabs

    async def heard_nothing(*a, **k):
        raise elevenlabs.TranscriptionError("empty")

    async def whisper_must_not_run(*a, **k):
        raise AssertionError("Whisper would hallucinate words into silence")

    monkeypatch.setattr(normalize.elevenlabs, "transcribe", heard_nothing)
    monkeypatch.setattr(normalize.whisper, "transcribe", whisper_must_not_run)
    assert await normalize.normalize_audio(_tone_wav(1.0), "audio/wav") == ""
