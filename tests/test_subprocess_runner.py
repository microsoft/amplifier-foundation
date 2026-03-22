"""Tests for subprocess_runner module - config serialization/deserialization for IPC."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from amplifier_foundation.subprocess_runner import DEFAULT_MAX_SUBPROCESS
from amplifier_foundation.subprocess_runner import _run_child_session
from amplifier_foundation.subprocess_runner import deserialize_subprocess_config
from amplifier_foundation.subprocess_runner import run_session_in_subprocess
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


class TestMainEntryPoint:
    """Tests for the __main__ entry point."""

    def test_missing_argv_exits_nonzero(self) -> None:
        """Test that running with no arguments exits with code 1 and prints usage to stderr."""
        # Run from the project root so the package is importable regardless of current CWD
        # (other tests call os.chdir which can move the CWD away from the project root)
        project_root = Path(__file__).parent.parent

        result = subprocess.run(
            [sys.executable, "-m", "amplifier_foundation.subprocess_runner"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )

        assert result.returncode == 1
        assert "Usage:" in result.stderr


class TestRunSessionInSubprocess:
    """Tests for the parent-side run_session_in_subprocess() function."""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Any) -> None:
        """Test success path: process exits zero, stdout is returned stripped."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"result output\n", b""))
        mock_process.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)
        ):
            result = await run_session_in_subprocess(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
            )

        assert result == "result output"

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_runtime_error(self, tmp_path: Any) -> None:
        """Test that non-zero exit code raises RuntimeError containing stderr text."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(
            return_value=(b"", b"something went wrong")
        )
        mock_process.returncode = 1

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)
        ):
            with pytest.raises(RuntimeError, match="something went wrong"):
                await run_session_in_subprocess(
                    config=config,
                    prompt=prompt,
                    parent_id=parent_id,
                    project_path=project_path,
                )

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self, tmp_path: Any) -> None:
        """Test that timeout kills process and raises TimeoutError."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        mock_process = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)
        ):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                with pytest.raises(TimeoutError, match="timed out after 30s"):
                    await run_session_in_subprocess(
                        config=config,
                        prompt=prompt,
                        parent_id=parent_id,
                        project_path=project_path,
                        timeout=30,
                    )

        mock_process.kill.assert_called_once()
        mock_process.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_temp_file_cleanup_on_success(self, tmp_path: Any) -> None:
        """Test that temp file is cleaned up after successful subprocess execution."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"result", b""))
        mock_process.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)
        ):
            with patch("os.unlink") as mock_unlink:
                await run_session_in_subprocess(
                    config=config,
                    prompt=prompt,
                    parent_id=parent_id,
                    project_path=project_path,
                )

        mock_unlink.assert_called_once()
        unlinked_path = mock_unlink.call_args[0][0]
        assert "amp_subprocess_" in unlinked_path
        assert unlinked_path.endswith(".json")

    @pytest.mark.asyncio
    async def test_temp_file_cleanup_on_error(self, tmp_path: Any) -> None:
        """Test that temp file is cleaned up even when subprocess fails."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"error occurred"))
        mock_process.returncode = 1

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)
        ):
            with patch("os.unlink") as mock_unlink:
                with pytest.raises(RuntimeError):
                    await run_session_in_subprocess(
                        config=config,
                        prompt=prompt,
                        parent_id=parent_id,
                        project_path=project_path,
                    )

        mock_unlink.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_session_id_in_config(self, tmp_path: Any) -> None:
        """Test that session_id is included in the serialized config file passed to child."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)
        session_id = "child-session-789"

        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"result", b""))
        mock_process.returncode = 0

        file_content: dict[str, Any] = {}

        async def capture_subprocess(*args: Any, **kwargs: Any) -> MagicMock:
            # args[3] is the temp config file path passed to the child
            config_path = args[3]
            with open(config_path) as fh:
                file_content["data"] = json.loads(fh.read())
            return mock_process

        with patch("asyncio.create_subprocess_exec", side_effect=capture_subprocess):
            await run_session_in_subprocess(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
                session_id=session_id,
            )

        assert file_content["data"]["session_id"] == session_id


class TestBundleContextSerialization:
    """Tests for bundle context fields in IPC payload (module_paths, bundle_package_paths, sys_paths)."""

    def test_roundtrip_with_bundle_context(self) -> None:
        """Test that module_paths, bundle_package_paths, and sys_paths round-trip correctly."""
        config = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-abc"
        project_path = "/tmp/project"
        module_paths = {"my_module": "/path/to/my_module", "other": "/path/to/other"}
        bundle_package_paths = ["/path/to/bundle1", "/path/to/bundle2"]
        sys_paths = ["/extra/path1", "/extra/path2"]

        serialized = serialize_subprocess_config(
            config=config,
            prompt=prompt,
            parent_id=parent_id,
            project_path=project_path,
            module_paths=module_paths,
            bundle_package_paths=bundle_package_paths,
            sys_paths=sys_paths,
        )
        deserialized = deserialize_subprocess_config(serialized)

        assert deserialized["module_paths"] == module_paths
        assert deserialized["bundle_package_paths"] == bundle_package_paths
        assert deserialized["sys_paths"] == sys_paths

    def test_roundtrip_without_bundle_context(self) -> None:
        """Test that module_paths, bundle_package_paths, sys_paths default to empty when not provided."""
        config = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-abc"
        project_path = "/tmp/project"

        serialized = serialize_subprocess_config(
            config=config,
            prompt=prompt,
            parent_id=parent_id,
            project_path=project_path,
        )
        deserialized = deserialize_subprocess_config(serialized)

        assert deserialized["module_paths"] == {}
        assert deserialized["bundle_package_paths"] == []
        assert deserialized["sys_paths"] == []


class TestSemaphoreConstants:
    """Tests for module-level semaphore constants."""

    def test_default_max_subprocess_is_4(self) -> None:
        """Test that DEFAULT_MAX_SUBPROCESS equals 4."""
        assert DEFAULT_MAX_SUBPROCESS == 4


class TestConcurrencyLimiting:
    """Tests that semaphore limits concurrent subprocess sessions."""

    @pytest.mark.asyncio
    async def test_max_concurrent_limits_parallelism(self, tmp_path: Any) -> None:
        """Test that max_concurrent=2 allows at most 2 concurrent subprocesses.

        Launches 6 concurrent calls with max_concurrent=2. Uses a slow_communicate
        that sleeps briefly to simulate subprocess work and tracks the peak concurrency.
        Asserts max_observed <= 2.
        """
        import amplifier_foundation.subprocess_runner as runner_module

        # Reset semaphore state between tests
        runner_module._subprocess_semaphore = None
        runner_module._semaphore_limit = runner_module.DEFAULT_MAX_SUBPROCESS

        active_count = 0
        max_observed = 0

        async def slow_communicate() -> tuple[bytes, bytes]:
            nonlocal active_count, max_observed
            active_count += 1
            if active_count > max_observed:
                max_observed = active_count
            await asyncio.sleep(0.05)
            active_count -= 1
            return (b"result", b"")

        mock_process = MagicMock()
        mock_process.communicate = slow_communicate
        mock_process.returncode = 0

        config: dict[str, Any] = {"provider": "anthropic"}
        project_path = str(tmp_path)

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)
        ):
            tasks = [
                run_session_in_subprocess(
                    config=config,
                    prompt="Hello",
                    parent_id="parent-123",
                    project_path=project_path,
                    max_concurrent=2,
                )
                for _ in range(6)
            ]
            await asyncio.gather(*tasks)

        assert max_observed <= 2
