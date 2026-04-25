"""음성 처리 데이터 모델.

STT(음성→텍스트)와 TTS(텍스트→음성) 결과 구조체 및 공통 예외 계층을 정의한다.
지원 오디오 포맷 목록(SUPPORTED_FORMATS)도 여기서 관리한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class VoiceError(Exception):
    """음성 작업의 기본 예외 클래스."""


class STTError(VoiceError):
    """음성→텍스트 변환 중 발생하는 예외."""


class TTSError(VoiceError):
    """텍스트→음성 합성 중 발생하는 예외."""


class UnsupportedFormatError(VoiceError):
    """지원하지 않는 오디오 포맷일 때 발생하는 예외."""


SUPPORTED_FORMATS = {"wav", "mp3", "ogg", "m4a", "webm", "flac"}


@dataclass
class STTResult:
    """음성→텍스트 변환 결과.

    text: 변환된 텍스트, duration_seconds: 처리 소요 시간, language: 감지된 언어 코드
    """

    text: str
    duration_seconds: float = 0.0
    language: str = ""


@dataclass
class TTSResult:
    """텍스트→음성 합성 결과.

    audio_path: 생성된 오디오 파일 경로, format: 오디오 포맷, duration_seconds: 처리 소요 시간
    """

    audio_path: Path
    format: str = "mp3"
    duration_seconds: float = 0.0
