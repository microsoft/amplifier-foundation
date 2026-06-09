"""Text-to-Speech (TTS) module for voice synthesis.

This module provides a protocol-based interface for TTS providers with
streaming support, targeting <500ms first-byte latency for responsive
voice output.

Providers:
    ElevenLabsTTS: High-quality neural TTS via ElevenLabs API.
    AzureTTS: Azure Cognitive Services neural TTS.

Example:
    >>> from amplifier_foundation.voice import ElevenLabsTTS, TTSConfig, AudioFormat
    >>> tts = ElevenLabsTTS()
    >>> audio = await tts.synthesize("Hello, world!")
    >>>
    >>> # With custom config
    >>> config = TTSConfig(voice_id="custom-voice", audio_format=AudioFormat.WAV)
    >>> audio = await tts.synthesize("Hello, world!", config)
    >>>
    >>> # Streaming
    >>> async for chunk in tts.synthesize_stream("Hello, world!"):
    ...     if chunk.data:
    ...         process_audio(chunk.data)
"""

from .protocol import AudioChunk
from .protocol import AudioFormat
from .protocol import TTSConfig
from .protocol import TTSConfigurationError
from .protocol import TTSError
from .protocol import TTSProviderProtocol
from .protocol import TTSSynthesisError

__all__ = [
    # Protocol
    "TTSProviderProtocol",
    # Data classes
    "TTSConfig",
    "AudioChunk",
    "AudioFormat",
    # Exceptions
    "TTSError",
    "TTSConfigurationError",
    "TTSSynthesisError",
    # Providers - lazy imports to avoid requiring SDK installation
    "ElevenLabsTTS",
    "AzureTTS",
]


def __getattr__(name: str):
    """Lazy import providers to avoid requiring SDK installation."""
    if name == "ElevenLabsTTS":
        from .elevenlabs import ElevenLabsTTS

        return ElevenLabsTTS
    if name == "AzureTTS":
        from .azure import AzureTTS

        return AzureTTS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
