"""Tests for speech-to-text processor."""

import pytest

from simpleclaw.voice.models import UnsupportedFormatError, SUPPORTED_FORMATS
from simpleclaw.voice.stt import STTProcessor


class TestSTTProcessor:
    @pytest.fixture
    def processor(self):
        return STTProcessor(api_key="", model="whisper-1")

    @pytest.mark.asyncio
    async def test_unsupported_format(self, processor, tmp_path):
        audio = tmp_path / "test.xyz"
        audio.write_bytes(b"fake audio")
        with pytest.raises(UnsupportedFormatError, match="Unsupported audio format"):
            await processor.transcribe(audio)

    @pytest.mark.asyncio
    async def test_file_not_found(self, processor):
        from simpleclaw.voice.models import STTError
        with pytest.raises(STTError, match="not found"):
            await processor.transcribe("/nonexistent/file.wav")

    @pytest.mark.asyncio
    async def test_empty_file(self, processor, tmp_path):
        audio = tmp_path / "empty.wav"
        audio.write_bytes(b"")
        result = await processor.transcribe(audio)
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_no_api_key(self, processor, tmp_path):
        from simpleclaw.voice.models import STTError
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake audio data")
        with pytest.raises(STTError, match="API key not configured"):
            await processor.transcribe(audio)

    def test_supported_formats(self):
        assert "wav" in SUPPORTED_FORMATS
        assert "mp3" in SUPPORTED_FORMATS
        assert "ogg" in SUPPORTED_FORMATS
        assert "xyz" not in SUPPORTED_FORMATS
