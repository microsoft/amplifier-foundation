"""ElevenLabs TTS provider implementation."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from typing import AsyncIterator

from .protocol import AudioChunk
from .protocol import AudioFormat
from .protocol import TTSConfig
from .protocol import TTSConfigurationError
from .protocol import TTSSynthesisError

if TYPE_CHECKING:
    from elevenlabs.client import AsyncElevenLabs


# Default voice: "Rachel" - a calm, natural-sounding voice
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

# ElevenLabs format mapping
_FORMAT_MAP: dict[AudioFormat, str] = {
    AudioFormat.MP3: "mp3_44100_128",
    AudioFormat.WAV: "pcm_44100",
    AudioFormat.OGG: "mp3_44100_128",  # ElevenLabs doesn't support OGG, fallback to MP3
    AudioFormat.PCM: "pcm_44100",
}


class ElevenLabsTTS:
    """ElevenLabs TTS implementation with streaming support.

    This provider uses the ElevenLabs API for high-quality voice synthesis.
    Streaming is supported via the websocket API for low-latency output.

    Environment Variables:
        ELEVENLABS_API_KEY: API key for ElevenLabs service.
        ELEVENLABS_VOICE_ID: Default voice ID (optional).

    Example:
        >>> tts = ElevenLabsTTS()
        >>> audio = await tts.synthesize("Hello, world!")
        >>> async for chunk in tts.synthesize_stream("Hello, world!"):
        ...     process_audio(chunk.data)
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_voice_id: str | None = None,
    ) -> None:
        """Initialize ElevenLabs TTS provider.

        Args:
            api_key: ElevenLabs API key. Falls back to ELEVENLABS_API_KEY env var.
            default_voice_id: Default voice ID. Falls back to ELEVENLABS_VOICE_ID env var.

        Raises:
            TTSConfigurationError: If no API key is provided or found.
        """
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise TTSConfigurationError(
                "ElevenLabs API key required. Set ELEVENLABS_API_KEY environment variable "
                "or pass api_key parameter."
            )

        self.default_voice_id = (
            default_voice_id
            or os.getenv("ELEVENLABS_VOICE_ID")
            or DEFAULT_VOICE_ID
        )
        self._client: AsyncElevenLabs | None = None

    async def _get_client(self) -> AsyncElevenLabs:
        """Get or create the async ElevenLabs client."""
        if self._client is None:
            try:
                from elevenlabs.client import AsyncElevenLabs
            except ImportError as e:
                raise TTSConfigurationError(
                    "elevenlabs package not installed. "
                    "Install with: pip install amplifier-foundation[tts]"
                ) from e

            self._client = AsyncElevenLabs(api_key=self.api_key)
        return self._client

    def _get_output_format(self, audio_format: AudioFormat) -> str:
        """Map AudioFormat to ElevenLabs output format string."""
        return _FORMAT_MAP.get(audio_format, "mp3_44100_128")

    async def synthesize(self, text: str, config: TTSConfig | None = None) -> bytes:
        """Synthesize text to audio, return complete audio bytes.

        Args:
            text: The text to synthesize into speech.
            config: Optional synthesis configuration.

        Returns:
            Complete audio data as bytes.

        Raises:
            TTSSynthesisError: If synthesis fails.
        """
        if not text.strip():
            return b""

        client = await self._get_client()
        voice_id = config.voice_id if config else self.default_voice_id
        output_format = self._get_output_format(
            config.audio_format if config else AudioFormat.MP3
        )

        try:
            # Use the generate method which returns an iterator
            audio_generator = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                output_format=output_format,
                model_id="eleven_turbo_v2",
            )

            # Collect all chunks into a single bytes object
            chunks: list[bytes] = []
            async for chunk in audio_generator:
                chunks.append(chunk)
            return b"".join(chunks)

        except Exception as e:
            raise TTSSynthesisError(f"ElevenLabs synthesis failed: {e}") from e

    async def synthesize_stream(
        self, text: str, config: TTSConfig | None = None
    ) -> AsyncIterator[AudioChunk]:
        """Stream synthesized audio chunks.

        Uses ElevenLabs streaming API for low-latency output.
        Target: <500ms first-byte latency.

        Args:
            text: The text to synthesize into speech.
            config: Optional synthesis configuration.

        Yields:
            AudioChunk objects containing sequential audio data.

        Raises:
            TTSSynthesisError: If synthesis fails.
        """
        if not text.strip():
            yield AudioChunk(data=b"", sequence=0, is_final=True)
            return

        client = await self._get_client()
        voice_id = config.voice_id if config else self.default_voice_id
        output_format = self._get_output_format(
            config.audio_format if config else AudioFormat.MP3
        )

        try:
            # Use streaming conversion for low latency
            audio_stream = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                output_format=output_format,
                model_id="eleven_turbo_v2",
                optimize_streaming_latency=3,  # Max optimization for lowest latency
            )

            sequence = 0
            async for chunk in audio_stream:
                yield AudioChunk(
                    data=chunk,
                    sequence=sequence,
                    is_final=False,
                )
                sequence += 1

            # Send final empty chunk to signal completion
            yield AudioChunk(data=b"", sequence=sequence, is_final=True)

        except Exception as e:
            raise TTSSynthesisError(f"ElevenLabs streaming failed: {e}") from e
