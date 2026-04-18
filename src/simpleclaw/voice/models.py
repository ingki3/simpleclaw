"""Data models for voice processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class VoiceError(Exception):
    """Base error for voice operations."""


class STTError(VoiceError):
    """Error in speech-to-text processing."""


class TTSError(VoiceError):
    """Error in text-to-speech processing."""


class UnsupportedFormatError(VoiceError):
    """Raised when an audio format is not supported."""


SUPPORTED_FORMATS = {"wav", "mp3", "ogg", "m4a", "webm", "flac"}


@dataclass
class STTResult:
    """Result of speech-to-text processing."""

    text: str
    duration_seconds: float = 0.0
    language: str = ""


@dataclass
class TTSResult:
    """Result of text-to-speech synthesis."""

    audio_path: Path
    format: str = "mp3"
    duration_seconds: float = 0.0
