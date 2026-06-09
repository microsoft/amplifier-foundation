"""Azure Cognitive Services TTS provider implementation."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING
from typing import AsyncIterator

from .protocol import AudioChunk
from .protocol import AudioFormat
from .protocol import TTSConfig
from .protocol import TTSConfigurationError
from .protocol import TTSSynthesisError


# Default Azure voice
DEFAULT_VOICE_NAME = "en-US-JennyNeural"

# Azure format mapping
_FORMAT_MAP: dict[AudioFormat, str] = {
    AudioFormat.MP3: "Audio16Khz128KBitRateMonoMp3",
    AudioFormat.WAV: "Riff24Khz16BitMonoPcm",
    AudioFormat.OGG: "Ogg24Khz16BitMonoOpus",
    AudioFormat.PCM: "Raw24Khz16BitMonoPcm",
}


class AzureTTS:
    """Azure Cognitive Services TTS implementation.

    This provider uses Azure Speech SDK for high-quality neural voice synthesis.
    Supports streaming output via push audio output stream.

    Environment Variables:
        AZURE_SPEECH_KEY: Subscription key for Azure Speech Service.
        AZURE_SPEECH_REGION: Azure region (e.g., "eastus", "westus2").

    Example:
        >>> tts = AzureTTS()
        >>> audio = await tts.synthesize("Hello, world!")
        >>> async for chunk in tts.synthesize_stream("Hello, world!"):
        ...     process_audio(chunk.data)
    """

    def __init__(
        self,
        subscription_key: str | None = None,
        region: str | None = None,
        default_voice_name: str | None = None,
    ) -> None:
        """Initialize Azure TTS provider.

        Args:
            subscription_key: Azure Speech subscription key. Falls back to AZURE_SPEECH_KEY.
            region: Azure region. Falls back to AZURE_SPEECH_REGION or "eastus".
            default_voice_name: Default voice name.

        Raises:
            TTSConfigurationError: If no subscription key is provided or found.
        """
        self.subscription_key = subscription_key or os.getenv("AZURE_SPEECH_KEY")
        if not self.subscription_key:
            raise TTSConfigurationError(
                "Azure Speech subscription key required. Set AZURE_SPEECH_KEY "
                "environment variable or pass subscription_key parameter."
            )

        self.region = region or os.getenv("AZURE_SPEECH_REGION", "eastus")
        self.default_voice_name = default_voice_name or DEFAULT_VOICE_NAME
        self._speech_config = None

    def _get_speech_config(self):
        """Get or create the Azure speech configuration."""
        if self._speech_config is None:
            try:
                import azure.cognitiveservices.speech as speechsdk
            except ImportError as e:
                raise TTSConfigurationError(
                    "azure-cognitiveservices-speech package not installed. "
                    "Install with: pip install amplifier-foundation[tts]"
                ) from e

            self._speech_config = speechsdk.SpeechConfig(
                subscription=self.subscription_key,
                region=self.region,
            )
        return self._speech_config

    def _get_output_format(self, audio_format: AudioFormat) -> str:
        """Map AudioFormat to Azure speech synthesis output format."""
        return _FORMAT_MAP.get(audio_format, "Audio16Khz128KBitRateMonoMp3")

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

        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError as e:
            raise TTSConfigurationError(
                "azure-cognitiveservices-speech package not installed. "
                "Install with: pip install amplifier-foundation[tts]"
            ) from e

        speech_config = self._get_speech_config()
        voice_name = config.voice_id if config else self.default_voice_name
        speech_config.speech_synthesis_voice_name = voice_name

        # Set output format
        output_format = self._get_output_format(
            config.audio_format if config else AudioFormat.MP3
        )
        speech_config.set_speech_synthesis_output_format(
            getattr(speechsdk.SpeechSynthesisOutputFormat, output_format)
        )

        # Use pull stream for complete audio
        stream = speechsdk.audio.PullAudioOutputStream()
        audio_config = speechsdk.audio.AudioOutputConfig(stream=stream)

        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        # Run synthesis in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                synthesizer.speak_text_async(text).get,
            )

            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                return result.audio_data
            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation = result.cancellation_details
                raise TTSSynthesisError(
                    f"Azure TTS synthesis canceled: {cancellation.reason} - "
                    f"{cancellation.error_details}"
                )
            else:
                raise TTSSynthesisError(
                    f"Azure TTS synthesis failed with reason: {result.reason}"
                )

        except TTSSynthesisError:
            raise
        except Exception as e:
            raise TTSSynthesisError(f"Azure TTS synthesis failed: {e}") from e

    async def synthesize_stream(
        self, text: str, config: TTSConfig | None = None
    ) -> AsyncIterator[AudioChunk]:
        """Stream synthesized audio chunks.

        Uses Azure push audio output stream for streaming output.
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

        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError as e:
            raise TTSConfigurationError(
                "azure-cognitiveservices-speech package not installed. "
                "Install with: pip install amplifier-foundation[tts]"
            ) from e

        speech_config = self._get_speech_config()
        voice_name = config.voice_id if config else self.default_voice_name
        speech_config.speech_synthesis_voice_name = voice_name

        # Set output format
        output_format = self._get_output_format(
            config.audio_format if config else AudioFormat.MP3
        )
        speech_config.set_speech_synthesis_output_format(
            getattr(speechsdk.SpeechSynthesisOutputFormat, output_format)
        )

        # Use a queue to pass audio chunks from the callback
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        class PushStreamCallback(speechsdk.audio.PushAudioOutputStreamCallback):
            """Callback to capture audio chunks as they're synthesized."""

            def write(self, audio_buffer: memoryview) -> int:
                # Schedule queue put from callback thread
                data = bytes(audio_buffer)
                loop.call_soon_threadsafe(audio_queue.put_nowait, data)
                return len(audio_buffer)

            def close(self) -> None:
                # Signal completion
                loop.call_soon_threadsafe(audio_queue.put_nowait, None)

        callback = PushStreamCallback()
        push_stream = speechsdk.audio.PushAudioOutputStream(callback)
        audio_config = speechsdk.audio.AudioOutputConfig(stream=push_stream)

        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        # Start synthesis in background
        async def run_synthesis():
            try:
                result = await loop.run_in_executor(
                    None,
                    synthesizer.speak_text_async(text).get,
                )
                if result.reason == speechsdk.ResultReason.Canceled:
                    cancellation = result.cancellation_details
                    raise TTSSynthesisError(
                        f"Azure TTS synthesis canceled: {cancellation.reason} - "
                        f"{cancellation.error_details}"
                    )
            except TTSSynthesisError:
                raise
            except Exception as e:
                raise TTSSynthesisError(f"Azure TTS streaming failed: {e}") from e

        # Start synthesis task
        synthesis_task = asyncio.create_task(run_synthesis())

        try:
            sequence = 0
            while True:
                chunk_data = await audio_queue.get()
                if chunk_data is None:
                    # Synthesis complete
                    yield AudioChunk(data=b"", sequence=sequence, is_final=True)
                    break

                yield AudioChunk(
                    data=chunk_data,
                    sequence=sequence,
                    is_final=False,
                )
                sequence += 1

            # Wait for synthesis to complete and check for errors
            await synthesis_task

        except Exception as e:
            synthesis_task.cancel()
            if isinstance(e, TTSSynthesisError):
                raise
            raise TTSSynthesisError(f"Azure TTS streaming failed: {e}") from e
