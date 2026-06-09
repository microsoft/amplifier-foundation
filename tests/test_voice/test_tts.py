"""Tests for Text-to-Speech module."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from amplifier_foundation.voice import AudioChunk
from amplifier_foundation.voice import AudioFormat
from amplifier_foundation.voice import TTSConfig
from amplifier_foundation.voice import TTSConfigurationError
from amplifier_foundation.voice import TTSProviderProtocol
from amplifier_foundation.voice import TTSSynthesisError


class TestProtocol:
    """Tests for TTSProviderProtocol compliance."""

    def test_protocol_attributes(self):
        """Protocol should define synthesize and synthesize_stream methods."""
        # Protocol should have these methods defined
        assert hasattr(TTSProviderProtocol, "synthesize")
        assert hasattr(TTSProviderProtocol, "synthesize_stream")

    def test_audio_format_enum(self):
        """AudioFormat should have expected values."""
        assert AudioFormat.MP3.value == "mp3"
        assert AudioFormat.WAV.value == "wav"
        assert AudioFormat.OGG.value == "ogg"
        assert AudioFormat.PCM.value == "pcm"

    def test_tts_config_defaults(self):
        """TTSConfig should have sensible defaults."""
        config = TTSConfig(voice_id="test-voice")
        assert config.voice_id == "test-voice"
        assert config.audio_format == AudioFormat.MP3
        assert config.sample_rate == 24000
        assert config.speed == 1.0
        assert config.pitch == 1.0
        assert config.extra == {}

    def test_audio_chunk_creation(self):
        """AudioChunk should store data correctly."""
        chunk = AudioChunk(data=b"audio-data", sequence=0, is_final=False)
        assert chunk.data == b"audio-data"
        assert chunk.sequence == 0
        assert chunk.is_final is False

        final_chunk = AudioChunk(data=b"", sequence=1, is_final=True)
        assert final_chunk.is_final is True


class TestElevenLabsTTS:
    """Tests for ElevenLabs TTS provider."""

    def test_init_requires_api_key(self):
        """Should raise error if no API key provided."""
        with patch.dict("os.environ", {}, clear=True):
            from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

            with pytest.raises(TTSConfigurationError, match="API key required"):
                ElevenLabsTTS()

    def test_init_with_api_key(self):
        """Should initialize with explicit API key."""
        from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key")
        assert tts.api_key == "test-key"

    def test_init_with_env_var(self):
        """Should use environment variable for API key."""
        with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "env-key"}):
            from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

            tts = ElevenLabsTTS()
            assert tts.api_key == "env-key"

    def test_default_voice_id(self):
        """Should have a default voice ID."""
        from amplifier_foundation.voice.elevenlabs import DEFAULT_VOICE_ID
        from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key")
        assert tts.default_voice_id == DEFAULT_VOICE_ID

    def test_custom_voice_id(self):
        """Should accept custom voice ID."""
        from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key", default_voice_id="custom-voice")
        assert tts.default_voice_id == "custom-voice"

    @pytest.mark.asyncio
    async def test_synthesize_empty_text(self):
        """Should return empty bytes for empty text."""
        from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key")
        result = await tts.synthesize("")
        assert result == b""

        result = await tts.synthesize("   ")
        assert result == b""

    @pytest.mark.asyncio
    async def test_synthesize_stream_empty_text(self):
        """Should yield single final chunk for empty text."""
        from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key")
        chunks = []
        async for chunk in tts.synthesize_stream(""):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert chunks[0].data == b""

    @pytest.mark.asyncio
    async def test_synthesize_mock(self):
        """Should call ElevenLabs SDK correctly."""
        from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key")

        # Mock the client
        mock_client = AsyncMock()

        async def mock_convert(*args, **kwargs):
            yield b"chunk1"
            yield b"chunk2"

        mock_client.text_to_speech.convert = mock_convert
        tts._client = mock_client

        result = await tts.synthesize("Hello")
        assert result == b"chunk1chunk2"

    @pytest.mark.asyncio
    async def test_synthesize_stream_mock(self):
        """Should stream audio chunks correctly."""
        from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

        tts = ElevenLabsTTS(api_key="test-key")

        # Mock the client
        mock_client = AsyncMock()

        async def mock_convert(*args, **kwargs):
            yield b"chunk1"
            yield b"chunk2"

        mock_client.text_to_speech.convert = mock_convert
        tts._client = mock_client

        chunks = []
        async for chunk in tts.synthesize_stream("Hello"):
            chunks.append(chunk)

        # Should have data chunks + final chunk
        assert len(chunks) == 3
        assert chunks[0].data == b"chunk1"
        assert chunks[0].sequence == 0
        assert chunks[0].is_final is False
        assert chunks[1].data == b"chunk2"
        assert chunks[1].sequence == 1
        assert chunks[1].is_final is False
        assert chunks[2].data == b""
        assert chunks[2].sequence == 2
        assert chunks[2].is_final is True


class TestAzureTTS:
    """Tests for Azure TTS provider."""

    def test_init_requires_subscription_key(self):
        """Should raise error if no subscription key provided."""
        with patch.dict("os.environ", {}, clear=True):
            from amplifier_foundation.voice.azure import AzureTTS

            with pytest.raises(TTSConfigurationError, match="subscription key required"):
                AzureTTS()

    def test_init_with_subscription_key(self):
        """Should initialize with explicit subscription key."""
        from amplifier_foundation.voice.azure import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        assert tts.subscription_key == "test-key"

    def test_init_with_env_var(self):
        """Should use environment variables."""
        with patch.dict(
            "os.environ",
            {"AZURE_SPEECH_KEY": "env-key", "AZURE_SPEECH_REGION": "westus2"},
        ):
            from amplifier_foundation.voice.azure import AzureTTS

            tts = AzureTTS()
            assert tts.subscription_key == "env-key"
            assert tts.region == "westus2"

    def test_default_region(self):
        """Should default to eastus region."""
        from amplifier_foundation.voice.azure import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        assert tts.region == "eastus"

    def test_default_voice_name(self):
        """Should have a default voice name."""
        from amplifier_foundation.voice.azure import DEFAULT_VOICE_NAME
        from amplifier_foundation.voice.azure import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        assert tts.default_voice_name == DEFAULT_VOICE_NAME

    @pytest.mark.asyncio
    async def test_synthesize_empty_text(self):
        """Should return empty bytes for empty text."""
        from amplifier_foundation.voice.azure import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        result = await tts.synthesize("")
        assert result == b""

        result = await tts.synthesize("   ")
        assert result == b""

    @pytest.mark.asyncio
    async def test_synthesize_stream_empty_text(self):
        """Should yield single final chunk for empty text."""
        from amplifier_foundation.voice.azure import AzureTTS

        tts = AzureTTS(subscription_key="test-key")
        chunks = []
        async for chunk in tts.synthesize_stream(""):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert chunks[0].data == b""


class TestProtocolCompliance:
    """Test that implementations comply with TTSProviderProtocol."""

    def test_elevenlabs_has_protocol_methods(self):
        """ElevenLabsTTS should have all protocol methods."""
        from amplifier_foundation.voice.elevenlabs import ElevenLabsTTS

        assert hasattr(ElevenLabsTTS, "synthesize")
        assert hasattr(ElevenLabsTTS, "synthesize_stream")
        assert asyncio.iscoroutinefunction(ElevenLabsTTS.synthesize)

    def test_azure_has_protocol_methods(self):
        """AzureTTS should have all protocol methods."""
        from amplifier_foundation.voice.azure import AzureTTS

        assert hasattr(AzureTTS, "synthesize")
        assert hasattr(AzureTTS, "synthesize_stream")
        assert asyncio.iscoroutinefunction(AzureTTS.synthesize)


class TestExceptions:
    """Test exception hierarchy."""

    def test_tts_error_is_base(self):
        """TTSError should be the base exception."""
        from amplifier_foundation.voice import TTSError

        assert issubclass(TTSConfigurationError, TTSError)
        assert issubclass(TTSSynthesisError, TTSError)

    def test_configuration_error_message(self):
        """Configuration errors should have clear messages."""
        error = TTSConfigurationError("Test message")
        assert "Test message" in str(error)

    def test_synthesis_error_message(self):
        """Synthesis errors should have clear messages."""
        error = TTSSynthesisError("Synthesis failed")
        assert "Synthesis failed" in str(error)
