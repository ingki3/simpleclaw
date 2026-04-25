"""텍스트→음성(TTS) 프로세서 — OpenAI TTS API 사용.

텍스트를 음성 오디오 파일로 합성한다.
- 최대 텍스트 길이 초과 시 자동 잘라내기
- 출력 경로 미지정 시 임시 파일 생성
- API 키 미설정 시 명확한 예외 발생
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from simpleclaw.voice.models import TTSError, TTSResult

logger = logging.getLogger(__name__)


class TTSProcessor:
    """텍스트를 음성 오디오로 합성하는 TTS 프로세서."""

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
        """텍스트를 음성 오디오로 합성한다.

        Args:
            text: 음성으로 변환할 텍스트.
            output_path: 오디오 파일 저장 경로. None이면 임시 파일을 생성한다.

        Returns:
            오디오 파일 경로를 담은 TTSResult, 텍스트가 비어 있으면 None.

        Raises:
            TTSError: 합성 실패 시.
        """
        if not text or not text.strip():
            return None

        start = time.time()

        # 최대 길이 초과 시 잘라내기
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

            # 응답 오디오 데이터를 파일로 저장
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
