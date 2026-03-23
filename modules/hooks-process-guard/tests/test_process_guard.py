"""Tests for the hooks-process-guard module.

TestProcessGuardConfig tests are expected to PASS after implementation.
TestMount tests are expected to FAIL until Task 6 (ProcessGuardHooks implementation).
"""

from unittest.mock import MagicMock, patch

import pytest

from amplifier_module_hooks_process_guard import (
    ProcessGuardConfig,
    ProcessGuardHooks,
    _kill_orphans,
    mount,
)


# — Config Tests —


class TestProcessGuardConfig:
    """Tests for ProcessGuardConfig dataclass defaults and custom values."""

    def test_defaults(self):
        """All default values match the spec."""
        config = ProcessGuardConfig()

        assert config.max_process_count == 128
        assert config.warning_threshold == 64
        assert config.kill_orphans_before_exec is True
        assert config.kill_patterns == ["pytest", "node.*test"]
        assert config.repeat_detection_window_seconds == 60
        assert config.repeat_detection_max_count == 5
        assert config.enabled is True

    def test_custom_values(self):
        """Custom values override defaults correctly."""
        config = ProcessGuardConfig(
            max_process_count=64,
            warning_threshold=32,
            kill_orphans_before_exec=False,
            kill_patterns=["custom_pattern", "another.*test"],
            repeat_detection_window_seconds=30,
            repeat_detection_max_count=3,
            enabled=False,
        )

        assert config.max_process_count == 64
        assert config.warning_threshold == 32
        assert config.kill_orphans_before_exec is False
        assert config.kill_patterns == ["custom_pattern", "another.*test"]
        assert config.repeat_detection_window_seconds == 30
        assert config.repeat_detection_max_count == 3
        assert config.enabled is False

    def test_partial_override(self):
        """Partial config overrides leave other fields at defaults."""
        config = ProcessGuardConfig(max_process_count=256)

        assert config.max_process_count == 256
        assert config.warning_threshold == 64  # default
        assert config.enabled is True  # default

    def test_kill_patterns_default_is_independent(self):
        """Each instance gets its own kill_patterns list (no shared mutable default)."""
        config1 = ProcessGuardConfig()
        config2 = ProcessGuardConfig()

        config1.kill_patterns.append("extra")

        assert "extra" not in config2.kill_patterns


# — Mount Tests (expected to FAIL until Task 6) —


class TestMount:
    """Tests for the mount() entry point.

    These tests are expected to FAIL until Task 6 because ProcessGuardHooks
    is a stub without the handle_tool_pre method.
    """

    @pytest.mark.asyncio
    async def test_registers_tool_pre_hook(self):
        """mount() registers handler on 'tool:pre' with priority=10."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()

        await mount(coordinator, {})

        coordinator.hooks.register.assert_called_once()
        call_args = coordinator.hooks.register.call_args
        assert call_args[0][0] == "tool:pre"
        assert call_args[1]["priority"] == 10

    @pytest.mark.asyncio
    async def test_returns_metadata(self):
        """mount() returns a metadata dict with name, version, description, config."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()

        result = await mount(coordinator, {})

        assert result["name"] == "hooks-process-guard"
        assert "version" in result
        assert "description" in result
        assert "config" in result

    @pytest.mark.asyncio
    async def test_mount_with_custom_config(self):
        """mount() creates ProcessGuardConfig from provided config dict."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()

        result = await mount(coordinator, {"max_process_count": 64, "enabled": False})

        # Config should be reflected in metadata
        assert result["config"]["max_process_count"] == 64
        assert result["config"]["enabled"] is False


# — Process Count Monitoring Tests —


class TestProcessCountMonitoring:
    """Tests for ProcessGuardHooks process count monitoring."""

    def _make_hooks(self, **config_overrides):
        """Helper to create ProcessGuardHooks with given config."""
        # Disable orphan cleanup by default so tests don't call pkill
        config_overrides.setdefault("kill_orphans_before_exec", False)
        config = ProcessGuardConfig(**config_overrides)
        return ProcessGuardHooks(config)

    def _make_bash_event(self):
        """Create a data dict simulating a bash tool event."""
        return {"tool_name": "bash", "tool_input": {"command": "ls -la"}}

    def _make_non_bash_event(self, tool_name="read_file"):
        """Create a data dict simulating a non-bash tool event."""
        return {"tool_name": tool_name, "tool_input": {}}

    @pytest.mark.asyncio
    async def test_continues_when_below_warning(self):
        """Returns continue when process count is below warning_threshold."""
        hooks = self._make_hooks(warning_threshold=64, max_process_count=128)

        with patch.object(hooks, "_get_process_count", return_value=10):
            result = await hooks.handle_tool_pre("tool:pre", self._make_bash_event())

        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_injects_warning_above_threshold(self):
        """Returns inject_context when process count is at or above warning_threshold."""
        hooks = self._make_hooks(warning_threshold=64, max_process_count=128)

        with patch.object(hooks, "_get_process_count", return_value=80):
            result = await hooks.handle_tool_pre("tool:pre", self._make_bash_event())

        assert result.action == "inject_context"
        assert result.context_injection_role == "user"
        assert result.ephemeral is True
        assert result.append_to_last_tool_result is True
        # Warning should include process count, thresholds, and remediation
        assert result.context_injection is not None
        assert "80" in result.context_injection  # current count
        assert "64" in result.context_injection  # warning threshold
        assert "128" in result.context_injection  # max threshold

    @pytest.mark.asyncio
    async def test_denies_above_critical(self):
        """Returns deny when process count is at or above max_process_count."""
        hooks = self._make_hooks(warning_threshold=64, max_process_count=128)

        with patch.object(hooks, "_get_process_count", return_value=128):
            result = await hooks.handle_tool_pre("tool:pre", self._make_bash_event())

        assert result.action == "deny"
        assert result.reason is not None

    @pytest.mark.asyncio
    async def test_ignores_non_bash_tools(self):
        """Returns continue for non-bash tools without checking process count."""
        hooks = self._make_hooks()

        # Even if process count would trigger deny, non-bash tools are ignored
        with patch.object(hooks, "_get_process_count", return_value=999) as mock_count:
            result = await hooks.handle_tool_pre(
                "tool:pre", self._make_non_bash_event()
            )

        assert result.action == "continue"
        # _get_process_count should NOT be called for non-bash tools
        mock_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_always_continues(self):
        """Returns continue immediately when disabled, regardless of process count."""
        hooks = self._make_hooks(enabled=False)

        with patch.object(hooks, "_get_process_count", return_value=999) as mock_count:
            result = await hooks.handle_tool_pre("tool:pre", self._make_bash_event())

        assert result.action == "continue"
        mock_count.assert_not_called()


# — Repeated Command Detection Tests —


class TestRepeatedCommandDetection:
    """Tests for repeated command detection in ProcessGuardHooks."""

    def _make_hooks(self, **config_overrides):
        """Helper to create ProcessGuardHooks with given config."""
        # Disable orphan cleanup by default so tests don't call pkill
        config_overrides.setdefault("kill_orphans_before_exec", False)
        config = ProcessGuardConfig(**config_overrides)
        return ProcessGuardHooks(config)

    def _make_bash_event(self, command="ls -la", session_id="session-1"):
        """Create a bash event data dict with command and session_id."""
        return {
            "tool_name": "bash",
            "session_id": session_id,
            "tool_input": {"command": command},
        }

    @pytest.mark.asyncio
    async def test_no_warning_on_first_call(self):
        """No warning is returned on the first call to a command."""
        hooks = self._make_hooks(repeat_detection_max_count=3)

        with patch.object(hooks, "_get_process_count", return_value=10):
            with patch("time.time", return_value=1000.0):
                result = await hooks.handle_tool_pre(
                    "tool:pre", self._make_bash_event()
                )

        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_warning_after_max_repeats(self):
        """Warning is returned when command has been repeated >= max_count times."""
        hooks = self._make_hooks(repeat_detection_max_count=3)

        with patch.object(hooks, "_get_process_count", return_value=10):
            with patch("time.time", return_value=1000.0):
                # First 3 calls - no warning (count before each is 0, 1, 2)
                for _ in range(3):
                    result = await hooks.handle_tool_pre(
                        "tool:pre", self._make_bash_event()
                    )
                    assert result.action == "continue"

                # 4th call - count before recording is 3 >= max_count=3 → warning
                result = await hooks.handle_tool_pre(
                    "tool:pre", self._make_bash_event()
                )

        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert "ls -la" in result.context_injection

    @pytest.mark.asyncio
    async def test_different_commands_do_not_trigger(self):
        """Different commands do not accumulate towards the repeat detection threshold."""
        hooks = self._make_hooks(repeat_detection_max_count=3)

        with patch.object(hooks, "_get_process_count", return_value=10):
            with patch("time.time", return_value=1000.0):
                for i in range(10):
                    result = await hooks.handle_tool_pre(
                        "tool:pre",
                        self._make_bash_event(command=f"command_{i}"),
                    )
                    assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_different_sessions_tracked_independently(self):
        """Different sessions track command history independently."""
        hooks = self._make_hooks(repeat_detection_max_count=3)

        with patch.object(hooks, "_get_process_count", return_value=10):
            with patch("time.time", return_value=1000.0):
                # Run same command 3 times in session-1 (no warning yet)
                for _ in range(3):
                    result = await hooks.handle_tool_pre(
                        "tool:pre", self._make_bash_event(session_id="session-1")
                    )
                    assert result.action == "continue"

                # First call in session-2 should NOT trigger warning
                result = await hooks.handle_tool_pre(
                    "tool:pre", self._make_bash_event(session_id="session-2")
                )
                assert result.action == "continue"

                # 4th call in session-1 SHOULD trigger warning
                result = await hooks.handle_tool_pre(
                    "tool:pre", self._make_bash_event(session_id="session-1")
                )
                assert result.action == "inject_context"

    @pytest.mark.asyncio
    async def test_old_entries_expire_from_window(self):
        """Old entries outside the time window do not count towards repeat detection."""
        hooks = self._make_hooks(
            repeat_detection_max_count=3, repeat_detection_window_seconds=60
        )

        with patch.object(hooks, "_get_process_count", return_value=10):
            # Run command 3 times at t=0
            with patch("time.time", return_value=0.0):
                for _ in range(3):
                    await hooks.handle_tool_pre("tool:pre", self._make_bash_event())

            # At t=120 (outside 60s window), same command should not trigger warning
            with patch("time.time", return_value=120.0):
                result = await hooks.handle_tool_pre(
                    "tool:pre", self._make_bash_event()
                )

        assert result.action == "continue"


# — Kill Orphans Helper Tests —


class TestKillOrphansHelper:
    """Tests for the _kill_orphans module-level helper function."""

    def test_calls_pkill_for_each_pattern(self):
        """_kill_orphans calls subprocess.run with pkill -f for each pattern."""
        patterns = ["pytest", "node.*test"]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _kill_orphans(patterns)

        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["pkill", "-f", "pytest"], capture_output=True, timeout=5
        )
        mock_run.assert_any_call(
            ["pkill", "-f", "node.*test"], capture_output=True, timeout=5
        )

    def test_ignores_pkill_errors(self):
        """_kill_orphans silently ignores all errors from pkill (including returncode=1)."""
        patterns = ["pytest", "node.*test"]

        with patch("subprocess.run") as mock_run:
            # Simulate pkill returning error (returncode=1 means no processes matched)
            mock_run.side_effect = Exception("pkill failed unexpectedly")
            # Should NOT raise
            _kill_orphans(patterns)

        # Both patterns should have been attempted
        assert mock_run.call_count == 2


# — Orphan Cleanup Integration Tests —


class TestOrphanCleanup:
    """Tests for orphan cleanup integration in handle_tool_pre."""

    def _make_hooks(self, **config_overrides):
        """Helper to create ProcessGuardHooks with given config."""
        config = ProcessGuardConfig(**config_overrides)
        return ProcessGuardHooks(config)

    def _make_bash_event(self, command="ls -la", session_id="session-1"):
        """Create a bash event data dict."""
        return {
            "tool_name": "bash",
            "session_id": session_id,
            "tool_input": {"command": command},
        }

    def _make_non_bash_event(self, tool_name="read_file"):
        """Create a non-bash event data dict."""
        return {"tool_name": tool_name, "tool_input": {}}

    @pytest.mark.asyncio
    async def test_kills_orphans_before_bash(self):
        """When kill_orphans_before_exec=True and tool is bash, _kill_orphans is called."""
        hooks = self._make_hooks(
            kill_orphans_before_exec=True,
            kill_patterns=["pytest", "node.*test"],
        )

        with patch.object(hooks, "_get_process_count", return_value=10):
            with patch(
                "amplifier_module_hooks_process_guard._kill_orphans"
            ) as mock_kill:
                await hooks.handle_tool_pre("tool:pre", self._make_bash_event())

        mock_kill.assert_called_once_with(["pytest", "node.*test"])

    @pytest.mark.asyncio
    async def test_skips_cleanup_when_disabled(self):
        """When kill_orphans_before_exec=False, _kill_orphans is NOT called."""
        hooks = self._make_hooks(
            kill_orphans_before_exec=False,
            kill_patterns=["pytest", "node.*test"],
        )

        with patch.object(hooks, "_get_process_count", return_value=10):
            with patch(
                "amplifier_module_hooks_process_guard._kill_orphans"
            ) as mock_kill:
                await hooks.handle_tool_pre("tool:pre", self._make_bash_event())

        mock_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_cleanup_for_non_bash_tools(self):
        """When tool is not bash, _kill_orphans is NOT called."""
        hooks = self._make_hooks(
            kill_orphans_before_exec=True,
            kill_patterns=["pytest", "node.*test"],
        )

        with patch("amplifier_module_hooks_process_guard._kill_orphans") as mock_kill:
            await hooks.handle_tool_pre("tool:pre", self._make_non_bash_event())

        mock_kill.assert_not_called()
