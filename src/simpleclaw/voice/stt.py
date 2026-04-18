"""Speech-to-text processor using OpenAI Whisper API."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from simpleclaw.voice.models import (
    STTError,
    STTResult,
    SUPPORTED_FORMATS,
    UnsupportedFormatError,
)

logger = logging.getLogger(__name__)


class STTProcessor:
    """Transcribes audio files to text."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "whisper-1",
        max_duration: int = 300,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_duration = max_duration

    async def transcribe(self, audio_path: str | Path) -> STTResult:
        """Transcribe an audio file to text.

        Args:
            audio_path: Path to the audio file.

        Returns:
            STTResult with transcribed text.

        Raises:
            UnsupportedFormatError: If the audio format is not supported.
            STTError: If transcription fails.
        """
        audio_path = Path(audio_path)
        start = time.time()

        if not audio_path.is_file():
            raise STTError(f"Audio file not found: {audio_path}")

        # Check format
        suffix = audio_path.suffix.lstrip(".").lower()
        if suffix not in SUPPORTED_FORMATS:
            raise UnsupportedFormatError(
                f"Unsupported audio format: .{suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
            )

        # Check file size (empty files)
        if audio_path.stat().st_size == 0:
            return STTResult(text="", duration_seconds=0.0)

        if not self._api_key:
            raise STTError("OpenAI API key not configured for STT")

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self._api_key)

            with open(audio_path, "rb") as f:
                response = await client.audio.transcriptions.create(
                    model=self._model,
                    file=f,
                )

            elapsed = time.time() - start
            text = response.text if hasattr(response, "text") else str(response)

            logger.info(
                "STT transcription completed in %.2fs: %d chars",
                elapsed,
                len(text),
            )

            return STTResult(
                text=text,
                duration_seconds=elapsed,
            )

        except ImportError:
            raise STTError("openai package not installed")
        except Exception as exc:
            raise STTError(f"Transcription failed: {exc}") from exc
