"""음성→텍스트(STT) 프로세서 — OpenAI Whisper API 사용.

오디오 파일을 받아 Whisper API로 텍스트를 추출한다.
- 지원 포맷 검증 (SUPPORTED_FORMATS)
- 빈 파일은 빈 결과 즉시 반환
- API 키 미설정 시 명확한 예외 발생
"""

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
    """오디오 파일을 텍스트로 변환하는 STT 프로세서."""

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
        """오디오 파일을 텍스트로 변환한다.

        Args:
            audio_path: 오디오 파일 경로.

        Returns:
            변환된 텍스트를 담은 STTResult.

        Raises:
            UnsupportedFormatError: 지원하지 않는 오디오 포맷인 경우.
            STTError: 변환 실패 시.
        """
        audio_path = Path(audio_path)
        start = time.time()

        if not audio_path.is_file():
            raise STTError(f"Audio file not found: {audio_path}")

        # 포맷 검증
        suffix = audio_path.suffix.lstrip(".").lower()
        if suffix not in SUPPORTED_FORMATS:
            raise UnsupportedFormatError(
                f"Unsupported audio format: .{suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
            )

        # 빈 파일은 API 호출 없이 빈 결과 반환
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
