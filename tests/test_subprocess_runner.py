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
from amplifier_foundation.subprocess_runner import RESULT_END_MARKER
from amplifier_foundation.subprocess_runner import RESULT_START_MARKER
from amplifier_foundation.subprocess_runner import _build_child_env
from amplifier_foundation.subprocess_runner import _extract_framed_result
from amplifier_foundation.subprocess_runner import _run_child_session
from amplifier_foundation.subprocess_runner import _sanitize_error
from amplifier_foundation.subprocess_runner import _validate_project_path
from amplifier_foundation.subprocess_runner import configure_subprocess_limit
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
            mock_instance.initialize = AsyncMock()
            mock_instance.coordinator = MagicMock()
            mock_instance.coordinator.mount = AsyncMock()
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
            mock_instance.initialize = AsyncMock()
            mock_instance.coordinator = MagicMock()
            mock_instance.coordinator.mount = AsyncMock()
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
            mock_instance.initialize = AsyncMock()
            mock_instance.coordinator = MagicMock()
            mock_instance.coordinator.mount = AsyncMock()
            MockSession.return_value = mock_instance

            await _run_child_session(str(config_file))

        MockSession.assert_called_once_with(
            config=config, parent_id=parent_id, session_id=None
        )


class TestChildBootstrapBundleContext:
    """Tests for child session bootstrap with bundle context (initialize, sys.path, module resolver)."""

    @pytest.mark.asyncio
    async def test_initialize_called_before_execute(self, tmp_path: Any) -> None:
        """Test that session.initialize() is called before session.execute()."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
            )
        )

        call_order: list[str] = []

        with patch(
            "amplifier_foundation.subprocess_runner.AmplifierSession"
        ) as MockSession:
            mock_instance = MagicMock()

            async def track_initialize() -> None:
                call_order.append("initialize")

            async def track_execute(p: str) -> str:
                call_order.append("execute")
                return "result"

            async def track_cleanup() -> None:
                call_order.append("cleanup")

            mock_instance.initialize = track_initialize
            mock_instance.execute = track_execute
            mock_instance.cleanup = track_cleanup
            mock_instance.coordinator = MagicMock()
            mock_instance.coordinator.mount = AsyncMock()
            MockSession.return_value = mock_instance

            await _run_child_session(str(config_file))

        assert "initialize" in call_order, "initialize was never called"
        assert "execute" in call_order, "execute was never called"
        init_idx = call_order.index("initialize")
        exec_idx = call_order.index("execute")
        assert init_idx < exec_idx, (
            f"initialize (pos {init_idx}) must come before execute (pos {exec_idx})"
        )

    @pytest.mark.asyncio
    async def test_sys_paths_added_before_initialize(self, tmp_path: Any) -> None:
        """Test that sys_paths entries are added to sys.path before session.initialize()."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)
        fake_path = "/fake/path/for/test"

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
                sys_paths=[fake_path],
            )
        )

        path_present_at_initialize: list[bool] = []

        with (
            patch(
                "amplifier_foundation.subprocess_runner.AmplifierSession"
            ) as MockSession,
            patch("amplifier_foundation.subprocess_runner.sys") as mock_sys,
        ):
            mock_sys.path = []
            mock_instance = MagicMock()

            async def track_initialize() -> None:
                path_present_at_initialize.append(fake_path in mock_sys.path)

            mock_instance.initialize = track_initialize
            mock_instance.execute = AsyncMock(return_value="result")
            mock_instance.cleanup = AsyncMock()
            mock_instance.coordinator = MagicMock()
            mock_instance.coordinator.mount = AsyncMock()
            MockSession.return_value = mock_instance

            await _run_child_session(str(config_file))

        assert path_present_at_initialize, "initialize was never called"
        assert path_present_at_initialize[0], (
            f"Expected '{fake_path}' to be in sys.path before initialize() was called"
        )
        assert fake_path in mock_sys.path, (
            f"Expected '{fake_path}' in sys.path but got: {mock_sys.path}"
        )

    @pytest.mark.asyncio
    async def test_module_resolver_mounted_when_module_paths_provided(
        self, tmp_path: Any
    ) -> None:
        """Test that BundleModuleResolver is constructed with Path objects and mounted on coordinator."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)
        module_paths = {"my_module": "/path/to/module"}

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
                module_paths=module_paths,
            )
        )

        with patch(
            "amplifier_foundation.subprocess_runner.AmplifierSession"
        ) as MockSession:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_instance.execute = AsyncMock(return_value="result")
            mock_instance.cleanup = AsyncMock()
            mock_instance.coordinator = MagicMock()
            mock_instance.coordinator.mount = AsyncMock()
            MockSession.return_value = mock_instance

            with patch(
                "amplifier_foundation.subprocess_runner.BundleModuleResolver"
            ) as MockResolver:
                mock_resolver_instance = MagicMock()
                MockResolver.return_value = mock_resolver_instance

                await _run_child_session(str(config_file))

        # Verify BundleModuleResolver was constructed with Path objects
        MockResolver.assert_called_once()
        call_kwargs = MockResolver.call_args[0][
            0
        ]  # First positional arg: module_paths dict
        assert "my_module" in call_kwargs, (
            "module key missing from resolver constructor"
        )
        assert call_kwargs["my_module"] == Path("/path/to/module"), (
            f"Expected Path('/path/to/module'), got {call_kwargs['my_module']!r}"
        )

        # Verify mount was called with the resolver instance as 'module-source-resolver'
        mock_instance.coordinator.mount.assert_called_once_with(
            "module-source-resolver", mock_resolver_instance
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

        framed_stdout = (
            f"{RESULT_START_MARKER}\nresult output\n{RESULT_END_MARKER}\n"
        ).encode()
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(framed_stdout, b""))
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

        framed_stdout = (
            f"{RESULT_START_MARKER}\nresult\n{RESULT_END_MARKER}\n"
        ).encode()
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(framed_stdout, b""))
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

        framed_stdout = (
            f"{RESULT_START_MARKER}\nresult\n{RESULT_END_MARKER}\n"
        ).encode()
        mock_process.communicate = AsyncMock(return_value=(framed_stdout, b""))

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


class TestSemaphoreSetOnce:
    """Tests for the set-once configure_subprocess_limit() pattern."""

    def setup_method(self) -> None:
        """Reset module state before each test."""
        import amplifier_foundation.subprocess_runner as runner_module

        runner_module._subprocess_semaphore = None
        runner_module._semaphore_limit = runner_module.DEFAULT_MAX_SUBPROCESS
        runner_module._semaphore_configured = False

    def test_configure_subprocess_limit_sets_limit(self) -> None:
        """Test that configure_subprocess_limit() sets the semaphore limit."""
        import amplifier_foundation.subprocess_runner as runner_module

        configure_subprocess_limit(6)
        assert runner_module._semaphore_limit == 6

    def test_configure_subprocess_limit_rejects_second_call(self) -> None:
        """Test that a second call with a different value raises RuntimeError matching 'already configured'."""
        configure_subprocess_limit(3)
        with pytest.raises(RuntimeError, match="already configured"):
            configure_subprocess_limit(5)

    def test_configure_subprocess_limit_same_value_is_noop(self) -> None:
        """Test that calling with the same value a second time is a no-op (no exception)."""
        import amplifier_foundation.subprocess_runner as runner_module

        configure_subprocess_limit(3)
        configure_subprocess_limit(3)  # Same value — should not raise
        assert runner_module._semaphore_limit == 3


class TestConcurrencyLimiting:
    """Tests that semaphore limits concurrent subprocess sessions."""

    @pytest.mark.asyncio
    async def test_configured_limit_restricts_parallelism(self, tmp_path: Any) -> None:
        """Test that configure_subprocess_limit(2) allows at most 2 concurrent subprocesses.

        Resets module state, configures limit to 2, then launches 6 concurrent calls.
        Uses a slow_communicate that sleeps briefly to simulate subprocess work and
        tracks the peak concurrency. Asserts max_observed <= 2.
        """
        import amplifier_foundation.subprocess_runner as runner_module

        # Reset semaphore state between tests
        runner_module._subprocess_semaphore = None
        runner_module._semaphore_limit = runner_module.DEFAULT_MAX_SUBPROCESS
        runner_module._semaphore_configured = False

        configure_subprocess_limit(2)

        active_count = 0
        max_observed = 0

        framed_result = (
            f"{RESULT_START_MARKER}\nresult\n{RESULT_END_MARKER}\n"
        ).encode()

        async def slow_communicate() -> tuple[bytes, bytes]:
            nonlocal active_count, max_observed
            active_count += 1
            if active_count > max_observed:
                max_observed = active_count
            await asyncio.sleep(0.05)
            active_count -= 1
            return (framed_result, b"")

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
                )
                for _ in range(6)
            ]
            await asyncio.gather(*tasks)

        assert max_observed <= 2


class TestStdoutFraming:
    """Tests for stdout framing protocol with envelope delimiters."""

    def test_framed_output_extracted_correctly(self) -> None:
        """Test that stray prints before/after envelope are ignored, only framed content extracted."""
        stdout = (
            f"stray print before\n"
            f"{RESULT_START_MARKER}\n"
            f"actual result content\n"
            f"{RESULT_END_MARKER}\n"
            f"stray print after\n"
        )
        result = _extract_framed_result(stdout)
        assert result == "actual result content"

    def test_unframed_output_raises_runtime_error(self) -> None:
        """Test that stdout without markers raises RuntimeError matching 'missing result envelope'."""
        stdout = "no markers here at all"
        with pytest.raises(RuntimeError, match="missing result envelope"):
            _extract_framed_result(stdout)


class TestErrorSanitization:
    """Tests for _sanitize_error() credential redaction."""

    def test_sanitize_error_redacts_api_keys(self) -> None:
        """Test that API keys matching sk-... are redacted."""
        msg = "Error: invalid key sk-ant-api03-sometoken12345 was rejected"
        result = _sanitize_error(msg)
        assert "sk-ant-api03-sometoken12345" not in result
        assert "[REDACTED]" in result

    def test_sanitize_error_redacts_key_value_patterns(self) -> None:
        """Test that key=value patterns are redacted."""
        msg = "Authentication failed: api_key=super-secret"
        result = _sanitize_error(msg)
        assert "super-secret" not in result
        assert "[REDACTED]" in result

    def test_sanitize_error_preserves_safe_messages(self) -> None:
        """Test that safe messages pass through unchanged."""
        msg = "ModuleNotFoundError: No module named 'foo'"
        result = _sanitize_error(msg)
        assert result == msg

    @pytest.mark.asyncio
    async def test_parent_raises_sanitized_error(self, tmp_path: Any) -> None:
        """Test that RuntimeError contains exit code but not raw credentials."""
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(
            return_value=(b"", b"Error: sk-secret12345678901234 token rejected")
        )
        mock_process.returncode = 1

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await run_session_in_subprocess(
                    config=config,
                    prompt=prompt,
                    parent_id=parent_id,
                    project_path=project_path,
                )

        error_msg = str(exc_info.value)
        assert "exit code 1" in error_msg
        assert "sk-secret" not in error_msg


class TestCleanupHardening:
    """Tests for project_path validation, temp file inside try, and file permissions."""

    def test_nonexistent_project_path_raises(self) -> None:
        """Test that _validate_project_path raises ValueError for a non-existent path."""
        with pytest.raises(ValueError, match="does not exist or is not a directory"):
            _validate_project_path("/nonexistent/path/that/does/not/exist/at/all")

    def test_file_as_project_path_raises(self, tmp_path: Any) -> None:
        """Test that _validate_project_path raises ValueError when path is a file."""
        file_path = tmp_path / "notadir.txt"
        file_path.write_text("hello")
        with pytest.raises(ValueError, match="does not exist or is not a directory"):
            _validate_project_path(str(file_path))

    def test_valid_project_path_passes(self, tmp_path: Any) -> None:
        """Test that _validate_project_path passes for a valid directory (no exception raised)."""
        # Should not raise
        _validate_project_path(str(tmp_path))

    @pytest.mark.asyncio
    async def test_parent_validates_project_path(self) -> None:
        """Test that run_session_in_subprocess validates project_path before spawning."""
        with pytest.raises(ValueError, match="does not exist or is not a directory"):
            await run_session_in_subprocess(
                config={},
                prompt="Hello",
                parent_id="parent-123",
                project_path="/nonexistent/path/that/does/not/exist/at/all",
            )


class TestMainJsonEnvelope:
    """Tests that the __main__ block emits a JSON envelope between framing markers."""

    def _exec_main_block(self, tmp_path: Any, capsys: Any, mock_return: str) -> str:
        """Helper: exec only the __main__ body in the patched module namespace.

        Uses AST to extract the body of the ``if __name__ == "__main__":`` block
        so that function definitions are not re-executed and our mock survives.
        Returns the captured stdout.
        """
        import ast
        import importlib.util

        import amplifier_foundation.subprocess_runner as runner_mod

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config={},
                prompt="test",
                parent_id="p-1",
                project_path=str(tmp_path),
            )
        )

        # Load the module source
        spec = importlib.util.find_spec("amplifier_foundation.subprocess_runner")
        assert spec and spec.origin, "Cannot locate subprocess_runner source"
        source = open(spec.origin).read()

        # Extract just the body of the if __name__ == "__main__": block via AST
        tree = ast.parse(source)
        main_body = None
        for node in tree.body:
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "__main__"
            ):
                main_body = ast.Module(body=node.body, type_ignores=[])
                break
        assert main_body is not None, "No __main__ block found in subprocess_runner"

        main_code = compile(main_body, spec.origin, "exec")

        # Build namespace from module (includes patched _run_child_session)
        # with sys.argv pointing at our config file
        with (
            patch("sys.argv", ["runner", str(config_file)]),
            patch(
                "amplifier_foundation.subprocess_runner._run_child_session",
                new=AsyncMock(return_value=mock_return),
            ),
            patch("sys.exit"),
        ):
            ns = dict(vars(runner_mod))
            ns["__name__"] = "__main__"
            ns["_run_child_session"] = AsyncMock(return_value=mock_return)
            exec(main_code, ns)  # noqa: S102

        return capsys.readouterr().out

    def test_success_emits_json_envelope(self, tmp_path: Any, capsys: Any) -> None:
        """__main__ success path emits a JSON envelope between framing markers."""
        import json as _json

        stdout = self._exec_main_block(
            tmp_path, capsys, mock_return="session output text"
        )
        framed = _extract_framed_result(stdout)
        # RED: currently emits raw text; after fix this must be valid JSON
        parsed = _json.loads(framed)
        assert parsed["output"] == "session output text"
        assert parsed["status"] == "success"
        assert "turn_count" in parsed
        assert "metadata" in parsed

    def test_error_emits_json_envelope_with_status_error(
        self, tmp_path: Any, capsys: Any
    ) -> None:
        """__main__ error path emits a JSON envelope with status='error' and exits 1."""
        import json as _json

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config={},
                prompt="test",
                parent_id="p-1",
                project_path=str(tmp_path),
            )
        )

        import importlib.util

        import amplifier_foundation.subprocess_runner as runner_mod

        spec = importlib.util.find_spec("amplifier_foundation.subprocess_runner")
        assert spec and spec.origin
        source = open(spec.origin).read()

        import ast

        tree = ast.parse(source)
        main_body = None
        for node in tree.body:
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "__main__"
            ):
                main_body = ast.Module(body=node.body, type_ignores=[])
                break
        assert main_body is not None
        main_code = compile(main_body, spec.origin, "exec")

        sys_exit_code: list[int] = []

        def capture_exit(code: int = 0) -> None:
            sys_exit_code.append(code)

        with (
            patch("sys.argv", ["runner", str(config_file)]),
            patch(
                "amplifier_foundation.subprocess_runner._run_child_session",
                new=AsyncMock(side_effect=RuntimeError("session failed")),
            ),
            patch("sys.exit", side_effect=capture_exit),
        ):
            ns = dict(vars(runner_mod))
            ns["__name__"] = "__main__"
            ns["_run_child_session"] = AsyncMock(
                side_effect=RuntimeError("session failed")
            )
            exec(main_code, ns)  # noqa: S102

        captured = capsys.readouterr()
        # RED: currently error path does NOT emit JSON to stdout
        framed = _extract_framed_result(captured.out)
        parsed = _json.loads(framed)
        assert parsed["status"] == "error"
        assert "session failed" in parsed.get("error", "")
        # Process should have called sys.exit(1)
        assert sys_exit_code and sys_exit_code[0] == 1


class TestEnvVarAllowlist:
    """Tests for environment variable allowlist in _build_child_env()."""

    def test_build_child_env_includes_required_vars(self) -> None:
        """Test that PATH, HOME, and LLM provider prefixes are included; unrelated secrets excluded."""
        test_env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/user",
            "AMPLIFIER_CONFIG": "some-config",
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "OPENAI_API_KEY": "sk-openai-test",
            "AZURE_OPENAI_API_KEY": "azure-key",
            "UNRELATED_SECRET": "should-not-appear",
            "DATABASE_PASSWORD": "super-secret-db-pass",
        }
        with patch.dict("os.environ", test_env, clear=True):
            result = _build_child_env()

        assert result["PATH"] == "/usr/bin:/bin"
        assert result["HOME"] == "/home/user"
        assert result["AMPLIFIER_CONFIG"] == "some-config"
        assert result["ANTHROPIC_API_KEY"] == "sk-ant-test"
        assert result["OPENAI_API_KEY"] == "sk-openai-test"
        assert result["AZURE_OPENAI_API_KEY"] == "azure-key"
        assert "UNRELATED_SECRET" not in result
        assert "DATABASE_PASSWORD" not in result

    def test_build_child_env_includes_common_provider_keys(self) -> None:
        """Test that GOOGLE_ and AWS_ prefixed vars are included in the filtered env."""
        test_env = {
            "GOOGLE_API_KEY": "google-key-123",
            "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/creds.json",
            "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG",
            "AWS_DEFAULT_REGION": "us-east-1",
            "MY_INTERNAL_SECRET": "hidden",
            "CORP_DATABASE_URL": "postgres://secret",
        }
        with patch.dict("os.environ", test_env, clear=True):
            result = _build_child_env()

        assert result["GOOGLE_API_KEY"] == "google-key-123"
        assert result["GOOGLE_APPLICATION_CREDENTIALS"] == "/path/to/creds.json"
        assert result["AWS_ACCESS_KEY_ID"] == "AKIAIOSFODNN7EXAMPLE"
        assert result["AWS_SECRET_ACCESS_KEY"] == "wJalrXUtnFEMI/K7MDENG"
        assert result["AWS_DEFAULT_REGION"] == "us-east-1"
        assert "MY_INTERNAL_SECRET" not in result
        assert "CORP_DATABASE_URL" not in result


class TestChildSessionCapabilities:
    """Tests that _run_child_session registers the session.working_dir capability."""

    @pytest.mark.asyncio
    async def test_child_registers_working_dir_capability(self, tmp_path: Any) -> None:
        """Test that _run_child_session registers session.working_dir on the coordinator.

        Without this capability, tool-filesystem defaults to allowed_write_paths: ['.']
        which may silently block investigation agents from writing artifacts.
        """
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
            )
        )

        with patch(
            "amplifier_foundation.subprocess_runner.AmplifierSession"
        ) as MockSession:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_instance.execute = AsyncMock(return_value="result")
            mock_instance.cleanup = AsyncMock()
            mock_instance.coordinator = MagicMock()
            mock_instance.coordinator.mount = AsyncMock()
            mock_instance.coordinator.register_capability = MagicMock()
            MockSession.return_value = mock_instance

            await _run_child_session(str(config_file))

        mock_instance.coordinator.register_capability.assert_any_call(
            "session.working_dir", project_path
        )

    @pytest.mark.asyncio
    async def test_working_dir_registered_after_initialize_before_execute(
        self, tmp_path: Any
    ) -> None:
        """Test that session.working_dir is registered AFTER initialize() and BEFORE execute().

        The capability must be available when tools (like tool-filesystem) are first invoked,
        which happens during execute(). Registering before initialize() is too early
        (coordinator may be reset); registering after execute() is too late.
        """
        config: dict[str, Any] = {"provider": "anthropic"}
        prompt = "Hello"
        parent_id = "parent-123"
        project_path = str(tmp_path)

        config_file = tmp_path / "config.json"
        config_file.write_text(
            serialize_subprocess_config(
                config=config,
                prompt=prompt,
                parent_id=parent_id,
                project_path=project_path,
            )
        )

        call_order: list[str] = []

        with patch(
            "amplifier_foundation.subprocess_runner.AmplifierSession"
        ) as MockSession:
            mock_instance = MagicMock()

            async def track_initialize() -> None:
                call_order.append("initialize")

            async def track_execute(p: str) -> str:
                call_order.append("execute")
                return "result"

            async def track_cleanup() -> None:
                call_order.append("cleanup")

            def track_register_capability(name: str, value: Any) -> None:
                if name == "session.working_dir":
                    call_order.append("register_working_dir")

            mock_instance.initialize = track_initialize
            mock_instance.execute = track_execute
            mock_instance.cleanup = track_cleanup
            mock_instance.coordinator = MagicMock()
            mock_instance.coordinator.mount = AsyncMock()
            mock_instance.coordinator.register_capability = track_register_capability
            MockSession.return_value = mock_instance

            await _run_child_session(str(config_file))

        assert "register_working_dir" in call_order, (
            "_run_child_session must call coordinator.register_capability('session.working_dir', ...)"
        )
        init_idx = call_order.index("initialize")
        reg_idx = call_order.index("register_working_dir")
        exec_idx = call_order.index("execute")
        assert reg_idx > init_idx, (
            f"register_working_dir (pos {reg_idx}) must come after initialize (pos {init_idx})"
        )
        assert reg_idx < exec_idx, (
            f"register_working_dir (pos {reg_idx}) must come before execute (pos {exec_idx})"
        )
