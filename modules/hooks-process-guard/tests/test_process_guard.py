"""Tests for the hooks-process-guard module.

TestProcessGuardConfig tests are expected to PASS after implementation.
TestMount tests are expected to FAIL until Task 6 (ProcessGuardHooks implementation).
"""

from unittest.mock import MagicMock

import pytest

from amplifier_module_hooks_process_guard import (
    ProcessGuardConfig,
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
