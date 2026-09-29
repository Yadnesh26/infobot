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
