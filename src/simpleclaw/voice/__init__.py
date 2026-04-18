"""Voice interface: STT and TTS processing."""

from simpleclaw.voice.models import (
    STTError,
    STTResult,
    TTSError,
    TTSResult,
    UnsupportedFormatError,
    VoiceError,
)
from simpleclaw.voice.stt import STTProcessor
from simpleclaw.voice.tts import TTSProcessor

__all__ = [
    "STTError",
    "STTProcessor",
    "STTResult",
    "TTSError",
    "TTSProcessor",
    "TTSResult",
    "UnsupportedFormatError",
    "VoiceError",
]
