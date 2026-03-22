"""Tests for the hooks-process-guard module.

TestProcessGuardConfig tests are expected to PASS after implementation.
TestMount tests are expected to FAIL until Task 6 (ProcessGuardHooks implementation).
"""

from unittest.mock import MagicMock, patch

import pytest

from amplifier_module_hooks_process_guard import (
    ProcessGuardConfig,
    ProcessGuardHooks,
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
        base = {}
        base.update(config_overrides)
        config = ProcessGuardConfig(**base)
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
