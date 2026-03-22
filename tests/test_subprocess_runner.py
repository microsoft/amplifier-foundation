"""Tests for subprocess_runner module - config serialization/deserialization for IPC."""

from __future__ import annotations

import json

import pytest

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
                {"module": "provider-anthropic", "config": {"default_model": "claude-3-haiku"}},
                {"module": "provider-openai", "config": {"default_model": "gpt-4o-mini"}},
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
