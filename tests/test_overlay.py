"""Tests for RuntimeOverlay - Phase 1 (S1 session baseline overlap + S2 mode-only).

S1 (overlap with session baseline) and S2 (mode-only) for agent/context/skill categories.
S3/S4 deferred to Phase 3.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_foundation.configurator._overlay import RuntimeOverlay, TransitionResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_coordinator(initial_agents=None, initial_capabilities=None):
    """Build a MagicMock coordinator following test_configurator.py pattern.

    Returns a MagicMock with:
        .config = {'agents': dict(initial_agents or {})}
        .register_capability / .get_capability backed by a capability_store dict
        .hooks = MagicMock() with .hooks.emit = AsyncMock()
    """
    coordinator = MagicMock()
    coordinator.config = {"agents": dict(initial_agents or {})}

    capability_store: dict = dict(initial_capabilities or {})

    def _register_capability(name, value):
        capability_store[name] = value

    def _get_capability(name):
        return capability_store.get(name)

    coordinator.register_capability = MagicMock(side_effect=_register_capability)
    coordinator.get_capability = MagicMock(side_effect=_get_capability)
    coordinator.hooks = MagicMock()
    coordinator.hooks.emit = AsyncMock()

    return coordinator


@pytest.fixture
def coordinator():
    """Bare coordinator with no initial agents."""
    return _make_coordinator()


@pytest.fixture
def overlay(coordinator):
    """RuntimeOverlay bound to coordinator with canonical mode event names."""
    return RuntimeOverlay(
        coordinator,
        success_event="mode:transition_completed",
        failure_event="mode:activation_failed",
    )


# ---------------------------------------------------------------------------
# S2 tests: mode-only agent contributions (no session baseline overlap)
# ---------------------------------------------------------------------------


class TestS2AgentPath:
    """S2 tests: mode-only agent contributions (no session baseline overlap)."""

    @pytest.mark.asyncio
    async def test_s2_agent_apply_mounts_into_coordinator_config(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() for agents category mounts agent into coordinator.config['agents']."""
        contributions = {
            "agents": {"mode-author": {"description": "A mode-author agent"}}
        }
        result: TransitionResult = await overlay.apply("mode:demo", contributions)

        assert result.success is True
        assert "mode-author" in coordinator.config["agents"]
        assert (
            coordinator.config["agents"]["mode-author"]["description"]
            == "A mode-author agent"
        )

    @pytest.mark.asyncio
    async def test_s2_agent_revoke_unmounts(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() removes previously applied agent from coordinator.config['agents']."""
        contributions = {
            "agents": {"mode-author": {"description": "A mode-author agent"}}
        }

        await overlay.apply("mode:demo", contributions)
        assert "mode-author" in coordinator.config["agents"]

        result = await overlay.revoke("mode:demo")

        assert result.success is True
        assert "mode-author" not in coordinator.config["agents"]

    @pytest.mark.asyncio
    async def test_revoke_unknown_scope_is_noop(self, overlay: RuntimeOverlay) -> None:
        """revoke() on a scope that was never applied returns success with empty unmounted list."""
        result = await overlay.revoke("mode:never-applied")

        assert result.success is True
        assert result.unmounted == []
