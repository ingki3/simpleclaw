"""Tests for text-to-speech processor."""

import pytest

from simpleclaw.voice.tts import TTSProcessor


class TestTTSProcessor:
    @pytest.fixture
    def processor(self):
        return TTSProcessor(api_key="", model="tts-1")

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self, processor):
        result = await processor.synthesize("")
        assert result is None

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_none(self, processor):
        result = await processor.synthesize("   ")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_api_key(self, processor, tmp_path):
        from simpleclaw.voice.models import TTSError
        with pytest.raises(TTSError, match="API key not configured"):
            await processor.synthesize("Hello world", tmp_path / "out.mp3")

    def test_max_text_length(self):
        processor = TTSProcessor(api_key="", max_text_length=100)
        assert processor._max_text_length == 100

    def test_default_settings(self):
        processor = TTSProcessor(api_key="test")
        assert processor._model == "tts-1"
        assert processor._voice == "alloy"
        assert processor._speed == 1.0
        assert processor._output_format == "mp3"
