"""Text-to-speech processor using OpenAI TTS API."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from simpleclaw.voice.models import TTSError, TTSResult

logger = logging.getLogger(__name__)


class TTSProcessor:
    """Synthesizes text to speech audio."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "tts-1",
        voice: str = "alloy",
        speed: float = 1.0,
        output_format: str = "mp3",
        max_text_length: int = 4096,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._speed = speed
        self._output_format = output_format
        self._max_text_length = max_text_length

    async def synthesize(
        self,
        text: str,
        output_path: str | Path | None = None,
    ) -> TTSResult | None:
        """Synthesize text to speech audio.

        Args:
            text: Text to convert to speech.
            output_path: Where to save the audio file. If None, uses a temp path.

        Returns:
            TTSResult with the audio file path, or None if text is empty.

        Raises:
            TTSError: If synthesis fails.
        """
        if not text or not text.strip():
            return None

        start = time.time()

        # Truncate if needed
        if len(text) > self._max_text_length:
            text = text[: self._max_text_length]
            logger.warning(
                "TTS text truncated to %d characters", self._max_text_length
            )

        if not self._api_key:
            raise TTSError("OpenAI API key not configured for TTS")

        if output_path is None:
            import tempfile

            output_path = Path(tempfile.mktemp(suffix=f".{self._output_format}"))
        else:
            output_path = Path(output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self._api_key)

            response = await client.audio.speech.create(
                model=self._model,
                voice=self._voice,
                input=text,
                speed=self._speed,
                response_format=self._output_format,
            )

            # Write audio data
            audio_data = response.read()
            output_path.write_bytes(audio_data)

            elapsed = time.time() - start
            logger.info(
                "TTS synthesis completed in %.2fs: %d chars -> %s",
                elapsed,
                len(text),
                output_path,
            )

            return TTSResult(
                audio_path=output_path,
                format=self._output_format,
                duration_seconds=elapsed,
            )

        except ImportError:
            raise TTSError("openai package not installed")
        except Exception as exc:
            raise TTSError(f"Synthesis failed: {exc}") from exc
