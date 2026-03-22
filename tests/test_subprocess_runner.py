"""Tests for subprocess_runner module - config serialization/deserialization for IPC."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from amplifier_foundation.subprocess_runner import _run_child_session
from amplifier_foundation.subprocess_runner import deserialize_subprocess_config
from amplifier_foundation.subprocess_runner import serialize_subprocess_config


class TestRoundTripMinimal:
    """Tests for minimal config round-trip."""

    def test_roundtrip_minimal(self) -> None:
        """Test that a minimal config round-trips correctly."""
        config = {"provider": "anthropic"}
        prompt = "Hello, world!"
        parent_id = "parent-session-123"
        project_path = "/tmp/my-project"

        serialized = serialize_subprocess_config(
            config=config,
            prompt=prompt,
            parent_id=parent_id,
            project_path=project_path,
        )
        deserialized = deserialize_subprocess_config(serialized)

        assert deserialized["config"] == config
        assert deserialized["prompt"] == prompt
        assert deserialized["parent_id"] == parent_id
        assert deserialized["project_path"] == project_path
        assert deserialized["session_id"] is None


class TestRoundTripFullConfig:
    """Tests for full config round-trip with providers, tools, and hooks."""

    def test_roundtrip_full_config(self) -> None:
        """Test that a full config with providers/tools/hooks round-trips correctly."""
        config = {
            "providers": [
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-3-haiku"},
                },
                {
                    "module": "provider-openai",
                    "config": {"default_model": "gpt-4o-mini"},
                },
            ],
            "tools": ["tool-bash", "tool-read-file", "tool-write-file"],
            "hooks": [{"on": "before_call", "module": "hook-logging"}],
        }
        prompt = "Analyze the codebase"
        parent_id = "parent-abc-def"
        project_path = "/workspace/my-repo"
        session_id = "child-session-456"

        serialized = serialize_subprocess_config(
            config=config,
            prompt=prompt,
            parent_id=parent_id,
            project_path=project_path,
            session_id=session_id,
        )
        deserialized = deserialize_subprocess_config(serialized)

        assert deserialized["config"] == config
        assert deserialized["prompt"] == prompt
        assert deserialized["parent_id"] == parent_id
        assert deserialized["project_path"] == project_path
        assert deserialized["session_id"] == session_id


class TestRoundTripWithoutSessionId:
    """Tests for round-trip without session_id."""

    def test_roundtrip_without_session_id(self) -> None:
        """Test that session_id defaults to None when not provided."""
        config = {"model": "gpt-4o"}
        prompt = "Write some code"
        parent_id = "parent-xyz"
        project_path = "/home/user/project"

        serialized = serialize_subprocess_config(
            config=config,
            prompt=prompt,
            parent_id=parent_id,
            project_path=project_path,
        )
        deserialized = deserialize_subprocess_config(serialized)

        assert deserialized["session_id"] is None


class TestMissingRequiredKeys:
    """Tests for missing required keys error."""

    def test_missing_required_keys(self) -> None:
        """Test that deserialize raises ValueError on missing required keys."""
        # Create a JSON string missing the 'config' key
        incomplete = json.dumps(
            {
                "prompt": "Hello",
                "parent_id": "parent-123",
                "project_path": "/tmp",
                "session_id": None,
            }
        )

        with pytest.raises(ValueError):
            deserialize_subprocess_config(incomplete)

    def test_missing_prompt_key(self) -> None:
        """Test that deserialize raises ValueError when prompt is missing."""
        incomplete = json.dumps(
            {
                "config": {"model": "gpt-4o"},
                "parent_id": "parent-123",
                "project_path": "/tmp",
                "session_id": None,
            }
        )

        with pytest.raises(ValueError):
            deserialize_subprocess_config(incomplete)

    def test_missing_parent_id_key(self) -> None:
        """Test that deserialize raises ValueError when parent_id is missing."""
        incomplete = json.dumps(
            {
                "config": {"model": "gpt-4o"},
                "prompt": "Hello",
                "project_path": "/tmp",
                "session_id": None,
            }
        )

        with pytest.raises(ValueError):
            deserialize_subprocess_config(incomplete)

    def test_missing_project_path_key(self) -> None:
        """Test that deserialize raises ValueError when project_path is missing."""
        incomplete = json.dumps(
            {
                "config": {"model": "gpt-4o"},
                "prompt": "Hello",
                "parent_id": "parent-123",
                "session_id": None,
            }
        )

        with pytest.raises(ValueError):
            deserialize_subprocess_config(incomplete)


class TestMalformedJson:
    """Tests for malformed JSON error."""

    def test_malformed_json(self) -> None:
        """Test that deserialize raises json.JSONDecodeError on malformed input."""
        malformed = "this is not valid json { broken"

        with pytest.raises(json.JSONDecodeError):
            deserialize_subprocess_config(malformed)

    def test_empty_string(self) -> None:
        """Test that deserialize raises json.JSONDecodeError on empty string."""
        with pytest.raises(json.JSONDecodeError):
            deserialize_subprocess_config("")


class TestRunChildSession:
    """Tests for _run_child_session async function."""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Any) -> None:
        """Test success path: AmplifierSession constructed with correct args, execute and cleanup called."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello, world!"
        parent_id = "parent-123"
        project_path = str(tmp_path)
        session_id = "child-456"

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
                session_id=session_id,
            )
        )

        with patch(
            "amplifier_foundation.subprocess_runner.AmplifierSession"
        ) as MockSession:
            mock_instance = MagicMock()
            mock_instance.execute = AsyncMock(return_value="result string")
            mock_instance.cleanup = AsyncMock()
            MockSession.return_value = mock_instance

            result = await _run_child_session(str(config_file))

        assert result == "result string"
        MockSession.assert_called_once_with(
            config=config, parent_id=parent_id, session_id=session_id
        )
        mock_instance.execute.assert_called_once_with(prompt)
        mock_instance.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_on_error(self, tmp_path: Any) -> None:
        """Test that cleanup is called even when execute raises RuntimeError."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Fail me"
        parent_id = "parent-789"
        project_path = str(tmp_path)
        session_id = "child-error"

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
                session_id=session_id,
            )
        )

        with patch(
            "amplifier_foundation.subprocess_runner.AmplifierSession"
        ) as MockSession:
            mock_instance = MagicMock()
            mock_instance.execute = AsyncMock(
                side_effect=RuntimeError("something went wrong")
            )
            mock_instance.cleanup = AsyncMock()
            MockSession.return_value = mock_instance

            with pytest.raises(RuntimeError, match="something went wrong"):
                await _run_child_session(str(config_file))

        mock_instance.cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_session_id(self, tmp_path: Any) -> None:
        """Test that None is passed as session_id when session_id is absent from config."""
        config: dict[str, Any] = {"provider": "openai"}
        prompt = "No session ID here"
        parent_id = "parent-no-id"
        project_path = str(tmp_path)

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
                # session_id omitted — defaults to None
            )
        )

        with patch(
            "amplifier_foundation.subprocess_runner.AmplifierSession"
        ) as MockSession:
            mock_instance = MagicMock()
            mock_instance.execute = AsyncMock(return_value="ok")
            mock_instance.cleanup = AsyncMock()
            MockSession.return_value = mock_instance

            await _run_child_session(str(config_file))

        MockSession.assert_called_once_with(
            config=config, parent_id=parent_id, session_id=None
        )
