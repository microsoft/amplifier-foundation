"""Tests for model_role parameter in tool-delegate."""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn=None,
    agents: dict | None = None,
    session_state: dict | None = None,
) -> DelegateTool:
    """Create a DelegateTool with mocked coordinator for model_role testing."""
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"

    coordinator.config = {"agents": agents or {}}

    # Session state for routing matrix availability
    coordinator.session_state = session_state or {}

    capabilities: dict = {
        "session.spawn": spawn_fn
        or AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        ),
        "session.resume": AsyncMock(return_value={}),
        "agents.list": lambda: agents or {},
        "agents.get": lambda name: (agents or {}).get(name),
        "self_delegation_depth": 0,
    }

    def get_capability(name):
        return capabilities.get(name)

    coordinator.get_capability = get_capability
    coordinator.get = MagicMock(return_value=None)  # hooks = None

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


def _install_mock_resolver(mock_resolve_fn):
    """Install a mock amplifier_module_hooks_routing.resolver module in sys.modules.

    Returns a cleanup function to remove it.
    """
    mock_resolver_mod = types.ModuleType("amplifier_module_hooks_routing.resolver")
    mock_resolver_mod.resolve_model_role = mock_resolve_fn  # type: ignore[attr-defined]

    mock_hooks_routing_mod = types.ModuleType("amplifier_module_hooks_routing")

    originals = {}
    for mod_name in (
        "amplifier_module_hooks_routing",
        "amplifier_module_hooks_routing.resolver",
    ):
        if mod_name in sys.modules:
            originals[mod_name] = sys.modules[mod_name]

    sys.modules["amplifier_module_hooks_routing"] = mock_hooks_routing_mod
    sys.modules["amplifier_module_hooks_routing.resolver"] = mock_resolver_mod

    def cleanup():
        for mod_name in (
            "amplifier_module_hooks_routing",
            "amplifier_module_hooks_routing.resolver",
        ):
            if mod_name in originals:
                sys.modules[mod_name] = originals[mod_name]
            elif mod_name in sys.modules:
                del sys.modules[mod_name]

    return cleanup


# =============================================================================
# Tests: model_role parameter
# =============================================================================


class TestDelegateModelRole:
    """Tests for model_role parameter in delegate tool execute()."""

    @pytest.mark.asyncio
    async def test_model_role_resolves_against_matrix(self):
        """model_role resolves against routing matrix and produces provider_preferences."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        # Mock the resolver to return a resolved preference
        mock_resolve = AsyncMock(
            return_value=[
                {"provider": "anthropic", "model": "claude-haiku-3.5", "config": {}}
            ]
        )
        cleanup = _install_mock_resolver(mock_resolve)

        try:
            tool = _make_delegate_tool(
                spawn_fn=spawn_fn,
                agents={
                    "test-agent": {
                        "description": "A test agent",
                    }
                },
                session_state={
                    "routing_matrix": {
                        "roles": {
                            "fast": {
                                "candidates": [
                                    {
                                        "provider": "anthropic",
                                        "model": "claude-haiku-*",
                                    },
                                ]
                            }
                        }
                    }
                },
            )
            # Wire up providers on coordinator.get("providers")
            tool.coordinator.get = MagicMock(
                side_effect=lambda key: (
                    {"provider-anthropic": MagicMock()} if key == "providers" else None
                )
            )

            await tool.execute(
                {
                    "agent": "test-agent",
                    "instruction": "Do something fast",
                    "context_depth": "none",
                    "model_role": "fast",
                }
            )

            spawn_fn.assert_called_once()
            _, kwargs = spawn_fn.call_args
            prefs = kwargs["provider_preferences"]
            assert prefs is not None, (
                "Expected resolved provider_preferences from model_role"
            )
            assert len(prefs) >= 1
            assert prefs[0].provider == "anthropic"
            assert prefs[0].model == "claude-haiku-3.5"

            # Verify resolver was called with correct args
            mock_resolve.assert_called_once()
            call_args = mock_resolve.call_args
            assert call_args[0][0] == ["fast"]  # roles
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_provider_preferences_overrides_model_role(self):
        """Explicit provider_preferences wins over model_role when both provided."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        # Even with resolver available, it should NOT be called
        mock_resolve = AsyncMock(return_value=[])
        cleanup = _install_mock_resolver(mock_resolve)

        try:
            tool = _make_delegate_tool(
                spawn_fn=spawn_fn,
                agents={
                    "test-agent": {"description": "A test agent"},
                },
                session_state={
                    "routing_matrix": {
                        "roles": {
                            "fast": {
                                "candidates": [
                                    {
                                        "provider": "anthropic",
                                        "model": "claude-haiku-3.5",
                                    },
                                ]
                            }
                        }
                    }
                },
            )

            await tool.execute(
                {
                    "agent": "test-agent",
                    "instruction": "Do something",
                    "context_depth": "none",
                    "model_role": "fast",
                    "provider_preferences": [
                        {"provider": "openai", "model": "gpt-5.2"},
                    ],
                }
            )

            spawn_fn.assert_called_once()
            _, kwargs = spawn_fn.call_args
            prefs = kwargs["provider_preferences"]
            assert len(prefs) == 1, "Explicit provider_preferences should win"
            assert prefs[0].provider == "openai"
            assert prefs[0].model == "gpt-5.2"

            # Resolver should NOT have been called
            mock_resolve.assert_not_called()
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_model_role_resolution_includes_config(self):
        """Config from resolved model_role is preserved in ProviderPreference."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        # Resolver returns a result with non-empty config
        mock_resolve = AsyncMock(
            return_value=[
                {
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-6",
                    "config": {"reasoning_effort": "high"},
                }
            ]
        )
        cleanup = _install_mock_resolver(mock_resolve)

        try:
            tool = _make_delegate_tool(
                spawn_fn=spawn_fn,
                agents={
                    "coding-agent": {"description": "A coding agent"},
                },
                session_state={
                    "routing_matrix": {
                        "roles": {
                            "coding": {
                                "candidates": [
                                    {
                                        "provider": "anthropic",
                                        "model": "claude-sonnet-4-6",
                                        "config": {"reasoning_effort": "high"},
                                    },
                                ]
                            }
                        }
                    }
                },
            )
            tool.coordinator.get = MagicMock(
                side_effect=lambda key: (
                    {"provider-anthropic": MagicMock()} if key == "providers" else None
                )
            )

            await tool.execute(
                {
                    "agent": "coding-agent",
                    "instruction": "Write a function",
                    "context_depth": "none",
                    "model_role": "coding",
                }
            )

            spawn_fn.assert_called_once()
            _, kwargs = spawn_fn.call_args
            prefs = kwargs["provider_preferences"]
            assert prefs is not None, (
                "Expected resolved provider_preferences from model_role"
            )
            assert len(prefs) == 1
            assert prefs[0].provider == "anthropic"
            assert prefs[0].model == "claude-sonnet-4-6"
            assert prefs[0].config == {"reasoning_effort": "high"}, (
                f"Expected config={{'reasoning_effort': 'high'}}, got {prefs[0].config!r}"
            )
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_model_role_without_matrix_falls_through(self):
        """model_role with no routing matrix falls through gracefully."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={
                "test-agent": {"description": "A test agent"},
            },
            session_state={},  # No routing_matrix
        )

        result = await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
                "model_role": "fast",
            }
        )

        # Should succeed (not error out) — just falls through
        assert result.success is True
        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        # No matrix means no resolution — provider_preferences stays None
        assert kwargs["provider_preferences"] is None
