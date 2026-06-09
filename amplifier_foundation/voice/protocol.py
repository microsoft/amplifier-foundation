"""Protocol for Text-to-Speech providers."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import TYPE_CHECKING
from typing import AsyncIterator
from typing import Protocol


class AudioFormat(Enum):
    """Supported audio output formats."""

    MP3 = "mp3"
    WAV = "wav"
    OGG = "ogg"
    PCM = "pcm"


@dataclass
class TTSConfig:
    """Configuration for TTS synthesis.

    Attributes:
        voice_id: Provider-specific voice identifier.
        audio_format: Output audio format.
        sample_rate: Audio sample rate in Hz.
        speed: Speech speed multiplier (1.0 = normal).
        pitch: Voice pitch adjustment (provider-specific).
    """

    voice_id: str
    audio_format: AudioFormat = AudioFormat.MP3
    sample_rate: int = 24000
    speed: float = 1.0
    pitch: float = 1.0
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class AudioChunk:
    """A chunk of synthesized audio data.

    Attributes:
        data: Raw audio bytes for this chunk.
        sequence: Zero-based sequence number for ordering.
        is_final: True if this is the last chunk in the stream.
    """

    data: bytes
    sequence: int
    is_final: bool


class TTSProviderProtocol(Protocol):
    """Protocol for TTS providers with streaming support.

    Implementations should target <500ms first-byte latency for streaming.
    """

    async def synthesize(self, text: str, config: TTSConfig | None = None) -> bytes:
        """Synthesize text to audio, return complete audio bytes.

        Args:
            text: The text to synthesize into speech.
            config: Optional synthesis configuration. If None, uses provider defaults.

        Returns:
            Complete audio data as bytes in the configured format.

        Raises:
            TTSError: If synthesis fails.
        """
        ...

    async def synthesize_stream(
        self, text: str, config: TTSConfig | None = None
    ) -> AsyncIterator[AudioChunk]:
        """Stream synthesized audio chunks.

        Target: <500ms first-byte latency for responsive voice output.

        Args:
            text: The text to synthesize into speech.
            config: Optional synthesis configuration. If None, uses provider defaults.

        Yields:
            AudioChunk objects containing sequential audio data.

        Raises:
            TTSError: If synthesis fails.
        """
        ...


class TTSError(Exception):
    """Base exception for TTS operations."""

    pass


class TTSConfigurationError(TTSError):
    """Raised when TTS provider is misconfigured."""

    pass


class TTSSynthesisError(TTSError):
    """Raised when audio synthesis fails."""

    pass
